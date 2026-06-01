"""
Dream session pipeline — fully isolated from core/pipeline.py.

Isolation contract (BY CONSTRUCTION):
- Never calls mood_state.update / detect_emotion / yandere check
- Never calls capture_turn / summarize_to_midterm / reflect_to_episodic
- Never writes author_note_extra
- Never calls notify_owner_turn
- Never calls any scheduler / gating / proposer
- Only reads the frozen context_snapshot; never calls fetch_context / retrieve /
  user_identity.load / mood_state.get during a dream turn
- Only writes to current_dream.jsonl via dream_log
- body_state is dream-local: tracker runs after LLM, result stored for next turn;
  ★ never writes reality mood_state (invariant)
"""

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

HARD_EXIT_KEYWORD = "/stop"
_SOFT_EXIT_ACCEPT_MARKER = "[[EXIT_DREAM_ACCEPT]]"


async def dream_turn(
    uid: str,
    user_msg: str,
) -> dict[str, Any]:
    """
    Process one dream conversation turn.

    Returns:
      {
        "reply":         str,
        "exit_accepted": bool,
        "force_exited":  bool,
        "error":         str,
      }
    """
    from core.dream.dream_state import read_state, write_state, DreamStatus

    state = read_state(uid)
    status = state.get("status")
    if status not in (DreamStatus.DREAM_ACTIVE.value, DreamStatus.DREAM_CLOSING.value):
        return {
            "reply": "",
            "exit_accepted": False,
            "force_exited": False,
            "error": "not_in_dream",
        }

    # ── Hard exit pre-LLM intercept ───────────────────────────────────────────
    if user_msg.strip().lower() == HARD_EXIT_KEYWORD:
        await force_exit_dream(uid)
        return {
            "reply": "（梦境已关闭）",
            "exit_accepted": False,
            "force_exited": True,
        }

    dream_id = state.get("dream_id") or _ensure_dream_id(uid, state)

    from core.dream.dream_state import get_local_state
    local_state = get_local_state(state)
    context_snapshot = state.get("context_snapshot", {})

    from core.dream.dream_log import append_turn, read_current
    dream_history = read_current(uid)

    # Load settings (lorebook + boundary_level)
    from core.dream.dream_settings import load as _load_settings
    settings = _load_settings(uid)

    # Dream-local lorebook matching — pure function, separate from reality lorebook (C4)
    lore_entries: list[str] = []
    if settings.get("enable_dream_lorebook", True):
        try:
            from core.dream.world_loader import load_dream_lore_entries, match_dream_lore
            _dream_lore = load_dream_lore_entries(state.get("frozen_world", "reality_derived"))
            if _dream_lore:
                lore_entries = match_dream_lore(_dream_lore, user_msg, dream_history)
        except Exception as e:
            logger.debug(f"[dream_pipeline] dream lorebook match skipped: {e}")

    jailbreak_preset = settings.get("jailbreak_preset", "default")
    jailbreak_text, jailbreak_preset_status = _load_preset_text(jailbreak_preset)

    from core.pipeline_registry import get as _get_pipeline2
    _pl2 = _get_pipeline2()
    if _pl2 is None:
        return {
            "reply": "",
            "exit_accepted": False,
            "force_exited": False,
            "error": "pipeline_not_initialized",
        }
    character = _pl2.character

    # ── Body state: build D5/D7 projection for THIS turn's prompt ────────────
    from core.dream.body_state import BodyState
    from core.dream.body_projection import project_body_for_yexuan, BoundaryLevel

    current_body = BodyState.from_dict(local_state.get("body_state"))
    current_yexuan_tension = float(local_state.get("emotional_tension") or 0.0)
    boundary_level = settings.get("boundary_level", BoundaryLevel.body_perceptible.value)

    lucid_mode = settings.get("lucid_mode", "lucid_shared")

    if boundary_level == BoundaryLevel.threshold_break.value:
        from core.dream.body_state import apply_threshold_break as _apply_tb
        current_body = _apply_tb(current_body)

    projection = project_body_for_yexuan(current_body, boundary_level, current_yexuan_tension)

    # If user is requesting a soft exit, append accept-marker instruction
    is_exit_request = _looks_like_exit_request(user_msg)
    user_msg_for_llm = user_msg
    if is_exit_request:
        user_msg_for_llm = (
            f"{user_msg}\n\n"
            f"[系统提示：若角色愿意放用户醒来，在回复末尾追加标记 {_SOFT_EXIT_ACCEPT_MARKER}，"
            f"其他情况不追加]"
        )

    from core.dream.dream_prompt import build_dream_prompt
    messages = build_dream_prompt(
        character=character,
        user_id=uid,
        user_message=user_msg_for_llm,
        context_snapshot=context_snapshot,
        dream_history=dream_history,
        local_state=local_state,
        lore_entries=lore_entries,
        jailbreak_text=jailbreak_text,
        jailbreak_preset_name=jailbreak_preset,
        jailbreak_preset_status=jailbreak_preset_status,
        body_projection_text=projection["d5_text"],
        yexuan_tension=current_yexuan_tension,
        world_id=state.get("frozen_world", "reality_derived"),
        lucid_mode=lucid_mode,
    )

    # Call LLM — zero reality side-effects
    from core import llm_client
    reply = await llm_client.chat(messages)

    # Detect soft exit acceptance
    exit_accepted = False
    if is_exit_request and _SOFT_EXIT_ACCEPT_MARKER in reply:
        reply = reply.replace(_SOFT_EXIT_ACCEPT_MARKER, "").strip()
        exit_accepted = True

    # ── Body tracker: update body_state + yexuan_tension AFTER reply ─────────
    # Runs post-LLM so 叶瑄 never sees raw numbers (by construction).
    from core.dream.body_tracker import analyze_turn as _analyze_body
    new_body = _analyze_body(user_msg, reply, current_body)
    new_projection = project_body_for_yexuan(new_body, boundary_level, current_yexuan_tension)

    # ── Write to dream log (never to any reality store) ──────────────────────
    append_turn(uid, dream_id, "user", user_msg)
    append_turn(uid, dream_id, "assistant", reply)

    # ── Persist updated dream-local state ────────────────────────────────────
    from core.dream.dream_state import patch_local_state
    state = read_state(uid)
    state = patch_local_state(
        state,
        emotional_tension=new_projection["yexuan_tension"],
        body_state=new_body.to_dict(),
    )
    write_state(uid, state)

    # Transition to DREAM_CLOSING if soft exit was accepted
    if exit_accepted:
        state = read_state(uid)
        state["status"] = DreamStatus.DREAM_CLOSING.value
        write_state(uid, state)
        await _do_close_dream(uid, dream_id, exit_type="soft")

    from core.narrative_parser import parse_narrative_segments as _parse_segs
    _parsed = _parse_segs(reply)

    return {
        "reply": reply,
        "exit_accepted": exit_accepted,
        "force_exited": False,
        "segments": _parsed["segments"],
        "segmented_content": _parsed["content"],
    }


async def force_exit_dream(uid: str) -> None:
    """
    Hard exit chokepoint — unconditional, immediate, penetrates all state.

    - Called pre-LLM for /stop keyword
    - Called from /dream/exit endpoint (no conversation_lock)
    - Idempotent: safe to call from any state
    - Cannot be disabled by config or role behavior (invariant D)
    """
    from core.dream.dream_state import read_state, write_state, DreamStatus

    state = read_state(uid)
    dream_id = state.get("dream_id", "")

    state["status"] = DreamStatus.DREAM_CLOSING.value
    write_state(uid, state)

    logger.info(f"[dream_pipeline] force_exit uid={uid} dream_id={dream_id}")
    await _do_close_dream(uid, dream_id, exit_type="hard_exit")


async def enter_dream(uid: str, entry_reason: str = "") -> dict[str, Any]:
    """
    Transition uid into DREAM_ACTIVE.

    Builds the frozen context snapshot, assigns a dream_id,
    and writes the new state. Called by the /dream/enter endpoint.
    """
    from core.dream.dream_state import read_state, write_state, DreamStatus
    from core.dream.dream_context import build_snapshot

    state = read_state(uid)
    allowed = {
        DreamStatus.REALITY_CHAT.value,
        DreamStatus.DREAM_ENTRANCE_AVAILABLE.value,
        DreamStatus.REALITY_AFTERGLOW.value,
    }
    if state.get("status") not in allowed:
        return {"ok": False, "error": f"cannot enter dream from status={state.get('status')}"}

    dream_id = f"dream_{uid}_{int(time.time())}"
    snapshot = await build_snapshot(uid, entry_reason=entry_reason)

    # Freeze world_layer and lucid_mode from settings for this dream session
    from core.dream.dream_settings import load as _load_settings_enter
    _settings_enter = _load_settings_enter(uid)
    frozen_world = _settings_enter.get("world_layer", "reality_derived")
    lucid_mode_entry = _settings_enter.get("lucid_mode", "lucid_shared")

    state["status"] = DreamStatus.DREAM_ACTIVE.value
    state["dream_id"] = dream_id
    state["context_snapshot"] = snapshot
    state["frozen_world"] = frozen_world
    state["lucid_mode"] = lucid_mode_entry
    # Clear all volatile local fields at dream start
    state.pop("emotional_tension", None)
    state.pop("scene_state", None)
    state.pop("symbolic_anchors", None)
    state.pop("body_state", None)
    write_state(uid, state)

    # Clear any leftover HUD smooth state from a previous interrupted dream
    from core.dream.dream_hud import delete_hud_state
    delete_hud_state(uid)

    logger.info(f"[dream_pipeline] entered dream uid={uid} dream_id={dream_id}")
    return {"ok": True, "dream_id": dream_id}


async def _do_close_dream(uid: str, dream_id: str, exit_type: str) -> None:
    """Archive log, schedule summary generation, transition to REALITY_AFTERGLOW."""
    from core.dream.dream_state import read_state, write_state, DreamStatus, clear_local_state
    from core.dream.dream_log import archive_current
    from core.dream.dream_hud import delete_hud_state

    if dream_id:
        archive_current(uid, dream_id)

    asyncio.create_task(_generate_summary_bg(uid, dream_id, exit_type))

    state = read_state(uid)
    state = clear_local_state(state)  # clears body_state + emotional_tension + scene etc.
    state["status"] = DreamStatus.REALITY_AFTERGLOW.value
    state["last_dream_id"] = dream_id
    state["last_exit_type"] = exit_type
    write_state(uid, state)

    delete_hud_state(uid)
    logger.info(f"[dream_pipeline] closed dream uid={uid} exit_type={exit_type}")


async def _generate_summary_bg(uid: str, dream_id: str, exit_type: str) -> None:
    try:
        from core.dream.dream_summary import generate_summary
        await generate_summary(uid, dream_id, exit_type)
    except Exception as e:
        logger.error(f"[dream_pipeline] summary failed uid={uid}: {e}")

    # Distill impression after summary (failure is warning-only per C7)
    try:
        from core.dream.distill_impression import distill_impression
        await distill_impression(uid, dream_id, exit_type)
    except Exception as e:
        logger.warning(f"[dream_pipeline] distill_impression failed uid={uid}: {e}")


def _ensure_dream_id(uid: str, state: dict) -> str:
    from core.dream.dream_state import write_state
    dream_id = f"dream_{uid}_{int(time.time())}"
    state["dream_id"] = dream_id
    write_state(uid, state)
    return dream_id


def _looks_like_exit_request(msg: str) -> bool:
    exit_words = ["醒来", "结束梦", "想醒", "离开梦", "退出梦", "结束这个梦", "我要醒"]
    return any(w in msg for w in exit_words)


def _load_preset_text(preset_name: str) -> tuple[str, str]:
    """
    Load D0 jailbreak content from characters/dream_presets/{preset_name}.md.
    Returns (text, status): status is "" | "fallback" | "disabled".
    Falls back to default.md if named preset is missing; returns disabled if default missing too.
    """
    import re
    from pathlib import Path

    _PRESETS_BASE = Path("characters/dream_presets")

    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", preset_name):
        logger.warning("[dream_pipeline] preset name %r rejected, using default", preset_name)
        preset_name = "default"

    def _read(name: str) -> str | None:
        p = _PRESETS_BASE / f"{name}.md"
        try:
            if p.exists():
                return p.read_text(encoding="utf-8").strip() or None
        except Exception as exc:
            logger.warning("[dream_pipeline] cannot read preset %r: %s", name, exc)
        return None

    text = _read(preset_name)
    if text is not None:
        return text, ""

    if preset_name != "default":
        default_text = _read("default")
        if default_text is not None:
            logger.warning("[dream_pipeline] preset %r missing, fallback to default", preset_name)
            return default_text, "fallback"

    logger.warning("[dream_pipeline] D0 disabled: preset %r and default both missing/empty", preset_name)
    return "", "disabled"
