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
import contextvars
import hashlib
from io import BytesIO
import logging
import re
import time
import unicodedata
import wave
from pathlib import Path
from typing import Protocol

from core.config_loader import get_config
from core.error_handler import log_error

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent

_GSV_PROVIDER = "gsv"
_OPENAI_COMPAT_PROVIDER = "openai_compatible"
_PROVIDER_ALIASES = {"gpt_sovits": _GSV_PROVIDER, "gsv": _GSV_PROVIDER}
_DEFAULT_GPT_MODEL = "不训练直接推v3底模！"
_DEFAULT_SOVITS_MODEL = "不训练直接推v2ProPlus底模！"
_GSV_VERSIONS = frozenset({"v2", "v3", "v2Pro", "v2ProPlus"})
_GSV_SYNTHESIS_LOCK = asyncio.Lock()
# Serialises local TTS ahead of the shared local-model lock; see _hold_local_resource.
_LOCAL_TTS_QUEUE_LOCK = asyncio.Lock()
# True only inside the ``local_resource()`` block in synthesize(), so a provider
# never releases a lock it does not hold.
_HOLDS_LOCAL_RESOURCE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "presence_holds_local_resource", default=False)
_GSV_ACTIVE_MODELS: dict[str, tuple[str, str]] = {}
# Mutable {"held": bool} for the current synthesis; lets the yield helper and the
# outer context agree on whether the shared lock is really owned (cancel-safe).
_LOCAL_RESOURCE_STATE: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "presence_local_resource_state", default=None)
_GSV_DEFAULT_SEGMENT_TIMEOUT = 30.0
_GSV_DEFAULT_MODEL_SWITCH_TIMEOUT = 60.0
_GSV_PROBE_TIMEOUT = 3.0
_TTS_DEFAULT_MAX_PENDING = 4
# Runtime observability (read by /tts-config -> runtime_status).
_TTS_RUNTIME: dict = {
    "pending": 0,
    "dropped": 0,
    "current": None,
    "last_error": None,
    "last_error_at": None,
    "unhealthy_urls": {},
}


class TtsBusyError(RuntimeError):
    """Raised when the TTS queue is full; the request is dropped, never parked."""


class GsvTimeoutError(RuntimeError):
    """A GSV call exceeded its hard timeout (the worker thread may still run)."""


def get_tts_runtime_status() -> dict:
    """Read-only snapshot: lock holder, queue length, last error, GSV health."""
    from core.video_call import local_resource

    current = _TTS_RUNTIME["current"]
    return {
        "pending": _TTS_RUNTIME["pending"],
        "dropped": _TTS_RUNTIME["dropped"],
        "current": dict(current) if current else None,
        "local_resource_locked": local_resource().locked(),
        "gsv_lock_locked": _GSV_SYNTHESIS_LOCK.locked(),
        "tts_queue_locked": _LOCAL_TTS_QUEUE_LOCK.locked(),
        "last_error": _TTS_RUNTIME["last_error"],
        "last_error_at": _TTS_RUNTIME["last_error_at"],
        "healthy": not _TTS_RUNTIME["unhealthy_urls"],
        "unhealthy_urls": dict(_TTS_RUNTIME["unhealthy_urls"]),
    }


def _record_tts_error(error: BaseException, api_url: str | None = None) -> None:
    _TTS_RUNTIME["last_error"] = f"{type(error).__name__}: {str(error)[:200]}"
    _TTS_RUNTIME["last_error_at"] = time.time()
    if api_url:
        # Any failure invalidates the model cache so the next call re-switches.
        _GSV_ACTIVE_MODELS.pop(api_url, None)
        if isinstance(error, GsvTimeoutError):
            _TTS_RUNTIME["unhealthy_urls"][api_url] = time.time()


def _probe_gsv_sync(api_url: str) -> bool:
    import urllib.request

    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        opener.open(api_url, timeout=_GSV_PROBE_TIMEOUT).close()
        return True
    except Exception as error:  # HTTP error pages still prove the server is alive
        return getattr(error, "code", None) is not None


async def _ensure_gsv_healthy(api_url: str) -> None:
    """After a timeout, probe before queueing more work behind a stuck server."""
    if api_url not in _TTS_RUNTIME["unhealthy_urls"]:
        return
    loop = asyncio.get_running_loop()
    try:
        ok = await asyncio.wait_for(
            loop.run_in_executor(None, _probe_gsv_sync, api_url), _GSV_PROBE_TIMEOUT + 1)
    except asyncio.TimeoutError:
        ok = False
    if not ok:
        raise RuntimeError("GSV is unhealthy (probe failed); request skipped")
    _TTS_RUNTIME["unhealthy_urls"].pop(api_url, None)
    _GSV_ACTIVE_MODELS.pop(api_url, None)


async def _run_gsv_call(timeout: float, func, *args):
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(loop.run_in_executor(None, func, *args), timeout)
    except asyncio.TimeoutError as error:
        raise GsvTimeoutError(f"GSV call timed out after {timeout:g}s") from error


def _float_cfg(cfg: dict, key: str, default: float) -> float:
    try:
        value = float(cfg.get(key, default))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


class _hold_local_resource:
    """Take the shared local-model lock with explicit ownership tracking.

    Local TTS requests queue on ``_LOCAL_TTS_QUEUE_LOCK`` before the shared lock.
    A speaker hands the shared lock back between sentences while still holding
    ``_GSV_SYNTHESIS_LOCK``; if a second TTS request were waiting on the shared
    lock it would take it and then wait for the GSV lock, deadlocking both
    forever.  With this outer queue, only vision can win the sentence gap.
    """

    async def __aenter__(self):
        from core.video_call import local_resource

        self._lock = local_resource()
        await _LOCAL_TTS_QUEUE_LOCK.acquire()
        try:
            await self._lock.acquire()
        except BaseException:
            _LOCAL_TTS_QUEUE_LOCK.release()
            raise
        self._state = {"held": True}
        self._tokens = (_HOLDS_LOCAL_RESOURCE.set(True), _LOCAL_RESOURCE_STATE.set(self._state))
        return self

    async def __aexit__(self, *exc):
        _HOLDS_LOCAL_RESOURCE.reset(self._tokens[0])
        _LOCAL_RESOURCE_STATE.reset(self._tokens[1])
        try:
            if self._state["held"]:
                self._state["held"] = False
                self._lock.release()
        finally:
            _LOCAL_TTS_QUEUE_LOCK.release()
        return False


_GSV_HARD_BOUNDARIES = frozenset("。！？；!?")
_GSV_SOFT_BOUNDARIES = frozenset("，,、:：—–-")
_GSV_DEFAULT_SEGMENT_MAX_CHARS = 42
_GSV_DEFAULT_SEGMENT_PAUSE_SECONDS = 0.25


def _resolve_audio_path(path: str, *, char_id: str | None = None) -> str:
    """Resolve ref_audio path: anchor relative paths, then fall back to same-stem variants."""
    if not path:
        return path
    if char_id:
        try:
            from core.userdata_assets import resolve_asset_path
            logical = resolve_asset_path(category="reference_audio", logical_id=str(path), char_id=char_id)
            if logical is not None:
                return str(logical.resolve())
        except Exception:
            pass
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


def _resolve_gsv_model_path(path: str, *, char_id: str | None = None, category: str = "gpt_model") -> str:
    """Resolve a local model path when it belongs to this project.

    GPT-SoVITS also accepts its own registered model IDs, so an unknown relative
    path deliberately passes through unchanged instead of being rejected here.
    """
    raw = str(path or "").strip()
    if not raw:
        return raw
    if char_id:
        try:
            from core.userdata_assets import resolve_asset_path
            logical = resolve_asset_path(category=category, logical_id=raw, char_id=char_id)
            if logical is not None:
                return str(logical.resolve())
        except Exception:
            pass
    local_path = Path(raw.replace("\\", "/"))
    candidate = local_path if local_path.is_absolute() else _PROJECT_ROOT / local_path
    return str(candidate.resolve()) if candidate.is_file() else raw


def _gsv_model_target(cfg: dict, key: str, fallback_key: str, default: str, *, char_id: str | None = None, category: str = "gpt_model") -> str:
    return _resolve_gsv_model_path(str(cfg.get(key) or cfg.get(fallback_key) or default), char_id=char_id, category=category)


def _active_provider_name(cfg: dict) -> str:
    requested = str(cfg.get("provider") or _GSV_PROVIDER).strip().lower()
    return _PROVIDER_ALIASES.get(requested, requested)


def _char_tts_preset_name(char_id: str) -> str | None:
    """角色卡 presence_ext.tts_preset（角色资产路由：与 presence_ext.model_routing 同构）。

    fail-soft：加载失败/字段缺失 → None（回落全局 tts 配置，与现有单角色部署零迁移）。
    """
    try:
        from core import character_loader
        char = character_loader.load(char_id)
        return (getattr(char, "presence_ext", None) or {}).get("tts_preset") or None
    except Exception:
        return None


def role_tts_preset_name(char_id: str, tts: dict | None = None) -> str | None:
    """An explicit admin route takes precedence over a character-card binding."""
    tts = tts if tts is not None else get_config().get("tts", {})
    routes = tts.get("role_routes") if isinstance(tts.get("role_routes"), dict) else {}
    return routes.get(char_id) or _char_tts_preset_name(char_id)


def role_tts_generated_name(char_id: str) -> str:
    return "role_" + hashlib.sha256(char_id.encode("utf-8")).hexdigest()[:16]


def resolve_tts_config(char_id: str | None = None) -> dict:
    """把 ``tts.presets.<name>`` 命名预设叠加在全局 ``tts:`` 配置之上（角色资产路由）。

    解析顺序：
      1. char_id 为空 → 直接返回全局 tts 配置（未接线的旧调用点行为不变）。
      2. 角色卡未声明 presence_ext.tts_preset → 回落全局配置。
      3. 声明了但 tts.presets 里找不到同名预设 → 记 warning，回落全局配置（fail-soft，
         不能因为一张卡填错预设名就让语音整体哑掉）。
      4. 找到 → 预设字段覆盖全局同名字段（浅合并，预设可以只覆盖部分字段，如只换
         provider/emotions，其余沿用全局默认）。

    只做配置层合并，不改 get_provider_config() / synthesize() 已有的 provider 解析逻辑——
    合并结果原样喂给它们，兼容 legacy 顶层 GSV 字段与 providers 块两种写法。
    """
    base = dict(get_config().get("tts", {}))
    if not char_id:
        return base
    preset_name = role_tts_preset_name(char_id, base)
    if not preset_name:
        return base
    presets = base.get("presets") if isinstance(base.get("presets"), dict) else {}
    preset = presets.get(preset_name)
    if not isinstance(preset, dict):
        logger.warning(
            "[voice_adapter] char_id=%r 声明的 tts_preset=%r 未在 tts.presets 中找到，回落全局 tts 配置",
            char_id, preset_name,
        )
        return base
    merged = dict(base)
    merged.update(preset)
    return merged


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
            "api_url", "ref_audio", "prompt_text", "reference_texts", "speed", "emotion_enabled",
            "emotions", "how_to_cut", "top_k", "top_p", "temperature",
            "ref_free", "if_freeze", "sample_steps", "if_sr", "pause_second",
            "gpt_model_path", "sovits_model_path", "gpt_model_fallback", "sovits_model_fallback",
            "gsv_version", "version_models",
            "external_segment_enabled", "segment_pause_seconds", "segment_max_chars",
        )
        if key in cfg
    }
    selected = dict(legacy_gsv if provider == _GSV_PROVIDER else {})
    selected.update(provider_blocks.get(provider) or {})
    return provider, selected


def get_provider_status(cfg: dict | None = None) -> dict:
    """Safe provider state for the admin panel; never includes credentials."""
    provider, selected = get_provider_config(cfg)
    supported = provider in {_GSV_PROVIDER, _OPENAI_COMPAT_PROVIDER}
    if provider == _GSV_PROVIDER:
        ready = bool(selected.get("api_url") and selected.get("ref_audio"))
        reason = "" if ready else "GSV requires api_url and ref_audio"
        if ready and selected.get("gsv_version", "auto") != "auto":
            models = selected_gsv_models(selected)
            ready = bool(models)
            reason = "" if ready else "Selected GSV version requires both GPT and SoVITS weights"
    elif provider == _OPENAI_COMPAT_PROVIDER:
        ready = bool(selected.get("base_url"))
        reason = "" if ready else "openai_compatible requires base_url (api_key optional for unauthenticated local deployments)"
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


def reference_prompt_text(cfg: dict, ref_audio: str, *, emotion_text: str = "") -> str:
    """Use the saved text for the selected audio, including emotion overrides."""
    if emotion_text:
        return emotion_text.strip()
    texts = cfg.get("reference_texts")
    if isinstance(texts, dict) and texts:
        for key in (ref_audio, Path(ref_audio).name, Path(ref_audio).stem):
            if key in texts:
                return str(texts[key]).strip()
        return ""
    return str(cfg.get("prompt_text") or "").strip()


def selected_gsv_models(cfg: dict) -> tuple[str, str] | None:
    """Choose a saved version pair; legacy auto keeps its existing fallbacks."""
    version = str(cfg.get("gsv_version") or "auto")
    if version == "auto":
        return (
            str(cfg.get("gpt_model_path") or cfg.get("gpt_model_fallback") or _DEFAULT_GPT_MODEL),
            str(cfg.get("sovits_model_path") or cfg.get("sovits_model_fallback") or _DEFAULT_SOVITS_MODEL),
        )
    if version not in _GSV_VERSIONS:
        return None
    routes = cfg.get("version_models")
    pair = routes.get(version) if isinstance(routes, dict) else None
    if not isinstance(pair, dict):
        return None
    gpt = str(pair.get("gpt_model_path") or "").strip()
    sovits = str(pair.get("sovits_model_path") or "").strip()
    return (gpt, sovits) if gpt and sovits else None


def _is_cjk_character(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


def _sanitize_tts_text(text: str) -> str:
    """Normalize line breaks and discard characters which are unsafe to speak.

    The model output is usually ordinary Unicode, but a few call paths can pass
    literal escaped newlines or control/format characters through.  Those must
    never become an empty, malformed GSV request.  Newlines are retained as
    explicit sentence boundaries; other Unicode ``C*`` categories are ignored.
    """
    normalized = str(text or "")
    normalized = normalized.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    normalized = re.sub(r"(?<![A-Za-z0-9])/(?:r/)?n(?![A-Za-z0-9])", "\n", normalized)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    safe = []
    for char in normalized:
        if char == "\n":
            safe.append(char)
        elif unicodedata.category(char).startswith("C") or char == "\ufffd":
            continue
        else:
            safe.append(char)
    return "".join(safe)


def _split_sentence_boundaries(text: str) -> list[str]:
    """Split at hard sentence boundaries and newlines, keeping punctuation."""
    parts: list[str] = []
    current: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\n":
            candidate = "".join(current).strip()
            if candidate:
                parts.append(candidate)
            current = []
            index += 1
            continue
        current.append(char)
        is_ellipsis = char == "…" or (char == "." and (index + 1 == len(text) or text[index + 1].isspace()))
        if char in _GSV_HARD_BOUNDARIES or is_ellipsis:
            while index + 1 < len(text) and text[index + 1] in "…!?。":
                index += 1
                current.append(text[index])
            candidate = "".join(current).strip()
            if candidate:
                parts.append(candidate)
            current = []
        index += 1
    candidate = "".join(current).strip()
    if candidate:
        parts.append(candidate)
    return parts


def _split_long_segment(text: str, max_chars: int) -> list[str]:
    """Use comma/dash boundaries only when a hard-bounded segment is too long."""
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    remainder = text.strip()
    while len(remainder) > max_chars:
        boundary = max(
            (index for index, char in enumerate(remainder[: max_chars + 1]) if char in _GSV_SOFT_BOUNDARIES),
            default=-1,
        )
        cut_at = boundary + 1 if boundary >= 0 else max_chars
        piece = remainder[:cut_at].strip()
        if piece:
            pieces.append(piece)
        remainder = remainder[cut_at:].lstrip()
    if remainder:
        pieces.append(remainder)
    return pieces


def _split_language_runs(text: str) -> list[tuple[str, str]]:
    """Separate Chinese and Latin speech so GSV receives the correct mode."""
    runs: list[tuple[str, str]] = []
    current: list[str] = []
    current_language: str | None = None

    def flush() -> None:
        nonlocal current, current_language
        value = "".join(current).strip()
        if current_language == "英文":
            value = value.translate(str.maketrans({
                "。": ".", "！": "!", "？": "?", "；": ";",
                "，": ",", "、": ",", "：": ":",
            }))
        if value and current_language:
            runs.append((value, current_language))
        current = []
        current_language = None

    pending_neutral: list[str] = []
    for char in text:
        language = "中文" if _is_cjk_character(char) else "英文" if char.isascii() and char.isalpha() else None
        if language is None:
            if current_language is None:
                pending_neutral.append(char)
            else:
                current.append(char)
            continue
        if current_language is None:
            current_language = language
            current.extend(pending_neutral)
            pending_neutral = []
        elif language != current_language:
            flush()
            current_language = language
        current.append(char)
    if current_language is not None:
        current.extend(pending_neutral)
    flush()
    return runs


def split_gsv_segments(text: str, cfg: dict) -> list[tuple[str, str]]:
    """Return clean GSV requests as ``(text, text_language)`` pairs.

    GSV's internal multi-sentence splitter can lose the start of a following
    sentence.  We therefore split here, ask GSV to use ``不切``, and later join
    the returned WAV files.  Commas/dashes stay within a sentence unless the
    sentence exceeds the configured safety limit.
    """
    cleaned = _sanitize_tts_text(text).strip()
    if not cleaned:
        return []
    if cfg.get("external_segment_enabled", True) is False:
        return [(cleaned, "中文")]
    try:
        max_chars = int(cfg.get("segment_max_chars", _GSV_DEFAULT_SEGMENT_MAX_CHARS))
    except (TypeError, ValueError):
        max_chars = _GSV_DEFAULT_SEGMENT_MAX_CHARS
    max_chars = max(12, min(max_chars, 200))
    segments: list[tuple[str, str]] = []
    for sentence in _split_sentence_boundaries(cleaned):
        for piece in _split_long_segment(sentence, max_chars):
            segments.extend(_split_language_runs(piece))
    return segments


def _join_pcm_wavs(wavs: list[bytes], pause_seconds: float) -> bytes | None:
    """Join matching PCM WAV payloads, returning None instead of corrupt audio."""
    if not wavs:
        return None
    if len(wavs) == 1:
        return wavs[0]
    try:
        decoded: list[tuple[wave._wave_params, bytes]] = []
        for payload in wavs:
            with wave.open(BytesIO(payload), "rb") as source:
                params = source.getparams()
                if params.comptype != "NONE":
                    raise wave.Error("compressed WAV is not safe to concatenate")
                decoded.append((params, source.readframes(source.getnframes())))
        first_params = decoded[0][0]
        signature = (
            first_params.nchannels,
            first_params.sampwidth,
            first_params.framerate,
            first_params.comptype,
        )
        if any((p.nchannels, p.sampwidth, p.framerate, p.comptype) != signature for p, _ in decoded[1:]):
            raise wave.Error("GSV WAV parameters differ between segments")
        pause_frames = int(max(0.0, min(pause_seconds, 1.0)) * first_params.framerate)
        silence = b"\x00" * pause_frames * first_params.nchannels * first_params.sampwidth
        output = BytesIO()
        with wave.open(output, "wb") as target:
            target.setparams(first_params)
            for index, (_, frames) in enumerate(decoded):
                if index:
                    target.writeframesraw(silence)
                target.writeframesraw(frames)
        return output.getvalue()
    except (EOFError, OSError, wave.Error) as error:
        logger.warning("[voice_adapter] cannot safely concatenate GSV WAV segments: %s", error)
        return None


async def _yield_shared_local_resource() -> None:
    """Release and re-take the shared local-model lock held by this synthesis.

    Local TTS and local vision must not run at once (one GPU), but holding the
    lock for a whole multi-sentence reply starved the video-call camera.  Only the
    call that took the lock may hand it back, hence the ContextVar guard.
    """
    from core.video_call import local_resource

    state = _LOCAL_RESOURCE_STATE.get()
    if not _HOLDS_LOCAL_RESOURCE.get() or not state or not state["held"]:
        return
    lock = local_resource()
    lock.release()
    state["held"] = False
    # Let a waiting vision request win the lock before we ask for it back.
    # If cancelled here or inside acquire(), held stays False so the outer
    # context never releases a lock it does not own.
    await asyncio.sleep(0)
    await lock.acquire()
    state["held"] = True


class TtsProvider(Protocol):
    async def synthesize(self, text: str, emotion: str, cfg: dict) -> bytes | None:
        """Synthesize an audio payload, returning None when the provider fails."""


class GsvProvider:
    async def synthesize(self, text: str, emotion: str, cfg: dict) -> bytes | None:
        api_url = str(cfg.get("api_url") or "http://127.0.0.1:9880").rstrip("/")
        char_id = str(cfg.get("_presence_char_id") or "") or None
        if cfg.get("emotion_enabled", False):
            emotions = cfg.get("emotions", {})
            ecfg = emotions.get(emotion) or emotions.get("neutral") or {}
            ref_audio = str(ecfg.get("ref_audio", "")).strip() or str(cfg.get("ref_audio", "")).strip()
            prompt_txt = reference_prompt_text(cfg, ref_audio, emotion_text=str(ecfg.get("prompt_text", "")))
            speed = float(ecfg.get("speed") or cfg.get("speed", 1.0))
        else:
            ref_audio = str(cfg.get("ref_audio", "")).strip()
            prompt_txt = reference_prompt_text(cfg, ref_audio)
            speed = float(cfg.get("speed", 1.0))
        if cfg.get("ref_free", False):
            prompt_txt = ""
        ref_audio = _resolve_audio_path(ref_audio, char_id=char_id)
        if not ref_audio:
            logger.warning("[voice_adapter] GSV ref_audio is not configured")
            return None

        model_pair = selected_gsv_models(cfg)
        if model_pair is None:
            logger.warning("[voice_adapter] selected GSV version lacks a complete model pair")
            return None
        gpt_model = _resolve_gsv_model_path(model_pair[0], char_id=char_id, category="gpt_model")
        sovits_model = _resolve_gsv_model_path(model_pair[1], char_id=char_id, category="sovits_model")
        segments = split_gsv_segments(text, cfg)
        if not segments:
            logger.warning("[voice_adapter] no speakable GSV text remains after sanitization")
            return None
        try:
            pause_seconds = float(cfg.get("segment_pause_seconds", _GSV_DEFAULT_SEGMENT_PAUSE_SECONDS))
        except (TypeError, ValueError):
            pause_seconds = _GSV_DEFAULT_SEGMENT_PAUSE_SECONDS

        def _build_client():
            import os
            from gradio_client import Client, handle_file

            os.environ["no_proxy"] = "localhost,127.0.0.1,::1"
            os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
            client = Client(api_url)
            active_models = _GSV_ACTIVE_MODELS.get(api_url)
            desired_models = (gpt_model, sovits_model)
            if active_models != desired_models:
                # Model selection is global mutable state inside a GSV server.
                # The surrounding async lock keeps a different request from
                # changing weights between this switch and its synthesis call.
                def switch_or_fallback(*, api_name: str, argument: str, desired: str, fallback: str) -> str:
                    try:
                        client.predict(**{argument: desired}, api_name=api_name)
                        return desired
                    except Exception as error:
                        if desired == fallback:
                            raise
                        logger.warning(
                            "[voice_adapter] GSV model switch failed for %s; falling back to base model: %s",
                            argument,
                            error,
                        )
                        client.predict(**{argument: fallback}, api_name=api_name)
                        return fallback

                active_sovits = switch_or_fallback(
                    api_name="/change_sovits_weights",
                    argument="sovits_path",
                    desired=sovits_model,
                    fallback=_DEFAULT_SOVITS_MODEL,
                )
                active_gpt = switch_or_fallback(
                    api_name="/change_gpt_weights",
                    argument="gpt_path",
                    desired=gpt_model,
                    fallback=_DEFAULT_GPT_MODEL,
                )
                _GSV_ACTIVE_MODELS[api_url] = (active_gpt, active_sovits)
            return client

        def _synthesize_segment(client, segment_text: str, segment_language: str) -> bytes:
            from gradio_client import handle_file

            result = client.predict(
                ref_wav_path=handle_file(ref_audio),
                prompt_text=prompt_txt,
                prompt_language=str(cfg.get("prompt_language") or "中文"),
                text=segment_text,
                text_language=segment_language,
                # We have already segmented this request.  Letting GSV cut
                # again reintroduces the v2 sentence-initial word loss.
                how_to_cut="不切",
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
            with open(result, "rb") as source:
                return source.read()

        yield_between_segments = (
            bool(cfg.get("yield_shared_resource_between_segments", True))
            and len(segments) >= int(cfg.get("yield_shared_resource_min_segments", 2))
        )
        segment_timeout = _float_cfg(cfg, "segment_timeout_seconds", _GSV_DEFAULT_SEGMENT_TIMEOUT)
        switch_timeout = _float_cfg(cfg, "model_switch_timeout_seconds", _GSV_DEFAULT_MODEL_SWITCH_TIMEOUT)
        await _ensure_gsv_healthy(api_url)
        try:
            async with _GSV_SYNTHESIS_LOCK:
                _TTS_RUNTIME["current"] = {
                    "api_url": api_url, "segments": len(segments), "started_at": time.time()}
                try:
                    client = await _run_gsv_call(switch_timeout, _build_client)
                    wavs: list[bytes] = []
                    for index, (segment_text, segment_language) in enumerate(segments):
                        if index and yield_between_segments:
                            # Hand the shared local-model lock back between sentences so a
                            # video-call frame can slip into the gap instead of starving.
                            await _yield_shared_local_resource()
                        wavs.append(await _run_gsv_call(
                            segment_timeout, _synthesize_segment, client, segment_text, segment_language))
                finally:
                    _TTS_RUNTIME["current"] = None
            joined = _join_pcm_wavs(wavs, pause_seconds)
            if joined is None:
                raise RuntimeError("GSV returned incompatible WAV segments")
            return joined
        except BaseException as error:
            _record_tts_error(error, api_url)
            raise


class OpenAICompatibleProvider:
    """OpenAI ``POST {base_url}/audio/speech`` 协议的通用 provider（Brief 107·B2）。

    覆盖两类真实场景：真·OpenAI/走同协议的云服务；以及自建开源 TTS 套了一层
    OpenAI 兼容外壳的部署（如 openai-edge-tts 之类的反代/网关，或引擎自带的
    openai-compat 模式）。不绑定具体厂商，所有字段都从
    ``tts.providers.openai_compatible`` 读，缺 base_url 直接判不可用（不去猜协议）。

    per-emotion 音色切换与 GSV 的 ``emotions`` 块同构，但 GSV 换的是参考音频文件，
    这里换的是 ``voice`` 预置音色名（云端/远程引擎通常不支持上传参考音频）：
        emotions:
          happy: {voice: "shimmer", speed: 1.1}
          sad:   {voice: "onyx", speed: 0.9}
    也接受纯字符串简写 ``happy: "shimmer"``（等价于只覆盖 voice，speed 用顶层默认）。
    """

    async def synthesize(self, text: str, emotion: str, cfg: dict) -> bytes | None:
        base_url = str(cfg.get("base_url") or "").rstrip("/")
        if not base_url:
            logger.warning("[voice_adapter] openai_compatible TTS 缺少 base_url，跳过")
            return None
        model = str(cfg.get("model") or "tts-1")
        voice = str(cfg.get("voice") or "alloy")
        response_format = str(cfg.get("response_format") or "wav")
        api_key = str(cfg.get("api_key") or "")
        speed = float(cfg.get("speed", 1.0))

        if cfg.get("emotion_enabled", False):
            voice_map = cfg.get("emotions", {}) or {}
            evoice = voice_map.get(emotion) or voice_map.get("neutral")
            if isinstance(evoice, dict):
                voice = str(evoice.get("voice") or voice)
                speed = float(evoice.get("speed") or speed)
            elif isinstance(evoice, str) and evoice:
                voice = evoice

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": model,
            "input": text,
            "voice": voice,
            "response_format": response_format,
            "speed": speed,
        }

        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=float(cfg.get("timeout", 20.0)))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{base_url}/audio/speech", json=payload, headers=headers
                ) as resp:
                    if resp.status != 200:
                        body = (await resp.text())[:300]
                        logger.warning(
                            "[voice_adapter] openai_compatible TTS 返回非 200: "
                            "status=%s body=%s", resp.status, body,
                        )
                        return None
                    return await resp.read()
        except Exception as e:
            log_error("voice_adapter.openai_compatible.synthesize", e)
            return None


_PROVIDERS: dict[str, TtsProvider] = {
    _GSV_PROVIDER: GsvProvider(),
    _OPENAI_COMPAT_PROVIDER: OpenAICompatibleProvider(),
}


def clean_tts_text(text: str) -> str:
    """Remove non-spoken parenthetical narration before on-demand synthesis.

    This is deliberately shared by QQ's proactive TTS and HTTP clients so a
    Dream reply never has a different narration rule depending on the channel.
    """
    from core.control_markers import strip_control_markers
    cleaned = strip_control_markers(str(text or ""))
    cleaned = re.sub(r"（[^）]*）", "", cleaned)
    cleaned = re.sub(r"\([^)]*\)", "", cleaned)
    # Render tags are visual-only and should never become literal spoken words.
    cleaned = re.sub(r"<[^>\n]{0,80}>", "", cleaned)
    return _sanitize_tts_text(cleaned).strip()


async def synthesize(text: str, emotion: str = "neutral", *, char_id: str | None = None) -> bytes | None:
    """
    将文本合成为语音，返回 wav 音频二进制数据。

    配置项（config.yaml tts 节）：
        api_url      — GPT-SoVITS API 地址，默认 http://127.0.0.1:9880
        ref_audio    — 参考音频本地路径（必填，留空时跳过合成）
        prompt_text  — 参考音频对应文字（可留空）
        speed        — 语速倍率，1.0 为正常

    char_id：给定时按该角色卡 presence_ext.tts_preset 解析命名预设（角色资产路由，
    见 resolve_tts_config()）；省略时用全局 tts 配置（未接线调用点的现状行为）。

    成功返回 bytes，失败返回 None（已记录详细日志）。
    超时：GSV 每段默认 30s、模型切换默认 60s（segment_timeout_seconds /
    model_switch_timeout_seconds）；等待队列上限 max_pending（默认 4），满则丢弃。
    """
    text = clean_tts_text(text)
    provider, provider_cfg = get_provider_config(resolve_tts_config(char_id))
    if char_id:
        provider_cfg = dict(provider_cfg)
        provider_cfg["_presence_char_id"] = char_id
    started_at = time.perf_counter()
    if not text:
        from core.api_call_log import append
        append(caller="tts", purpose="synthesize", provider=provider, model="", duration_ms=0, ok=False, output_hint="empty_text")
        logger.warning("[voice_adapter] text is empty after TTS sanitization")
        return None
    adapter = _PROVIDERS.get(provider)
    if adapter is None:
        from core.api_call_log import append
        append(caller="tts", purpose="synthesize", provider=provider, model="", duration_ms=int((time.perf_counter() - started_at) * 1000), ok=False, output_hint="unsupported_provider")
        logger.warning("[voice_adapter] unsupported provider=%s", provider)
        return None
    try:
        max_pending = int(provider_cfg.get("max_pending", _TTS_DEFAULT_MAX_PENDING))
    except (TypeError, ValueError):
        max_pending = _TTS_DEFAULT_MAX_PENDING
    if _TTS_RUNTIME["pending"] >= max(1, max_pending):
        from core.api_call_log import append
        _TTS_RUNTIME["dropped"] += 1
        append(caller="tts", purpose="synthesize", provider=provider, model="", duration_ms=0, ok=False, output_hint="queue_full")
        logger.warning("[voice_adapter] TTS queue full (%d pending); request dropped", _TTS_RUNTIME["pending"])
        return None
    _TTS_RUNTIME["pending"] += 1
    try:
        from urllib.parse import urlsplit
        endpoint = str(provider_cfg.get("api_url") or provider_cfg.get("base_url") or "")
        local_tts = urlsplit(endpoint).hostname in {"localhost", "127.0.0.1", "::1"}
        if local_tts:
            async with _hold_local_resource():
                audio_bytes = await adapter.synthesize(text, emotion, provider_cfg)
        else:
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
    finally:
        _TTS_RUNTIME["pending"] -= 1


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
        with open(amr_path, "rb") as converted_audio:
            b64 = base64.b64encode(converted_audio.read()).decode("ascii")
        await qq_adapter.send_record(target_id, f"base64://{b64}", is_group)
    except Exception as error:
        logger.warning("[voice_adapter] ffmpeg/AMR path failed, falling back to base64 wav: %s", error)
        b64 = base64.b64encode(audio_bytes).decode("ascii")
        await qq_adapter.send_record(target_id, f"base64://{b64}", is_group)
    finally:
        for path in (wav_path, amr_path):
            if path:
                try:
                    os.unlink(path)
                except OSError as error:
                    logger.debug("[voice_adapter] temp cleanup failed for %s: %s", path, error)


# ── 类封装 ─────────────────────────────────────────────────────────────────────

class VoiceAdapter:
    """VoiceAdapter 类封装，代理到模块级函数"""

    async def synthesize(self, text: str, emotion: str = "neutral") -> bytes | None:
        return await synthesize(text, emotion)

    async def send_voice(self, target_id: str, audio_bytes: bytes, is_group: bool = False):
        await send_voice(target_id, audio_bytes, is_group)
