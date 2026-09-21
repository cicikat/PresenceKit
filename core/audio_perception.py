"""Opt-in named STT connections and ephemeral, scoped auditory impressions."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import hashlib
import json
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

import aiohttp
from core.audio_music_contract import (
    ANALYSIS_VERSION,
    CONFIG_ROOT,
    PROMPT_LAYER,
    RECEIPT_CAPACITY,
    RECEIPT_TTL_S,
    conservative_impression,
)
from core.config_loader import get_config

SUFFIXES = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".webm", ".opus", ".amr", ".silk"}
MAX_BYTES = 25 * 1024 * 1024
TONES = frozenset({"calm", "tired", "bright", "tense", "unclear"})
_current = ContextVar("audio_impression", default=None)
_receipts = {}


def config():
    block = get_config().get("stt_presets") or {}
    return {"enabled": block.get("enabled") is True, "presets": block.get("presets") or {},
            "routes": block.get("routes") or {}}


def snapshot():
    block = config()
    result = deepcopy(block)
    for preset in result["presets"].values():
        preset["api_key_configured"] = bool(preset.pop("api_key", ""))
    name = block["routes"].get("voice_message", "")
    preset = block["presets"].get(name) or {}
    try:
        validate_preset(preset)
        ready = True
    except (ValueError, TypeError):
        ready = False
    stt_effective = block["enabled"] and ready
    analysis_on = speech_analysis_enabled()
    try:
        from core.audio_analysis import deps_ready
        analysis_deps = bool(deps_ready())
    except Exception:
        analysis_deps = False
    result.update(configured=ready, effective=stt_effective,
                  blocking_reason="disabled" if not block["enabled"] else ("" if ready else "missing_connection"),
                  source="stt_presets", purpose="voice_message",
                  legacy_local_transcribe="stt_presets" not in get_config(),
                  speech_analysis_enabled=analysis_on,
                  analysis_deps_ready=analysis_deps,
                  speech_analysis_effective=stt_effective and analysis_on and analysis_deps)
    return result


def speech_analysis_enabled() -> bool:
    """``audio_music.speech_analysis``; missing or non-true is off. STT ≠ analysis."""
    block = get_config().get(CONFIG_ROOT) or {}
    return block.get("speech_analysis") is True


def validate_preset(preset):
    base = str(preset.get("base_url", "")).strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("STT base URL must be HTTP(S), without credentials or query")
    model = str(preset.get("model", "")).strip()
    if not model or len(model) > 200:
        raise ValueError("STT model is required")
    timeout = float(preset.get("timeout_seconds", 12))
    if not 1 <= timeout <= 20:
        raise ValueError("STT timeout must be 1–20 seconds")
    return {"base_url": base, "model": model, "api_key": str(preset.get("api_key", "")),
            "timeout_seconds": timeout, "protocol": "audio_transcriptions"}


async def _request(data, filename, preset):
    form = aiohttp.FormData()
    form.add_field("file", data, filename=Path(filename).name, content_type="application/octet-stream")
    form.add_field("model", preset["model"])
    form.add_field("response_format", "json")
    headers = {"Authorization": "Bearer " + preset["api_key"]} if preset.get("api_key") else {}
    from core.proxy_config import get_aiohttp_proxy
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=preset["timeout_seconds"])) as session:
        async with session.post(preset["base_url"] + "/audio/transcriptions", data=form,
                                headers=headers, proxy=get_aiohttp_proxy(), allow_redirects=False) as response:
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.content.iter_chunked(16384):
                size += len(chunk)
                if size > 256 * 1024:
                    raise ValueError("STT response too large")
                chunks.append(chunk)
            return json.loads(b"".join(chunks))


async def ingest_audio_bytes(data, filename):
    if not data or len(data) > MAX_BYTES or Path(filename).suffix.lower() not in SUFFIXES:
        return None
    block = config()
    if not block["enabled"]:
        return None
    try:
        preset = validate_preset(block["presets"][block["routes"]["voice_message"]])
        result = await asyncio.wait_for(_request(data, filename, preset), preset["timeout_seconds"])
        text = result.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        # Optional provider field only; never infer emotion from transcript words.
        tone = result.get("tone", "unclear")
        tone = tone if isinstance(tone, str) and tone in TONES else "unclear"
        payload = {"text": text.strip()[:12000], "tone": tone}
    except Exception:
        # No payload, URL, key or provider exception is logged.
        return None
    if speech_analysis_enabled():
        try:
            payload.update(await _attach_acoustic(bytes(data), filename, payload["tone"]))
        except Exception:
            compact = _compact_acoustic(None, payload["tone"])
            payload.update({
                "tone": compact["impression"],
                "provider_tone_hint": compact.get("provider_tone_hint"),
                "acoustic": compact,
            })
    return payload


def _compact_acoustic(analysis: dict | None, provider_tone: str) -> dict:
    summary = (analysis or {}).get("pitch_summary") or {}
    pace = (analysis or {}).get("pace") or {}
    energy = (analysis or {}).get("energy") or {}
    voiced = (analysis or {}).get("voiced_ratio") or {}
    status = (analysis or {}).get("analysis_status") or "failed"
    quality = (analysis or {}).get("quality") or "insufficient"
    usable = status == "ok" and quality == "ok"
    impression = conservative_impression(
        quality="ok" if usable else (quality if quality != "ok" else "insufficient"),
        median_hz=summary.get("median_hz") if usable else None,
        variation=summary.get("variation") if usable else None,
        pace=pace.get("value") if usable else None,
        energy_dbfs=energy.get("dbfs") if usable else None,
        provider_tone=provider_tone,
    )
    return {
        "analysis_status": status,
        "quality": quality,
        "median_hz": summary.get("median_hz"),
        "pace": pace.get("value"),
        "pace_unit": pace.get("unit"),
        "energy_dbfs": energy.get("dbfs"),
        "voiced_ratio": voiced.get("value"),
        "impression": impression["impression"],
        "impression_quality": impression["quality"],
        "analysis_version": (analysis or {}).get("analysis_version") or ANALYSIS_VERSION,
        "provider_tone_hint": impression.get("provider_tone_hint"),
    }


async def _attach_acoustic(data: bytes, filename: str, provider_tone: str) -> dict:
    """Fail-open: analysis errors never invent transcript text or block STT."""
    analysis = None
    try:
        from core.audio_analysis import analyze_audio
        analysis = await analyze_audio(data, mode="speech", filename=filename)
    except Exception:
        analysis = None
    compact = _compact_acoustic(analysis, provider_tone)
    return {
        "tone": compact["impression"],
        "provider_tone_hint": compact.get("provider_tone_hint"),
        "acoustic": compact,
    }


@contextmanager
def impression(result):
    token = _current.set(result)
    try:
        yield
    finally:
        _current.reset(token)


def _acoustic_prompt(tone: str, acoustic: dict) -> str:
    parts = [
        "本轮确有语音转写。极粗听觉印象：" + tone
        + "（规则 " + str(acoustic.get("analysis_version") or ANALYSIS_VERSION)
        + "，分析 " + str(acoustic.get("analysis_status") or "failed")
        + "，质量 " + str(acoustic.get("quality") or "insufficient") + "）。",
    ]
    features = []
    if acoustic.get("analysis_status") == "ok" and acoustic.get("median_hz") is not None:
        features.append("音高中位约 %.0f Hz" % float(acoustic["median_hz"]))
    if acoustic.get("analysis_status") == "ok" and acoustic.get("pace") is not None:
        features.append("发音事件率约 %.1f onsets/s" % float(acoustic["pace"]))
    if acoustic.get("analysis_status") == "ok" and acoustic.get("energy_dbfs") is not None:
        features.append(
            "能量约 %.0f dBFS（受设备增益、距离、压缩影响，不是实际声压）"
            % float(acoustic["energy_dbfs"])
        )
    if acoustic.get("analysis_status") == "ok" and acoustic.get("voiced_ratio") is not None:
        features.append("有声占比约 %.0f%%" % (100.0 * float(acoustic["voiced_ratio"])))
    if features:
        parts.append("可读特征：" + "，".join(features) + "。")
    hint = acoustic.get("provider_tone_hint")
    if isinstance(hint, str) and hint in TONES:
        parts.append("供应商旁路 tone 仅作独立 hint：" + hint + "，不能覆盖声学失败。")
    parts.append(
        "这是不确定的听觉印象，不是情绪、健康或人格事实；unclear 表示无法判断。"
        "不要据此断言用户感受。完整音高曲线未注入。"
    )
    return "".join(parts)


def prompt_hint():
    value = _current.get()
    if not value:
        return None
    tone = value.get("tone")
    if tone not in TONES:
        tone = "unclear"
    acoustic = value.get("acoustic") if isinstance(value.get("acoustic"), dict) else None
    if acoustic:
        content = _acoustic_prompt(tone, acoustic)
    else:
        content = (
            "本轮确有语音转写。极粗听觉印象：" + tone
            + "。这是不确定的听觉印象，不是情绪、健康或人格事实；unclear 表示无法判断。不要据此断言用户感受。"
        )
    return {"role": "system", "_layer": PROMPT_LAYER, "content": content}


def _scope(channel):
    from core.scheduler.loop import _active_char_id_or_none
    uid = str((get_config().get("scheduler") or {}).get("owner_id") or "")
    return (uid, _active_char_id_or_none(), channel)


def _receipt_payload(result):
    tone = result.get("tone") if isinstance(result, dict) else None
    payload = {"tone": tone if tone in TONES else "unclear"}
    hint = result.get("provider_tone_hint") if isinstance(result, dict) else None
    if hint in TONES:
        payload["provider_tone_hint"] = hint
    acoustic = result.get("acoustic") if isinstance(result, dict) else None
    if isinstance(acoustic, dict):
        payload["acoustic"] = {
            key: acoustic.get(key)
            for key in (
                "analysis_status", "quality", "median_hz", "pace", "pace_unit",
                "energy_dbfs", "voiced_ratio", "impression", "impression_quality",
                "analysis_version", "provider_tone_hint",
            )
        }
    return payload


def issue_receipt(result, channel):
    now = time.monotonic()
    for key, row in list(_receipts.items()):
        if row[0] <= now:
            _receipts.pop(key, None)
    if len(_receipts) >= RECEIPT_CAPACITY:
        _receipts.pop(next(iter(_receipts)))
    key = secrets.token_urlsafe(24)
    _receipts[key] = (
        now + RECEIPT_TTL_S,
        _scope(channel),
        hashlib.sha256(result["text"].strip().encode()).hexdigest(),
        _receipt_payload(result),
    )
    return key


def consume_receipt(key, text, channel):
    if not isinstance(key, str) or len(key) > 128:
        return None
    row = _receipts.pop(key, None)
    if not row or not config()["enabled"] or row[0] <= time.monotonic() or row[1] != _scope(channel):
        return None
    if row[2] != hashlib.sha256(text.strip().encode()).hexdigest():
        return None
    payload = row[3]
    if isinstance(payload, str):
        return {"tone": payload if payload in TONES else "unclear"}
    if not isinstance(payload, dict):
        return {"tone": "unclear"}
    return dict(payload)


def voice_context(channel):
    def decorate(func):
        @wraps(func)
        async def wrapped(*args, **kwargs):
            body = kwargs.get("body") or (args[0] if args else {})
            value = consume_receipt(body.get("audio_perception_id"), body.get("message") or "", channel)
            with impression(value):
                return await func(*args, **kwargs)
        return wrapped
    return decorate
