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
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from admin.auth import require_scopes

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MB

# ── 本地 STT 后端：模型/设备/精度见 core/stt_local.py（config `stt_local`，可热切换）──


def _transcribe_sync(audio_path: str) -> str:
    from core import stt_local
    cfg = stt_local.settings()
    backend = stt_local.get_backend(cfg)
    started = time.perf_counter()
    ok = False
    try:
        text = _run_backend(backend, cfg, audio_path)
        ok = True
        return text
    finally:
        from core.api_call_log import append
        append(caller="stt", purpose="transcribe_local", provider=backend["backend"],
               model=f"{backend['model_size']}/{backend['device']}/{backend['compute_type']}",
               duration_ms=int((time.perf_counter() - started) * 1000), ok=ok,
               output_hint="fallback:" + backend["fallback"]["from"] if backend["fallback"] else "")


def _run_backend(backend: dict, cfg: dict, audio_path: str) -> str:
    from core.stt_vocabulary import prompt, hotwords, correct
    hint = prompt()
    model = backend["model"]
    if backend["backend"] == "faster_whisper":
        options = {"initial_prompt": hint or None, "hotwords": hotwords() or None,
                   "vad_filter": True, "beam_size": cfg["beam_size"]}
        segments, _ = model.transcribe(audio_path, language="zh", **options)
        accepted = []
        filtered = 0
        for seg in segments:
            if getattr(seg, "no_speech_prob", 0.0) >= 0.8 or getattr(seg, "avg_logprob", 0.0) <= -1.5:
                filtered += 1
                continue
            accepted.append(seg.text)
        if not accepted:
            logger.info("[transcribe] 未识别到可用语音片段（低置信度过滤 %d 段）", filtered)
        return correct("".join(accepted).strip())
    result = model.transcribe(audio_path, language="zh", initial_prompt=hint or None)
    return correct(result["text"].strip())


def _timeout_seconds() -> float:
    from core import stt_local
    return stt_local.settings()["timeout_seconds"]


# ── 接口 ─────────────────────────────────────────────────────────────────────

@router.post("/transcribe", summary="语音转写")
async def transcribe_audio(
    file: UploadFile = File(...),
    channel: str = Form("desktop"),
    _auth=Depends(require_scopes("chat")),
):
    data = await file.read(MAX_AUDIO_BYTES + 1)

    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="音频超过 25MB 上限")

    if len(data) == 0:
        raise HTTPException(status_code=422, detail="音频内容为空")

    from core.config_loader import get_config
    from core.audio_perception import ingest_audio_bytes, issue_receipt
    if "stt_presets" in get_config():
        result = await ingest_audio_bytes(data, file.filename or "voice.webm")
        if not result:
            raise HTTPException(status_code=422, detail="语音识别未启用、未配置或未能听清，请重试或输入文字")
        return {
            "text": result["text"],
            "tone": result["tone"],
            "audio_perception_id": issue_receipt(result, channel),
        }

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
        def run_and_cleanup(path):
            try:
                return _transcribe_sync(path)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        worker_path, tmp_path = tmp_path, None
        text = await asyncio.wait_for(loop.run_in_executor(None, run_and_cleanup, worker_path), timeout=_timeout_seconds())
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except asyncio.TimeoutError as e:
        raise HTTPException(status_code=504, detail="语音转写超时，请缩短录音或稍后重试") from e
    except Exception as e:
        logger.warning(f"[transcribe] 转写失败: {e}")
        raise HTTPException(status_code=422, detail="语音转写失败")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    if not text:
        raise HTTPException(status_code=422, detail="没有识别到语音，请重试或输入文字")
    from core.audio_perception import speech_analysis_enabled, _attach_acoustic, issue_receipt
    if speech_analysis_enabled():
        acoustic = await _attach_acoustic(data, file.filename or "voice.webm", "unclear")
        result = {"text": text, **acoustic}
        return {"text": text, "tone": result["tone"],
                "audio_perception_id": issue_receipt(result, channel)}
    return {"text": text}
