"""
语音输出适配器 — 对接 GPT-SoVITS v2 整合包

接口规格（GPT-SoVITS v2 推理 API）：
  POST http://127.0.0.1:9880/tts
  Content-Type: application/json
  Body:
    text            要合成的文字
    text_lang       文本语言，固定 "zh"
    ref_audio_path  参考音频本地路径（config.tts.ref_audio，必填）
    prompt_lang     参考音频语言，固定 "zh"
    prompt_text     参考音频对应文字（config.tts.prompt_text，可留空）
    top_k           5
    top_p           1.0
    temperature     1.0
    speed_factor    语速倍率（config.tts.speed，默认 1.0）
  返回：音频流（wav bytes），HTTP 200

启用条件：config.yaml  tts.enabled = true
"""

import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import Protocol

from core.config_loader import get_config
from core.error_handler import log_error

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent

_GSV_PROVIDER = "gsv"
_RESERVED_OPENAI_COMPAT_PROVIDER = "openai_compatible"
_PROVIDER_ALIASES = {"gpt_sovits": _GSV_PROVIDER, "gsv": _GSV_PROVIDER}


def _resolve_audio_path(path: str) -> str:
    """Resolve ref_audio path: anchor relative paths, then fall back to same-stem variants."""
    if not path:
        return path
    p = Path(path) if Path(path).is_absolute() else _PROJECT_ROOT / path
    if p.exists():
        return str(p)
    # Try alternate extensions in priority order
    for ext in (".wav", ".mp3", ".MP3", ".flac", ".ogg"):
        alt = p.with_suffix(ext)
        if alt.exists():
            logger.debug(f"[voice_adapter] ref_audio fallback {p.name} → {alt.name}")
            return str(alt)
    # Glob same-stem prefix (handles names like 生气.mp4_xxx.wav)
    matches = sorted(p.parent.glob(f"{p.stem}*.wav")) + sorted(p.parent.glob(f"{p.stem}*.mp3"))
    if matches:
        logger.debug(f"[voice_adapter] ref_audio glob fallback {p.name} → {matches[0].name}")
        return str(matches[0])
    return str(p)


def _active_provider_name(cfg: dict) -> str:
    requested = str(cfg.get("provider") or _GSV_PROVIDER).strip().lower()
    return _PROVIDER_ALIASES.get(requested, requested)


def get_provider_config(cfg: dict | None = None) -> tuple[str, dict]:
    """Return active provider settings with legacy top-level GSV fields mapped in.

    Existing ``tts.api_url`` / ``ref_audio`` configurations remain authoritative
    whenever the new ``tts.providers.gsv`` block has not overridden a field.
    """
    cfg = dict(cfg or get_config().get("tts", {}))
    provider = _active_provider_name(cfg)
    provider_blocks = cfg.get("providers") if isinstance(cfg.get("providers"), dict) else {}
    legacy_gsv = {
        key: cfg[key]
        for key in (
            "api_url", "ref_audio", "prompt_text", "speed", "emotion_enabled",
            "emotions", "how_to_cut", "top_k", "top_p", "temperature",
            "ref_free", "if_freeze", "sample_steps", "if_sr", "pause_second",
        )
        if key in cfg
    }
    selected = dict(legacy_gsv if provider == _GSV_PROVIDER else {})
    selected.update(provider_blocks.get(provider) or {})
    return provider, selected


def get_provider_status(cfg: dict | None = None) -> dict:
    """Safe provider state for the admin panel; never includes credentials."""
    provider, selected = get_provider_config(cfg)
    supported = provider in {_GSV_PROVIDER, _RESERVED_OPENAI_COMPAT_PROVIDER}
    if provider == _GSV_PROVIDER:
        ready = bool(selected.get("api_url") and selected.get("ref_audio"))
        reason = "" if ready else "GSV requires api_url and ref_audio"
    elif provider == _RESERVED_OPENAI_COMPAT_PROVIDER:
        ready = False
        reason = "OpenAI-compatible/cloud provider is reserved but not activated"
    else:
        ready = False
        reason = f"Unknown TTS provider: {provider}"
    return {
        "provider": provider,
        "supported": supported,
        "ready": ready,
        "reason": reason,
        "api_key_configured": bool(selected.get("api_key")),
    }


def get_safe_provider_params(cfg: dict | None = None) -> dict:
    """Return editable active-provider settings without ever returning api_key."""
    _provider, selected = get_provider_config(cfg)
    return {key: value for key, value in selected.items() if key != "api_key"}


class TtsProvider(Protocol):
    async def synthesize(self, text: str, emotion: str, cfg: dict) -> bytes | None:
        """Synthesize an audio payload, returning None when the provider fails."""


class GsvProvider:
    async def synthesize(self, text: str, emotion: str, cfg: dict) -> bytes | None:
        api_url = str(cfg.get("api_url") or "http://127.0.0.1:9880").rstrip("/")
        if cfg.get("emotion_enabled", False):
            emotions = cfg.get("emotions", {})
            ecfg = emotions.get(emotion) or emotions.get("neutral") or {}
            ref_audio = str(ecfg.get("ref_audio", "")).strip() or str(cfg.get("ref_audio", "")).strip()
            prompt_txt = str(ecfg.get("prompt_text", "")).strip() or str(cfg.get("prompt_text", "")).strip()
            speed = float(ecfg.get("speed") or cfg.get("speed", 1.0))
        else:
            ref_audio = str(cfg.get("ref_audio", "")).strip()
            prompt_txt = str(cfg.get("prompt_text", "")).strip()
            speed = float(cfg.get("speed", 1.0))
        ref_audio = _resolve_audio_path(ref_audio)
        if not ref_audio:
            logger.warning("[voice_adapter] GSV ref_audio is not configured")
            return None

        def _sync_call():
            import os
            from gradio_client import Client, handle_file

            os.environ["no_proxy"] = "localhost,127.0.0.1,::1"
            os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
            client = Client(api_url)
            result = client.predict(
                ref_wav_path=handle_file(ref_audio),
                prompt_text=prompt_txt,
                prompt_language="中文",
                text=text,
                text_language="中文",
                how_to_cut=cfg.get("how_to_cut", "凑四句一切"),
                top_k=int(cfg.get("top_k", 15)),
                top_p=float(cfg.get("top_p", 1.0)),
                temperature=float(cfg.get("temperature", 1.0)),
                ref_free=bool(cfg.get("ref_free", False)),
                speed=speed,
                if_freeze=bool(cfg.get("if_freeze", False)),
                inp_refs=None,
                sample_steps=int(cfg.get("sample_steps", 8)),
                if_sr=bool(cfg.get("if_sr", False)),
                pause_second=float(cfg.get("pause_second", 0.3)),
                api_name="/get_tts_wav",
            )
            with open(result, "rb") as f:
                return f.read()

        return await asyncio.get_event_loop().run_in_executor(None, _sync_call)


class ReservedOpenAICompatibleProvider:
    """Configuration slot only; cloud protocol is intentionally not guessed."""

    async def synthesize(self, text: str, emotion: str, cfg: dict) -> bytes | None:
        logger.warning("[voice_adapter] openai_compatible TTS is reserved but not activated")
        return None


_PROVIDERS: dict[str, TtsProvider] = {
    _GSV_PROVIDER: GsvProvider(),
    _RESERVED_OPENAI_COMPAT_PROVIDER: ReservedOpenAICompatibleProvider(),
}


async def synthesize(text: str, emotion: str = "neutral") -> bytes | None:
    """
    将文本合成为语音，返回 wav 音频二进制数据。

    配置项（config.yaml tts 节）：
        api_url      — GPT-SoVITS API 地址，默认 http://127.0.0.1:9880
        ref_audio    — 参考音频本地路径（必填，留空时跳过合成）
        prompt_text  — 参考音频对应文字（可留空）
        speed        — 语速倍率，1.0 为正常

    成功返回 bytes，失败返回 None（已记录详细日志）。
    超时 15 秒。
    """
    provider, provider_cfg = get_provider_config()
    started_at = time.perf_counter()
    adapter = _PROVIDERS.get(provider)
    if adapter is None:
        from core.api_call_log import append
        append(caller="tts", purpose="synthesize", provider=provider, model="", duration_ms=int((time.perf_counter() - started_at) * 1000), ok=False, output_hint="unsupported_provider")
        logger.warning("[voice_adapter] unsupported provider=%s", provider)
        return None
    try:
        audio_bytes = await adapter.synthesize(text, emotion, provider_cfg)
        from core.api_call_log import append
        append(caller="tts", purpose="synthesize", provider=provider, model="", duration_ms=int((time.perf_counter() - started_at) * 1000), ok=bool(audio_bytes), output_hint=f"{len(audio_bytes)}_bytes" if audio_bytes else "empty_audio")
        if audio_bytes:
            logger.info("[voice_adapter] provider=%s synthesized %d bytes", provider, len(audio_bytes))
            return audio_bytes
        return None
    except Exception as e:
        from core.api_call_log import append
        append(caller="tts", purpose="synthesize", provider=provider, model="", duration_ms=int((time.perf_counter() - started_at) * 1000), ok=False, output_hint=type(e).__name__)
        log_error("voice_adapter.synthesize", e)
        return None


async def send_voice(target_id: str, audio_bytes: bytes, is_group: bool = False):
    """
    将音频 bytes 通过 NapCat 以语音消息形式发送（OneBot 11 record 段）。

    参数：
        target_id   — 私聊时为 user_id，群聊时为 group_id
        audio_bytes — synthesize() 返回的 wav bytes
        is_group    — True=群聊，False=私聊
    """
    from core import qq_adapter
    import subprocess, tempfile, os
    wav_path = amr_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            wav_path = f.name
        amr_path = wav_path.replace(".wav", ".amr")
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav_path, "-ar", "8000", "-ab", "12.2k", "-ac", "1", amr_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
        await qq_adapter.send_record(target_id, f"file:///{amr_path}", is_group)
    except Exception:
        b64 = base64.b64encode(audio_bytes).decode("ascii")
        await qq_adapter.send_record(target_id, f"base64://{b64}", is_group)
    finally:
        if wav_path:
            try: os.unlink(wav_path)
            except: pass
        if amr_path:
            try: os.unlink(amr_path)
            except: pass


# ── 类封装 ─────────────────────────────────────────────────────────────────────

class VoiceAdapter:
    """VoiceAdapter 类封装，代理到模块级函数"""

    async def synthesize(self, text: str, emotion: str = "neutral") -> bytes | None:
        return await synthesize(text, emotion)

    async def send_voice(self, target_id: str, audio_bytes: bytes, is_group: bool = False):
        await send_voice(target_id, audio_bytes, is_group)
