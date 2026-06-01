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
import re

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
    "memory_access", "boundary_level", "world_layer", "lucid_mode",
    "enable_dream_lorebook", "jailbreak_preset", "display",
})

_SAFE_PRESET_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


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


_BOUNDARY_FACTOR: dict[str, int] = {
    "vague": 10,
    "body_perceptible": 20,
    "numbers_visible": 35,
    "threshold_break": 35,
}


def _compute_hud_v0(state: dict, settings: dict, body) -> dict:
    """Compute Dream HUD v0 derived fields. Pure, no side effects, no I/O."""
    heat = body.heat
    sensitivity = body.sensitivity
    raw_tension = float(state.get("emotional_tension", 0.0))
    emotion_tension = round(raw_tension * 100)

    physiological_arousal = round(min(100.0, max(0.0, heat)))

    world = state.get("frozen_world") or settings.get("world_layer", "reality_derived")
    base_intimacy = (heat + sensitivity + emotion_tension) / 3.0
    if world == "abo":
        base_intimacy *= 1.2
    elif world == "cat":
        base_intimacy *= 0.8
    intimacy_tendency = round(min(100.0, max(0.0, base_intimacy)))

    boundary_factor = _BOUNDARY_FACTOR.get(settings.get("boundary_level", "body_perceptible"), 20)
    boundary_intrusion = round(min(100.0, max(0.0, heat * 0.4 + emotion_tension * 0.4 + boundary_factor)))

    anchor_score = min(len(list(state.get("symbolic_anchors") or [])) * 10, 40)
    obsession = round(min(100.0, max(0.0, emotion_tension * 0.7 + anchor_score * 0.3)))

    turn_factor = 10  # no turn_count tracked in v0
    dream_depth = round(min(100.0, max(0.0, (heat + sensitivity + turn_factor) / 3.0)))

    scene_bonus = 20 if state.get("scene_state") else 0
    dream_stability = round(min(100.0, max(0.0, 100 - emotion_tension * 0.4 - boundary_intrusion * 0.2 + scene_bonus)))

    if emotion_tension < 25:
        emotion_label = "平静"
    elif emotion_tension < 45:
        emotion_label = "专注"
    elif emotion_tension < 65:
        emotion_label = "克制"
    elif emotion_tension < 80:
        emotion_label = "紧绷"
    else:
        emotion_label = "临界"

    scene_state = state.get("scene_state")
    if scene_state:
        scene_label = scene_state
    elif dream_stability > 70:
        scene_label = "稳定"
    elif dream_depth > 70:
        scene_label = "下沉"
    elif boundary_intrusion > 60:
        scene_label = "边界波动"
    else:
        scene_label = "梦境中"

    return {
        "emotion_label": emotion_label,
        "scene_label": scene_label,
        "emotion_tension": emotion_tension,
        "boundary_intrusion": boundary_intrusion,
        "intimacy_tendency": intimacy_tendency,
        "obsession": obsession,
        "dream_stability": dream_stability,
        "dream_depth": dream_depth,
        "physiological_arousal": physiological_arousal,
    }


@router.get("/dream/state", summary="读取梦境状态（只读 UI 面板字段）")
async def dream_state_get():
    """
    Read-only UI panel. Returns safe defaults when no dream is active.

    HUD v1: EMA-smoothed fields, anchor_charge injection, world multipliers.
    Persists smooth values to dream_hud_state.json (dream-local, cleared at close).
    Does not read mood_state, user_identity, or any reality store.

    body.{heat,sensitivity,tension} — user always sees own numbers (orthogonal to
      boundary_level, which controls 叶瑄's perception only).
    yexuan_tension — 叶瑄's dream-local emotional tension (0.0–1.0).
    HUD fields: emotion_label, scene_label, emotion_tension, boundary_intrusion,
      intimacy_tendency, obsession, dream_stability, dream_depth,
      physiological_arousal — all int 0–100.
    """
    uid = _owner_uid()
    from core.dream.dream_state import read_state, DreamStatus
    from core.dream.body_state import BodyState
    from core.dream.dream_settings import load as _load_settings
    from core.dream.dream_hud import derive_hud_v1, load_hud_state, save_hud_state

    state = read_state(uid)
    body = BodyState.from_dict(state.get("body_state") or {})
    settings = _load_settings(uid)

    base = {
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

    # HUD v1: EMA smooth + anchor_charge + world corrections
    # When dream is not active we still compute (using zeroed body), but do not persist.
    dream_active = state.get("status") in (
        DreamStatus.DREAM_ACTIVE.value,
        DreamStatus.DREAM_CLOSING.value,
        DreamStatus.DREAM_EXIT_REQUESTED.value,
    )
    prev_smooth = load_hud_state(uid) if dream_active else {}
    smooth, hud = derive_hud_v1(state, settings, body, prev_smooth)
    if dream_active:
        save_hud_state(uid, smooth)

    base.update(hud)
    return base


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
                    enable_dream_lorebook / display

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
    if "jailbreak_preset" in updates:
        val = updates["jailbreak_preset"]
        if not isinstance(val, str) or not _SAFE_PRESET_RE.match(val):
            errors.append(
                f"jailbreak_preset={val!r} 非法，只允许字母/数字/下划线/短横线（1-64字符）"
            )
    if "display" in updates:
        val = updates["display"]
        if not isinstance(val, dict):
            errors.append(f"display 必须为对象，收到：{val!r}")
        elif set(val) != {"physiological_arousal"}:
            errors.append("display 只允许 physiological_arousal 字段")
        elif not isinstance(val["physiological_arousal"], bool):
            errors.append(
                "display.physiological_arousal 必须为 bool，"
                f"收到：{val['physiological_arousal']!r}"
            )
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    current = _load(uid)
    current.update(updates)
    _save(uid, current)
    return {"ok": True, "settings": current}
