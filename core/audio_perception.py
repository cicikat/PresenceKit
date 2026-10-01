"""Opt-in named STT connections and ephemeral, scoped auditory impressions."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import hashlib
import importlib.util
import io
import json
import logging
from pathlib import Path
import secrets
import time
import wave
from urllib.parse import urlsplit

import aiohttp
from core.audio_music_contract import (
    ANALYSIS_VERSION,
    CONFIG_ROOT,
    IMPRESSIONS,
    PROMPT_LAYER,
    RECEIPT_CAPACITY,
    RECEIPT_TTL_S,
    conservative_impression,
)
SUFFIXES = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".webm", ".opus", ".amr", ".silk"}
MAX_BYTES = 25 * 1024 * 1024
TONES = IMPRESSIONS  # v1 label set (v0's five plus unsteady / breathy / low_toned)
# Plain-language gloss for the prompt. No label claims an emotion, and low_toned is an
# absolute pitch + pace statement: there is no personal baseline to compare against.
TONE_GLOSS = {
    "unsteady": "音高起伏不定",
    "breathy": "气声成分偏多",
    "low_toned": "音高偏低且语速偏慢（仅按绝对音高与语速判断，没有个人基线）",
}
_current = ContextVar("audio_impression", default=None)
_receipts = {}
logger = logging.getLogger(__name__)


def get_config():
    from core.config_loader import get_config as live_get_config
    return live_get_config()


def config():
    block = (get_config() or {}).get("stt_presets") or {}
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
    legacy_local = "stt_presets" not in (get_config() or {})
    stt_effective = (block["enabled"] and ready) or (legacy_local and local_stt_installed())
    analysis_on = speech_analysis_enabled()
    try:
        from core.audio_analysis import deps_ready
        analysis_deps = bool(deps_ready())
    except Exception:
        analysis_deps = False
    try:
        from core.audio_analysis import analysis_stats
        result["voice_analysis_stats"] = analysis_stats()
    except Exception:
        result["voice_analysis_stats"] = None
    result.update(configured=ready, effective=stt_effective,
                  blocking_reason="" if stt_effective else ("missing_dependency" if legacy_local else "disabled" if not block["enabled"] else "missing_connection"),
                  source="legacy_local" if legacy_local else "stt_presets", purpose="voice_message",
                  legacy_local_transcribe=legacy_local,
                  speech_analysis_enabled=analysis_on,
                  analysis_deps_ready=analysis_deps,
                  speech_analysis_effective=stt_effective and analysis_on and analysis_deps)
    result["audio_music"] = audio_music_flags_snapshot(
        stt_effective=stt_effective,
        stt_blocking_reason=result["blocking_reason"],
        analysis_deps=analysis_deps,
        speech_on=analysis_on,
    )
    return result


def speech_analysis_enabled() -> bool:
    """``audio_music.speech_analysis``; missing or non-true is off. STT ≠ analysis."""
    block = (get_config() or {}).get(CONFIG_ROOT) or {}
    return block.get("speech_analysis") is True


def local_stt_installed() -> bool:
    return bool(importlib.util.find_spec("faster_whisper") or importlib.util.find_spec("whisper"))


def audio_music_flags_snapshot(
    *,
    stt_effective: bool | None = None,
    stt_blocking_reason: str = "",
    analysis_deps: bool | None = None,
    speech_on: bool | None = None,
) -> dict:
    """Desired vs effective for the four default-off ``audio_music`` switches.

    STT already configured is not speech analysis available. Host presence is
    observational for ``music_control``; command acceptance follows the switch.
    """
    from core.listening_store import music_analysis_enabled
    from core.music_playback_stimulus import music_autonomy_enabled
    from core.player_adapter import music_control_enabled

    if analysis_deps is None:
        try:
            from core.audio_analysis import deps_ready
            analysis_deps = bool(deps_ready())
        except Exception:
            analysis_deps = False
    if stt_effective is None:
        block = config()
        try:
            name = block["routes"].get("voice_message", "")
            validate_preset(block["presets"].get(name) or {})
            ready = True
        except (ValueError, TypeError):
            ready = False
        legacy_local = "stt_presets" not in (get_config() or {})
        stt_effective = (block["enabled"] and ready) or (legacy_local and local_stt_installed())
        stt_blocking_reason = "" if stt_effective else ("missing_dependency" if legacy_local else "disabled" if not block["enabled"] else "missing_connection")
    if speech_on is None:
        speech_on = speech_analysis_enabled()
    music_on = music_analysis_enabled()
    control_on = music_control_enabled()
    autonomy_on = music_autonomy_enabled()

    def _speech_state() -> tuple[str, str]:
        if not speech_on:
            return "disabled", "disabled"
        if not stt_effective:
            return "stt-not-effective", stt_blocking_reason or "stt-not-effective"
        if not analysis_deps:
            return "missing-dependency", "missing_dependency"
        return "enabled", ""

    def _music_state() -> tuple[str, str]:
        if not music_on:
            return "disabled", "disabled"
        if not analysis_deps:
            return "missing-dependency", "missing_dependency"
        return "enabled", ""

    def _autonomy_state() -> tuple[str, str]:
        if not autonomy_on:
            return "disabled", "disabled"
        if not control_on:
            return "music-control-off", "music_control_disabled"
        return "enabled", ""

    speech_state, speech_block = _speech_state()
    music_state, music_block = _music_state()
    autonomy_state, autonomy_block = _autonomy_state()
    return {
        "speech_analysis": {
            "desired_enabled": speech_on,
            "effective_state": speech_state,
            "blocking_reason": speech_block,
            "stt_effective": stt_effective,
            "analysis_deps_ready": analysis_deps,
            "description": "STT 已配置不等于声学分析可用；分析失败不挡文字",
        },
        "music_analysis": {
            "desired_enabled": music_on,
            "effective_state": music_state,
            "blocking_reason": music_block,
            "analysis_deps_ready": analysis_deps,
            "description": "只能分析 backend_readable 受控音频；关开关或非可读时保持 unavailable",
        },
        "music_control": {
            "desired_enabled": control_on,
            "effective_state": "enabled" if control_on else "disabled",
            "blocking_reason": "" if control_on else "disabled",
            "description": "开启后管理面 HTMLAudioElement 宿主可接受命令；网易云/媒体键不是这个开关",
        },
        "music_autonomy": {
            "desired_enabled": autonomy_on,
            "effective_state": autonomy_state,
            "blocking_reason": autonomy_block,
            "description": "关闭时账本仍可记账，但不入队 music_playback；发言仍经 autonomy",
        },
    }


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
    # OpenAI-compatible /audio/transcriptions only has a free-text `prompt`
    # field, no separate hotwords parameter. We send the bare comma-joined
    # word list (no natural-language template) to cut the Whisper decoder's
    # prompt-echo risk — see docs/audio-perception.md 结论 1 / 已拍板的决定 1.
    from core.stt_vocabulary import hotwords
    hint = hotwords()
    if hint:
        form.add_field("prompt", hint)
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
    started = time.perf_counter()
    preset_name = block["routes"].get("voice_message", "")
    ok = False
    output_hint = ""
    try:
        preset = validate_preset(block["presets"][preset_name])
        from core.stt_vocabulary import hotwords, is_prompt_echo
        hint = hotwords()
        result = await asyncio.wait_for(_request(data, filename, preset), preset["timeout_seconds"])
        text = result.get("text")
        if not isinstance(text, str) or not text.strip():
            output_hint = "empty_text"
            return None
        text = text.strip()[:12000]
        if hint and is_prompt_echo(text, hint):
            # Decoder repeated the biasing hint back as "transcription" — not
            # a real utterance. Treat exactly like "didn't catch it".
            output_hint = "prompt_echo"
            return None
        # Optional provider field only; never infer emotion from transcript words.
        tone = result.get("tone", "unclear")
        tone = tone if isinstance(tone, str) and tone in TONES else "unclear"
        from core.stt_vocabulary import correct
        payload = {"text": correct(text), "tone": tone}
        ok = True
    except Exception as error:
        # Never log payload, URL, key or provider response body — only the
        # exception class, which carries no request/response content.
        output_hint = type(error).__name__
        logger.warning("[audio_perception] 远程 STT 转写失败: %s", output_hint)
        return None
    finally:
        from core.api_call_log import append
        append(caller="stt", purpose="transcribe_remote", provider=preset_name,
               model=str((block["presets"].get(preset_name) or {}).get("model", "")),
               duration_ms=int((time.perf_counter() - started) * 1000), ok=ok,
               output_hint=output_hint)
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
    voice = (analysis or {}).get("voice_quality") or {}
    impression = conservative_impression(
        quality="ok" if usable else (quality if quality != "ok" else "insufficient"),
        median_hz=summary.get("median_hz") if usable else None,
        variation=summary.get("variation") if usable else None,
        pace=pace.get("value") if usable else None,
        energy_dbfs=energy.get("dbfs") if usable else None,
        provider_tone=provider_tone,
        linear_r2=voice.get("linear_r2") if usable else None,
        hf_ratio=voice.get("hf_ratio") if usable else None,
    )
    return {
        "analysis_status": status,
        "quality": quality,
        "median_hz": summary.get("median_hz"),
        "pace": pace.get("value"),
        "pace_unit": pace.get("unit"),
        "energy_dbfs": energy.get("dbfs"),
        "voiced_ratio": voiced.get("value"),
        "hf_ratio": voice.get("hf_ratio") if usable else None,
        "variation": summary.get("variation") if usable else None,
        "impression": impression["impression"],
        "impression_quality": impression["quality"],
        "analysis_version": (analysis or {}).get("analysis_version") or ANALYSIS_VERSION,
        "provider_tone_hint": impression.get("provider_tone_hint"),
    }


def _decode_short_speech_wav(data: bytes) -> bytes:
    """Decode a short browser recording in memory for the PCM-only analyzer."""
    from faster_whisper.audio import decode_audio
    import numpy as np

    samples = decode_audio(io.BytesIO(data), sampling_rate=16000)
    if not 0 < len(samples) <= 16000 * 120:
        raise ValueError("speech duration outside analysis budget")
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm)
    return output.getvalue()


async def _attach_acoustic(data: bytes, filename: str, provider_tone: str) -> dict:
    """Fail-open: analysis errors never invent transcript text or block STT."""
    analysis = None
    try:
        from core.audio_analysis import analyze_audio
        if Path(filename).suffix.lower() in {".webm", ".ogg", ".opus", ".m4a", ".mp3"} and len(data) <= 2 * 1024 * 1024:
            data = await asyncio.wait_for(asyncio.to_thread(_decode_short_speech_wav, data), timeout=2)
            filename = "voice.wav"
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
    if acoustic.get("analysis_status") == "ok" and acoustic.get("variation") is not None:
        features.append("音高起伏（四分位距/中位数）约 %.2f" % float(acoustic["variation"]))
    if acoustic.get("analysis_status") == "ok" and acoustic.get("hf_ratio") is not None:
        features.append("2.5 kHz 以上能量占比约 %.0f%%" % (100.0 * float(acoustic["hf_ratio"])))
    if features:
        parts.append("可读特征：" + "，".join(features) + "。")
    if tone in TONE_GLOSS:
        parts.append("该印象指" + TONE_GLOSS[tone] + "。")
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
    value["_prompt_built"] = True
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
                "energy_dbfs", "voiced_ratio", "hf_ratio", "variation",
                "impression", "impression_quality",
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
    active = config()["enabled"] or ("stt_presets" not in (get_config() or {}) and speech_analysis_enabled())
    if not row or not active or row[0] <= time.monotonic() or row[1] != _scope(channel):
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
            message = body.get("message") or ""
            receipt_text = body.get("audio_perception_text")
            if not isinstance(receipt_text, str) or not receipt_text.strip() or receipt_text.strip() not in message:
                receipt_text = message
            value = consume_receipt(body.get("audio_perception_id"), receipt_text, channel)
            with impression(value):
                result = await func(*args, **kwargs)
            if channel == "desktop" and isinstance(result, dict) and body.get("audio_perception_id"):
                return {**result, "audio_perception_applied": bool(value and value.get("_prompt_built"))}
            return result
        return wrapped
    return decorate
