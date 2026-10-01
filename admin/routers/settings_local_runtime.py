"""本地模型运行参数页接口（工单 D2；工单 H 增加 sherpa-onnx 引擎）。

GET  /settings/local-runtime                    — 配置 + 实际生效状态（configured / effective / 回落原因）+ sherpa 模型就位状态
GET  /settings/local-runtime/hardware           — 只读硬件/依赖探测（不加载任何模型）
PUT  /settings/local-runtime/stt                — 先热切换、成功后才写入 config.yaml
POST /settings/local-runtime/stt/sherpa/download — 后台下载 sherpa-onnx 模型（约 200 MB，SHA-256 逐文件校验）；进度见 GET 的 sherpa

只放本机 STT 引擎（faster-whisper / sherpa-onnx）的运行参数；远程 OpenAI 兼容 STT 连接仍在 /stt-presets，
两处不重叠。注意 POST /transcribe 在配置里存在 stt_presets 块时远程优先，本页选的本地引擎只有在没有
该块时才会被 /transcribe 用到。
"""
import asyncio
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core import stt_local, stt_sherpa
from core.config_loader import get_config

router = APIRouter()
CONFIG_FILE = Path("config.yaml")


def _view() -> dict[str, Any]:
    return {"stt": stt_local.snapshot(get_config()),
            "options": {"engines": list(stt_local.ENGINES),
                        "model_sizes": list(stt_local.MODEL_SIZES),
                        "devices": list(stt_local.DEVICES),
                        "compute_types": list(stt_local.COMPUTE_TYPES),
                        "sherpa_models": {key: spec["label"] for key, spec in stt_sherpa.MODELS.items()},
                        "sherpa_decoding_methods": list(stt_sherpa.DECODING_METHODS)},
            "sherpa": stt_sherpa.probe(),
            "remote_stt_overrides_local": "stt_presets" in (get_config() or {}),
            "defaults": dict(stt_local.DEFAULTS)}


@router.get("/settings/local-runtime", summary="本地模型运行参数与实际生效状态")
async def get_local_runtime(auth=Depends(require_scopes("admin"))):
    return _view()


@router.get("/settings/local-runtime/hardware", summary="本机硬件能力探测（只读，不加载模型）")
async def get_local_runtime_hardware(auth=Depends(require_scopes("admin"))):
    return await asyncio.get_running_loop().run_in_executor(None, stt_local.probe_hardware)


class SttLocalUpdate(BaseModel):
    engine: Optional[str] = None
    model_size: Optional[str] = None
    device: Optional[str] = None
    compute_type: Optional[str] = None
    beam_size: Optional[int] = None
    timeout_seconds: Optional[float] = None
    sherpa_onnx: Optional[dict[str, Any]] = None


@router.put("/settings/local-runtime/stt", summary="保存本地 STT 运行参数：先热切换，成功后才落盘")
async def update_local_stt(body: SttLocalUpdate, auth=Depends(require_scopes("admin"))):
    current = stt_local.settings(get_config())
    updates = {k: v for k, v in body.model_dump().items() if v is not None and k != "sherpa_onnx"}
    merged = {**current, **updates}
    # Field-wise merge of the sherpa group, so saving the Whisper fields never resets it (and back).
    merged["sherpa_onnx"] = {**current["sherpa_onnx"], **(body.sherpa_onnx or {})}
    try:
        cfg = stt_local.validate(merged)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    # Model load is blocking (and may download weights): keep the event loop free.
    applied = await asyncio.get_running_loop().run_in_executor(None, stt_local.reload, cfg)
    if not applied["ok"]:
        # Nothing was written: the previous working instance and config stay in force.
        raise HTTPException(status_code=409, detail={
            "saved": False, "error": applied["error"],
            "kept": applied["effective"], "message": "切换失败，已保留原有配置与可用实例"})

    full_cfg = read_config_file(CONFIG_FILE)
    full_cfg["stt_local"] = cfg
    write_config_file(CONFIG_FILE, full_cfg)
    from core import config_loader
    config_loader.reload_config()
    return {"saved": True, **_view()}


class SherpaDownload(BaseModel):
    model: Optional[str] = None
    # Lets the page download from the mirror currently typed in the field without saving first.
    download_base: Optional[str] = None


@router.post("/settings/local-runtime/stt/sherpa/download",
             summary="后台下载 sherpa-onnx 模型文件（SHA-256 逐文件校验，进度见 GET /settings/local-runtime）")
async def download_sherpa_model(body: SherpaDownload, auth=Depends(require_scopes("admin"))):
    group = stt_local.settings(get_config())["sherpa_onnx"]
    model = body.model or group["model"]
    if model not in stt_sherpa.MODELS:
        raise HTTPException(status_code=422, detail=f"未知模型：{model}")
    base = group["download_base"]
    if body.download_base:
        try:
            base = stt_sherpa.validate({"download_base": body.download_base})["download_base"]
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    if not stt_sherpa.start_download(model, base):
        raise HTTPException(status_code=409, detail="已有下载在进行中")
    return {"started": True, "download": stt_sherpa.download_status()}
