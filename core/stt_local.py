"""Local speech-to-text runtime (faster-whisper) with explicit, hot-swappable settings.

Config block ``stt_local`` (defaults are chosen for unknown, CPU-only hardware):

    stt_local:
      model_size: small        # tiny | base | small | medium | large-v3
      device: auto             # auto | cpu | cuda
      compute_type: int8       # int8 | int8_float16 | float16 | float32
      beam_size: 5
      timeout_seconds: 20

Measured on the dev box (CPU): ``small/int8`` is ~4x faster than ``base/int8`` on
12 s segments and more accurate.  GPU is opt-in: ``auto`` only adopts CUDA after a
real tiny inference succeeds, otherwise it falls back to CPU *and records why*.
An explicit ``cuda`` that cannot run is an error, never a silent downgrade.

This is the local-runtime half; the remote OpenAI-compatible connection stays in
``stt_presets`` (``core/audio_perception.py``).
"""
from __future__ import annotations

import importlib.util
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

MODEL_SIZES = ("tiny", "base", "small", "medium", "large-v3")
DEVICES = ("auto", "cpu", "cuda")
COMPUTE_TYPES = ("int8", "int8_float16", "float16", "float32")
DEFAULTS: dict[str, Any] = {
    "model_size": "small",
    "device": "auto",
    "compute_type": "int8",
    "beam_size": 5,
    "timeout_seconds": 20.0,
}
_CUDA_ONLY_COMPUTE = {"float16", "int8_float16"}
_FAILURE_RETRY_SECONDS = 30.0


class SttLocalError(RuntimeError):
    """Local STT cannot run with the requested settings; ``str(error)`` is user-facing."""


# ── settings ────────────────────────────────────────────────────────────────

def validate(payload: dict[str, Any]) -> dict[str, Any]:
    """Strict validation for admin writes; raises ValueError naming the bad field."""
    block = payload if isinstance(payload, dict) else {}
    out = dict(DEFAULTS)
    if "model_size" in block:
        if block["model_size"] not in MODEL_SIZES:
            raise ValueError(f"model_size 必须是 {'/'.join(MODEL_SIZES)}")
        out["model_size"] = block["model_size"]
    if "device" in block:
        if block["device"] not in DEVICES:
            raise ValueError(f"device 必须是 {'/'.join(DEVICES)}")
        out["device"] = block["device"]
    if "compute_type" in block:
        if block["compute_type"] not in COMPUTE_TYPES:
            raise ValueError(f"compute_type 必须是 {'/'.join(COMPUTE_TYPES)}")
        out["compute_type"] = block["compute_type"]
    if "beam_size" in block:
        beam = block["beam_size"]
        if isinstance(beam, bool) or not isinstance(beam, int) or not 1 <= beam <= 10:
            raise ValueError("beam_size 必须是 1–10 的整数")
        out["beam_size"] = beam
    if "timeout_seconds" in block:
        timeout = block["timeout_seconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 5 <= timeout <= 120:
            raise ValueError("timeout_seconds 必须在 5–120 之间")
        out["timeout_seconds"] = float(timeout)
    if out["device"] == "cpu" and out["compute_type"] in _CUDA_ONLY_COMPUTE:
        raise ValueError(f"compute_type={out['compute_type']} 只能在 cuda 上使用，cpu 请选 int8 或 float32")
    return out


def settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Runtime read: a bad hand-edited block degrades to defaults instead of breaking STT."""
    if config is None:
        from core.config_loader import get_config
        config = get_config() or {}
    try:
        return validate(config.get("stt_local") or {})
    except ValueError as error:
        logger.warning("[stt_local] 配置无效，使用默认值: %s", error)
        return dict(DEFAULTS)


def _key(cfg: dict[str, Any]) -> tuple:
    return (cfg["model_size"], cfg["device"], cfg["compute_type"])


# ── hardware probing ────────────────────────────────────────────────────────

def diagnose(error: BaseException | str) -> str:
    """Turn a CUDA loader error into a next step a person can act on."""
    text = str(error)
    low = text.lower()
    if "cublas" in low:
        return ("缺少 cuBLAS 12 运行库（cublas64_12）。安装 CUDA 12 运行库，"
                "或在配置里把 device 改回 cpu。原始错误: " + text)
    if "cudnn" in low:
        return ("缺少 cuDNN 9 运行库。安装 cuDNN 9，或把 device 改回 cpu。原始错误: " + text)
    if "out of memory" in low:
        return "显存不足（可能被其他本地模型占用）。换更小的模型、降低精度，或改用 cpu。原始错误: " + text
    return text


def probe_hardware() -> dict[str, Any]:
    """Read-only capability probe for the admin page; never loads a Whisper model."""
    info: dict[str, Any] = {"faster_whisper": False, "cuda_devices": 0, "devices": ["cpu"],
                            "missing_runtime": "", "hint": ""}
    try:
        import faster_whisper  # noqa: F401
        info["faster_whisper"] = True
    except ImportError:
        info["hint"] = "未安装 faster-whisper：pip install faster-whisper"
        return info
    try:
        import ctranslate2
        info["cuda_devices"] = int(ctranslate2.get_cuda_device_count())
    except Exception as error:  # noqa: BLE001 - probing must not raise
        info["missing_runtime"] = diagnose(error)
    if info["cuda_devices"] > 0:
        info["devices"].append("cuda")
        info["hint"] = ("检测到 CUDA 设备，但这只说明驱动可见；能否真正推理要看运行库，"
                        "auto 会实际跑一次极短推理再决定。")
    else:
        info["hint"] = info["missing_runtime"] or "没有检测到 CUDA 设备，将使用 CPU。"
    return info


# ── model lifecycle ─────────────────────────────────────────────────────────

_build_lock = threading.Lock()   # serialises (re)builds; requests hold their own model ref
_active: dict[str, Any] | None = None
_failures: dict[tuple, tuple[float, str]] = {}
_last_change: dict[str, Any] = {}


def _load(model_size: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel
    return WhisperModel(model_size, device=device, compute_type=compute_type)


def _smoke(model) -> None:
    """One real, tiny inference: a model that loads can still fail at first kernel launch."""
    import numpy as np
    segments, _ = model.transcribe(np.zeros(8000, dtype="float32"), language="zh",
                                   vad_filter=False, beam_size=1)
    list(segments)


def _cuda_device_count() -> int:
    try:
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count())
    except Exception:  # noqa: BLE001
        return 0


def _build(cfg: dict[str, Any]) -> dict[str, Any]:
    """Build a model for ``cfg`` or raise SttLocalError. Does not touch ``_active``."""
    size, device, compute = cfg["model_size"], cfg["device"], cfg["compute_type"]
    fallback: dict[str, str] | None = None
    if device in ("auto", "cuda"):
        reason = ""
        if _cuda_device_count() <= 0:
            reason = "没有检测到 CUDA 设备"
        else:
            try:
                model = _load(size, "cuda", compute)
                _smoke(model)
                return {"model": model, "backend": "faster_whisper", "device": "cuda",
                        "model_size": size, "compute_type": compute, "fallback": None}
            except ImportError:
                raise
            except Exception as error:  # noqa: BLE001
                reason = diagnose(error)
        if device == "cuda":
            raise SttLocalError("device=cuda 无法使用：" + reason)
        logger.warning("[stt_local] device=auto 回落 CPU：%s", reason)
        fallback = {"from": "cuda", "reason": reason}
        compute = "int8" if compute in _CUDA_ONLY_COMPUTE else compute
    try:
        model = _load(size, "cpu", compute)
    except ImportError as error:
        raise error
    except Exception as error:  # noqa: BLE001
        raise SttLocalError(f"本地 Whisper {size}/cpu/{compute} 加载失败：{error}") from error
    return {"model": model, "backend": "faster_whisper", "device": "cpu",
            "model_size": size, "compute_type": compute, "fallback": fallback}


def _build_legacy(cfg: dict[str, Any]) -> dict[str, Any]:
    """openai-whisper compatibility when faster-whisper is not installed."""
    try:
        import whisper as _whisper
    except ImportError as error:
        raise SttLocalError("STT 未安装，请 pip install faster-whisper 或 openai-whisper") from error
    size = cfg["model_size"] if cfg["model_size"] != "large-v3" else "large"
    return {"model": _whisper.load_model(size), "backend": "whisper", "device": "cpu",
            "model_size": size, "compute_type": "float32", "fallback": None}


def _activate(cfg: dict[str, Any]) -> dict[str, Any]:
    global _active
    try:
        built = _build(cfg)
    except ImportError:
        built = _build_legacy(cfg)
    built["key"] = _key(cfg)
    built["loaded_at"] = time.time()
    _active = built
    _failures.pop(_key(cfg), None)
    logger.info("[stt_local] 已加载 %s/%s/%s%s", built["model_size"], built["device"],
                built["compute_type"],
                f"（回落：{built['fallback']['reason']}）" if built["fallback"] else "")
    return built


def get_backend(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the active backend for ``cfg``, rebuilding when the settings changed.

    In-flight requests keep the instance they already hold; a swap only replaces
    ``_active`` after the new model is fully built and smoke-tested.
    """
    cfg = cfg or settings()
    active = _active
    if active is not None and active["key"] == _key(cfg):
        return active
    with _build_lock:
        active = _active
        if active is not None and active["key"] == _key(cfg):
            return active
        recent = _failures.get(_key(cfg))
        if recent and time.monotonic() - recent[0] < _FAILURE_RETRY_SECONDS:
            raise SttLocalError(recent[1])
        try:
            return _activate(cfg)
        except SttLocalError as error:
            _failures[_key(cfg)] = (time.monotonic(), str(error))
            raise


def reload(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply new settings now (admin save). On failure the previous instance stays live."""
    global _last_change
    cfg = cfg or settings()
    previous = _active
    with _build_lock:
        _failures.pop(_key(cfg), None)
        try:
            built = _activate(cfg)
            _last_change = {"ok": True, "at": time.time(), "error": ""}
            return {"ok": True, "effective": _effective(built)}
        except SttLocalError as error:
            _failures[_key(cfg)] = (time.monotonic(), str(error))
            _last_change = {"ok": False, "at": time.time(), "error": str(error)}
            return {"ok": False, "error": str(error),
                    "effective": _effective(previous) if previous else None}


def _effective(active: dict[str, Any]) -> dict[str, Any]:
    return {"backend": active["backend"], "model_size": active["model_size"],
            "device": active["device"], "compute_type": active["compute_type"],
            "fallback": active["fallback"]}


def snapshot(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """configured / effective / blocking_reason, for the admin page and observability."""
    cfg = settings(config)
    active = _active
    failure = _failures.get(_key(cfg))
    return {
        "configured": cfg,
        "effective": _effective(active) if active else None,
        "matches_config": bool(active and active["key"] == _key(cfg)),
        "blocking_reason": failure[1] if failure else "",
        "last_change": dict(_last_change),
    }


def reset_for_tests() -> None:
    global _active, _last_change
    _active = None
    _last_change = {}
    _failures.clear()


# ── startup warmup（工单 B）──────────────────────────────────────────────────

def _local_stt_selected() -> bool:
    """True when this deployment will actually use the local backend.

    A pure-remote deployment (``stt_presets`` configured and enabled) should
    not pay the model-load cost. ``faster_whisper``/``whisper`` missing is
    also "not selected" — ``get_backend()`` would just raise.
    """
    try:
        from core import audio_perception
        remote = audio_perception.config()
        if remote.get("enabled"):
            return False
    except Exception:  # noqa: BLE001 - never block warmup decision on this
        pass
    return bool(importlib.util.find_spec("faster_whisper") or importlib.util.find_spec("whisper"))


def warmup() -> None:
    """Build (and smoke-test) the local backend now, off the event loop.

    Fire-and-forget: callers must not await this to completion from the
    startup path. Fails closed — any exception here only means the first
    real request pays the cold-start cost it would have paid anyway.
    """
    try:
        if not _local_stt_selected():
            return
        get_backend(settings())
        logger.info("[stt_local] 启动预热完成")
    except Exception as error:  # noqa: BLE001 - warmup must never crash startup
        logger.warning("[stt_local] 启动预热失败（首次请求仍会重试）: %s", error)


async def warmup_async() -> None:
    """Awaitable wrapper so startup can ``asyncio.create_task`` without blocking."""
    try:
        import asyncio as _asyncio
        await _asyncio.to_thread(warmup)
    except Exception:  # noqa: BLE001 - see warmup()
        logger.warning("[stt_local] 启动预热任务异常", exc_info=True)
