"""Dream observability API responsibilities; legacy names re-exported by dream.py."""

from admin.routers import dream_archives
from admin.routers import dream_common
from admin.routers import dream_presets

import logging
import json

from fastapi import APIRouter, Depends, Query
from admin.auth import require_scopes

router = APIRouter()
logger = logging.getLogger("admin.routers.dream")

@router.get("/dream/invariants", summary="跨世界身份稳定性（只读）")
async def dream_invariants_get(
    char_id: str | None = Query(default=None),
    _auth=Depends(require_scopes("activity")),
):
    from core.dream.invariants import load
    selected_char = dream_common._validated_live_char_id(char_id)
    entries = load(dream_common._owner_uid(), char_id=selected_char)
    entries.sort(key=lambda item: (bool(item.get("contradicted_by")), int(item.get("count") or 0)), reverse=True)
    return {"entries": entries}


@router.get("/dream/state", summary="读取梦境状态（只读 UI 面板字段）")
async def dream_state_get(
    char_id: str | None = Query(default=None),
    _auth=Depends(require_scopes("activity")),
):
    """
    Read-only UI panel. Returns safe defaults when no dream is active.

    HUD v1: EMA-smoothed fields, anchor_charge injection, world multipliers.
    Persists smooth values to dream_hud_state.json (dream-local, cleared at close).
    Does not read mood_state, user_identity, or any reality store.

    body.{heat,sensitivity,tension} — user always sees own numbers (orthogonal to
      boundary_level, which controls the character's perception only).
    char_tension — the character's dream-local emotional tension (0.0–1.0).
    HUD fields: emotion_label, scene_label, emotion_tension, boundary_intrusion,
      intimacy_tendency, obsession, dream_stability, dream_depth,
      physiological_arousal — all int 0–100.
    """
    uid = dream_common._owner_uid()
    selected_char = dream_common._validated_live_char_id(char_id)
    from core.dream.dream_state import (
        read_state, DreamStatus, DreamGuardStatus,
        derive_dream_state_projection, get_reality_guard_status,
    )
    from core.dream.body_state import BodyState
    from core.dream.dream_settings import load as _load_settings
    from core.dream.dream_hud import derive_hud_v1, load_hud_state, save_hud_state

    state = read_state(uid)
    body = BodyState.from_dict(state.get("body_state") or {})
    settings = _load_settings(uid, char_id=selected_char)

    dream_mode = state.get("dream_mode", "sandbox")
    scenario_info: dict | None = None
    scenario_injection_mode = "strict_stage"
    if dream_mode == "scenario" and state.get("scenario_core"):
        scenario_injection_mode = state.get("scenario_injection_mode", "strict_stage")
        if scenario_injection_mode not in dream_presets._VALID_SCENARIO_INJECTION_MODE:
            scenario_injection_mode = "strict_stage"
        _sc = state["scenario_core"]
        from core.dream.scenario_projection import scenario_projection_metadata

        _scenario_projection = scenario_projection_metadata(
            _sc, injection_mode=scenario_injection_mode
        )
        scenario_info = {
            "script_id": _sc.get("script_id"),
            "current_stage_id": _sc.get("current_stage_id"),
            "stage_turns": int(_sc.get("stage_turns") or 0),
            "last_progress_signal": _sc.get("last_progress_signal"),
            "last_control_status": _sc.get("last_control_status"),
            "last_control_version": _sc.get("last_control_version"),
            "matched_exit_ids": list(_sc.get("last_matched_exit_ids") or []),
            "blocked_ids": list(_sc.get("last_blocked_ids") or []),
            "valid_exit_sign_count": int(_sc.get("last_valid_exit_sign_count") or 0),
            "unknown_exit_sign_count": int(_sc.get("last_unknown_exit_sign_count") or 0),
            "unknown_blocked_event_count": int(_sc.get("last_unknown_blocked_event_count") or 0),
            "advance_disposition": _sc.get("advance_disposition"),
            "advance_blocked_reason": _sc.get("advance_blocked_reason"),
            "advance_blocked_current_bucket": _sc.get("advance_blocked_current_bucket"),
            "advance_blocked_target_bucket": _sc.get("advance_blocked_target_bucket"),
            "stall_turns": int(_sc.get("stall_turns") or 0),
            "recovery_pending": bool(_sc.get("recovery_pending")),
            "blocked_event_count": len(_sc.get("last_blocked_events") or []),
            "scenario_injection_mode": scenario_injection_mode,
            "projection": _scenario_projection,
        }

    base = {
        "status": state.get("status", "REALITY_CHAT"),
        "dream_id": state.get("dream_id"),
        "dream_mode": dream_mode,
        "scenario": scenario_info,
        "scenario_injection_mode": scenario_injection_mode if dream_mode == "scenario" else None,
        "frozen_world": state.get("frozen_world"),
        "lucid_mode": state.get("lucid_mode"),
        "body": {
            "heat": round(body.heat, 2),
            "sensitivity": round(body.sensitivity, 2),
            "tension": round(body.tension, 2),
        },
        "char_tension": float(state.get("emotional_tension", 0.0)),
        "scene_state": state.get("scene_state"),
        "symbolic_anchors": list(state.get("symbolic_anchors") or []),
        "flow_entries": list(state.get("flow_entries") or []),
        "exit_observation": state.get("last_exit_observation"),
        "wake_observation": state.get("last_wake_observation"),
        "last_exit_mechanism": state.get("last_exit_mechanism"),
        "last_exit_initiator": state.get("last_exit_initiator"),
        "last_completion": state.get("last_completion"),
        "last_exit_reason": state.get("last_exit_reason"),
    }

    # Structured status projection for the desktop client (Brief 94 §2): replaces
    # the client's own blanket "正在做梦无法聊天" guess with real bucket + timing +
    # the actual chat-blocking verdict (mirrors chat.py's guard exactly).
    base.update(derive_dream_state_projection(state))
    base["blocks_chat"] = get_reality_guard_status(uid) != DreamGuardStatus.ALLOW

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


@router.get("/dream/operations", summary="读取梦境退出与明信片运维状态（只读）")
async def dream_operations_get(_auth=Depends(require_scopes("activity"))):
    """Return bounded lifecycle metadata for the management surface.

    This endpoint deliberately omits archive turns, postcard letter text,
    prompts, and filesystem paths.  Replay is a separate explicitly scoped
    archive endpoint added for the desktop reader.
    """
    uid = dream_common._owner_uid()
    from core.dream.dream_state import read_state
    from core.dream.exit_observability import DELIVERY_CONTINUATION, list_records
    from core.dream.scenario_progress_audit import list_records as list_scenario_progress
    from core.sandbox import get_paths as _get_paths

    char_id = dream_common._active_dream_char_id()
    state = read_state(uid)
    lifecycle = list_records(char_id=char_id, limit=50)
    continuation_lifecycle = list_records(
        char_id=char_id,
        limit=20,
        delivery_kind=DELIVERY_CONTINUATION,
    )
    scenario_dream_id = str(state.get("dream_id") or state.get("last_dream_id") or "")
    scenario_rows = list_scenario_progress(
        char_id=char_id,
        dream_id=scenario_dream_id or None,
        limit=50,
    )
    scenario_reconciler_rows = [
        row for row in scenario_rows if row.get("record_kind") == "reconciler"
    ]
    scenario_progress_rows = [
        row for row in scenario_rows if row.get("record_kind") != "reconciler"
    ]
    scenario_last = scenario_progress_rows[0] if scenario_progress_rows else None
    scenario_current = state.get("scenario_core") or {}
    scenario_progress = {
        "dream_id": scenario_dream_id or None,
        "current_stage_id": scenario_current.get("current_stage_id") or (
            scenario_last.get("current_stage_id") if scenario_last else None
        ),
        "final_stage_id": scenario_last.get("current_stage_id") if scenario_last else None,
        "last": scenario_last,
        "recent": scenario_progress_rows,
    }
    reconciler_statuses = [row.get("reconciler_status") for row in scenario_reconciler_rows]
    scenario_reconciliation = {
        "dream_id": scenario_dream_id or None,
        "injection_mode": state.get("scenario_injection_mode")
        or state.get("last_scenario_injection_mode")
        or "strict_stage",
        "latest": scenario_reconciler_rows[0] if scenario_reconciler_rows else None,
        "recent": scenario_reconciler_rows,
        "trigger_count": len(scenario_reconciler_rows),
        "applied_count": sum(1 for row in scenario_reconciler_rows if row.get("reconciler_applied")),
        "stale_count": sum(1 for status in reconciler_statuses if status == "stale"),
        "failed_count": sum(1 for status in reconciler_statuses if status == "failed"),
    }
    archive_page = await dream_archives.dream_archive_list(offset=0, limit=20, char_id=char_id)
    last_dream_id = str(state.get("last_dream_id") or "")
    latest_archive = next(
        (item for item in archive_page["items"] if item.get("dream_id") == last_dream_id),
        None,
    )
    close_metadata_consistent = None
    if latest_archive and last_dream_id:
        pairs = (
            ("last_dream_mode", "dream_mode"),
            ("last_exit_mechanism", "exit_mechanism"),
            ("last_exit_initiator", "exit_initiator"),
            ("last_completion", "completion"),
            ("last_exit_reason", "exit_reason"),
        )
        archive_values = [str(latest_archive.get(archive_key) or "") for _, archive_key in pairs]
        if all(value not in {"", "unknown"} for value in archive_values):
            close_metadata_consistent = all(
                str(state.get(state_key) or "") == str(latest_archive.get(archive_key) or "")
                for state_key, archive_key in pairs
            )

    schedule: list[dict] = []
    schedule_path = _get_paths().dreams_postcards_dir(char_id=char_id) / "schedule.json"
    try:
        raw = json.loads(schedule_path.read_text(encoding="utf-8")) if schedule_path.exists() else []
        if isinstance(raw, list):
            for item in raw[-50:]:
                if not isinstance(item, dict):
                    continue
                schedule.append({
                    key: item.get(key)
                    for key in (
                        "dream_id", "scheduled_date", "sent", "attempts", "last_error",
                        "generation_status", "delivery_status", "eligibility_reason",
                        "completion", "exit_mechanism", "exit_initiator",
                    )
                })
    except Exception:
        schedule = []

    afterglow: dict = {"present": False, "created_at": None}
    try:
        from core.memory.user_hidden_state_store import _load_afterglow_raw
        residue = _load_afterglow_raw(uid, char_id=char_id) or {}
        afterglow = {
            "present": bool(residue),
            "created_at": residue.get("created_at"),
        }
    except Exception:
        pass

    summary: dict = {"present": False, "created_at": None, "dream_id": None}
    if dream_common._SAFE_DREAM_ID_RE.fullmatch(last_dream_id):
        summary_path = _get_paths().dreams_summaries_dir(char_id=char_id) / f"dream_{last_dream_id}.summary.json"
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
            summary = {
                "present": bool(data),
                "created_at": data.get("created_at"),
                "dream_id": last_dream_id,
            }
        except Exception:
            pass

    return {
        "char_id": char_id,
        "current": {
            key: state.get(key)
            for key in (
                "status", "dream_id", "last_dream_id", "last_greeted_dream_id",
                "last_exit_type", "last_exit_mechanism", "last_exit_initiator",
                "last_completion", "last_exit_reason", "last_exit_assistant_turns",
                "last_archive_ok", "last_exited_at", "last_wake_observation",
                "exit_duplicate_count", "last_duplicate_exit_at", "last_close_attempt",
            )
        },
        "consistency": {
            "last_dream_id_present": bool(last_dream_id),
            "last_greeted_matches_last_dream": bool(
                state.get("last_greeted_dream_id") in (None, "", last_dream_id)
            ),
            "close_metadata_consistent": close_metadata_consistent,
        },
        "archives": archive_page["items"],
        "exit_lifecycle": lifecycle,
        "continuation": {
            "last": continuation_lifecycle[0] if continuation_lifecycle else None,
            "recent": continuation_lifecycle,
        },
        "scenario_progress": scenario_progress,
        "scenario_reconciliation": scenario_reconciliation,
        "postcards": schedule,
        "afterglow": afterglow,
        "summary": summary,
    }


@router.get("/dream/stats", summary="梦境次数统计（只读，有效梦 > N 轮）")
async def dream_stats_get(
    char_id: str | None = Query(default=None),
    _auth=Depends(require_scopes("activity")),
):
    from core.dream.dream_log import count_valid_dreams
    selected_char = dream_common._validated_live_char_id(char_id)
    return count_valid_dreams(char_id=selected_char)
