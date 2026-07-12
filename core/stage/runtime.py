"""Reality Stage entry that connects views, delivery, and memory projection."""
from __future__ import annotations

import logging
import uuid

from core.stage.projection import enqueue_reality_projection
from core.stage.runner import StageTurnResult, run_owner_turn
from core.stage.store import load_stage
from core.stage.views import StageViewRegistry

logger = logging.getLogger(__name__)

_VIEWS = StageViewRegistry()


async def run_reality_stage_turn(
    group_id: str,
    owner_content: str,
    *,
    fanout: bool = True,
    turn_id: str | None = None,
    round_id: str | None = None,
) -> StageTurnResult:
    stage = load_stage(group_id)
    if stage is None:
        raise ValueError(f"stage not found: {group_id!r}")
    if stage.domain != "reality":
        raise RuntimeError("run_reality_stage_turn requires a reality Stage")

    # Resolve the round id upfront so WS lifecycle frames share the same id as
    # the transcript entries.  Prefer explicit round_id, fall back to turn_id,
    # then generate a fresh UUID.
    resolved_round_id: str = round_id or turn_id or uuid.uuid4().hex

    async def generate(stg, speaker_id, transcript, _turn_id, triggered_by):
        return await _VIEWS.get(speaker_id).generate(
            stg,
            transcript,
            _turn_id,
            triggered_by,
        )

    async def deliver(speaker_id: str, content: str, _turn_id: str):
        if not fanout:
            return
        # Desktop WS: push directly with char_id + round_id for React correlation.
        try:
            from channels import desktop_ws as _dws
            if _dws.is_connected():
                await _dws.push_message(
                    content, char_id=speaker_id, round_id=resolved_round_id
                )
        except Exception:
            logger.debug("[stage.runtime] WS deliver failed", exc_info=True)
        # Other channels (mobile, QQ): push without round_id.
        from channels import registry as _reg
        for _ch in _reg.get_active():
            if getattr(_ch, "name", None) != "desktop":
                try:
                    await _ch.send(content, stage.owner_uid, char_id=speaker_id)
                except Exception:
                    logger.debug(
                        "[stage.runtime] deliver fanout to %s failed",
                        getattr(_ch, "name", "?"), exc_info=True,
                    )

    if fanout:
        try:
            from channels import desktop_ws as _dws
            if _dws.is_connected():
                await _dws.push_group_round_start(resolved_round_id, group_id)
        except Exception:
            logger.debug("[stage.runtime] WS group_round_start push failed", exc_info=True)

    kwargs = dict(
        generate_reply=generate,
        deliver_reply=deliver,
        turn_id=resolved_round_id,
        derived_keywords={char_id: _VIEWS.get(char_id).topic_keywords(stage.owner_uid) for char_id in stage.roster},
    )
    try:
        result = await run_owner_turn(group_id, owner_content, **kwargs)
    except TypeError as exc:
        # Compatibility for narrow integrations that still stub the pre-52 runner.
        if "derived_keywords" not in str(exc):
            raise
        kwargs.pop("derived_keywords")
        result = await run_owner_turn(group_id, owner_content, **kwargs)

    if fanout:
        try:
            from channels import desktop_ws as _dws
            if _dws.is_connected():
                await _dws.push_group_round_end(resolved_round_id, group_id)
        except Exception:
            logger.debug("[stage.runtime] WS group_round_end push failed", exc_info=True)

    await enqueue_reality_projection(group_id)
    if result.replies:
        from core.stage.char_relations import enqueue_relation_updates

        await enqueue_relation_updates(group_id, result.turn_id)
    return result
