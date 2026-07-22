"""
Dream Stage round entry — Brief 100 §2/§3.

Reuses `core.stage.store` / `core.stage.arbiter` / `core.stage.runner` exactly
as reality Stage does: `run_owner_turn()` runs byte-for-byte unmodified,
supplied with dream-tree storage functions (`load_stage_fn` /
`load_transcript_fn` / `append_transcript_fn` / `trace_path_fn`, see
core/stage/runner.py) so every transcript entry and arbiter trace stays
inside the isolated `data/runtime/dreams/_stage/{group_id}/` tree instead of
the reality group's `data/runtime/groups/{group_id}/`.

Isolation contract (BY CONSTRUCTION — mirrors core/dream/dream_pipeline.py;
see tests/test_dream_isolation_guard.py for the automated static-scan tripwire
that enforces this — worded below without the literal marker strings it scans
for, so this docstring doesn't trip its own guard):
- Never enqueues the reality-side mid-term consolidation job
- Never imports the reality-side character-relationship write path
- Never calls Pipeline.fetch_context / Pipeline.build_prompt
- Only appends to the dream-tree transcript via core.stage.dream_store
- Phase R/T are force-off (settings.max_reactions=0, topic_seed_prob=0) —
  no generate_reaction callback is even wired, Brief 100 §0
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import replace
from pathlib import Path

from core.dream.body_projection import project_body_for_yexuan
from core.dream.body_state import BodyState
from core.dream.body_tracker import analyze_turn as _analyze_body_turn
from core.dream.dream_state import DreamStatus
from core.stage.dream_settings import load as load_dream_group_settings
from core.stage.dream_state import (
    patch_local_state,
    read_state as read_dream_group_state,
    write_state as write_dream_group_state,
)
from core.stage.dream_store import append_dream_transcript, load_dream_transcript
from core.stage.dream_views import DreamStageViewRegistry
from core.stage.models import Stage, TranscriptEntry
from core.stage.runner import StageTurnResult, run_owner_turn
from core.stage.store import load_stage as load_reality_stage

logger = logging.getLogger(__name__)

_VIEWS = DreamStageViewRegistry()

# A Dream Stage round is serialized per group.  A second HTTP send must not
# queue behind a stuck LLM call and create another permanent typing indicator.
_ACTIVE_ROUNDS: dict[str, str] = {}
_DREAM_STAGE_TURN_TIMEOUT_S = 90.0


class DreamStageBusyError(RuntimeError):
    """Raised when a group already owns an in-flight Dream Stage round."""


def has_active_round(group_id: str) -> bool:
    """Whether this process is currently executing a round for ``group_id``."""
    return group_id in _ACTIVE_ROUNDS


def reserve_round(group_id: str, round_id: str) -> bool:
    """Atomically reserve a round before its background task is scheduled."""
    if has_active_round(group_id):
        return False
    _ACTIVE_ROUNDS[group_id] = round_id
    return True


def _dream_trace_path(group_id: str) -> Path:
    from core.sandbox import get_paths
    return get_paths().dream_group_arbiter_trace(group_id=group_id)


def _load_dream_stage(group_id: str) -> Stage | None:
    """Project the underlying reality Stage (roster/owner_uid/arbitration
    knobs) into a dream-domain view for this round.

    A group dream is layered onto an already-created reality group (built via
    POST /group/create) — it reuses that group's roster and most arbitration
    settings, but Phase R/T are hardwired off regardless of the reality
    group's configured values (Brief 100 §0 "群设置强制位").
    """
    base = load_reality_stage(group_id)
    if base is None:
        return None
    dream_settings = replace(
        base.settings,
        max_reactions=0,
        topic_seed_prob=0.0,
        allow_silent_rounds=False,
    )
    return replace(base, domain="dream", settings=dream_settings)


def _append_dream_transcript(stage: Stage, entry: TranscriptEntry) -> bool:
    return append_dream_transcript(stage.group_id, entry)


async def run_dream_stage_turn(
    group_id: str,
    owner_content: str,
    *,
    fanout: bool = True,
    turn_id: str | None = None,
    round_id: str | None = None,
) -> StageTurnResult:
    resolved_round_id: str = round_id or turn_id or uuid.uuid4().hex
    active_round_id = _ACTIVE_ROUNDS.get(group_id)
    if active_round_id is not None and active_round_id != resolved_round_id:
        raise DreamStageBusyError(
            f"group dream round already running: group={group_id!r} round={active_round_id!r}"
        )
    _ACTIVE_ROUNDS[group_id] = resolved_round_id
    try:
        reality_stage = load_reality_stage(group_id)
        if reality_stage is None:
            raise ValueError(f"stage not found: {group_id!r}")
        if reality_stage.domain != "reality":
            raise RuntimeError("run_dream_stage_turn requires an underlying reality Stage")

        dream_state = read_dream_group_state(group_id)
        if dream_state.get("status") not in (DreamStatus.DREAM_ACTIVE.value, DreamStatus.DREAM_CLOSING.value):
            raise RuntimeError(f"group dream not active: group={group_id!r}")
        dream_state.update({
            "active_round_id": resolved_round_id,
            "round_status": "running",
            "round_started_at": time.time(),
            "last_round_error": None,
        })
        write_dream_group_state(group_id, dream_state)
        logger.debug("[dream_runtime] round state=running group=%s round=%s", group_id, resolved_round_id)

        async def generate(stg, speaker_id, transcript, _turn_id, triggered_by):
            return await _VIEWS.get(speaker_id).generate(stg, transcript, _turn_id, triggered_by)

        async def deliver(speaker_id: str, content: str, _turn_id: str):
            if not fanout:
                return
            _msg_id = uuid.uuid4().hex
            try:
                from channels import ui_push as _ui_push
                await _ui_push.pseudo_stream_push(
                    content, msg_id=_msg_id, char_id=speaker_id, round_id=resolved_round_id,
                    domain="dream", profile="dream",
                )
            except Exception:
                logger.debug("[dream_runtime] pseudo_stream_push failed", exc_info=True)
            try:
                from channels import desktop_ws as _dws
                if _dws.is_connected():
                    await _dws.push_message(
                        content, msg_id=_msg_id, char_id=speaker_id, round_id=resolved_round_id,
                        domain="dream",
                    )
            except Exception:
                logger.debug("[dream_runtime] WS deliver failed", exc_info=True)

        if fanout:
            try:
                from channels import desktop_ws as _dws
                if _dws.is_connected():
                    await _dws.push_group_round_start(resolved_round_id, group_id, domain="dream")
            except Exception:
                logger.debug("[dream_runtime] WS group_round_start push failed", exc_info=True)

        try:
            result = await asyncio.wait_for(
                run_owner_turn(
                    group_id,
                    owner_content,
                    generate_reply=generate,
                    deliver_reply=deliver,
                    turn_id=resolved_round_id,
                    load_stage_fn=_load_dream_stage,
                    load_transcript_fn=load_dream_transcript,
                    append_transcript_fn=_append_dream_transcript,
                    trace_path_fn=_dream_trace_path,
                ),
                timeout=_DREAM_STAGE_TURN_TIMEOUT_S,
            )
        except TimeoutError as exc:
            logger.warning("[dream_runtime] round state=timed_out group=%s round=%s timeout_s=%s", group_id, resolved_round_id, _DREAM_STAGE_TURN_TIMEOUT_S)
            _mark_round_finished(group_id, resolved_round_id, error="timeout")
            raise TimeoutError(f"group dream round timed out after {_DREAM_STAGE_TURN_TIMEOUT_S:g}s") from exc

        if result.replies:
            _update_shared_state_after_round(group_id, owner_content, result.replies)
        _mark_round_finished(group_id, resolved_round_id)
        logger.debug("[dream_runtime] round state=finished group=%s round=%s", group_id, resolved_round_id)
        return result
    except BaseException:
        _mark_round_finished(group_id, resolved_round_id, error="failed")
        logger.debug("[dream_runtime] round state=failed group=%s round=%s", group_id, resolved_round_id, exc_info=True)
        raise
    finally:
        if fanout:
            try:
                from channels import desktop_ws as _dws
                if _dws.is_connected():
                    await _dws.push_group_round_end(resolved_round_id, group_id, domain="dream")
            except Exception:
                logger.debug("[dream_runtime] WS group_round_end push failed", exc_info=True)
        _ACTIVE_ROUNDS.pop(group_id, None)


def _mark_round_finished(group_id: str, round_id: str, *, error: str | None = None) -> None:
    """Expose a terminal result without reviving a dream that hard-exited."""
    state = read_dream_group_state(group_id)
    if state.get("active_round_id") != round_id:
        return
    if state.get("status") not in (DreamStatus.DREAM_ACTIVE.value, DreamStatus.DREAM_CLOSING.value):
        return
    state.pop("active_round_id", None)
    state.pop("round_started_at", None)
    state["round_status"] = "timed_out" if error == "timeout" else "failed" if error else "idle"
    state["last_round_error"] = error
    write_dream_group_state(group_id, state)


def _update_shared_state_after_round(
    group_id: str, owner_content: str, replies: tuple[TranscriptEntry, ...],
) -> None:
    """Body tracker + tension coupling after a round (Brief 100 §2).

    `body_state` is shared (one body for the whole group); `char_tension` is
    per-char. Each replying character's line nudges the SAME shared body in
    turn, then each gets its own tension projected from the resulting body —
    mirrors the solo pipeline's "tracker runs after LLM" invariant, just
    applied once per replying character instead of once per turn. Best-effort:
    a failure here must never fail the round that already delivered replies.
    """
    try:
        state = read_dream_group_state(group_id)
        settings = load_dream_group_settings(group_id)
        boundary_level = settings.get("boundary_level", "body_perceptible")
        body = BodyState.from_dict(state.get("body_state") or {})
        char_tension = dict(state.get("char_tension") or {})
        for entry in replies:
            body = _analyze_body_turn(owner_content, entry.content, body)
            prev_tension = float(char_tension.get(entry.speaker_id, 0.0))
            projection = project_body_for_yexuan(body, boundary_level, prev_tension)
            char_tension[entry.speaker_id] = projection["yexuan_tension"]
        state = patch_local_state(state, char_tension=char_tension, body_state=body.to_dict())
        write_dream_group_state(group_id, state)
    except Exception:
        logger.warning("[dream_runtime] shared state update failed group=%s", group_id, exc_info=True)
