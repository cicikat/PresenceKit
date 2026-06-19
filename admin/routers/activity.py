"""
活动状态路由

GET /activity/current  — 当前角色活动状态（activity_manager 维护的内部状态机）
GET /activity/list     — 所有已启用 reality activity 元信息（由 registry 驱动）
"""

from datetime import datetime

from fastapi import APIRouter, Depends

from admin.auth import verify_token
from core import activity_manager
from core.activity.registry import list_enabled_activities

router = APIRouter()


@router.get("/list", summary="获取所有已启用 Activity 元信息")
async def get_activity_list(auth=Depends(verify_token)):
    return [
        {
            "id": m.id,
            "label": m.label,
            "kind": m.kind,
            "enabled": m.enabled,
            "route_prefix": m.route_prefix,
            "frontend_key": m.frontend_key,
            "memory_policy": {
                "transcript": m.memory_policy.transcript,
                "summary_threshold": m.memory_policy.summary_threshold,
                "main_memory": m.memory_policy.main_memory,
            },
            "has_companion_chat": m.has_companion_chat,
        }
        for m in list_enabled_activities()
    ]


def _get_activity_text() -> str:
    """
    优先级：梦境 > 共同活动会话 > 随机池。
    返回当前动向文案字符串。
    """
    # ── 1. 梦境检查 ───────────────────────────────────────────────────────────
    try:
        from core.config_loader import get_config
        uid = str(get_config().get("default_user_id", "owner"))
    except Exception:
        uid = "owner"

    try:
        from core.dream.dream_state import read_state, DreamStatus
        dream = read_state(uid)
        if dream.get("status") in (DreamStatus.DREAM_ACTIVE.value, DreamStatus.DREAM_CLOSING.value):
            return "在做梦"
    except Exception:
        pass

    # ── 2. 活跃共同活动检查 ───────────────────────────────────────────────────
    try:
        import json
        from core.sandbox import get_paths
        raw = json.loads(get_paths().active_prompt_assets().read_text(encoding="utf-8"))
        char_id = (raw.get("active_character") or "").strip()
    except Exception:
        char_id = ""

    if char_id:
        # 阅读
        try:
            from core.activity import activity_store as reading_store
            session = reading_store.find_active_session(char_id, uid)
            if session is not None:
                return f"在和你一起看《{session.filename}》"
        except Exception:
            pass

        # 五子棋
        try:
            from core.activity.gomoku import get_active_session as get_gomoku_session
            if get_gomoku_session(uid, char_id) is not None:
                return "在和你下五子棋"
        except Exception:
            pass

        # 国际象棋
        try:
            from core.activity.store import find_active_session as find_chess_session
            if find_chess_session(char_id, uid, "chess") is not None:
                return "在和你下国际象棋"
        except Exception:
            pass

    # ── 3. 随机活动池 ─────────────────────────────────────────────────────────
    state = activity_manager.get_current()
    return state.get("current", "")


@router.get("/current", summary="获取当前活动状态")
async def get_activity_state(auth=Depends(verify_token)):
    state = activity_manager.get_current()

    started_at = None
    raw = state.get("started_at")
    if raw:
        try:
            started_at = datetime.fromisoformat(raw).timestamp()
        except Exception:
            pass

    text = _get_activity_text()

    return {
        "id": None,
        "text": text,
        "arc": state.get("arc"),
        "started_at": started_at,
        "next_switch_at": state.get("expected_until_ts"),
        "thinking_about_eligible": bool(state.get("thinking_about")),
    }
