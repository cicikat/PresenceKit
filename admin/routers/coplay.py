"""
Coplay (陪玩模式) session endpoints — Brief 38 骨架。

GET  /coplay/state   — 只读状态（status/game_id/game_name/enabled/last_probe）
POST /coplay/arm     — off → armed（用户显式开启陪玩模式）；coplay.enabled=false
                        （部署级禁用）时返回 409，不 arm 一个 watcher 永远不会
                        消费的状态（Brief 54-A，避免"成功了但什么都不会发生"）
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


def _coplay_enabled() -> bool:
    """部署级"允许陪玩功能"开关，默认 True（缺省=允许，Brief 54-A 消灭双开关）。"""
    return bool(get_config().get("coplay", {}).get("enabled", True))


@router.get("/coplay/state", summary="读取陪玩模式状态（只读）")
async def coplay_state_get(_auth=Depends(require_scopes("activity"))):
    from core.coplay import watcher
    from core.coplay.session import read_state

    uid = _owner_uid()
    char_id = _active_char_id()
    state = read_state(uid, char_id=char_id)
    return {
        "status": state.get("status"),
        "game_id": state.get("game_id"),
        "game_name": state.get("game_name"),
        "enabled": _coplay_enabled(),
        # 调试字段：watcher 上一次 tick 探测到的原始信号，fail-open（未探测过/
        # 探测失败时为 None），供前端设置页排查"检测卡在哪一步"（Brief 54-A §4）。
        "last_probe": watcher.get_last_probe(uid),
    }


@router.post("/coplay/arm", summary="开启陪玩模式（off → armed）")
async def coplay_arm(_auth=Depends(require_scopes("activity"))):
    from core.coplay.session import arm

    if not _coplay_enabled():
        raise HTTPException(
            status_code=409,
            detail="陪玩功能已在部署配置中禁用（config.yaml: coplay.enabled=false），"
                   "请修改配置并重启后端后再试",
        )

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
