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
    result.update(configured=ready, effective=block["enabled"] and ready,
                  blocking_reason="disabled" if not block["enabled"] else ("" if ready else "missing_connection"),
                  source="stt_presets", purpose="voice_message",
                  legacy_local_transcribe="stt_presets" not in get_config())
    return result


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
        return {"text": text.strip()[:12000], "tone": tone}
    except Exception:
        # No payload, URL, key or provider exception is logged.
        return None


@contextmanager
def impression(result):
    token = _current.set(result)
    try:
        yield
    finally:
        _current.reset(token)


def prompt_hint():
    value = _current.get()
    if not value:
        return None
    tone = value.get("tone")
    if tone not in TONES:
        tone = "unclear"
    return {"role": "system", "_layer": "3.8_audio_impression",
            "content": "本轮确有语音转写。极粗听觉印象：" + tone
            + "。这是不确定的听觉印象，不是情绪、健康或人格事实；unclear 表示无法判断。不要据此断言用户感受。"}


def _scope(channel):
    from core.scheduler.loop import _active_char_id_or_none
    uid = str((get_config().get("scheduler") or {}).get("owner_id") or "")
    return (uid, _active_char_id_or_none(), channel)


def issue_receipt(result, channel):
    now = time.monotonic()
    for key, row in list(_receipts.items()):
        if row[0] <= now:
            _receipts.pop(key, None)
    if len(_receipts) >= 128:
        _receipts.pop(next(iter(_receipts)))
    key = secrets.token_urlsafe(24)
    _receipts[key] = (now + 300, _scope(channel), hashlib.sha256(result["text"].strip().encode()).hexdigest(), result["tone"])
    return key


def consume_receipt(key, text, channel):
    if not isinstance(key, str) or len(key) > 128:
        return None
    row = _receipts.pop(key, None)
    if not row or not config()["enabled"] or row[0] <= time.monotonic() or row[1] != _scope(channel):
        return None
    if row[2] != hashlib.sha256(text.strip().encode()).hexdigest():
        return None
    return {"tone": row[3]}


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
