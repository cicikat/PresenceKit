"""
语音转写接口 POST /transcribe
接收 multipart 音频，通过本地 Whisper 转写，返回 { "text": "..." }

依赖（任选其一，推荐前者）：
  pip install faster-whisper
  pip install openai-whisper
"""

import asyncio
import logging
import os
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from admin.auth import verify_token

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MB

# ── 懒加载 STT 后端 ──────────────────────────────────────────────────────────

_stt_lock = threading.Lock()
_stt_backend: tuple | str | None = None  # None=未初始化, 'unavailable'=无可用库, tuple=(name, model)


def _init_stt() -> None:
    global _stt_backend
    with _stt_lock:
        if _stt_backend is not None:
            return
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel("base", device="cpu", compute_type="int8")
            _stt_backend = ("faster_whisper", model)
            logger.info("[transcribe] 使用 faster-whisper base 模型")
            return
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"[transcribe] faster-whisper 加载失败: {e}")
        try:
            import whisper as _whisper
            model = _whisper.load_model("base")
            _stt_backend = ("whisper", model)
            logger.info("[transcribe] 使用 openai-whisper base 模型")
            return
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"[transcribe] openai-whisper 加载失败: {e}")
        _stt_backend = "unavailable"
        logger.warning("[transcribe] 未安装可用 STT 库，请 pip install faster-whisper 或 openai-whisper")


def _transcribe_sync(audio_path: str) -> str:
    _init_stt()
    if _stt_backend == "unavailable":
        raise RuntimeError("STT 未安装，请 pip install faster-whisper 或 openai-whisper")
    backend_name, model = _stt_backend  # type: ignore[misc]
    if backend_name == "faster_whisper":
        segments, _ = model.transcribe(audio_path, language="zh")
        return "".join(seg.text for seg in segments).strip()
    else:
        result = model.transcribe(audio_path, language="zh")
        return result["text"].strip()


# ── 接口 ─────────────────────────────────────────────────────────────────────

@router.post("/transcribe", summary="语音转写")
async def transcribe_audio(
    file: UploadFile = File(...),
    channel: str = Form("desktop"),
    _auth=Depends(verify_token),
):
    data = await file.read()

    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="音频超过 25MB 上限")

    if len(data) == 0:
        raise HTTPException(status_code=422, detail="音频内容为空")

    # 根据文件名或 content-type 决定扩展名（影响 ffmpeg 解码路径）
    suffix = ".webm"
    fname = file.filename or ""
    if fname:
        ext = Path(fname).suffix.lower()
        if ext in {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".webm", ".opus"}:
            suffix = ext

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name

        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, _transcribe_sync, tmp_path)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.warning(f"[transcribe] 转写失败: {e}")
        raise HTTPException(status_code=422, detail="语音转写失败")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return {"text": text}
