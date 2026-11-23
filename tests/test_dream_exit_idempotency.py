"""Brief 170 close idempotency and Reality continuation contracts."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _active_state(uid: str, *, mode: str = "scenario") -> dict:
    return {
        "user_id": uid,
        "status": "DREAM_ACTIVE",
        "dream_id": "dream-close-once",
        "char_id": "dreamer",
        "dream_mode": mode,
        "frozen_world": "world-frozen",
    }


@pytest.mark.asyncio
async def test_force_exit_closes_and_seeds_once(sandbox, monkeypatch):
    from core.dream import dream_pipeline
    from core.dream.dream_state import read_state, write_state

    uid = "owner-close-once"
    write_state(uid, _active_state(uid))
    archive = MagicMock(return_value=True)
    scheduled = []

    def fake_create_task(coro):
        scheduled.append(coro)
        coro.close()
        return SimpleNamespace()

    monkeypatch.setattr("core.dream.dream_log.archive_current", archive)
    monkeypatch.setattr("core.dream.dream_hud.delete_hud_state", lambda _uid: None)
    monkeypatch.setattr(dream_pipeline.asyncio, "create_task", fake_create_task)

    first = await dream_pipeline.force_exit_dream(uid)
    state_after_first = read_state(uid)
    second = await dream_pipeline.force_exit_dream(uid)
    state_after_second = read_state(uid)

    assert first["already_closed"] is False
    assert second["already_closed"] is True
    assert second["dream_id"] == "dream-close-once"
    assert archive.call_count == 1
    assert len(scheduled) == 1
    for key in (
        "last_dream_id", "last_dream_mode", "last_exit_mechanism",
        "last_exit_initiator", "last_completion", "last_exit_reason",
        "last_exit_assistant_turns", "last_archive_ok", "last_exited_at",
    ):
        assert state_after_second.get(key) == state_after_first.get(key), key
    assert state_after_second["last_dream_mode"] == "scenario"
    assert state_after_second["status"] == "REALITY_AFTERGLOW"


@pytest.mark.asyncio
async def test_continuation_uses_reality_pipeline_and_marks_after_send(sandbox, monkeypatch):
    from core.dream import reality_continuation
    from core.dream.dream_state import read_state, write_state
    from core.dream.exit_observability import (
        CONTINUATION_SENT,
        DELIVERY_CONTINUATION,
        get_record,
    )

    uid = "owner-continuation"
    dream_id = "dream-continuation"
    write_state(uid, {
        "status": "REALITY_AFTERGLOW",
        "last_dream_id": dream_id,
        "char_id": "dreamer",
    })
    pipeline = SimpleNamespace(
        context=SimpleNamespace(fetch=AsyncMock(return_value={"history": [], "profile": {}, "relation": {}, "group_context": "", "user_identity_text": "", "user_facts_text": "", "event_search_result": "", "lore_entries": []})),
        build_prompt=MagicMock(return_value=([{"role": "user", "content": "safe"}], {})),
        run_llm=AsyncMock(return_value="现实侧的一句回应"),
        _active_character_id="dreamer",
    )
    sent = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr("core.pipeline_registry.get", lambda: pipeline)
    monkeypatch.setattr("core.turn_sink.record_assistant_turn", sent)

    assert reality_continuation.enqueue(uid, dream_id, char_id="dreamer") is True
    task = reality_continuation._tasks[(uid, dream_id)]
    await task

    assert pipeline.context.fetch.await_count == 1
    assert pipeline.run_llm.await_count == 1
    assert "梦" in pipeline.context.fetch.await_args.args[1]
    assert "archive" not in pipeline.context.fetch.await_args.args[1]
    sent.assert_awaited_once()
    assert get_record(
        dream_id,
        char_id="dreamer",
        delivery_kind=DELIVERY_CONTINUATION,
    )["lifecycle"] == CONTINUATION_SENT
    assert read_state(uid)["last_greeted_dream_id"] == dream_id
    assert reality_continuation.enqueue(uid, dream_id, char_id="dreamer") is False


@pytest.mark.asyncio
async def test_continuation_cancels_when_new_reality_turn_arrives(sandbox, monkeypatch):
    from core.conversation_gate import conversation_lock
    from core.dream import reality_continuation
    from core.dream.dream_state import write_state
    from core.dream.exit_observability import DELIVERY_CONTINUATION, get_record
    from core.scheduler import proactive_ledger
    from core.scheduler.proactive_ledger import record_user_message

    uid = "owner-continuation-race"
    dream_id = "dream-continuation-race"
    monkeypatch.setattr(proactive_ledger, "time", SimpleNamespace(time=lambda: 1000.0))
    write_state(uid, {
        "status": "REALITY_AFTERGLOW",
        "last_dream_id": dream_id,
        "char_id": "dreamer",
    })
    monkeypatch.setattr("core.pipeline_registry.get", lambda: None)

    async with conversation_lock(uid):
        assert reality_continuation.enqueue(uid, dream_id, char_id="dreamer") is True
        record_user_message(uid)
        await asyncio.sleep(0)

    task = reality_continuation._tasks[(uid, dream_id)]
    await task
    row = get_record(dream_id, char_id="dreamer", delivery_kind=DELIVERY_CONTINUATION)
    assert row["lifecycle"] == "cancelled"
    assert row["reason_code"] == "new_user_turn"
