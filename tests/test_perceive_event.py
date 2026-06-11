"""
tests/test_perceive_event.py — core/perceive_event.py + desktop_wake Path B integration

Covers:
 1. same event_id twice → only first ACCEPTED, second DUPLICATE
 2. same dedupe_key concurrent → only one ACCEPTED (asyncio.gather race)
 3. different event_id → each independently ACCEPTED (not killed)
 4. DUPLICATE event must not fanout (turn handler not called)
 5. DUPLICATE event must not trigger post_process (turn handler not called)
 6. Dream Guard BLOCK_ACTIVE → BLOCKED_DREAM, turn handler not called
 7. char_id missing → resolved from active_prompt_assets, not a hardcoded fallback
 8. trigger + wake same event_id → only one ACCEPTED (cross-source dedup)
 9. HTTP return and WS fanout from record_assistant_turn do NOT each run LLM
10. Ordinary owner chat (run_owner_chat_turn) is unaffected by perceive_event
"""

import asyncio
import time
import pytest


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_dedup(monkeypatch):
    """Reset perceive_event module state before each test."""
    from core.perceive_event import clear_dedup_registry_for_test
    clear_dedup_registry_for_test()
    yield
    clear_dedup_registry_for_test()


def _allow_dream_guard(monkeypatch):
    """Monkeypatch dream guard so it always returns ALLOW."""
    from core.dream import dream_state as _ds

    class _FakeStatus:
        ALLOW = "allow"

    monkeypatch.setattr(_ds, "get_reality_guard_status", lambda uid: _FakeStatus.ALLOW)
    monkeypatch.setattr(_ds, "DreamGuardStatus", _FakeStatus)


def _block_dream_guard(monkeypatch, status_value="BLOCK_ACTIVE"):
    """Monkeypatch dream guard so it always returns a blocking status."""
    from core.dream import dream_state as _ds

    class _FakeStatus:
        ALLOW = "allow"
        BLOCK_ACTIVE = "block_active"
        BLOCK_UNCERTAIN = "block_uncertain"

    def _blocked(uid):
        return getattr(_FakeStatus, status_value)

    monkeypatch.setattr(_ds, "get_reality_guard_status", _blocked)
    monkeypatch.setattr(_ds, "DreamGuardStatus", _FakeStatus)


# ── Test 1: same event_id twice → first ACCEPTED, second DUPLICATE ────────────

async def test_same_event_id_second_is_duplicate(monkeypatch):
    _allow_dream_guard(monkeypatch)
    from core.perceive_event import PerceiveEvent, receive_perceive_event, PerceiveStatus

    event = PerceiveEvent(
        source="desktop_wake", uid="u1", channel="desktop", kind="wake",
        event_id="evt-abc-123",
    )

    r1 = await receive_perceive_event(event)
    r2 = await receive_perceive_event(event)

    assert r1.status == PerceiveStatus.ACCEPTED, f"first call should be ACCEPTED, got {r1.status}"
    assert r2.status == PerceiveStatus.DUPLICATE, f"second call should be DUPLICATE, got {r2.status}"
    assert r2.existing_turn_id == r1.event_id


# ── Test 2: same dedupe_key concurrent → only one ACCEPTED ───────────────────

async def test_concurrent_same_key_only_one_accepted(monkeypatch):
    _allow_dream_guard(monkeypatch)
    from core.perceive_event import PerceiveEvent, receive_perceive_event, PerceiveStatus

    # Both events share the same auto-generated dedupe_key (same bucket, same payload)
    ts = time.time()
    e1 = PerceiveEvent(source="desktop_wake", uid="u1", channel="desktop", kind="wake", created_at=ts)
    e2 = PerceiveEvent(source="desktop_wake", uid="u1", channel="desktop", kind="wake", created_at=ts)

    results = await asyncio.gather(
        receive_perceive_event(e1),
        receive_perceive_event(e2),
    )

    statuses = [r.status for r in results]
    assert statuses.count(PerceiveStatus.ACCEPTED) == 1, f"exactly one ACCEPTED expected: {statuses}"
    assert statuses.count(PerceiveStatus.DUPLICATE) == 1, f"exactly one DUPLICATE expected: {statuses}"


# ── Test 3: different event_id → each ACCEPTED independently ─────────────────

async def test_different_event_ids_both_accepted(monkeypatch):
    _allow_dream_guard(monkeypatch)
    from core.perceive_event import PerceiveEvent, receive_perceive_event, PerceiveStatus

    e1 = PerceiveEvent(
        source="desktop_wake", uid="u1", channel="desktop", kind="wake",
        event_id="evt-111",
    )
    e2 = PerceiveEvent(
        source="desktop_wake", uid="u1", channel="desktop", kind="wake",
        event_id="evt-222",
    )

    r1 = await receive_perceive_event(e1)
    r2 = await receive_perceive_event(e2)

    assert r1.status == PerceiveStatus.ACCEPTED, f"first different event should be ACCEPTED: {r1}"
    assert r2.status == PerceiveStatus.ACCEPTED, f"second different event should be ACCEPTED: {r2}"


# ── Test 4: DUPLICATE does not fanout ─────────────────────────────────────────

async def test_duplicate_does_not_fanout(monkeypatch):
    """Caller must check status before calling fanout — DUPLICATE status signals no fanout."""
    _allow_dream_guard(monkeypatch)
    from core.perceive_event import PerceiveEvent, receive_perceive_event, PerceiveStatus

    fanout_calls: list[str] = []

    async def fake_turn_handler(event_id: str):
        # Simulates what a caller would do: only fanout on ACCEPTED
        event = PerceiveEvent(
            source="desktop_wake", uid="u2", channel="desktop", kind="wake",
            event_id=event_id,
        )
        result = await receive_perceive_event(event)
        if result.status == PerceiveStatus.ACCEPTED:
            fanout_calls.append(event_id)

    await fake_turn_handler("same-id")
    await fake_turn_handler("same-id")  # duplicate

    assert len(fanout_calls) == 1, f"fanout should fire exactly once, got {fanout_calls}"


# ── Test 5: DUPLICATE does not trigger post_process ──────────────────────────

async def test_duplicate_does_not_post_process(monkeypatch):
    """Caller must check status before post_process — DUPLICATE must not write memory."""
    _allow_dream_guard(monkeypatch)
    from core.perceive_event import PerceiveEvent, receive_perceive_event, PerceiveStatus

    pp_calls: list[str] = []

    async def fake_pipeline_turn(event_id: str):
        event = PerceiveEvent(
            source="scheduler", uid="u3", channel="system", kind="scheduled",
            event_id=event_id,
        )
        result = await receive_perceive_event(event)
        if result.status == PerceiveStatus.ACCEPTED:
            pp_calls.append(f"post_process:{event_id}")

    await fake_pipeline_turn("sched-111")
    await fake_pipeline_turn("sched-111")  # duplicate

    assert len(pp_calls) == 1, f"post_process should fire once, got {pp_calls}"


# ── Test 6: Dream Guard BLOCK → BLOCKED_DREAM, turn handler not called ────────

async def test_dream_guard_blocked_prevents_turn(monkeypatch):
    _block_dream_guard(monkeypatch, "BLOCK_ACTIVE")
    from core.perceive_event import PerceiveEvent, receive_perceive_event, PerceiveStatus

    turn_called: list[str] = []

    event = PerceiveEvent(
        source="desktop_wake", uid="u4", channel="desktop", kind="wake",
        event_id="evt-dream",
    )
    result = await receive_perceive_event(event)

    # Caller would only invoke turn handler on ACCEPTED
    if result.status == PerceiveStatus.ACCEPTED:
        turn_called.append("llm")

    assert result.status == PerceiveStatus.BLOCKED_DREAM, f"expected BLOCKED_DREAM: {result}"
    assert not turn_called, "turn handler must not be called when dream guard blocks"


# ── Test 7: char_id missing → resolved from active_prompt_assets ─────────────

async def test_char_id_resolved_from_active_prompt_assets(monkeypatch, sandbox):
    """No char_id on event → resolve from active_prompt_assets.json, no hardcoded fallback."""
    _allow_dream_guard(monkeypatch)

    # Write a fake active_prompt_assets.json
    apa_path = sandbox.active_prompt_assets()
    apa_path.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    apa_path.write_text(
        _json.dumps({"active_character": "char-testid"}), encoding="utf-8"
    )

    from core.perceive_event import PerceiveEvent, receive_perceive_event, PerceiveStatus, _resolve_char_id

    # _resolve_char_id is a public helper — test it directly
    resolved = _resolve_char_id("u5", None)
    assert resolved == "char-testid", f"expected 'char-testid', got {resolved!r}"

    # Also verify via full receive_perceive_event
    event = PerceiveEvent(
        source="desktop_wake", uid="u5", channel="desktop", kind="wake",
        event_id="evt-chartest",
        char_id=None,  # explicitly absent
    )
    result = await receive_perceive_event(event)
    assert result.status == PerceiveStatus.ACCEPTED
    # The resolved char_id is embedded in the dedupe_key when there is no event_id
    # (here event_id IS supplied, so key is "eid:evt-chartest" — but resolution must not crash)


async def test_char_id_missing_no_hardcoded_fallback(monkeypatch, sandbox):
    """
    When active_prompt_assets.json can't be read (OSError), char_id is None —
    never falls back to a hardcoded character name like 'AI' or 'yexuan'.
    """
    _allow_dream_guard(monkeypatch)

    # Force active_prompt_assets() to raise OSError (simulates unreadable file)
    import core.data_paths as _dp

    class _BrokenPath:
        def read_text(self, encoding="utf-8"):
            raise OSError("simulated I/O failure")

    original = _dp.DataPaths.active_prompt_assets
    monkeypatch.setattr(_dp.DataPaths, "active_prompt_assets", lambda self: _BrokenPath())

    from core.perceive_event import _resolve_char_id
    result = _resolve_char_id("u6", None)

    assert result is None, (
        f"must return None when active_prompt_assets is unreadable, got {result!r}"
    )


# ── Test 8: trigger + wake same event_id → cross-source dedup ────────────────

async def test_cross_source_same_event_id_deduped(monkeypatch):
    """
    If a scheduler trigger and a desktop_wake carry the same event_id,
    the second one must be DUPLICATE regardless of source difference.
    """
    _allow_dream_guard(monkeypatch)
    from core.perceive_event import PerceiveEvent, receive_perceive_event, PerceiveStatus

    shared_event_id = "shared-wakeup-001"

    e_trigger = PerceiveEvent(
        source="scheduler", uid="u7", channel="system", kind="trigger",
        event_id=shared_event_id,
    )
    e_wake = PerceiveEvent(
        source="desktop_wake", uid="u7", channel="desktop", kind="wake",
        event_id=shared_event_id,
    )

    r_trigger = await receive_perceive_event(e_trigger)
    r_wake = await receive_perceive_event(e_wake)

    assert r_trigger.status == PerceiveStatus.ACCEPTED
    assert r_wake.status == PerceiveStatus.DUPLICATE, (
        f"wake with same event_id as trigger must be DUPLICATE, got {r_wake.status}"
    )


# ── Test 9: HTTP reply and WS fanout do not each trigger a new LLM ────────────

async def test_fanout_does_not_trigger_additional_llm(monkeypatch):
    """
    record_assistant_turn fanout only delivers an already-generated reply.
    This test verifies that calling record_assistant_turn with fanout='all'
    does NOT call pipeline.run_llm again.
    """
    llm_call_count: list[int] = [0]

    class _FakePipeline:
        async def post_process(self, uid, content, reply, **kwargs):
            return {"turn_id": "t-1", "critical_written": True, "emotion": "neutral"}

        async def run_llm(self, messages):
            llm_call_count[0] += 1
            return "只有一次LLM"

    class _FakeDesktopChannel:
        name = "desktop"
        is_active = True
        sent: list = []

        async def send(self, content, uid, behavior=None, msg_id=None):
            self.sent.append(content)

    from channels import registry as _reg
    _reg._channels = {}
    _reg.register(_FakeDesktopChannel())

    monkeypatch.setattr("channels.desktop_ws.is_connected", lambda: False)

    from core.turn_sink import TurnSource, record_assistant_turn

    # Simulate: LLM called once outside, then record_assistant_turn with fanout="all"
    reply = await _FakePipeline().run_llm([])
    await record_assistant_turn(
        assistant_text=reply,
        uid="u8",
        source=TurnSource.TRIGGER,
        trigger_name="test_trigger",
        fanout="all",
        pipeline=_FakePipeline(),
    )

    assert llm_call_count[0] == 1, (
        f"LLM must be called exactly once; fanout must not trigger a second call, got {llm_call_count[0]}"
    )

    # cleanup
    _reg._channels = {}


# ── Test 10: ordinary run_owner_chat_turn is unaffected by perceive_event ─────

async def test_ordinary_owner_chat_unaffected(monkeypatch):
    """
    run_owner_chat_turn does NOT go through receive_perceive_event.
    It must continue working normally after perceive_event is introduced.
    """
    record_called: list[str] = []

    async def fake_record_assistant_turn(**kwargs):
        record_called.append(kwargs.get("user_text", ""))
        from core.turn_sink import TurnResult
        return TurnResult(
            turn_id="t-chat", written_to_memory=True, fanout_targets=["desktop"]
        )

    import core.turn_sink as _sink
    monkeypatch.setattr(_sink, "record_assistant_turn", fake_record_assistant_turn)

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "owner1"}, "memory": {"short_term_rounds": 20}},
    )

    class _FakePipeline:
        character = type("C", (), {"name": "Companion"})()

        async def fetch_context(self, uid, message, *a, **kw):
            return {}

        def build_prompt(self, uid, message, context, **kw):
            return [{"role": "user", "content": message}], {}

        async def run_llm(self, messages):
            return "好呀"

        def _current_reality_scope(self, uid):
            return type("Scope", (), {"character_id": "yexuan"})()

        def _refresh_character_if_needed(self):
            pass

        _active_character_id = "yexuan"

    import core.pipeline_registry as _preg
    monkeypatch.setattr(_preg, "_pipeline", _FakePipeline())

    # Stub tool probe so it returns None (no tool call)
    import admin.routers.chat as _chat

    async def _no_tool(msg, uid):
        return None

    monkeypatch.setattr(_chat, "_probe_and_execute_tools", _no_tool)

    # Stub channel lookup
    from channels import registry as _reg
    _reg._channels = {}

    # Stub user profile
    monkeypatch.setattr("core.memory.user_profile.get_affection_level", lambda uid: {"value": 5, "label": "好感"})

    # Stub write_envelope
    import core.write_envelope as _we
    monkeypatch.setattr(_we, "stamp_user_chat", lambda: _we.WriteEnvelope())

    result = await _chat.run_owner_chat_turn("你好", "desktop")

    assert isinstance(result, dict), "run_owner_chat_turn must return a dict"
    assert "reply" in result
    assert result["turn_id"] == result["msg_id"] == "t-chat"
    assert len(record_called) == 1, f"record_assistant_turn should be called once, got {record_called}"


# ── Test 11 (bonus): desktop_wake Path B uses perceive_event gate ─────────────

async def test_desktop_wake_path_b_uses_perceive_gate(monkeypatch):
    """
    When /desktop/wake is called twice rapidly with the same implicit dedupe_key,
    only the first call runs the LLM; the second is short-circuited.
    """
    from core.perceive_event import clear_dedup_registry_for_test
    clear_dedup_registry_for_test()

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "owner99"}},
    )

    llm_calls: list[int] = [0]

    class _FakePipeline:
        character = type("C", (), {"name": "Companion"})()

        async def fetch_context(self, uid, prompt, *a, **kw):
            return {}

        def build_prompt(self, uid, prompt, context, **kw):
            return [], {}

        async def run_llm(self, messages):
            llm_calls[0] += 1
            return "早上好"

        async def post_process(self, uid, content, reply, **kwargs):
            return {"turn_id": "t-x", "critical_written": True, "emotion": "neutral"}

        def _current_reality_scope(self, uid):
            return type("Scope", (), {"character_id": "yexuan"})()

    import core.pipeline_registry as _preg
    monkeypatch.setattr(_preg, "_pipeline", _FakePipeline())

    # No active_prompt_assets → char_id resolves to None (no crash expected)
    monkeypatch.setattr(
        "core.perceive_event._resolve_char_id",
        lambda uid, char_id: "yexuan",
    )

    # Allow dream guard
    from core.dream import dream_state as _ds

    class _FakeDGS:
        ALLOW = "allow"

    monkeypatch.setattr(_ds, "get_reality_guard_status", lambda uid: _FakeDGS.ALLOW)
    monkeypatch.setattr(_ds, "DreamGuardStatus", _FakeDGS)

    # Stub short_term.load so Path A finds nothing pending
    monkeypatch.setattr("core.memory.short_term.load", lambda uid, char_id=None: [])

    # Stub active_prompt_assets for Path A char resolution
    import json as _json

    class _FakeAPA:
        def read_text(self, encoding="utf-8"):
            return _json.dumps({"active_character": "yexuan"})

    monkeypatch.setattr("core.sandbox.DataPaths.active_prompt_assets", lambda self: _FakeAPA())

    # Stub record_assistant_turn to avoid real file I/O
    async def fake_record(**kwargs):
        from core.turn_sink import TurnResult
        return TurnResult(turn_id="t-wake", written_to_memory=True, fanout_targets=[])

    import core.turn_sink as _ts
    monkeypatch.setattr(_ts, "record_assistant_turn", fake_record)

    # Stub response_processor
    monkeypatch.setattr("core.response_processor.strip_render_tags", lambda s: s)

    # Stub reality_output_guard
    monkeypatch.setattr(
        "core.reality_output_guard.clean_reality_reply_text",
        lambda text, name: text,
    )

    # Stub desktop_ws.get_connect_time so Path A (last_seen check) is skipped
    monkeypatch.setattr("channels.desktop_ws.get_connect_time", lambda: 0.0)
    monkeypatch.setattr("channels.desktop_ws.is_connected", lambda: False)

    from admin.routers.chat import desktop_wake

    # First call: no last_seen → Path A skipped → Path B should run
    r1 = await desktop_wake({})
    # Second call with same implicit key (same minute bucket, same payload)
    r2 = await desktop_wake({})

    assert llm_calls[0] == 1, (
        f"LLM must be called exactly once across two rapid wake calls, got {llm_calls[0]}"
    )
    assert r1.get("source") == "live_wake", f"first wake should succeed: {r1}"
    assert r1.get("turn_id") == r1.get("msg_id") == "t-wake"
    assert r2.get("source") == "duplicate_wake", f"second wake should be deduped: {r2}"
