"""One-lock-per-round Stage turn runner."""
from __future__ import annotations

import inspect
import logging
import random
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from core.conversation_gate import conversation_lock
from core.safe_write import rotate_jsonl_if_needed, safe_append_jsonl
from core.memory.episodic_memory import _texture_similarity
from core.stage.arbiter import _is_question, score_candidates
from core.stage.models import Stage, TranscriptEntry
from core.stage.store import append_transcript, load_stage, load_transcript

LoadStageFn = Callable[[str], "Stage | None"]
LoadTranscriptFn = Callable[[str], list[TranscriptEntry]]
AppendTranscriptFn = Callable[[Stage, TranscriptEntry], bool]
TracePathFn = Callable[[str], Path]


def _default_trace_path(group_id: str) -> Path:
    from core.sandbox import get_paths
    return get_paths().stage_arbiter_trace(group_id=group_id)

GenerateReply = Callable[[Stage, str, list[TranscriptEntry], str, str], str | Awaitable[str]]
DeliverReply = Callable[[str, str, str], None | Awaitable[None]]

logger = logging.getLogger(__name__)

_ARBITER_TRACE_MAX_BYTES = 5 * 1024 * 1024
_ARBITER_TRACE_KEEP_N = 3
ECHO_SIM_THRESHOLD = 0.55
SILENCE_THRESHOLD = 0.35


@dataclass(frozen=True)
class StageTurnResult:
    group_id: str
    turn_id: str
    replies: tuple[TranscriptEntry, ...]
    ai_chain_depth: int


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


def _rank_candidates(stage: Stage, transcript: list[TranscriptEntry], candidates, derived_keywords):
    """Keep the base runner compatible with lightweight test/integration scorers."""
    if derived_keywords is None:
        return score_candidates(stage, transcript, candidates=candidates)
    return score_candidates(stage, transcript, candidates=candidates, derived_keywords=derived_keywords)


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
    trace_path_fn: TracePathFn = _default_trace_path,
) -> None:
    """Persist one decision record. Observation must never block a Stage turn."""
    try:
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
        path = trace_path_fn(stage.group_id)
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
    *,
    previous_ai_content: str | None = None,
    append_transcript_fn: AppendTranscriptFn = append_transcript,
) -> tuple[TranscriptEntry | None, bool]:
    content = str(
        await _resolve(generate_reply(stage, speaker_id, list(transcript), turn_id, triggered_by))
        or ""
    ).strip()
    if not content:
        return None, False
    if previous_ai_content and _texture_similarity(content, previous_ai_content) > ECHO_SIM_THRESHOLD:
        return None, True
    entry = TranscriptEntry(
        speaker_id=speaker_id,
        content=content,
        timestamp=time.time(),
        turn_id=turn_id,
        triggered_by=triggered_by,
    )
    if not append_transcript_fn(stage, entry):
        raise RuntimeError(f"failed to append stage reply group={stage.group_id!r}")
    transcript.append(entry)
    if deliver_reply is not None:
        await _resolve(deliver_reply(speaker_id, content, turn_id))
    return entry, False


async def run_owner_turn(
    group_id: str,
    owner_content: str,
    *,
    generate_reply: GenerateReply,
    deliver_reply: DeliverReply | None = None,
    turn_id: str | None = None,
    derived_keywords: dict[str, tuple[str, ...]] | None = None,
    generate_reaction: GenerateReply | None = None,
    load_stage_fn: LoadStageFn = load_stage,
    load_transcript_fn: LoadTranscriptFn = load_transcript,
    append_transcript_fn: AppendTranscriptFn = append_transcript,
    trace_path_fn: TracePathFn = _default_trace_path,
) -> StageTurnResult:
    """Run Phase A + Phase B under one owner conversation lock.

    `load_stage_fn` / `load_transcript_fn` / `append_transcript_fn` /
    `trace_path_fn` default to the reality Stage store (core.stage.store) so
    every existing reality caller is unaffected. A Dream Stage round
    (core.stage.dream_runtime.run_dream_stage_turn) supplies dream-tree
    equivalents instead — the orchestration algorithm below is shared
    byte-for-byte between reality and dream, only the persisted artifacts'
    physical location differs.
    """
    stage = load_stage_fn(group_id)
    if stage is None:
        raise ValueError(f"stage not found: {group_id!r}")
    if stage.status != "active":
        raise RuntimeError(f"stage is not active: {group_id!r}")
    owner_content = str(owner_content).strip()
    if not owner_content:
        raise ValueError("owner_content must not be empty")
    resolved_turn_id = turn_id or uuid.uuid4().hex

    async def _gen_and_append(*args, **kwargs):
        return await _generate_and_append(*args, append_transcript_fn=append_transcript_fn, **kwargs)

    def _trace_call(*args, **kwargs):
        return _append_arbiter_trace(*args, trace_path_fn=trace_path_fn, **kwargs)

    async with conversation_lock(stage.owner_uid):
        owner_entry = TranscriptEntry(
            speaker_id="owner",
            content=owner_content,
            timestamp=time.time(),
            turn_id=resolved_turn_id,
            triggered_by="user",
        )
        if not append_transcript_fn(stage, owner_entry):
            raise RuntimeError(f"failed to append owner stage message group={group_id!r}")
        transcript = load_transcript_fn(group_id)
        replies: list[TranscriptEntry] = []

        # Phase A: each candidate speaks at most once in the direct response wave.
        attempted: set[str] = set()
        responded = 0
        max_responders = min(stage.settings.max_responders, len(stage.roster))
        min_responders = min(stage.settings.min_responders, max_responders)
        initial_ranked = _rank_candidates(stage, transcript, list(stage.roster), derived_keywords)
        from core.recall_gate import is_low_information
        from core.stage.arbiter import addressed_kind
        has_vocative = any(addressed_kind(stage, char_id, owner_content) == "vocative" for char_id in stage.roster)
        may_be_silent = (
            min_responders == 0 and not has_vocative and initial_ranked and initial_ranked[0].total < SILENCE_THRESHOLD
            and stage.settings.allow_silent_rounds and is_low_information(owner_content)
        )
        if may_be_silent:
            _trace_call(stage, transcript, initial_ranked, turn_id=resolved_turn_id, phase="A", selected=[], chain_depth=0, extra={"silent_round": True, "silent_reason": "low_information"})
            return StageTurnResult(stage.group_id, resolved_turn_id, (), 0)
        while responded < max_responders:
            candidates = [char_id for char_id in stage.roster if char_id not in attempted]
            ranked = _rank_candidates(stage, transcript, candidates, derived_keywords)
            if not ranked:
                break
            pick = ranked[0]
            if responded >= min_responders and pick.total < stage.settings.respond_threshold:
                _trace_call(
                    stage, transcript, ranked, turn_id=resolved_turn_id, phase="A",
                    selected=[], chain_depth=0,
                )
                break
            attempted.add(pick.char_id)
            entry, _echo_cut = await _gen_and_append(
                stage,
                pick.char_id,
                transcript,
                resolved_turn_id,
                "user",
                generate_reply,
                deliver_reply,
            )
            _trace_call(
                stage, transcript[:-1] if entry is not None else transcript, ranked,
                turn_id=resolved_turn_id, phase="A",
                selected=[entry.speaker_id] if entry is not None else [], chain_depth=0,
            )
            if entry is not None:
                replies.append(entry)
                responded += 1

        # A configured minimum is a delivery contract, not merely a scoring
        # preference.  Every candidate may have produced an empty/invalid
        # response on the first pass; retry the best eligible speaker once so
        # a transient validator/provider failure cannot silently end the round.
        if responded < min_responders:
            fallback_ranked = _rank_candidates(stage, transcript, list(stage.roster), derived_keywords)
            if fallback_ranked:
                fallback = fallback_ranked[0]
                entry, _echo_cut = await _gen_and_append(
                    stage,
                    fallback.char_id,
                    transcript,
                    resolved_turn_id,
                    "user_retry",
                    generate_reply,
                    deliver_reply,
                )
                _trace_call(
                    stage, transcript[:-1] if entry is not None else transcript, fallback_ranked,
                    turn_id=resolved_turn_id, phase="A", selected=[entry.speaker_id] if entry else [],
                    chain_depth=0, extra={"minimum_reply_retry": True},
                )
                if entry is not None:
                    replies.append(entry)
                    responded += 1

        # Phase B: bounded autonomous continuation, rescored after every reply.
        ai_chain_depth = 0
        while ai_chain_depth < stage.settings.max_ai_chain_depth and transcript:
            latest_speaker = transcript[-1].speaker_id
            candidates = [char_id for char_id in stage.roster if char_id != latest_speaker]
            ranked = _rank_candidates(stage, transcript, candidates, derived_keywords)
            # AI chain uses a looser threshold so peer_reply bonus can clear the bar.
            if not ranked or ranked[0].total < stage.settings.respond_threshold * 0.8:
                if ranked:
                    _trace_call(
                        stage, transcript, ranked, turn_id=resolved_turn_id, phase="B",
                        selected=[], chain_depth=ai_chain_depth,
                    )
                break
            pick = ranked[0]
            entry, echo_cut = await _gen_and_append(
                stage,
                pick.char_id,
                transcript,
                resolved_turn_id,
                latest_speaker,
                generate_reply,
                deliver_reply,
                previous_ai_content=(
                    transcript[-1].content if transcript[-1].speaker_id != "owner" else None
                ),
            )
            _trace_call(
                stage, transcript[:-1] if entry is not None else transcript, ranked,
                turn_id=resolved_turn_id, phase="B",
                selected=[entry.speaker_id] if entry is not None else [], chain_depth=ai_chain_depth,
                extra={"echo_cut": True} if echo_cut else None,
            )
            if entry is None:
                break
            replies.append(entry)
            ai_chain_depth += 1

        # Snapshot Phase A+B's own outcome before Phase R (reactions) touches
        # the transcript — the topic-seed "did the round fall flat" check
        # (below) is about the substantive A+B exchange, not trailing noise.
        ab_reply_count = len(replies)
        ab_last_entry = transcript[-1] if transcript else None

        # Phase R: bounded noise-tier reactions (Brief 85 §3). A one-shot pass
        # over near-miss candidates — not rescored, not counted against
        # max_ai_chain_depth. generate_reaction is optional so callers that
        # don't wire it (or older test doubles) simply skip this phase.
        reaction_budget = stage.settings.max_reactions
        if reaction_budget > 0 and generate_reaction is not None and transcript:
            spoken_this_turn = {entry.speaker_id for entry in replies}
            reaction_peer = transcript[-1].speaker_id
            reaction_candidates = [
                char_id for char_id in stage.roster
                if char_id not in spoken_this_turn and char_id != reaction_peer
            ]
            if reaction_candidates:
                ranked = _rank_candidates(stage, transcript, reaction_candidates, derived_keywords)
                reactors = [
                    item for item in ranked
                    if stage.settings.react_threshold <= item.total < stage.settings.speak_threshold
                ][:reaction_budget]
                reacted: list[str] = []
                for item in reactors:
                    entry, _echo_cut = await _gen_and_append(
                        stage,
                        item.char_id,
                        transcript,
                        resolved_turn_id,
                        reaction_peer,
                        generate_reaction,
                        deliver_reply,
                    )
                    if entry is not None:
                        replies.append(entry)
                        reacted.append(entry.speaker_id)
                if ranked:
                    _trace_call(
                        stage, transcript, ranked, turn_id=resolved_turn_id, phase="R",
                        selected=reacted, chain_depth=ai_chain_depth,
                        extra={"reaction": True},
                    )

        # Phase T: round-end topic seed (Brief 85 §4). Terminal — nothing loops
        # after this, so a seed can never chain into a new Phase B (no
        # recursion to guard against by construction).
        if stage.settings.topic_seed_prob > 0 and transcript:
            falls_flat = ab_reply_count < 2 or (
                ab_last_entry is not None
                and not _is_question(ab_last_entry.content)
                and not any(
                    addressed_kind(stage, char_id, ab_last_entry.content) == "vocative"
                    for char_id in stage.roster if char_id != ab_last_entry.speaker_id
                )
            )
            if falls_flat and random.random() < stage.settings.topic_seed_prob:
                spoken_this_round = {entry.speaker_id for entry in replies}
                seed_candidates = [char_id for char_id in stage.roster if char_id not in spoken_this_round]
                if seed_candidates:
                    seeder = max(
                        seed_candidates,
                        key=lambda char_id: (
                            stage.settings.talkativeness.get(char_id, 0.5),
                            -stage.roster.index(char_id),
                        ),
                    )
                    entry, _echo_cut = await _gen_and_append(
                        stage, seeder, transcript, resolved_turn_id, "topic_seed",
                        generate_reply, deliver_reply,
                    )
                    if entry is not None:
                        replies.append(entry)
                    _trace_call(
                        stage, transcript[:-1] if entry is not None else transcript, [],
                        turn_id=resolved_turn_id, phase="T",
                        selected=[entry.speaker_id] if entry is not None else [],
                        chain_depth=ai_chain_depth, extra={"topic_seed": True},
                    )

    return StageTurnResult(
        group_id=group_id,
        turn_id=resolved_turn_id,
        replies=tuple(replies),
        ai_chain_depth=ai_chain_depth,
    )
