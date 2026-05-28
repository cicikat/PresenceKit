"""
Dream session endpoints.

POST  /dream/enter    — enter dream (build frozen snapshot, DREAM_ACTIVE)
POST  /dream/chat     — dream turn (goes to dream_pipeline, never reality pipeline)
POST  /dream/exit     — hard exit (force_exit_dream, unconditional)
GET   /dream/state    — read-only UI panel state (projected fields only)
GET   /dream/settings — read full per-uid dream settings
PATCH /dream/settings — partial update (enum-validated; only affects next dream)

Invariants:
- /dream/chat never calls notify_owner_turn, never triggers scheduler/gating.
- conversation_lock(uid) wraps the full dream_turn for serialization safety.
- Hard reject: DREAM_ACTIVE / DREAM_CLOSING prevents reality endpoints from
  processing turns (safety net implemented in chat.py and mobile.py).
- GET /dream/state is pure read-only: never writes files, never triggers any
  reality pipeline or mood_state. Returns safe defaults (status=REALITY_CHAT)
  when no dream is active.
- PATCH /dream/settings NEVER writes into dream_state. frozen_world and
  lucid_mode are frozen at dream entry from settings; PATCH only affects the
  next dream session entered via POST /dream/enter.
"""

import logging

from fastapi import APIRouter, HTTPException

from core.config_loader import get_config

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Enum validators for PATCH /dream/settings ─────────────────────────────────
_VALID_MEMORY_ACCESS = frozenset({"card_only", "relationship_summary", "full_snapshot"})
_VALID_BOUNDARY_LEVEL = frozenset({"vague", "body_perceptible", "numbers_visible", "threshold_break"})
_VALID_WORLD_LAYER = frozenset({"reality_derived", "abo", "vampire", "cat", "flower_bud", "custom"})
_VALID_LUCID_MODE = frozenset({"lucid_shared", "non_lucid"})

_ENUM_VALIDATORS: dict[str, frozenset] = {
    "memory_access": _VALID_MEMORY_ACCESS,
    "boundary_level": _VALID_BOUNDARY_LEVEL,
    "world_layer": _VALID_WORLD_LAYER,
    "lucid_mode": _VALID_LUCID_MODE,
}

_PATCH_ALLOWED = frozenset({
    "memory_access", "boundary_level", "world_layer", "lucid_mode", "enable_dream_lorebook",
})


def _owner_uid() -> str:
    uid = str(get_config().get("scheduler", {}).get("owner_id", "owner"))
    if not uid:
        raise HTTPException(status_code=503, detail="owner_id 未配置")
    return uid


@router.post("/dream/enter", summary="进入梦境")
async def dream_enter(body: dict = {}):
    uid = _owner_uid()
    entry_reason = (body.get("entry_reason") or "").strip()

    from core.dream.dream_pipeline import enter_dream
    result = await enter_dream(uid, entry_reason=entry_reason)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error", "cannot enter dream"))
    return result


@router.post("/dream/chat", summary="梦境对话（独立 pipeline）")
async def dream_chat(body: dict):
    """
    Dream turn endpoint — routes to dream_pipeline, never to reality pipeline.

    conversation_lock(uid) serializes the full turn.
    Does NOT call notify_owner_turn, scheduler, or gating.
    """
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message 不能为空")

    uid = _owner_uid()

    from core.conversation_gate import conversation_lock
    from core.dream.dream_pipeline import dream_turn

    async with conversation_lock(uid):
        result = await dream_turn(uid, message)

    if err := result.get("error"):
        raise HTTPException(status_code=409, detail=err)

    return result


@router.post("/dream/exit", summary="强退梦境（硬出口，不可被拒）")
async def dream_exit():
    """
    Hard exit — unconditional, immediate, penetrates all state.
    Cannot be disabled by config or role behavior (invariant D).
    """
    uid = _owner_uid()

    from core.dream.dream_pipeline import force_exit_dream
    await force_exit_dream(uid)

    return {"ok": True, "exited": True}


@router.get("/dream/state", summary="读取梦境状态（只读 UI 面板字段）")
async def dream_state_get():
    """
    Pure read-only: never writes files, never triggers reality pipeline.
    Returns safe defaults (status=REALITY_CHAT, body zeros) when no dream active.

    body.{heat,sensitivity,tension} — her cyber body numbers.
      user_sees_own_numbers is always True; orthogonal to boundary_level
      (which controls 叶瑄's perception, not the UI panel).
    yexuan_tension — 叶瑄's dream-local emotional tension (0.0–1.0).
    """
    uid = _owner_uid()
    from core.dream.dream_state import read_state
    from core.dream.body_state import BodyState

    state = read_state(uid)
    body = BodyState.from_dict(state.get("body_state") or {})

    return {
        "status": state.get("status", "REALITY_CHAT"),
        "dream_id": state.get("dream_id"),
        "frozen_world": state.get("frozen_world"),
        "lucid_mode": state.get("lucid_mode"),
        "body": {
            "heat": round(body.heat, 2),
            "sensitivity": round(body.sensitivity, 2),
            "tension": round(body.tension, 2),
        },
        "yexuan_tension": float(state.get("emotional_tension", 0.0)),
        "scene_state": state.get("scene_state"),
        "symbolic_anchors": list(state.get("symbolic_anchors") or []),
    }


@router.get("/dream/settings", summary="读取梦境设置（全字段）")
async def dream_settings_get():
    """Read-only: returns all dream settings fields with defaults applied."""
    uid = _owner_uid()
    from core.dream.dream_settings import load as _load
    return _load(uid)


@router.patch("/dream/settings", summary="部分更新梦境设置（校验枚举值；仅影响下一场梦）")
async def dream_settings_patch(body: dict):
    """
    Partial update for dream settings. Validates enum values before writing.

    Allowed fields: memory_access / boundary_level / world_layer / lucid_mode /
                    enable_dream_lorebook

    ★ NEVER backfills into a running dream's frozen_world / lucid_mode.
      Those fields are frozen at dream entry (enter_dream reads from settings
      and copies into dream_state). PATCH only affects the next dream session.
      Changing world_layer while DREAM_ACTIVE does NOT change the current dream.
    """
    uid = _owner_uid()
    from core.dream.dream_settings import load as _load, save as _save

    updates = {k: v for k, v in body.items() if k in _PATCH_ALLOWED}
    if not updates:
        raise HTTPException(status_code=422, detail=f"可更新字段：{sorted(_PATCH_ALLOWED)}")

    errors: list[str] = []
    for key, valid_set in _ENUM_VALIDATORS.items():
        if key in updates:
            val = updates[key]
            if val not in valid_set:
                errors.append(f"{key}={val!r} 非法，有效值：{sorted(valid_set)}")
    if "enable_dream_lorebook" in updates and not isinstance(updates["enable_dream_lorebook"], bool):
        errors.append(
            f"enable_dream_lorebook 必须为 bool，收到：{updates['enable_dream_lorebook']!r}"
        )
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    current = _load(uid)
    current.update(updates)
    _save(uid, current)
    return {"ok": True, "settings": current}
