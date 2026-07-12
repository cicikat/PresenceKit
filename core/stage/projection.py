"""Project shared Stage transcript segments into per-character fixation jobs."""
from __future__ import annotations

from dataclasses import replace

from core.conversation_gate import conversation_lock
from core.memory.scope import MemoryScope
from core.stage.context import render_projection_segment
from core.stage.models import now_iso
from core.stage.store import load_stage, load_transcript, save_stage

MEMORY_STRENGTH_BASE = 0.4
MEMORY_STRENGTH_PER_SPOKEN_LINE = 0.15
MEMORY_STRENGTH_PER_ADDRESS = 0.1
MEMORY_STRENGTH_MIN = 0.4
MEMORY_STRENGTH_MAX = 0.9


def _addressed_count(stage, segment, char_id: str) -> int:
    """Count named-address signals, retaining the pre-Brief-52 boolean fallback."""
    count = 0
    for entry in segment:
        addressed = getattr(entry, "addressed", None)
        if addressed is None and isinstance(entry, dict):
            addressed = entry.get("addressed")
        if isinstance(addressed, (tuple, list, set, frozenset)):
            count += sum(str(item) == char_id for item in addressed)
        elif addressed == char_id:
            count += 1
        else:
            raw = getattr(entry, "_addressed", None)
            if raw is None and isinstance(entry, dict):
                raw = entry.get("_addressed")
            if raw is True:
                count += 1
            elif isinstance(raw, (tuple, list, set, frozenset)):
                count += sum(str(item) == char_id for item in raw)
            elif raw == char_id:
                count += 1
            elif raw is None:
                # Brief 52's current classifier is content-derived rather than
                # persisted on TranscriptEntry; preserve it as the primary path.
                from core.stage.arbiter import addressed_kind

                if addressed_kind(stage, char_id, entry.content) != "none":
                    count += 1
    return count


def participation_memory_strength(stage, segment, char_id: str) -> float:
    spoke = sum(entry.speaker_id == char_id for entry in segment)
    addressed = _addressed_count(stage, segment, char_id)
    strength = (
        MEMORY_STRENGTH_BASE
        + MEMORY_STRENGTH_PER_SPOKEN_LINE * spoke
        + MEMORY_STRENGTH_PER_ADDRESS * addressed
    )
    return max(MEMORY_STRENGTH_MIN, min(MEMORY_STRENGTH_MAX, strength))


async def enqueue_reality_projection(group_id: str) -> int:
    """Enqueue each unprojected transcript segment once; return job count."""
    stage = load_stage(group_id)
    if stage is None:
        raise ValueError(f"stage not found: {group_id!r}")
    if stage.domain != "reality":
        return 0

    async with conversation_lock(stage.owner_uid):
        stage = load_stage(group_id)
        if stage is None:
            raise ValueError(f"stage not found: {group_id!r}")
        transcript = load_transcript(group_id)
        segment = transcript[stage.projection_cursor:]
        if not segment:
            return 0
        rendered = render_projection_segment(stage, segment)
        if not rendered:
            return 0

        from core.post_process import slow_queue

        source = f"group:{stage.group_id}"
        source_turn_id = f"{source}:{stage.projection_cursor}:{len(transcript)}"
        for char_id in stage.roster:
            # Use the character's own lines as `reply` so summarize_turn produces
            # a meaningful fact-based summary rather than echoing an instruction.
            char_lines = [e.content for e in segment if e.speaker_id == char_id]
            char_reply = "\n".join(char_lines)
            slow_queue.enqueue("summarize_to_midterm", {
                "turn_id": source_turn_id,
                "uid": stage.owner_uid,
                "user_content": "群聊共享记录：\n" + rendered,
                "reply": char_reply,
                "tags": ["group_chat"],
                "emotion": "neutral",
                "force_reflect": True,
                "char_id": char_id,
                "scope": MemoryScope.reality_scope(stage.owner_uid, char_id).to_payload(),
                "source": source,
                "memory_strength": participation_memory_strength(stage, segment, char_id),
            })

        updated = replace(stage, projection_cursor=len(transcript), updated_at=now_iso())
        if not save_stage(updated):
            raise RuntimeError(f"failed to advance stage projection cursor {group_id!r}")
        return len(stage.roster)
