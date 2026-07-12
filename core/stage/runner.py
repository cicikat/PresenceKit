"""One-lock-per-round Stage turn runner."""
from __future__ import annotations

import inspect
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

from core.conversation_gate import conversation_lock
from core.safe_write import rotate_jsonl_if_needed, safe_append_jsonl
from core.stage.arbiter import score_candidates
from core.stage.models import Stage, TranscriptEntry
from core.stage.store import append_transcript, load_stage, load_transcript

GenerateReply = Callable[[Stage, str, list[TranscriptEntry], str, str], str | Awaitable[str]]
DeliverReply = Callable[[str, str, str], None | Awaitable[None]]

logger = logging.getLogger(__name__)

_ARBITER_TRACE_MAX_BYTES = 5 * 1024 * 1024
_ARBITER_TRACE_KEEP_N = 3


@dataclass(frozen=True)
class StageTurnResult:
    group_id: str
    turn_id: str
    replies: tuple[TranscriptEntry, ...]
    ai_chain_depth: int


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


def _append_arbiter_trace(
    stage: Stage,
    transcript: list[TranscriptEntry],
    ranked,
    *,
    turn_id: str,
    phase: str,
    selected: list[str],
    chain_depth: int,
    extra: dict | None = None,
) -> None:
    """Persist one decision record. Observation must never block a Stage turn."""
    try:
        from core.sandbox import get_paths

        latest = transcript[-1] if transcript else None
        record = {
            "ts": time.time(),
            "round_id": turn_id,
            "turn_id": turn_id,
            "phase": phase,
            "latest_speaker": latest.speaker_id if latest else "owner",
            "latest_excerpt": latest.content[:40] if latest else "",
            "addressed": [
                item.char_id for item in ranked if item.parts.get("addressed", 0.0) > 0
            ],
            "candidates": [
                {"char_id": item.char_id, "total": item.total, "parts": item.parts}
                for item in ranked
            ],
            "selected": selected,
            "chain_depth": chain_depth,
        }
        if extra:
            record.update(extra)
        path = get_paths().stage_arbiter_trace(group_id=stage.group_id)
        rotate_jsonl_if_needed(path, _ARBITER_TRACE_MAX_BYTES, _ARBITER_TRACE_KEEP_N)
        if not safe_append_jsonl(path, record):
            logger.debug("[stage.runner] arbiter trace append failed group=%s", stage.group_id)
    except Exception:
        logger.debug("[stage.runner] arbiter trace suppressed group=%s", stage.group_id, exc_info=True)


async def _generate_and_append(
    stage: Stage,
    speaker_id: str,
    transcript: list[TranscriptEntry],
    turn_id: str,
    triggered_by: str,
    generate_reply: GenerateReply,
    deliver_reply: DeliverReply | None,
) -> TranscriptEntry | None:
    content = str(
        await _resolve(generate_reply(stage, speaker_id, list(transcript), turn_id, triggered_by))
        or ""
    ).strip()
    if not content:
        return None
    entry = TranscriptEntry(
        speaker_id=speaker_id,
        content=content,
        timestamp=time.time(),
        turn_id=turn_id,
        triggered_by=triggered_by,
    )
    if not append_transcript(stage, entry):
        raise RuntimeError(f"failed to append stage reply group={stage.group_id!r}")
    transcript.append(entry)
    if deliver_reply is not None:
        await _resolve(deliver_reply(speaker_id, content, turn_id))
    return entry


async def run_owner_turn(
    group_id: str,
    owner_content: str,
    *,
    generate_reply: GenerateReply,
    deliver_reply: DeliverReply | None = None,
    turn_id: str | None = None,
) -> StageTurnResult:
    """Run Phase A + Phase B under one owner conversation lock."""
    stage = load_stage(group_id)
    if stage is None:
        raise ValueError(f"stage not found: {group_id!r}")
    if stage.status != "active":
        raise RuntimeError(f"stage is not active: {group_id!r}")
    owner_content = str(owner_content).strip()
    if not owner_content:
        raise ValueError("owner_content must not be empty")
    resolved_turn_id = turn_id or uuid.uuid4().hex

    async with conversation_lock(stage.owner_uid):
        owner_entry = TranscriptEntry(
            speaker_id="owner",
            content=owner_content,
            timestamp=time.time(),
            turn_id=resolved_turn_id,
            triggered_by="user",
        )
        if not append_transcript(stage, owner_entry):
            raise RuntimeError(f"failed to append owner stage message group={group_id!r}")
        transcript = load_transcript(group_id)
        replies: list[TranscriptEntry] = []

        # Phase A: each candidate speaks at most once in the direct response wave.
        attempted: set[str] = set()
        responded = 0
        max_responders = min(stage.settings.max_responders, len(stage.roster))
        min_responders = min(stage.settings.min_responders, max_responders)
        while responded < max_responders:
            candidates = [char_id for char_id in stage.roster if char_id not in attempted]
            ranked = score_candidates(stage, transcript, candidates=candidates)
            if not ranked:
                break
            pick = ranked[0]
            if responded >= min_responders and pick.total < stage.settings.respond_threshold:
                _append_arbiter_trace(
                    stage, transcript, ranked, turn_id=resolved_turn_id, phase="A",
                    selected=[], chain_depth=0,
                )
                break
            attempted.add(pick.char_id)
            entry = await _generate_and_append(
                stage,
                pick.char_id,
                transcript,
                resolved_turn_id,
                "user",
                generate_reply,
                deliver_reply,
            )
            _append_arbiter_trace(
                stage, transcript[:-1] if entry is not None else transcript, ranked,
                turn_id=resolved_turn_id, phase="A",
                selected=[entry.speaker_id] if entry is not None else [], chain_depth=0,
            )
            if entry is not None:
                replies.append(entry)
                responded += 1

        # Phase B: bounded autonomous continuation, rescored after every reply.
        ai_chain_depth = 0
        while ai_chain_depth < stage.settings.max_ai_chain_depth and transcript:
            latest_speaker = transcript[-1].speaker_id
            candidates = [char_id for char_id in stage.roster if char_id != latest_speaker]
            ranked = score_candidates(stage, transcript, candidates=candidates)
            # AI chain uses a looser threshold so peer_reply bonus can clear the bar.
            if not ranked or ranked[0].total < stage.settings.respond_threshold * 0.8:
                if ranked:
                    _append_arbiter_trace(
                        stage, transcript, ranked, turn_id=resolved_turn_id, phase="B",
                        selected=[], chain_depth=ai_chain_depth,
                    )
                break
            pick = ranked[0]
            entry = await _generate_and_append(
                stage,
                pick.char_id,
                transcript,
                resolved_turn_id,
                latest_speaker,
                generate_reply,
                deliver_reply,
            )
            _append_arbiter_trace(
                stage, transcript[:-1] if entry is not None else transcript, ranked,
                turn_id=resolved_turn_id, phase="B",
                selected=[entry.speaker_id] if entry is not None else [], chain_depth=ai_chain_depth,
            )
            if entry is None:
                break
            replies.append(entry)
            ai_chain_depth += 1

    return StageTurnResult(
        group_id=group_id,
        turn_id=resolved_turn_id,
        replies=tuple(replies),
        ai_chain_depth=ai_chain_depth,
    )
