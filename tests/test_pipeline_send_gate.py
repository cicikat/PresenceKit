"""
tests/test_pipeline_send_gate.py — _pipeline_send perceive_event gate + conversation_lock

P1 gate audit: _pipeline_send now routes through receive_perceive_event (Dream Guard +
TTL dedup) and holds conversation_lock(uid) for the full fetch_context → build_prompt
→ run_llm → record_assistant_turn critical section.

Covers:
1. _pipeline_send + desktop_wake Path B concurrent → same uid, at most one LLM in section
2. Two _pipeline_send calls, same uid, different triggers → serialize, never concurrent LLM
3. Different uid → not blocked by each other (uid-level lock, no cross-uid serialization)
4. conversation_lock is uid-level, not char_id level (different char_id same uid → same lock)
5. Dream Guard BLOCK_ACTIVE → _pipeline_send returns None, no LLM
6. Dream Guard BLOCK_UNCERTAIN → _pipeline_send returns None (fail-closed)
7. Duplicate scheduler event (same trigger, same 60s bucket) → no LLM, no post_process/fanout
"""

import asyncio
import time
import uuid

import pytest


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    """Reset perceive_event dedup registry and conversation locks between tests."""
    from core.perceive_event import clear_dedup_registry_for_test
    clear_dedup_registry_for_test()
    import core.conversation_gate as _cg
    _cg._conversation_locks.clear()
    # Save and restore pipeline via pipeline_registry (R7-B: scheduler no longer owns _pipeline)
    import core.scheduler.loop as _loop
    import core.pipeline_registry as _preg
    orig_pipeline = _preg.get()
    # Reset active-window so user is not flagged as active
    orig_last_msg = _loop._last_user_message_time
    _loop._last_user_message_time = 0.0
    yield
    clear_dedup_registry_for_test()
    _cg._conversation_locks.clear()
    _preg.register(orig_pipeline)
    _loop._last_user_message_time = orig_last_msg


def _allow_dream_guard(monkeypatch):
    from core.dream import dream_state as _ds

    class _FakeStatus:
        ALLOW = "allow"

    monkeypatch.setattr(_ds, "get_reality_guard_status", lambda uid: _FakeStatus.ALLOW)
    monkeypatch.setattr(_ds, "DreamGuardStatus", _FakeStatus)


def _block_dream_guard(monkeypatch, status="BLOCK_ACTIVE"):
    from core.dream import dream_state as _ds

    class _FakeStatus:
        ALLOW = "allow"
        BLOCK_ACTIVE = "block_active"
        BLOCK_UNCERTAIN = "block_uncertain"

    monkeypatch.setattr(_ds, "get_reality_guard_status",
                        lambda uid: getattr(_FakeStatus, status))
    monkeypatch.setattr(_ds, "DreamGuardStatus", _FakeStatus)


def _make_fake_pipeline(llm_fn=None):
    class _FakePipeline:
        async def fetch_context(self, uid, content, *a, **kw):
            return {}

        def build_prompt(self, uid, content, context, **kw):
            return [{"role": "user", "content": content}], {}

        async def run_llm(self, messages):
            if llm_fn:
                return await llm_fn(messages)
            return "test_reply"

        async def post_process(self, uid, content, reply, **kwargs):
            return {"turn_id": "t-test", "critical_written": True, "emotion": "neutral"}

    return _FakePipeline()


def _setup_pipeline_send(monkeypatch, owner_id="owner1", char_id="yexuan", pipeline=None):
    """Patch all _pipeline_send runtime dependencies."""
    import core.scheduler.loop as _loop

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": owner_id}, "character": {"name": "叶瑄"}},
    )
    monkeypatch.setattr(_loop, "_active_char_id_or_none", lambda: char_id)
    monkeypatch.setattr(
        "core.scheduler.triggers.birthday._is_birthday_period", lambda: False,
    )
    if pipeline is not None:
        import core.pipeline_registry as _preg
        _preg.register(pipeline)

    import core.turn_sink as _ts

    async def _fake_record(**kwargs):
        from core.turn_sink import TurnResult
        return TurnResult(turn_id="t-sched", written_to_memory=True, fanout_targets=[])

    monkeypatch.setattr(_ts, "record_assistant_turn", _fake_record)
    monkeypatch.setattr("channels.desktop_ws.is_connected", lambda: False)


# ── Test 1: _pipeline_send + desktop_wake Path B concurrent → serialize ───────

async def test_pipeline_send_and_path_b_serialize_on_same_uid(monkeypatch):
    """
    _pipeline_send and a desktop_wake Path B-style caller both acquire
    conversation_lock(uid).  For the same uid, run_llm must never be concurrent.
    """
    concurrent_count = [0]
    max_concurrent = [0]
    total_llm = [0]

    async def tracked_llm(messages):
        concurrent_count[0] += 1
        max_concurrent[0] = max(max_concurrent[0], concurrent_count[0])
        await asyncio.sleep(0.05)  # hold slot; lets other coroutine try to enter
        concurrent_count[0] -= 1
        total_llm[0] += 1
        return "reply"

    fp = _make_fake_pipeline(tracked_llm)
    _allow_dream_guard(monkeypatch)
    _setup_pipeline_send(monkeypatch, owner_id="owner-wake", char_id="yexuan", pipeline=fp)

    from core.conversation_gate import conversation_lock as _conv_lock
    from core.perceive_event import PerceiveEvent, receive_perceive_event, PerceiveStatus
    import core.scheduler.loop as _loop

    async def path_b_style_call():
        """Simulates desktop_wake Path B: perceive_event gate → conversation_lock → LLM."""
        event = PerceiveEvent(
            source="desktop_wake", uid="owner-wake", channel="desktop", kind="wake",
            event_id=str(uuid.uuid4()),  # unique id → never deduped
        )
        result = await receive_perceive_event(event)
        if result.status != PerceiveStatus.ACCEPTED:
            return None
        async with _conv_lock("owner-wake"):
            return await fp.run_llm([])

    await asyncio.gather(
        _loop._pipeline_send("morning prompt", trigger_name="morning_greeting"),
        path_b_style_call(),
    )

    assert max_concurrent[0] <= 1, (
        f"at most 1 concurrent run_llm for same uid; max was {max_concurrent[0]}"
    )
    assert total_llm[0] >= 1, "at least one LLM call must complete"


# ── Test 2: Two _pipeline_send, same uid, different triggers → serialize ──────

async def test_two_pipeline_sends_same_uid_serialize(monkeypatch):
    """
    Two _pipeline_send calls with different trigger_names (different dedupe keys)
    both pass the gate but serialize on conversation_lock — run_llm never concurrent.
    """
    in_llm = [False]
    overlap_detected = [False]
    total_llm = [0]

    async def checking_llm(messages):
        if in_llm[0]:
            overlap_detected[0] = True
        in_llm[0] = True
        await asyncio.sleep(0.05)  # yield; second coroutine must wait on lock, not enter here
        in_llm[0] = False
        total_llm[0] += 1
        return "reply"

    fp = _make_fake_pipeline(checking_llm)
    _allow_dream_guard(monkeypatch)
    _setup_pipeline_send(monkeypatch, owner_id="owner2", char_id="yexuan", pipeline=fp)

    import core.scheduler.loop as _loop

    await asyncio.gather(
        _loop._pipeline_send("morning", trigger_name="morning_greeting"),
        _loop._pipeline_send("night",   trigger_name="night_reminder"),
    )

    assert not overlap_detected[0], (
        "run_llm must never be concurrent for the same uid"
    )
    assert total_llm[0] == 2, (
        f"both triggers should complete (different dedupe keys); got {total_llm[0]}"
    )


# ── Test 3: Different uid → concurrent, not blocked ──────────────────────────

async def test_different_uid_locks_are_independent():
    """
    conversation_lock is keyed by uid only.  Different uids get different lock objects
    and can be concurrent; same uid always gets the same lock.
    """
    import core.conversation_gate as _cg
    _cg._conversation_locks.clear()

    lock_a1 = _cg.conversation_lock("uid-A")
    lock_a2 = _cg.conversation_lock("uid-A")
    lock_b  = _cg.conversation_lock("uid-B")

    assert lock_a1 is lock_a2, "same uid → same lock object"
    assert lock_a1 is not lock_b, "different uid → different lock object"


async def test_different_uid_tasks_can_run_concurrently():
    """
    Two tasks for different uids must be able to enter their critical sections
    simultaneously (not serialized by each other's lock).
    """
    import core.conversation_gate as _cg
    _cg._conversation_locks.clear()

    both_inside = asyncio.Event()
    uid_entered: dict[str, bool] = {"uid-X": False, "uid-Y": False}

    async def critical_section(uid: str):
        async with _cg.conversation_lock(uid):
            uid_entered[uid] = True
            if all(uid_entered.values()):
                both_inside.set()
            await asyncio.sleep(0.03)

    await asyncio.gather(
        critical_section("uid-X"),
        critical_section("uid-Y"),
    )

    assert both_inside.is_set(), (
        "tasks for different uids must be able to be inside conversation_lock simultaneously"
    )


# ── Test 4: uid-level lock — different char_id, same uid → same lock ─────────

def test_conversation_lock_is_uid_level_not_char_id_level():
    """
    conversation_lock does NOT split on char_id.  Two turns for the same uid but
    different characters must serialize on the same lock (uid-level semantics).
    """
    import core.conversation_gate as _cg
    _cg._conversation_locks.clear()

    # Simulate what both _pipeline_send and a char-switched caller would produce
    lock_uid1_charA = _cg.conversation_lock("uid-1")
    lock_uid1_charB = _cg.conversation_lock("uid-1")  # same uid, "different char_id" context

    assert lock_uid1_charA is lock_uid1_charB, (
        "same uid must share one conversation_lock regardless of char_id — uid-level semantics"
    )


# ── Test 5: Dream Guard BLOCK_ACTIVE → no LLM ────────────────────────────────

async def test_dream_guard_block_active_prevents_llm(monkeypatch):
    """When dream guard returns BLOCK_ACTIVE, _pipeline_send returns None without calling LLM."""
    llm_called = [0]

    async def counting_llm(messages):
        llm_called[0] += 1
        return "reply"

    fp = _make_fake_pipeline(counting_llm)
    _block_dream_guard(monkeypatch, "BLOCK_ACTIVE")
    _setup_pipeline_send(monkeypatch, owner_id="owner5", pipeline=fp)

    import core.scheduler.loop as _loop
    result = await _loop._pipeline_send("test", trigger_name="morning_greeting")

    assert result is None, f"BLOCK_ACTIVE should make _pipeline_send return None, got {result!r}"
    assert llm_called[0] == 0, (
        f"LLM must not be called when dream guard blocks; called {llm_called[0]} times"
    )


# ── Test 6: Dream Guard BLOCK_UNCERTAIN → no LLM (fail-closed) ───────────────

async def test_dream_guard_block_uncertain_prevents_llm(monkeypatch):
    """Dream guard fail-closed: BLOCK_UNCERTAIN also prevents LLM (not just BLOCK_ACTIVE)."""
    llm_called = [0]

    async def counting_llm(messages):
        llm_called[0] += 1
        return "reply"

    fp = _make_fake_pipeline(counting_llm)
    _block_dream_guard(monkeypatch, "BLOCK_UNCERTAIN")
    _setup_pipeline_send(monkeypatch, owner_id="owner6", pipeline=fp)

    import core.scheduler.loop as _loop
    result = await _loop._pipeline_send("test", trigger_name="morning_greeting")

    assert result is None, f"BLOCK_UNCERTAIN should return None (fail-closed), got {result!r}"
    assert llm_called[0] == 0, (
        f"LLM must not be called on uncertain dream guard; called {llm_called[0]} times"
    )


# ── Test 7: Duplicate scheduler event → no LLM, no post_process ──────────────

async def test_duplicate_scheduler_event_no_llm_no_post_process(monkeypatch):
    """
    Same trigger_name fired twice within the same 60s time bucket → second is DUPLICATE.
    No LLM call and no post_process on the second invocation.
    """
    llm_called = [0]
    pp_called = [0]

    async def counting_llm(messages):
        llm_called[0] += 1
        return "reply"

    class _TrackingPipeline:
        async def fetch_context(self, uid, content, *a, **kw):
            return {}

        def build_prompt(self, uid, content, context, **kw):
            return [], {}

        async def run_llm(self, messages):
            return await counting_llm(messages)

        async def post_process(self, uid, content, reply, **kwargs):
            pp_called[0] += 1
            return {"turn_id": "t", "critical_written": True, "emotion": "neutral"}

    fp = _TrackingPipeline()
    _allow_dream_guard(monkeypatch)

    import core.scheduler.loop as _loop
    import core.turn_sink as _ts

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "owner7"}, "character": {"name": "叶瑄"}},
    )
    monkeypatch.setattr(_loop, "_active_char_id_or_none", lambda: "yexuan")
    monkeypatch.setattr("core.scheduler.triggers.birthday._is_birthday_period", lambda: False)
    import core.pipeline_registry as _preg
    _preg.register(fp)

    # Stub record_assistant_turn to call post_process so we can observe it
    async def _record_with_pp(**kwargs):
        pipeline = kwargs.get("pipeline") or fp
        await pipeline.post_process(
            kwargs.get("uid", ""),
            kwargs.get("trigger_name", ""),
            kwargs.get("assistant_text", ""),
        )
        from core.turn_sink import TurnResult
        return TurnResult(turn_id="t-7", written_to_memory=True, fanout_targets=[])

    monkeypatch.setattr(_ts, "record_assistant_turn", _record_with_pp)
    monkeypatch.setattr("channels.desktop_ws.is_connected", lambda: False)

    # Both calls in the same 60s time bucket (default created_at = time.time())
    result1 = await _loop._pipeline_send("test1", trigger_name="morning_greeting")
    result2 = await _loop._pipeline_send("test2", trigger_name="morning_greeting")

    assert result1 == "reply", f"first call should succeed: {result1!r}"
    assert result2 is None, f"duplicate should return None: {result2!r}"
    assert llm_called[0] == 1, (
        f"LLM must be called exactly once; got {llm_called[0]}"
    )
    assert pp_called[0] == 1, (
        f"post_process must be called exactly once; got {pp_called[0]}"
    )


# ── Test 8: ACCEPTED path logs perceive_event=true, not legacy_path=true ─────

async def test_pipeline_send_logs_perceive_event_true(monkeypatch, caplog):
    """
    After perceive_event gate accepts, _pipeline_send must log perceive_event=true
    (not legacy_path=true) to avoid misleading operators into thinking the old path
    is still active.
    """
    import logging

    fp = _make_fake_pipeline()
    _allow_dream_guard(monkeypatch)
    _setup_pipeline_send(monkeypatch, owner_id="owner8", char_id="yexuan", pipeline=fp)

    import core.scheduler.loop as _loop

    with caplog.at_level(logging.INFO, logger="core.scheduler.loop"):
        result = await _loop._pipeline_send("test", trigger_name="morning_greeting")

    assert result is not None, "gate-accepted call should return a reply"

    accepted_logs = [r.message for r in caplog.records if "perceive_event=true" in r.message]
    assert accepted_logs, (
        "Expected a log record containing 'perceive_event=true' after gate acceptance; "
        f"found none. All records: {[r.message for r in caplog.records]}"
    )

    misleading_logs = [r.message for r in caplog.records if "legacy_path=true" in r.message]
    assert not misleading_logs, (
        f"Found misleading 'legacy_path=true' log — must not appear after P1 gate migration: {misleading_logs}"
    )
