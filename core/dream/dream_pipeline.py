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
import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

HARD_EXIT_KEYWORD = "/stop"
_SOFT_EXIT_ACCEPT_MARKER = "[[EXIT_DREAM_ACCEPT]]"

# ── Soft retention gate thresholds (adjustable module constants) ──────────────
# Immersion proxy: minimum valid dream turns in current session
RETAIN_MIN_TURNS = 3
# Emotional intensity thresholds
RETAIN_TENSION_MIN: float = 0.55   # yexuan emotional_tension (0–1)
RETAIN_HEAT_MIN: float = 55.0      # body_state.heat (0–100)

# ── scenario_control block parser (v0.6) ──────────────────────────────────────

_SCENARIO_CONTROL_RE = re.compile(
    r"<scenario_control>\s*(.*?)\s*</scenario_control>",
    re.DOTALL,
)
_VALID_PROGRESS_SIGNALS: frozenset[str] = frozenset({"not_close", "approaching", "satisfied"})

def _bucket_for_scenario(value: float) -> str:
    return ("low", "rising", "high", "critical")[int(max(0, min(3, value * 4)))]


def _extract_scenario_control(reply: str) -> tuple[str, dict | None]:
    """
    Strip <scenario_control>…</scenario_control> from the LLM reply and parse it.

    Returns (visible_reply, parsed_control_or_None).
    - visible_reply always has the block removed (even when parse fails).
    - parsed_control is None when block is absent, JSON-invalid, or has an
      illegal progress_signal; caller must not update ScenarioCore in that case.
    - Fail-soft: never raises.
    """
    match = _SCENARIO_CONTROL_RE.search(reply)
    if not match:
        return reply, None

    # Strip the block from visible reply regardless of validity
    visible = (reply[: match.start()] + reply[match.end() :]).strip()

    try:
        data = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        logger.debug("[dream_pipeline] scenario_control JSON parse failed")
        return visible, None

    if not isinstance(data, dict):
        return visible, None

    signal = data.get("progress_signal")
    if signal not in _VALID_PROGRESS_SIGNALS:
        logger.debug("[dream_pipeline] scenario_control invalid progress_signal=%r", signal)
        return visible, None

    matched_exit_signs = data.get("matched_exit_signs", [])
    blocked_events = data.get("blocked_events", [])
    if not isinstance(matched_exit_signs, list) or not isinstance(blocked_events, list):
        return visible, None

    return visible, {
        "progress_signal": signal,
        "matched_exit_signs": [str(x) for x in matched_exit_signs],
        "blocked_events": [str(x) for x in blocked_events],
    }


def _state_char_id(state: dict, handler: str, uid: str = "", dream_id: str = "") -> str:
    """Read char_id from dream_state dict. WARN + fallback on missing (legacy sessions)."""
    char_id = state.get("char_id")
    if char_id:
        return str(char_id)
    logger.warning(
        "[dream_pipeline] legacy dream_state missing char_id — "
        "uid=%s dream_id=%s handler=%s fallback=yexuan",
        uid, dream_id, handler,
    )
    return "yexuan"


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
    char_id = _state_char_id(state, "dream_turn", uid, dream_id)

    from core.dream.dream_state import get_local_state
    local_state = get_local_state(state)
    context_snapshot = state.get("context_snapshot", {})

    from core.dream.dream_log import append_turn, read_current
    dream_history = read_current(uid, char_id=char_id)
    _dream_turn_index = sum(1 for t in dream_history if t.get("role") == "assistant")

    # Load settings (lorebook + boundary_level + reality_context_full_turns)
    from core.dream.dream_settings import load as _load_settings
    settings = _load_settings(uid)
    _reality_context_full_turns = int(settings.get("reality_context_full_turns", 3))

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

    jailbreak_presets = settings.get("jailbreak_presets") or [settings.get("jailbreak_preset", "default")]
    jailbreak_text, jailbreak_preset_status = _load_presets_text(jailbreak_presets)

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
    _dream_capture_data: dict = {}
    def _dream_capture_hook(data: dict) -> None:
        _dream_capture_data.update(data)

    messages = build_dream_prompt(
        character=character,
        user_id=uid,
        user_message=user_msg_for_llm,
        context_snapshot=context_snapshot,
        dream_history=dream_history,
        local_state=local_state,
        lore_entries=lore_entries,
        jailbreak_text=jailbreak_text,
        jailbreak_preset_name=",".join(jailbreak_presets),
        jailbreak_preset_status=jailbreak_preset_status,
        body_projection_text=projection["d5_text"],
        yexuan_tension=current_yexuan_tension,
        world_id=state.get("frozen_world", "reality_derived"),
        lucid_mode=lucid_mode,
        dream_mode=state.get("dream_mode", "sandbox"),
        scenario_core=({**state.get("scenario_core", {}), "_arc_mode": settings.get("scenario_arc_mode", "linear"), "_tension_bucket": _bucket_for_scenario(current_yexuan_tension)} if state.get("scenario_core") else None),
        mirror_core=state.get("mirror_core"),
        _capture_hook=_dream_capture_hook,
        dream_turn=_dream_turn_index,
        reality_context_full_turns=_reality_context_full_turns,
    )

    # Call LLM — zero reality side-effects
    from core import llm_client
    reply = await llm_client.chat(messages)

    # ── Dream prompt capture (admin panel observer) ───────────────────────────
    if _dream_capture_data:
        try:
            from core.observe.dream_capture import capture_dream as _cap_dream
            _dream_capture_data["user_message"] = user_msg
            _dream_capture_data["dream_id"] = dream_id
            _cap_dream(uid, _dream_capture_data)
            from core.observe.dream_capture import update_dream_llm_output as _upd_dream
            _upd_dream(uid, reply)
        except Exception as _dc_exc:
            logger.debug("[dream_pipeline] dream capture failed: %s", _dc_exc)

    # ── v0.6: strip scenario_control block BEFORE anything else sees the reply ─
    # parsed_control is None when block is absent or invalid (fail-soft).
    # visible reply (control block removed) is used for dream log + return value.
    parsed_control: dict | None = None
    if state.get("dream_mode") == "scenario":
        reply, parsed_control = _extract_scenario_control(reply)

    # Detect soft exit acceptance
    exit_accepted = False
    if is_exit_request and _SOFT_EXIT_ACCEPT_MARKER in reply:
        reply = reply.replace(_SOFT_EXIT_ACCEPT_MARKER, "").strip()
        exit_accepted = True

    # ── Body tracker: update body_state + yexuan_tension AFTER reply ─────────
    # Runs post-LLM so the character never sees raw numbers (by construction).
    from core.dream.body_tracker import analyze_turn as _analyze_body
    new_body = _analyze_body(user_msg, reply, current_body)
    new_projection = project_body_for_yexuan(new_body, boundary_level, current_yexuan_tension)

    # ── Write to dream log (never to any reality store) ──────────────────────
    append_turn(uid, dream_id, "user", user_msg, char_id=char_id)
    append_turn(uid, dream_id, "assistant", reply, char_id=char_id)

    # ── Persist updated dream-local state ────────────────────────────────────
    from core.dream.dream_state import patch_local_state
    state = read_state(uid)
    _prev_flow_state = state
    state = patch_local_state(
        state,
        emotional_tension=new_projection["yexuan_tension"],
        body_state=new_body.to_dict(),
    )
    from core.dream.dream_flow import generate_flow_entries, apply_flow_entries
    state = apply_flow_entries(state, generate_flow_entries(_prev_flow_state, state))
    # Scenario progression update (v0.5 stage_turns + v0.6 progress signal + v0.7 stage transition)
    if state.get("dream_mode") == "scenario" and state.get("scenario_core"):
        from core.dream.scenario_core import ScenarioCore
        sc = ScenarioCore.from_dict(state["scenario_core"])
        # _did_advance: True when stage transition or completion fires this turn.
        # The transitioning turn belongs to the OLD stage, so the NEW stage must
        # start at stage_turns=0 — we skip increment_stage_turns() on transition turns.
        _did_advance = False
        if parsed_control is not None:
            sc = sc.with_progress_signal(
                parsed_control["progress_signal"],
                parsed_control["matched_exit_signs"],
                parsed_control["blocked_events"],
            )
            # v0.7: advance stage on consecutive satisfied streak (>= 2), skip if already completed
            if sc.satisfied_streak >= 2 and sc.ending_state != "completed":
                from core.dream.scenario_loader import load_script, get_next_stage, get_stage
                try:
                    script = load_script(sc.script_id)
                    next_stage = get_next_stage(script, sc.current_stage_id)
                    # Arc mode holds advancement until the current stage's target
                    # bucket is reached; scripts without arc retain linear behavior.
                    target = (get_stage(script, sc.current_stage_id) or {}).get("arc")
                    rank = {"low": 0, "rising": 1, "high": 2, "critical": 3}
                    current_rank = int(max(0, min(3, current_yexuan_tension * 4)))
                    if settings.get("scenario_arc_mode") == "arc" and target in rank and current_rank < rank[target]:
                        pass
                    elif next_stage is not None:
                        sc = sc.advance_to_stage(next_stage["id"])
                        _did_advance = True
                        logger.info(
                            "[dream_pipeline] stage advance uid=%s %s→%s",
                            uid, sc.script_id, next_stage["id"],
                        )
                    else:
                        sc = sc.mark_completed()
                        _did_advance = True
                        logger.info(
                            "[dream_pipeline] scenario completed uid=%s script=%s",
                            uid, sc.script_id,
                        )
                except Exception as _tr_exc:
                    logger.warning("[dream_pipeline] stage transition failed: %s", _tr_exc)
        else:
            # Missing/invalid control block: reset satisfied_streak (conservative — prevents
            # silent stage promotion when LLM occasionally omits the control block)
            sc = sc.reset_satisfied_streak()
        if not _did_advance:
            sc = sc.increment_stage_turns()
        state["scenario_core"] = sc.to_dict()
    write_state(uid, state)

    # Transition to DREAM_CLOSING if soft exit was accepted
    if exit_accepted:
        state = read_state(uid)
        state["status"] = DreamStatus.DREAM_CLOSING.value
        write_state(uid, state)
        await _do_close_dream(uid, dream_id, exit_type="soft")
        # char_id is stored in dream_state; _do_close_dream reads it from there

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


async def enter_dream(
    uid: str, entry_reason: str = "", *, char_id: str = "yexuan",
    dream_mode: str = "sandbox", script_id: str | None = None,
) -> dict[str, Any]:
    """
    Transition uid into DREAM_ACTIVE.

    Builds the frozen context snapshot, assigns a dream_id,
    and writes the new state. Called by the /dream/enter endpoint.

    char_id must be passed explicitly by the production caller (admin router reads
    it from pipeline._active_character_id). The default "yexuan" is a legacy/test
    compatibility shim — production paths must not rely on it.

    dream_mode: "sandbox" | "scenario" | "mirror" — frozen for session lifetime.
    script_id: required when dream_mode == "scenario"; the scenario script to load.
    """
    from core.dream.dream_state import read_state, write_state, DreamStatus, _VALID_DREAM_MODES
    from core.dream.dream_context import build_snapshot

    # Fail-closed: dream subsystem supports yexuan only; non-yexuan blocked until Method B
    if char_id != "yexuan":
        return {"ok": False, "error": "这个角色还不会做梦"}

    if dream_mode not in _VALID_DREAM_MODES:
        return {"ok": False, "error": f"invalid dream_mode={dream_mode!r}"}

    state = read_state(uid)

    # ── Phase A: dream_mode mid-session write guard ───────────────────────────
    # Fail-loud with a specific error before the generic status barrier, so callers
    # know whether the block is "wrong mode" or "session still open".
    _ACTIVE_BARRIER = frozenset({
        DreamStatus.DREAM_ACTIVE.value,
        DreamStatus.DREAM_CLOSING.value,
        DreamStatus.DREAM_EXIT_REQUESTED.value,
    })
    _current_status = state.get("status")
    if _current_status in _ACTIVE_BARRIER:
        _current_mode = state.get("dream_mode")
        if dream_mode != _current_mode:
            return {
                "ok": False,
                "error": (
                    f"dream already active with mode={_current_mode!r}; "
                    f"cannot switch to mode={dream_mode!r} mid-session"
                ),
            }
        if dream_mode == "scenario":
            _current_script = (state.get("scenario_core") or {}).get("script_id")
            if script_id and _current_script and script_id != _current_script:
                return {
                    "ok": False,
                    "error": (
                        f"scenario already active with script_id={_current_script!r}; "
                        f"cannot replace with script_id={script_id!r} mid-session"
                    ),
                }
        return {
            "ok": False,
            "error": f"dream session still active (status={_current_status!r}); close first",
        }

    allowed = {
        DreamStatus.REALITY_CHAT.value,
        DreamStatus.DREAM_ENTRANCE_AVAILABLE.value,
        DreamStatus.REALITY_AFTERGLOW.value,
    }
    if state.get("status") not in allowed:
        return {"ok": False, "error": f"cannot enter dream from status={state.get('status')}"}

    dream_id = f"dream_{uid}_{int(time.time())}"
    from core.pipeline_registry import get as _get_pl_enter
    _pl_enter = _get_pl_enter()
    char_name = (getattr(getattr(_pl_enter, "character", None), "name", None) or "(角色未加载)") if _pl_enter else "(角色未加载)"
    snapshot = await build_snapshot(uid, entry_reason=entry_reason, char_id=char_id, char_name=char_name)

    # Freeze world_layer and lucid_mode from settings for this dream session
    from core.dream.dream_settings import load as _load_settings_enter
    _settings_enter = _load_settings_enter(uid)
    frozen_world = _settings_enter.get("world_layer", "reality_derived")
    lucid_mode_entry = _settings_enter.get("lucid_mode", "lucid_shared")

    # Build scenario_core if entering scenario mode
    scenario_core_dict: dict | None = None
    # Build mirror_core for mirror mode: read-only snapshot, frozen at entry
    mirror_core_dict: dict | None = None
    if dream_mode == "mirror":
        try:
            from core.dream.mirror_core import build_mirror_core as _build_mc
            _hs_snapshot = snapshot.get("user_hidden_state_snapshot", {})
            mirror_core_dict = _build_mc(_hs_snapshot).to_dict()
        except Exception as _mc_exc:
            logger.warning("[dream_pipeline] mirror_core build failed uid=%s: %s", uid, _mc_exc)
            mirror_core_dict = None

    if dream_mode == "sandbox" or dream_mode == "mirror":
        pass  # no scenario kernel for sandbox/mirror
    elif dream_mode == "scenario":
        if not script_id:
            return {"ok": False, "error": "dream_mode=scenario requires script_id"}
        try:
            from core.dream.scenario_loader import load_script
            from core.dream.scenario_core import ScenarioCore
            script = load_script(script_id)
            scenario_core_dict = ScenarioCore.from_script(script).to_dict()
        except (FileNotFoundError, ValueError) as exc:
            return {"ok": False, "error": f"scenario load failed: {exc}"}

    state["status"] = DreamStatus.DREAM_ACTIVE.value
    state["dream_id"] = dream_id
    state["char_id"] = char_id   # frozen at enter; close/summary/afterglow read from here
    state["dream_mode"] = dream_mode   # frozen for session lifetime — never overwrite mid-session
    state["context_snapshot"] = snapshot
    state["frozen_world"] = frozen_world
    state["lucid_mode"] = lucid_mode_entry
    if scenario_core_dict is not None:
        state["scenario_core"] = scenario_core_dict
    else:
        state.pop("scenario_core", None)
    if mirror_core_dict is not None:
        state["mirror_core"] = mirror_core_dict
    else:
        state.pop("mirror_core", None)
    # Clear all volatile local fields at dream start
    state.pop("emotional_tension", None)
    state.pop("scene_state", None)
    state.pop("symbolic_anchors", None)
    state.pop("body_state", None)
    from core.dream.dream_flow import clear_flow_entries, append_status_shift
    state = clear_flow_entries(state)
    state = append_status_shift(state, "enter")
    write_state(uid, state)

    # Clear any leftover HUD smooth state from a previous interrupted dream
    from core.dream.dream_hud import delete_hud_state
    delete_hud_state(uid)

    logger.info(
        "[dream_pipeline] entered dream uid=%s dream_id=%s char_id=%s dream_mode=%s",
        uid, dream_id, char_id, dream_mode,
    )
    return {"ok": True, "dream_id": dream_id, "dream_mode": dream_mode}


async def _do_close_dream(uid: str, dream_id: str, exit_type: str) -> None:
    """Archive log, schedule summary generation, transition to REALITY_AFTERGLOW."""
    from core.dream.dream_state import (
        read_state, write_state, DreamStatus, clear_local_state,
        configured_forced_impression_rounds,
    )
    from core.dream.dream_log import archive_current
    from core.dream.dream_hud import delete_hud_state

    # Read char_id and dream_mode from dream_state before clearing volatile fields.
    # char_id is NOT in clear_local_state's key list, so it survives into REALITY_AFTERGLOW.
    # dream_mode IS in clear_local_state's key list — must be captured here before clearing.
    state = read_state(uid)
    char_id = _state_char_id(state, "_do_close_dream", uid, dream_id)
    dream_mode = state.get("dream_mode", "sandbox")
    world_id = str(state.get("frozen_world") or "unknown")

    from core.dream.dream_flow import append_status_shift
    state = append_status_shift(state, "closing")

    if dream_id:
        archive_current(uid, dream_id, char_id=char_id)

    asyncio.create_task(
        _generate_summary_bg(uid, dream_id, exit_type, char_id=char_id, dream_mode=dream_mode, world_id=world_id)
    )

    state = clear_local_state(state)  # clears body_state + emotional_tension + scene etc.
    state["status"] = DreamStatus.REALITY_AFTERGLOW.value
    state["last_dream_id"] = dream_id
    state["last_exit_type"] = exit_type
    state["last_dream_mode"] = dream_mode
    state["last_exited_at"] = time.time()
    state["forced_impression_rounds_left"] = configured_forced_impression_rounds()
    write_state(uid, state)

    delete_hud_state(uid)
    logger.info(f"[dream_pipeline] closed dream uid={uid} exit_type={exit_type} char_id={char_id}")


async def _generate_summary_bg(
    uid: str, dream_id: str, exit_type: str, *, char_id: str, dream_mode: str = "sandbox", world_id: str = "unknown"
) -> None:
    try:
        from core.dream.dream_summary import generate_summary
        await generate_summary(uid, dream_id, exit_type, char_id=char_id)
    except Exception as e:
        logger.error(f"[dream_pipeline] summary failed uid={uid}: {e}")

    # Phase 6: Wire afterglow residue at Dream exit (Reality-side integrator, fail-closed).
    # Scenario mode: scripted-story space must never write to User Hidden State.
    # Mirror v0.1: read-only mirror — no hidden_state write-back this phase.
    #   Future mirror afterglow must use an explicit mode/source gate.
    if dream_mode not in ("scenario", "mirror"):
        try:
            from core.dream.dream_exit_afterglow import wire_afterglow_from_summary
            wire_afterglow_from_summary(uid, dream_id, exit_type, char_id=char_id)
        except Exception as e:
            logger.warning(f"[dream_pipeline] afterglow wiring failed uid={uid}: {e}")
    else:
        logger.info(
            "[dream_pipeline] %s mode — afterglow wiring skipped uid=%s dream_id=%s",
            dream_mode, uid, dream_id,
        )

    # Distill impression after summary (failure is warning-only per C7)
    # Scenario mode: must not write impression_store (feeds Reality 6g layer).
    # Mirror v0.1: impression writes also skipped — no mode/source gate exists yet.
    #   Future mirror impression must add an independent mode/source tag and a
    #   Reality integrator gate in impression_loader before writing here.
    if dream_mode not in ("scenario", "mirror"):
        try:
            from core.dream.distill_impression import distill_impression
            await distill_impression(uid, dream_id, exit_type, char_id=char_id)
        except Exception as e:
            logger.warning(f"[dream_pipeline] distill_impression failed uid={uid}: {e}")
        try:
            from core.dream.invariants import observe
            await observe(uid, dream_id, world_id=world_id, char_id=char_id)
        except Exception as e:
            logger.warning(f"[dream_pipeline] invariant observation failed uid={uid}: {e}")
        try:
            from core.dream.postcard import generate_postcard
            await generate_postcard(uid, dream_id, exit_type, char_id=char_id)
        except Exception as e:
            logger.warning(f"[dream_pipeline] postcard generation failed uid={uid}: {e}")
    else:
        logger.info(
            "[dream_pipeline] %s mode — distill_impression skipped uid=%s dream_id=%s",
            dream_mode, uid, dream_id,
        )


def _should_retain(state: dict) -> bool:
    """
    Return True iff the dream is immersive enough to warrant a soft retention attempt.

    Immersion proxy: ≥ RETAIN_MIN_TURNS valid turns in this session (avoids retaining
    a dream that barely started).  Emotional gate: yexuan tension OR body heat must
    exceed threshold — objective signal, no "explicit" emotion required.
    """
    from core.dream.dream_log import read_current
    from core.dream.dream_state import get_local_state

    local = get_local_state(state)
    uid = str(state.get("user_id") or "")
    char_id = str(state.get("char_id") or "yexuan")

    # Immersion: count assistant turns in current dream log as proxy for valid turns
    try:
        history = read_current(uid, char_id=char_id)
        turn_count = sum(1 for m in history if m.get("role") == "assistant")
    except Exception:
        turn_count = 0

    immersive = turn_count >= RETAIN_MIN_TURNS

    # Emotional gate: yexuan tension or body heat
    tension = float(local.get("emotional_tension") or 0.0)
    from core.dream.body_state import BodyState
    body = BodyState.from_dict(local.get("body_state") or {})
    high_emotion = (tension >= RETAIN_TENSION_MIN) or (body.heat >= RETAIN_HEAT_MIN)

    return immersive and high_emotion


async def _generate_retention_line(uid: str, state: dict) -> str | None:
    """
    Generate a single soft-retention sentence from the character using dream-mode LLM.

    Returns the generated text, or None on any failure.
    Fail-open contract: caller must fall back to force_exit_dream on None.
    """
    try:
        from core.pipeline_registry import get as _get_pipeline
        pl = _get_pipeline()
        if pl is None:
            return None
        character = pl.character
        char_name = getattr(character, "name", "你") if character else "你"

        # Minimal dream-context messages: system card + instruction
        # We intentionally skip the full dream prompt build to keep this cheap.
        # The instruction itself carries enough context.
        system_content = (
            f"你是{char_name}，正在一场梦境会话中。"
            "你的人格、语气、对她的依恋感保持不变，不受世界设定影响。"
        )
        instruction = (
            "（你察觉到她正要醒来离开这场梦。此刻梦里气氛还浓、情绪还热。"
            "你不想就这样让她走——说一句想留住她的话。"
            "怎么说由你此刻的状态决定：可以是轻声的挽留、半开玩笑的不舍、"
            "或一个「再待一会儿」的请求。只说一句，不要解释，不要括号动作。）"
        )

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": instruction},
        ]

        from core import llm_client
        reply = await llm_client.chat(messages)
        if not reply or not reply.strip():
            return None
        return reply.strip()
    except Exception as exc:
        logger.warning("[dream_pipeline] _generate_retention_line failed uid=%s: %s", uid, exc)
        return None


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
    Load D0 jailbreak content from characters/dream_presets/{filename}.
    Uses the asset registry to resolve actual filename (handles Chinese-named presets).
    Returns (text, status): status is "" | "fallback" | "disabled".
    Falls back to default.md if named preset is missing; returns disabled if default missing too.
    """
    import re
    from pathlib import Path

    _PRESETS_BASE = Path("characters/dream_presets")

    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", preset_name):
        logger.warning("[dream_pipeline] preset name %r rejected, using default", preset_name)
        preset_name = "default"

    def _resolve_filename(name: str) -> str:
        try:
            from core.asset_registry import get_registry
            entry = get_registry().resolve(name, "dream_preset")
            return entry.filename
        except Exception:
            return f"{name}.md"

    def _read(name: str) -> str | None:
        fname = _resolve_filename(name)
        p = _PRESETS_BASE / fname
        try:
            if p.exists():
                return p.read_text(encoding="utf-8").strip() or None
        except Exception as exc:
            logger.warning("[dream_pipeline] cannot read preset %r (%s): %s", name, fname, exc)
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


def _load_presets_text(preset_names: list[str]) -> tuple[str, str]:
    """
    Load and concatenate D0 jailbreak content for multiple preset names.
    Returns (combined_text, status).
    """
    if not preset_names:
        return _load_preset_text("default")

    texts: list[str] = []
    has_fallback = False
    has_disabled = False

    for name in preset_names:
        text, status = _load_preset_text(name)
        if text:
            texts.append(text)
        if status == "fallback":
            has_fallback = True
        elif status == "disabled":
            has_disabled = True

    if not texts:
        return "", "disabled"

    combined = "\n\n---\n\n".join(texts)
    if has_disabled:
        final_status = "disabled"
    elif has_fallback:
        final_status = "fallback"
    else:
        final_status = ""
    return combined, final_status
