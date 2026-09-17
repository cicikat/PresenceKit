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
from dataclasses import replace
from typing import Any

from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

HARD_EXIT_KEYWORD = "/stop"

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
_NATURAL_PROGRESS_SIGNALS = {
    "未接近": "not_close",
    "正在接近": "approaching",
    "已经满足": "satisfied",
}
_SCENARIO_CONTROL_V2 = 2

def _bucket_for_scenario(value: float) -> str:
    return ("low", "rising", "high", "critical")[int(max(0, min(3, value * 4)))]


def _scenario_bucket_rank(value: float) -> int:
    return {"low": 0, "rising": 1, "high": 2, "critical": 3}[_bucket_for_scenario(value)]


def _parse_scenario_control(reply: str) -> tuple[str, dict | None, str]:
    """
    Strip <scenario_control>…</scenario_control> from the LLM reply and parse it.

    Returns (visible_reply, parsed_control_or_None, parse_status).
    - visible_reply always has the block removed (even when parse fails).
    - parse_status is ``valid``, ``missing``, or ``invalid``.  The status is
      kept separate from the parsed payload so the state observer can explain
      why a turn did not advance.
    - Fail-soft: never raises.
    """
    match = _SCENARIO_CONTROL_RE.search(reply)
    if not match:
        return reply, None, "missing"

    # Strip the block from visible reply regardless of validity
    visible = (reply[: match.start()] + reply[match.end() :]).strip()

    raw_control = match.group(1).strip()
    try:
        data = json.loads(raw_control)
    except (json.JSONDecodeError, ValueError):
        lines = {}
        for line in raw_control.splitlines():
            if "：" in line:
                key, value = line.split("：", 1)
                lines[key.strip()] = value.strip()
        signal = _NATURAL_PROGRESS_SIGNALS.get(lines.get("进展", ""))
        if signal is None:
            logger.debug("[dream_pipeline] scenario_control natural parse failed")
            return visible, None, "invalid"

        def _items(key: str) -> list[str]:
            value = lines.get(key, "")
            if not value or value == "无":
                return []
            return [item.strip() for item in re.split(r"[；;]", value) if item.strip()]

        return visible, {
            "control_version": 1,
            "progress_signal": signal,
            "matched_exit_signs": _items("命中"),
            "blocked_events": _items("越界"),
        }, "valid"

    if not isinstance(data, dict):
        return visible, None, "invalid"

    # v2 is the only format taught by the current Scenario prompt. It reports
    # short, current-stage IDs; the model cannot submit a stage/next-stage id.
    if "hit" in data:
        hit = data.get("hit")
        blocked = data.get("blocked", [])
        if (
            not isinstance(hit, list)
            or not isinstance(blocked, list)
            or any(not isinstance(item, str) or not item.strip() for item in hit)
            or any(not isinstance(item, str) or not item.strip() for item in blocked)
        ):
            return visible, None, "invalid"
        return visible, {
            "control_version": _SCENARIO_CONTROL_V2,
            "hit": _dedupe_control_items(hit),
            "blocked": _dedupe_control_items(blocked),
        }, "valid"

    signal = data.get("progress_signal")
    if signal not in _VALID_PROGRESS_SIGNALS:
        logger.debug("[dream_pipeline] scenario_control invalid progress_signal=%r", signal)
        return visible, None, "invalid"
    # Keep the legacy JSON shape strict before whitelist normalization.
    matched_exit_signs = data.get("matched_exit_signs", [])
    blocked_events = data.get("blocked_events", [])
    if not isinstance(matched_exit_signs, list) or not isinstance(blocked_events, list):
        return visible, None, "invalid"

    return visible, {
        "control_version": 1,
        "progress_signal": signal,
        "matched_exit_signs": [str(x) for x in matched_exit_signs],
        "blocked_events": [str(x) for x in blocked_events],
    }, "valid"


def _extract_scenario_control(reply: str) -> tuple[str, dict | None]:
    """Compatibility wrapper for callers/tests that only need the old pair."""
    visible, parsed, _status = _parse_scenario_control(reply)
    if isinstance(parsed, dict) and parsed.get("control_version") == 1:
        parsed = {key: value for key, value in parsed.items() if key != "control_version"}
    return visible, parsed


def _dedupe_control_items(items: Any) -> list[str]:
    """Normalize a control list to stable, unique strings without free text."""
    if not isinstance(items, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item).strip()
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _normalize_scenario_control(
    parsed_control: dict | None,
    current_stage: dict[str, Any] | None,
    *,
    parse_status: str = "valid",
) -> dict[str, Any]:
    """Filter a parsed observation against the current stage's two whitelists.

    This is deliberately a pure, deterministic function.  It does not inspect
    visible prose, natural-language keywords, later stages, or user data.
    Unknown values are counted and discarded; their text never enters state or
    the next prompt.
    """
    if parse_status not in {"valid", "missing", "invalid"}:
        parse_status = "invalid"
    if parse_status != "valid" or not isinstance(parsed_control, dict):
        if parse_status == "valid":
            parse_status = "invalid"
        return {
            "status": parse_status,
            "control_version": None,
            "progress_signal": None,
            "matched_exit_signs": [],
            "matched_exit_ids": [],
            "blocked_events": [],
            "blocked_ids": [],
            "valid_exit_sign_count": 0,
            "unknown_exit_sign_count": 0,
            "unknown_blocked_event_count": 0,
        }

    stage = current_stage if isinstance(current_stage, dict) else {}
    exit_sign_items = stage.get("exit_signs")
    blocked_event_items = stage.get("not_yet_allowed")
    allowed_exit_signs = {
        str(item).strip()
        for item in (exit_sign_items if isinstance(exit_sign_items, list) else [])
        if str(item).strip()
    }
    allowed_blocked_events = {
        str(item).strip()
        for item in (blocked_event_items if isinstance(blocked_event_items, list) else [])
        if str(item).strip()
    }

    if parsed_control.get("control_version") == _SCENARIO_CONTROL_V2:
        exit_id_map = {
            f"E{index}": str(item).strip()
            for index, item in enumerate(exit_sign_items if isinstance(exit_sign_items, list) else [], 1)
            if isinstance(item, str) and item.strip()
        }
        blocked_id_map = {
            f"B{index}": str(item).strip()
            for index, item in enumerate(blocked_event_items if isinstance(blocked_event_items, list) else [], 1)
            if isinstance(item, str) and item.strip()
        }
        raw_hit_ids = _dedupe_control_items(parsed_control.get("hit"))
        raw_blocked_ids = _dedupe_control_items(parsed_control.get("blocked"))
        valid_hit_ids = [item for item in raw_hit_ids if item in exit_id_map]
        valid_blocked_ids = [item for item in raw_blocked_ids if item in blocked_id_map]
        return {
            "status": "valid",
            "control_version": _SCENARIO_CONTROL_V2,
            "progress_signal": "satisfied" if valid_hit_ids else "not_close",
            "matched_exit_signs": valid_hit_ids,
            "matched_exit_ids": valid_hit_ids,
            "blocked_events": valid_blocked_ids,
            "blocked_ids": valid_blocked_ids,
            "valid_exit_sign_count": len(valid_hit_ids),
            "unknown_exit_sign_count": len(raw_hit_ids) - len(valid_hit_ids),
            "unknown_blocked_event_count": len(raw_blocked_ids) - len(valid_blocked_ids),
        }

    raw_exit_signs = _dedupe_control_items(parsed_control.get("matched_exit_signs"))
    raw_blocked_events = _dedupe_control_items(parsed_control.get("blocked_events"))
    matched_exit_signs = [item for item in raw_exit_signs if item in allowed_exit_signs]
    blocked_events = [item for item in raw_blocked_events if item in allowed_blocked_events]
    signal = parsed_control.get("progress_signal")
    if signal not in _VALID_PROGRESS_SIGNALS:
        return {
            "status": "invalid",
            "control_version": parsed_control.get("control_version", 1),
            "progress_signal": None,
            "matched_exit_signs": [],
            "matched_exit_ids": [],
            "blocked_events": [],
            "blocked_ids": [],
            "valid_exit_sign_count": 0,
            "unknown_exit_sign_count": 0,
            "unknown_blocked_event_count": 0,
        }
    return {
        "status": "valid",
        "control_version": parsed_control.get("control_version", 1),
        "progress_signal": signal,
        "matched_exit_signs": matched_exit_signs,
        "matched_exit_ids": [
            f"E{index}"
            for index, item in enumerate(exit_sign_items if isinstance(exit_sign_items, list) else [], 1)
            if isinstance(item, str) and item.strip() and item.strip() in matched_exit_signs
        ],
        "blocked_events": blocked_events,
        "blocked_ids": [
            f"B{index}"
            for index, item in enumerate(blocked_event_items if isinstance(blocked_event_items, list) else [], 1)
            if isinstance(item, str) and item.strip() and item.strip() in blocked_events
        ],
        "valid_exit_sign_count": len(matched_exit_signs),
        "unknown_exit_sign_count": len(raw_exit_signs) - len(matched_exit_signs),
        "unknown_blocked_event_count": len(raw_blocked_events) - len(blocked_events),
    }


def _adjudicate_scenario_progress(
    normalized: dict[str, Any],
    *,
    current_stage: dict[str, Any] | None,
    next_stage: dict[str, Any] | None,
    ending_state: str | None,
    scenario_arc_mode: str,
    current_bucket: str,
) -> dict[str, Any]:
    """Make the deterministic stage decision for one normalized observation."""
    status = normalized.get("status")
    if status == "missing":
        return {"advance_to": None, "disposition": "control_missing", "blocked_reason": None}
    if status != "valid":
        return {"advance_to": None, "disposition": "control_invalid", "blocked_reason": None}

    if normalized.get("control_version") == _SCENARIO_CONTROL_V2 and not normalized.get("matched_exit_signs"):
        return {"advance_to": None, "disposition": "no_progress", "blocked_reason": None}

    signal = normalized.get("progress_signal")
    if signal == "approaching":
        return {"advance_to": None, "disposition": "approaching", "blocked_reason": None}
    if signal == "not_close":
        return {"advance_to": None, "disposition": "not_close", "blocked_reason": None}
    if signal != "satisfied":
        return {"advance_to": None, "disposition": "control_invalid", "blocked_reason": None}
    if not normalized.get("matched_exit_signs"):
        return {
            "advance_to": None,
            "disposition": "satisfied_without_valid_exit_sign",
            "blocked_reason": "satisfied_without_valid_exit_sign",
        }
    if ending_state == "completed":
        return {"advance_to": None, "disposition": "completed", "blocked_reason": None}

    stage = current_stage if isinstance(current_stage, dict) else {}
    target = stage.get("arc")
    rank = {"low": 0, "rising": 1, "high": 2, "critical": 3}
    if (
        scenario_arc_mode == "arc"
        and target in rank
        and rank.get(current_bucket, 0) < rank[target]
    ):
        return {
            "advance_to": None,
            "disposition": "arc_blocked",
            "blocked_reason": "arc_target_not_reached",
            "blocked_current_bucket": current_bucket,
            "blocked_target_bucket": target,
        }
    if next_stage is not None and next_stage.get("id"):
        return {
            "advance_to": str(next_stage["id"]),
            "disposition": "advanced",
            "blocked_reason": None,
        }
    return {"advance_to": None, "disposition": "completed", "blocked_reason": None}


def _state_char_id(state: dict, handler: str, uid: str = "", dream_id: str = "") -> str:
    """Read char_id from dream_state dict. WARN + fallback on missing (legacy sessions)."""
    char_id = state.get("char_id")
    if char_id:
        return str(char_id)
    logger.warning(
        "[dream_pipeline] legacy dream_state missing char_id — "
        "uid=%s dream_id=%s handler=%s fallback=%s",
        uid, dream_id, handler, DEFAULT_CHAR_ID,
    )
    return DEFAULT_CHAR_ID


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
    if state.get("dream_mode") == "rpg":
        return {
            "reply": "",
            "exit_accepted": False,
            "force_exited": False,
            "error": "RPG_ENDPOINT_REQUIRED",
        }
    if status not in (
        DreamStatus.DREAM_ACTIVE.value,
        DreamStatus.DREAM_EXIT_REQUESTED.value,
        DreamStatus.DREAM_CLOSING.value,
    ):
        return {
            "reply": "",
            "exit_accepted": False,
            "force_exited": False,
            "error": "not_in_dream",
        }

    # ── Hard exit pre-LLM intercept ───────────────────────────────────────────
    if user_msg.strip().lower() == HARD_EXIT_KEYWORD:
        close_result = await force_exit_dream(uid)
        return {
            "reply": "（梦境已关闭）",
            "exit_accepted": False,
            "force_exited": True,
            "dream_id": close_result.get("dream_id"),
            "already_closed": bool(close_result.get("already_closed")),
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
    # The setting is only a next-session preference.  An active Scenario uses
    # the value frozen by enter_dream; legacy state without the field is strict.
    scenario_injection_mode = state.get(
        "scenario_injection_mode", settings.get("scenario_injection_mode", "strict_stage")
    )
    if scenario_injection_mode not in {"strict_stage", "full_script"}:
        scenario_injection_mode = "strict_stage"

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

    # If user is requesting a soft exit, append a redundant local-turn hint.
    # The authoritative contract also lives in dream_prompt's D8 layer so the
    # model sees it even when this message is transformed by a provider.
    is_exit_request = _looks_like_exit_request(user_msg)
    user_msg_for_llm = user_msg
    if is_exit_request:
        user_msg_for_llm = (
            f"{user_msg}\n\n"
            "[系统提示：若接受用户离开，请按 Dream prompt 中的严格 dream_control JSON 协议输出；"
            "若不接受则输出 stay 控制块。不要用自然语言替代控制块。]"
        )

    from core.dream.dream_prompt import build_dream_prompt
    _dream_capture_data: dict = {}
    scenario_prompt_core: dict[str, Any] | None = None
    scenario_recovery_injected = False
    scenario_reconcile_request: dict[str, Any] | None = None
    if state.get("scenario_core"):
        scenario_prompt_core = {
            **state["scenario_core"],
            "_arc_mode": settings.get("scenario_arc_mode", "linear"),
            "_tension_bucket": _bucket_for_scenario(current_yexuan_tension),
        }
        scenario_recovery_injected = bool(
            state.get("dream_mode") == "scenario"
            and state["scenario_core"].get("recovery_pending")
        )

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
        scenario_core=scenario_prompt_core,
        mirror_core=state.get("mirror_core"),
        _capture_hook=_dream_capture_hook,
        dream_turn=_dream_turn_index,
        reality_context_full_turns=_reality_context_full_turns,
        scenario_injection_mode=scenario_injection_mode,
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

    # Strip machine control blocks BEFORE anything else sees the reply.
    # Neither visible prose nor legacy marker text can close a Dream.
    from core.dream.exit_contract import (
        CONTROL_ABSENT,
        CONTROL_INVALID,
        CONTROL_DECLINED,
        DREAM_CONTROL_ACCEPT,
        CONTROL_MISSING,
        EXIT_REASON_CONTROL_INVALID,
        EXIT_REASON_CONTROL_MISSING,
        parse_dream_control,
        public_control_observation,
    )
    exit_control = parse_dream_control(reply)
    reply = exit_control.visible_reply

    # ── v0.6: strip scenario_control block BEFORE anything else sees the reply ─
    # parsed_control is None when block is absent or invalid (fail-soft).
    # visible reply (control block removed) is used for dream log + return value.
    parsed_control: dict | None = None
    scenario_control_status = "missing"
    if state.get("dream_mode") == "scenario":
        reply, parsed_control, scenario_control_status = _parse_scenario_control(reply)

    # Detect soft exit acceptance only from the structured control contract.
    exit_accepted = is_exit_request and exit_control.decision == DREAM_CONTROL_ACCEPT
    if is_exit_request and exit_control.status in {CONTROL_ABSENT, CONTROL_INVALID}:
        state = read_state(uid)
        observation_status = CONTROL_MISSING if exit_control.status == CONTROL_ABSENT else CONTROL_INVALID
        observation_reason = (
            EXIT_REASON_CONTROL_MISSING
            if exit_control.status == CONTROL_ABSENT
            else EXIT_REASON_CONTROL_INVALID
        )
        state["last_exit_observation"] = public_control_observation(
            status=observation_status,
            reason=observation_reason,
            dream_id=dream_id,
            ts=time.time(),
        )
        write_state(uid, state)
        logger.warning(
            "[dream_pipeline] exit control %s uid=%s dream_id=%s",
            observation_status, uid, dream_id,
        )

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
    # Scenario progression update (stage turns + deterministic Brief 166 decision)
    if state.get("dream_mode") == "scenario" and state.get("scenario_core"):
        from core.dream.scenario_core import ScenarioCore
        from core.dream.scenario_loader import get_next_stage, get_stage, load_script

        sc = ScenarioCore.from_dict(state["scenario_core"])
        _scenario_stage_before = sc.current_stage_id
        # _did_advance: True when stage transition or completion fires this turn.
        # The transitioning turn belongs to the OLD stage, so the NEW stage must
        # start at stage_turns=0 — we skip increment_stage_turns() on transition turns.
        _did_advance = False
        decision: dict[str, Any]
        current_stage = None
        next_stage = None
        try:
            script = load_script(sc.script_id)
            current_stage = get_stage(script, sc.current_stage_id)
            next_stage = get_next_stage(script, sc.current_stage_id)
            normalized = _normalize_scenario_control(
                parsed_control,
                current_stage,
                parse_status=scenario_control_status,
            )
            decision = _adjudicate_scenario_progress(
                normalized,
                current_stage=current_stage,
                next_stage=next_stage,
                ending_state=sc.ending_state,
                scenario_arc_mode=str(settings.get("scenario_arc_mode", "linear")),
                current_bucket=_bucket_for_scenario(current_yexuan_tension),
            )
        except Exception as _tr_exc:
            logger.warning("[dream_pipeline] scenario progress adjudication failed: %s", _tr_exc)
            normalized = _normalize_scenario_control(
                parsed_control,
                None,
                parse_status="invalid" if scenario_control_status == "valid" else scenario_control_status,
            )
            decision = {
                "advance_to": None,
                "disposition": "control_invalid",
                "blocked_reason": "stage_lookup_failed",
            }

        # A recovery cue is a one-shot input.  Consume the prior cue before
        # applying this turn's new observation; a newly blocked event below
        # may set it again for the following turn.
        if scenario_recovery_injected:
            sc = replace(sc, recovery_pending=False)

        if normalized["status"] == "valid":
            sc = sc.with_progress_signal(
                normalized["progress_signal"],
                normalized["matched_exit_signs"],
                normalized["blocked_events"],
            )
            # A model-reported satisfied without a current-stage hit is not a
            # compatibility streak either; it must never become a latent gate.
            if decision["disposition"] == "satisfied_without_valid_exit_sign":
                sc = replace(sc, satisfied_streak=0)
        else:
            sc = replace(
                sc,
                last_progress_signal=None,
                last_matched_exit_signs=[],
                last_blocked_events=[],
                satisfied_streak=0,
            )

        observation = {
            "last_control_status": normalized.get("status"),
            "last_control_version": normalized.get("control_version"),
            "last_valid_exit_sign_count": int(normalized.get("valid_exit_sign_count", 0)),
            "last_unknown_exit_sign_count": int(normalized.get("unknown_exit_sign_count", 0)),
            "last_unknown_blocked_event_count": int(normalized.get("unknown_blocked_event_count", 0)),
            "advance_disposition": decision["disposition"],
            "advance_blocked_reason": decision.get("blocked_reason"),
            "advance_blocked_current_bucket": decision.get("blocked_current_bucket"),
            "advance_blocked_target_bucket": decision.get("blocked_target_bucket"),
        }
        observation["last_matched_exit_ids"] = list(normalized.get("matched_exit_ids") or [])
        observation["last_blocked_ids"] = list(normalized.get("blocked_ids") or [])
        if decision["disposition"] in {"advanced", "completed"}:
            next_stall_turns = 0
        elif decision["disposition"] == "approaching":
            next_stall_turns = max(0, sc.stall_turns - 1)
        else:
            next_stall_turns = sc.stall_turns + 1
        observation["stall_turns"] = next_stall_turns
        observation["recovery_pending"] = bool(
            normalized["status"] == "valid" and normalized.get("blocked_events")
        )
        if decision.get("advance_to"):
            prior_signal = sc.last_progress_signal
            sc = sc.advance_to_stage(decision["advance_to"])
            # Keep the just-adjudicated observation visible after the stage
            # reset; recovery/stage-local state is still cleared by the method.
            sc = replace(
                sc,
                last_progress_signal=prior_signal,
                **observation,
            )
            _did_advance = True
            logger.info(
                "[dream_pipeline] stage advance uid=%s %s→%s",
                uid, sc.script_id, decision["advance_to"],
            )
        elif decision["disposition"] == "completed" and sc.ending_state != "completed":
            sc = replace(sc.mark_completed(), **observation)
            _did_advance = True
            logger.info("[dream_pipeline] scenario completed uid=%s script=%s", uid, sc.script_id)
        else:
            sc = replace(sc, **observation)
        if not _did_advance:
            sc = sc.increment_stage_turns()
        state["scenario_core"] = sc.to_dict()
        try:
            from core.dream.scenario_progress_audit import record as _record_scenario_progress

            _record_scenario_progress(
                dream_id,
                char_id=char_id,
                turn_index=_dream_turn_index,
                current_stage_id=sc.current_stage_id,
                control_status=normalized.get("status"),
                control_version=normalized.get("control_version"),
                matched_exit_ids=normalized.get("matched_exit_ids"),
                blocked_ids=normalized.get("blocked_ids"),
                valid_exit_sign_count=normalized.get("valid_exit_sign_count", 0),
                unknown_exit_sign_count=normalized.get("unknown_exit_sign_count", 0),
                unknown_blocked_event_count=normalized.get("unknown_blocked_event_count", 0),
                disposition=decision.get("disposition", "control_invalid"),
                detail_reason=decision.get("blocked_reason") or "",
                from_stage_id=_scenario_stage_before if decision.get("advance_to") else "",
                to_stage_id=decision.get("advance_to") or "",
                stall_turns=sc.stall_turns,
                recovery_pending=sc.recovery_pending,
            )
        except Exception as _audit_exc:
            logger.warning("[dream_pipeline] scenario progress audit failed: %s", _audit_exc)
        if not _did_advance and next_stage is not None and not exit_accepted:
            try:
                from core.dream.scenario_reconciler import stall_threshold

                trigger = ""
                if normalized.get("status") == "missing":
                    trigger = "control_missing"
                elif normalized.get("status") == "invalid":
                    trigger = "control_invalid"
                elif sc.stall_turns >= stall_threshold():
                    trigger = "stalled"
                elif (
                    scenario_injection_mode == "full_script"
                    and normalized.get("status") == "valid"
                    and not normalized.get("matched_exit_ids")
                ):
                    trigger = "full_script_sync"
                if trigger:
                    dialogue = [
                        {
                            "role": item.get("role"),
                            "text": str(item.get("content") or item.get("text") or "")[:900],
                        }
                        for item in dream_history[-5:]
                        if isinstance(item, dict)
                    ]
                    dialogue.extend([
                        {"role": "user", "text": user_msg[:900]},
                        {"role": "assistant", "text": reply[:900]},
                    ])
                    scenario_reconcile_request = {
                        "uid": uid,
                        "dream_id": dream_id,
                        "char_id": char_id,
                        "turn_index": _dream_turn_index,
                        "assistant_turn_id": f"{dream_id}:assistant:{_dream_turn_index + 1}",
                        "state_version": 0,
                        "from_stage_id": _scenario_stage_before,
                        "current_stage": current_stage if isinstance(current_stage, dict) else {},
                        "next_stage": next_stage if isinstance(next_stage, dict) else {},
                        "completion_signals": [
                            str(normalized.get("progress_signal") or ""),
                            *[str(item) for item in (normalized.get("matched_exit_ids") or [])[:8]],
                        ],
                        "dialogue": dialogue[-7:],
                        "trigger": trigger,
                    }
            except Exception:
                logger.debug("[dream_pipeline] scenario reconciler request build failed", exc_info=True)
    assistant_turn_id = f"{dream_id}:assistant:{_dream_turn_index + 1}"
    if state.get("dream_mode") == "scenario":
        state["last_assistant_turn_id"] = assistant_turn_id
    write_state(uid, state)
    if scenario_reconcile_request is not None:
        scenario_reconcile_request["state_version"] = int(state.get("state_version") or 0)

    # Transition to DREAM_CLOSING if soft exit was accepted
    if exit_accepted:
        state = read_state(uid)
        state["status"] = DreamStatus.DREAM_CLOSING.value
        write_state(uid, state)
        close_result = await _do_close_dream(uid, dream_id, exit_type="soft")
        # char_id is stored in dream_state; _do_close_dream reads it from there
    else:
        close_result = {"already_closed": False, "closed_now": False}

    from core.narrative_parser import parse_narrative_segments as _parse_segs
    _parsed = _parse_segs(reply)

    return {
        "reply": reply,
        "exit_accepted": exit_accepted,
        "force_exited": False,
        "dream_id": dream_id,
        "already_closed": bool(close_result.get("already_closed")),
        "continuation_eligible": bool(exit_accepted and close_result.get("closed_now")),
        "segments": _parsed["segments"],
        "segmented_content": _parsed["content"],
        "scenario_reconcile_request": scenario_reconcile_request,
    }


async def force_exit_dream(
    uid: str,
    *,
    exit_mechanism: str = "user_hard_exit",
    exit_initiator: str = "user",
    exit_reason: str = "user_hard_exit",
) -> dict[str, Any]:
    """
    Hard exit chokepoint — unconditional, immediate, penetrates all state.

    - Called pre-LLM for /stop keyword
    - Called from /dream/exit endpoint (no conversation_lock)
    - Idempotent: safe to call from any state
    - Cannot be disabled by config or role behavior (invariant D)
    """
    from core.dream.dream_state import read_state_checked, write_state, DreamStatus

    state, state_ok = read_state_checked(uid)
    if not state_ok:
        return {
            "ok": False,
            "exited": False,
            "already_closed": False,
            "closed_now": False,
            "dream_id": None,
            "archive_ok": False,
            "error": "dream_state_unavailable",
        }
    status = state.get("status")
    dream_id = str(state.get("dream_id") or "").strip()
    active_statuses = {
        DreamStatus.DREAM_ACTIVE.value,
        DreamStatus.DREAM_EXIT_REQUESTED.value,
        DreamStatus.DREAM_CLOSING.value,
    }
    if status not in active_statuses or not dream_id:
        duplicate_count = 0
        if state.get("last_dream_id"):
            try:
                duplicate_count = max(0, int(state.get("exit_duplicate_count") or 0)) + 1
            except (TypeError, ValueError):
                duplicate_count = 1
            state["exit_duplicate_count"] = duplicate_count
            state["last_duplicate_exit_at"] = time.time()
            write_state(uid, state)
        return {
            "ok": True,
            "exited": True,
            "already_closed": True,
            "closed_now": False,
            "dream_id": str(state.get("last_dream_id") or "") or None,
            "dream_mode": state.get("last_dream_mode"),
            "exit_mechanism": state.get("last_exit_mechanism"),
            "exit_initiator": state.get("last_exit_initiator"),
            "completion": state.get("last_completion"),
            "exit_reason": state.get("last_exit_reason"),
            "assistant_turns": state.get("last_exit_assistant_turns"),
            "archive_ok": state.get("last_archive_ok"),
            "exited_at": state.get("last_exited_at"),
            "duplicate_count": duplicate_count or state.get("exit_duplicate_count", 0),
        }

    if state.get("dream_mode") == "rpg":
        return _force_exit_rpg_dream(uid, state, dream_id)

    if status != DreamStatus.DREAM_CLOSING.value:
        state["status"] = DreamStatus.DREAM_CLOSING.value
        write_state(uid, state)

    logger.info(f"[dream_pipeline] force_exit uid={uid} dream_id={dream_id}")
    return await _do_close_dream(
        uid,
        dream_id,
        exit_type="hard_exit",
        exit_mechanism=exit_mechanism,
        exit_initiator=exit_initiator,
        exit_reason=exit_reason,
    )


def _force_exit_rpg_dream(uid: str, state: dict[str, Any], dream_id: str) -> dict[str, Any]:
    """Hard-close an RPG session with a player-only archive and no LLM."""
    from core.dream.dream_state import DreamStatus, clear_local_state, write_state
    from core.dream.rpg_store import close

    char_id = str(state.get("char_id") or DEFAULT_CHAR_ID)
    from core.dream.rpg_archive import archive_session
    archive_ok, archive_reason = archive_session(uid, dream_id, char_id=char_id)
    if not archive_ok:
        state["status"] = DreamStatus.DREAM_CLOSING.value
        state["last_rpg_archive_reason"] = archive_reason
        write_state(uid, state)
        return {"ok": True, "exited": False, "already_closed": False, "closed_now": False,
                "dream_id": dream_id, "dream_mode": "rpg", "archive_ok": False,
                "rpg_archive_reason": archive_reason, "rpg_session_health": "closing"}
    _core, health = close(uid, dream_id, char_id=char_id)
    closed_at = time.time()
    state = clear_local_state(state)
    state.update({"status": DreamStatus.REALITY_CHAT.value, "last_dream_id": dream_id,
                  "last_dream_mode": "rpg", "last_exited_at": closed_at,
                  "last_archive_ok": archive_ok and health == "ok", "last_exit_reason": "rpg_hard_exit",
                  "last_rpg_archive_reason": archive_reason,
                  "last_rpg_session_health": health, "forced_impression_rounds_left": 0})
    write_state(uid, state)
    return {"ok": True, "exited": True, "already_closed": False, "closed_now": True,
            "dream_id": dream_id, "dream_mode": "rpg", "archive_ok": archive_ok and health == "ok",
            "rpg_archive_reason": archive_reason,
            "exited_at": closed_at, "rpg_session_health": health}


async def enter_dream(
    uid: str, entry_reason: str = "", *, char_id: str = DEFAULT_CHAR_ID,
    dream_mode: str = "sandbox", script_id: str | None = None,
) -> dict[str, Any]:
    """
    Transition uid into DREAM_ACTIVE.

    Builds the frozen context snapshot, assigns a dream_id,
    and writes the new state. Called by the /dream/enter endpoint.

    char_id must be passed explicitly by the production caller (admin router reads
    it from pipeline._active_character_id). The default follows the deployment's
    configured primary character for legacy/test callers.

    dream_mode: "sandbox" | "scenario" | "mirror" — frozen for session lifetime.
    script_id: required when dream_mode == "scenario"; the scenario script to load.
    """
    from core.dream.dream_state import read_state, write_state, DreamStatus, _VALID_DREAM_MODES
    from core.dream.dream_context import build_snapshot

    # Fail-closed: only the deployment's primary character dreams until Method B.
    if char_id != DEFAULT_CHAR_ID:
        return {"ok": False, "error": "这个角色还不会做梦"}

    if dream_mode not in _VALID_DREAM_MODES:
        return {"ok": False, "error": f"invalid dream_mode={dream_mode!r}"}

    # Brief 100 §3 reverse mutual exclusion: a group dream (Dream Stage) owned by
    # this uid blocks solo /dream/enter, mirroring the forward check in
    # core.stage.dream_state.has_active_group_dream_for_owner() used by
    # /group/{id}/dream/enter.
    try:
        from core.stage.dream_state import has_active_group_dream_for_owner
        if has_active_group_dream_for_owner(uid):
            return {"ok": False, "error": "本群正在进行群聊梦境，无法同时开始单人梦境"}
    except Exception as exc:
        logger.error("[dream_pipeline] group dream cross-check failed uid=%s: %s", uid, exc)
        return {"ok": False, "error": "无法确认群梦状态，暂不能入梦"}

    state = read_state(uid)

    # ── Phase A: dream_mode mid-session write guard ───────────────────────────
    # Fail-loud with a specific error before the generic status barrier, so callers
    # know whether the block is "wrong mode" or "session still open".
    if dream_mode == "rpg":
        from core.dream.dream_state import read_state_checked
        state, state_ok = read_state_checked(uid)
        if not state_ok:
            return {"ok": False, "error": "dream_state_unavailable"}

    _ACTIVE_BARRIER = frozenset({
        DreamStatus.DREAM_ACTIVE.value,
        DreamStatus.DREAM_CLOSING.value,
        DreamStatus.DREAM_EXIT_REQUESTED.value,
    })
    _current_status = state.get("status")
    if _current_status in _ACTIVE_BARRIER:
        if char_id != state.get("char_id"):
            return {
                "ok": False,
                "error": "dream already active; cannot switch character mid-session",
            }
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
        if dream_mode == "rpg":
            _current_script = (state.get("rpg_session") or {}).get("script_id")
            if script_id and _current_script and script_id != _current_script:
                return {
                    "ok": False,
                    "error": (
                        f"rpg already active with script_id={_current_script!r}; "
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

    if dream_mode == "rpg":
        if not script_id:
            return {"ok": False, "error": "dream_mode=rpg requires script_id"}
        try:
            from core.dream.scenario_loader import load_script
            from core.dream.rpg_models import RpgCore
            from core.dream.rpg_store import create
            load_script(script_id)
        except (FileNotFoundError, ValueError) as exc:
            return {"ok": False, "error": f"rpg scenario load failed: {exc}"}
        dream_id = f"dream_{uid}_{int(time.time())}"
        now = time.time()
        core = RpgCore(dream_id=dream_id, script_id=script_id, owner_uid=str(uid), char_id=char_id, created_at=now, updated_at=now)
        if not create(core):
            return {"ok": False, "error": "rpg_session_create_failed"}
        state.update({"status": DreamStatus.DREAM_ACTIVE.value, "dream_id": dream_id, "dream_started_at": now,
                      "char_id": char_id, "dream_mode": "rpg", "rpg_session": {"script_id": script_id, "status": "active"}})
        for key in ("context_snapshot", "scenario_core", "mirror_core", "frozen_world", "lucid_mode"):
            state.pop(key, None)
        if not write_state(uid, state):
            from core.dream.rpg_store import mark_uncertain
            mark_uncertain(
                uid,
                dream_id,
                char_id=char_id,
                error_code="RPG_DREAM_STATE_WRITE_FAILED",
            )
            return {"ok": False, "error": "rpg_dream_state_write_failed"}
        return {"ok": True, "dream_id": dream_id, "dream_mode": "rpg", "script_id": script_id}

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
    scenario_injection_mode = _settings_enter.get("scenario_injection_mode", "strict_stage")
    if scenario_injection_mode not in {"strict_stage", "full_script"}:
        scenario_injection_mode = "strict_stage"

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
            if scenario_injection_mode == "full_script":
                from core.dream.scenario_projection import validate_full_script_budget

                budget = validate_full_script_budget(script)
                if not budget["ok"]:
                    return {
                        "ok": False,
                        "error": "scenario full-script projection exceeds configured budget",
                        "error_code": "scenario_full_script_budget_exceeded",
                        "scenario_injection_mode": scenario_injection_mode,
                        "projection": {
                            key: budget[key]
                            for key in (
                                "stage_count", "estimated_chars", "estimated_tokens",
                                "max_stages", "max_chars", "max_tokens", "reasons",
                                "projection_version",
                            )
                        },
                    }
            scenario_core_dict = ScenarioCore.from_script(script).to_dict()
        except (FileNotFoundError, ValueError) as exc:
            return {"ok": False, "error": f"scenario load failed: {exc}"}

    state["status"] = DreamStatus.DREAM_ACTIVE.value
    state["dream_id"] = dream_id
    state["dream_started_at"] = time.time()  # GET /dream/state projection (Brief 94 §2)
    state["char_id"] = char_id   # frozen at enter; close/summary/afterglow read from here
    state["dream_mode"] = dream_mode   # frozen for session lifetime — never overwrite mid-session
    state["context_snapshot"] = snapshot
    if dream_mode == "scenario":
        state["scenario_injection_mode"] = scenario_injection_mode
    else:
        state.pop("scenario_injection_mode", None)
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
    return {
        "ok": True,
        "dream_id": dream_id,
        "dream_mode": dream_mode,
        "scenario_injection_mode": scenario_injection_mode if dream_mode == "scenario" else None,
    }


async def _do_close_dream(
    uid: str,
    dream_id: str,
    exit_type: str,
    *,
    exit_mechanism: str | None = None,
    exit_initiator: str | None = None,
    exit_reason: str | None = None,
) -> dict[str, Any]:
    """Archive log, schedule summary generation, transition to REALITY_AFTERGLOW."""
    from core.dream.dream_state import (
        read_state, write_state, DreamStatus, clear_local_state,
        configured_forced_impression_rounds,
    )
    from core.dream.dream_log import archive_current, read_current
    from core.dream.dream_hud import delete_hud_state
    from core.dream.exit_contract import (
        EXIT_INITIATOR_CHARACTER,
        EXIT_INITIATOR_USER,
        EXIT_MECHANISM_CHARACTER_ACCEPT,
        EXIT_MECHANISM_USER_HARD_EXIT,
        completion_for_exit,
    )

    # A close is a single-writer transition. Repeated EXIT/WAKE calls observe
    # the durable afterglow metadata and must not archive, summarize, or mutate
    # the original close contract a second time.
    state = read_state(uid)
    current_dream_id = str(state.get("dream_id") or "").strip()
    if (
        not dream_id
        or current_dream_id != str(dream_id)
        or state.get("status") not in {
            DreamStatus.DREAM_ACTIVE.value,
            DreamStatus.DREAM_EXIT_REQUESTED.value,
            DreamStatus.DREAM_CLOSING.value,
        }
    ):
        return {
            "ok": True,
            "exited": True,
            "already_closed": True,
            "closed_now": False,
            "dream_id": str(state.get("last_dream_id") or "") or None,
            "dream_mode": state.get("last_dream_mode"),
            "exit_mechanism": state.get("last_exit_mechanism"),
            "exit_initiator": state.get("last_exit_initiator"),
            "completion": state.get("last_completion"),
            "exit_reason": state.get("last_exit_reason"),
            "assistant_turns": state.get("last_exit_assistant_turns"),
            "archive_ok": state.get("last_archive_ok"),
            "exited_at": state.get("last_exited_at"),
        }

    # Read char_id and dream_mode from dream_state before clearing volatile fields.
    # char_id is NOT in clear_local_state's key list, so it survives into REALITY_AFTERGLOW.
    # dream_mode IS in clear_local_state's key list — must be captured here before clearing.
    char_id = _state_char_id(state, "_do_close_dream", uid, dream_id)
    dream_mode = state.get("dream_mode", "sandbox")
    scenario_injection_mode = state.get("scenario_injection_mode", "strict_stage")
    world_id = str(state.get("frozen_world") or "unknown")

    from core.dream.dream_flow import append_status_shift
    state = append_status_shift(state, "closing")

    assistant_turns = 0
    if dream_id:
        current_turns = read_current(uid, char_id=char_id)
        assistant_turns = sum(1 for turn in current_turns if turn.get("role") == "assistant")

    if exit_mechanism is None:
        if exit_type == "soft":
            exit_mechanism = EXIT_MECHANISM_CHARACTER_ACCEPT
        else:
            exit_mechanism = EXIT_MECHANISM_USER_HARD_EXIT
    if exit_initiator is None:
        exit_initiator = (
            EXIT_INITIATOR_CHARACTER
            if exit_mechanism == EXIT_MECHANISM_CHARACTER_ACCEPT
            else EXIT_INITIATOR_USER
        )
    if exit_reason is None:
        exit_reason = (
            "character_accepted"
            if exit_mechanism == EXIT_MECHANISM_CHARACTER_ACCEPT
            else "user_hard_exit"
        )
    completion = completion_for_exit(exit_mechanism, assistant_turns)
    archive_ok = True
    if dream_id:
        archive_ok = archive_current(uid, dream_id, char_id=char_id)

    if not archive_ok:
        # Keep DREAM_CLOSING and the current transcript in place.  A failed
        # archive is not a completed close; the next hard-exit attempt may retry
        # the single archive operation and no summary/afterglow writer is started.
        state["last_archive_ok"] = False
        state["last_close_attempt"] = {
            "dream_id": dream_id,
            "exit_type": exit_type,
            "exit_mechanism": exit_mechanism,
            "exit_initiator": exit_initiator,
            "exit_reason": exit_reason,
            "archive_ok": False,
            "attempted_at": time.time(),
        }
        write_state(uid, state)
        logger.error("[dream_pipeline] close held in DREAM_CLOSING after archive failure uid=%s dream_id=%s", uid, dream_id)
        return {
            "ok": False,
            "exited": False,
            "already_closed": False,
            "closed_now": False,
            "dream_id": dream_id,
            "dream_mode": dream_mode,
            "exit_mechanism": exit_mechanism,
            "exit_initiator": exit_initiator,
            "completion": completion,
            "exit_reason": exit_reason,
            "assistant_turns": assistant_turns,
            "archive_ok": False,
            "exited_at": None,
            "error": "dream_archive_failed",
        }

    exit_metadata = {
        "exit_mechanism": exit_mechanism,
        "exit_initiator": exit_initiator,
        "completion": completion,
        "exit_reason": exit_reason,
        "assistant_turns": assistant_turns,
        "archive_ok": bool(archive_ok),
        "dream_mode": dream_mode,
    }

    if dream_id:
        asyncio.create_task(
            _generate_summary_bg(
                uid,
                dream_id,
                exit_type,
                char_id=char_id,
                dream_mode=dream_mode,
                world_id=world_id,
                exit_metadata=exit_metadata,
            )
        )

    state = clear_local_state(state)  # clears body_state + emotional_tension + scene etc.
    state["status"] = DreamStatus.REALITY_AFTERGLOW.value
    # force_exit_dream() is documented idempotent ("safe to call from any state"),
    # so a redundant hard_exit can reach here with dream_id=="" after a prior close
    # already cleared state["dream_id"]. Blindly overwriting last_dream_id with ""
    # corrupts the value dream_exit's proposer depends on (empty last_dream_id makes
    # it return None forever — this was observed for real: two status_shift entries
    # 33s apart in flow_entries, then last_dream_id=="" stuck ever after). Never let
    # an empty incoming dream_id clobber a previously-recorded one.
    state["last_dream_id"] = dream_id
    state["last_exit_type"] = exit_type
    state["last_exit_mechanism"] = exit_mechanism
    state["last_exit_initiator"] = exit_initiator
    state["last_completion"] = completion
    state["last_exit_reason"] = exit_reason
    state["last_exit_assistant_turns"] = assistant_turns
    state["last_archive_ok"] = bool(archive_ok)
    state["last_dream_mode"] = dream_mode
    if dream_mode == "scenario":
        state["last_scenario_injection_mode"] = scenario_injection_mode
    state["last_exited_at"] = time.time()
    state["forced_impression_rounds_left"] = configured_forced_impression_rounds()
    write_state(uid, state)

    if dream_id:
        try:
            from core.dream.exit_observability import record as _record_exit_lifecycle

            _record_exit_lifecycle(
                uid,
                dream_id,
                char_id=char_id,
                lifecycle="waiting_afterglow",
                reason_code="afterglow_not_ready",
            )
        except Exception as exc:
            logger.warning("[dream_pipeline] exit lifecycle seed failed uid=%s dream_id=%s: %s", uid, dream_id, exc)

    delete_hud_state(uid)
    logger.info(f"[dream_pipeline] closed dream uid={uid} exit_type={exit_type} char_id={char_id}")
    return {
        "ok": True,
        "exited": True,
        "already_closed": False,
        "closed_now": True,
        "dream_id": dream_id,
        "dream_mode": dream_mode,
        "exit_mechanism": exit_mechanism,
        "exit_initiator": exit_initiator,
        "completion": completion,
        "exit_reason": exit_reason,
        "assistant_turns": assistant_turns,
        "archive_ok": bool(archive_ok),
        "exited_at": state.get("last_exited_at"),
    }


async def _generate_summary_bg(
    uid: str,
    dream_id: str,
    exit_type: str,
    *,
    char_id: str,
    dream_mode: str = "sandbox",
    world_id: str = "unknown",
    exit_metadata: dict[str, Any] | None = None,
) -> None:
    try:
        from core.dream.dream_summary import generate_summary
        await generate_summary(
            uid,
            dream_id,
            exit_type,
            char_id=char_id,
            exit_metadata=exit_metadata,
        )
    except Exception as e:
        logger.error(f"[dream_pipeline] summary failed uid={uid}: {e}")

    # Phase 6 / Brief 90 §3: Wire afterglow residue at Dream exit (Reality-side
    # integrator, fail-closed). Scenario mode: scripted-story space must never
    # write to User Hidden State. Mirror v0.2: gate open — mode="mirror" is
    # threaded through to AfterglowResidueInput; integrate_afterglow_and_save()
    # still only ever touches sensitivity.current / embodied_ease.
    if dream_mode != "scenario":
        try:
            from core.dream.dream_exit_afterglow import wire_afterglow_from_summary
            wire_afterglow_from_summary(uid, dream_id, exit_type, char_id=char_id, mode=dream_mode)
        except Exception as e:
            logger.warning(f"[dream_pipeline] afterglow wiring failed uid={uid}: {e}")
    else:
        logger.info(
            "[dream_pipeline] %s mode — afterglow wiring skipped uid=%s dream_id=%s",
            dream_mode, uid, dream_id,
        )

    # Distill impression after summary (failure is warning-only per C7)
    # Scenario mode: must not write impression_store (feeds Reality 6g layer).
    # Mirror v0.2 (Brief 90 §1): gate open — entries are stamped mode="mirror"
    # and impression_loader gates their Reality read-back (contract ①②).
    if dream_mode != "scenario":
        try:
            from core.dream.distill_impression import distill_impression
            await distill_impression(uid, dream_id, exit_type, char_id=char_id, mode=dream_mode)
        except Exception as e:
            logger.warning(f"[dream_pipeline] distill_impression failed uid={uid}: {e}")
    else:
        logger.info(
            "[dream_pipeline] %s mode — distill_impression skipped uid=%s dream_id=%s",
            dream_mode, uid, dream_id,
        )

    # Cross-world invariant observation + postcard generation stay sandbox-only:
    # neither is part of this brief's mirror write-back contract, and both are
    # independent, never-prompt-facing archive readers unrelated to impression_loader.
    if dream_mode == "sandbox":
        try:
            from core.dream.invariants import observe
            await observe(uid, dream_id, world_id=world_id, char_id=char_id)
        except Exception as e:
            logger.warning(f"[dream_pipeline] invariant observation failed uid={uid}: {e}")
        try:
            from core.dream.postcard import generate_postcard
            await generate_postcard(
                uid,
                dream_id,
                exit_type,
                char_id=char_id,
                completion=(exit_metadata or {}).get("completion"),
                exit_metadata=exit_metadata,
            )
        except Exception as e:
            logger.warning(f"[dream_pipeline] postcard generation failed uid={uid}: {e}")


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
    char_id = str(state.get("char_id") or DEFAULT_CHAR_ID)

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
    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", preset_name):
        logger.warning("[dream_pipeline] preset name %r rejected, using default", preset_name)
        preset_name = "default"

    def _resolve_path(name: str):
        try:
            from core.asset_registry import get_registry
            entry = get_registry().resolve(name, "dream_preset")
            return entry.path()
        except Exception:
            from core.authored_asset_resolver import resolve_layered_file
            from core.sandbox import get_paths
            user_dir, legacy_dir = get_paths().dream_preset_read_dirs()
            item = resolve_layered_file(
                user_dir, legacy_dir, f"{name}.md", logical_asset="dream_preset"
            )
            return item.path if item is not None else get_paths().dream_preset_write_path(name)

    def _read(name: str) -> str | None:
        p = _resolve_path(name)
        try:
            if p.exists():
                return p.read_text(encoding="utf-8").strip() or None
        except Exception as exc:
            logger.warning("[dream_pipeline] cannot read preset %r (%s): %s", name, p, exc)
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
