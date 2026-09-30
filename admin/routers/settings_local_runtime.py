"""本地模型运行参数页接口（工单 D2）。

GET /settings/local-runtime            — 配置 + 实际生效状态（configured / effective / 回落原因）
GET /settings/local-runtime/hardware   — 只读硬件能力探测（不加载 Whisper 模型）
PUT /settings/local-runtime/stt        — 先热切换、成功后才写入 config.yaml

只放本机 faster-whisper 的运行参数；远程 OpenAI 兼容 STT 连接仍在 /stt-presets，两处不重叠。
"""
import asyncio
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from admin.auth import require_scopes
from admin.config_control import read_config_file, write_config_file
from core import stt_local
from core.config_loader import get_config

router = APIRouter()
CONFIG_FILE = Path("config.yaml")


def _view() -> dict[str, Any]:
    return {"stt": stt_local.snapshot(get_config()),
            "options": {"model_sizes": list(stt_local.MODEL_SIZES),
                        "devices": list(stt_local.DEVICES),
                        "compute_types": list(stt_local.COMPUTE_TYPES)},
            "defaults": dict(stt_local.DEFAULTS)}


@router.get("/settings/local-runtime", summary="本地模型运行参数与实际生效状态")
async def get_local_runtime(auth=Depends(require_scopes("admin"))):
    return _view()


@router.get("/settings/local-runtime/hardware", summary="本机硬件能力探测（只读，不加载模型）")
async def get_local_runtime_hardware(auth=Depends(require_scopes("admin"))):
    return await asyncio.get_running_loop().run_in_executor(None, stt_local.probe_hardware)


class SttLocalUpdate(BaseModel):
    model_size: Optional[str] = None
    device: Optional[str] = None
    compute_type: Optional[str] = None
    beam_size: Optional[int] = None
    timeout_seconds: Optional[float] = None


@router.put("/settings/local-runtime/stt", summary="保存本地 STT 运行参数：先热切换，成功后才落盘")
async def update_local_stt(body: SttLocalUpdate, auth=Depends(require_scopes("admin"))):
    current = stt_local.settings(get_config())
    merged = {**current, **{k: v for k, v in body.model_dump().items() if v is not None}}
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
