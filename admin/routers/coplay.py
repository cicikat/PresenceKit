"""
Coplay (陪玩模式) session endpoints — Brief 38 骨架。

GET  /coplay/state   — 只读状态（status/game_id/game_name）
POST /coplay/arm     — off → armed（用户显式开启陪玩模式）
POST /coplay/disarm  — 任意状态 → off（硬关闭，总是成功）

scope 复用 docs/security.md 里已有的 `activity`（活动/梦境 overlay 全生命周期），
与 /dream/* 、/activity/* 同一档位——见 docs/security.md §profile 表。

真正的游戏检测（armed → active）由 Brief 39 的 watcher 驱动，本 router 不提供
手工 set-active 端点：active/closing 转换只应由 watcher 触发，避免和真实检测
状态打架。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from admin.auth import require_scopes
from core.config_loader import get_config

router = APIRouter()
logger = logging.getLogger(__name__)


def _owner_uid() -> str:
    uid = str(get_config().get("scheduler", {}).get("owner_id", "owner"))
    if not uid:
        raise HTTPException(status_code=503, detail="owner_id 未配置")
    return uid


def _active_char_id() -> str:
    from core.pipeline_registry import get as _get_pipeline
    pl = _get_pipeline()
    char_id = pl._active_character_id if pl else None
    return char_id or "yexuan"


@router.get("/coplay/state", summary="读取陪玩模式状态（只读）")
async def coplay_state_get(_auth=Depends(require_scopes("activity"))):
    from core.coplay.session import read_state

    uid = _owner_uid()
    char_id = _active_char_id()
    state = read_state(uid, char_id=char_id)
    return {
        "status": state.get("status"),
        "game_id": state.get("game_id"),
        "game_name": state.get("game_name"),
    }


@router.post("/coplay/arm", summary="开启陪玩模式（off → armed）")
async def coplay_arm(_auth=Depends(require_scopes("activity"))):
    from core.coplay.session import arm

    uid = _owner_uid()
    char_id = _active_char_id()
    state = arm(uid, char_id=char_id)
    return {"ok": True, "status": state.get("status")}


@router.post("/coplay/disarm", summary="关闭陪玩模式（任意状态 → off，总是成功）")
async def coplay_disarm(_auth=Depends(require_scopes("activity"))):
    from core.coplay.session import disarm

    uid = _owner_uid()
    char_id = _active_char_id()
    state = disarm(uid, char_id=char_id)
    return {"ok": True, "status": state.get("status")}
