from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _reset():
    from core import session_scope

    session_scope.reset_for_tests()
    yield
    session_scope.reset_for_tests()


def _grant(monkeypatch, *, label="desktop-token", char_id="character_a"):
    from core import session_scope

    monkeypatch.setattr(session_scope, "_owner_id", lambda: "owner")
    monkeypatch.setattr(session_scope, "_validate_character", lambda value: str(value))
    return session_scope.create_session(token_label=label, char_id=char_id)


def test_session_is_bound_to_token_owner_and_character(monkeypatch):
    from core import session_scope

    grant = _grant(monkeypatch)
    assert session_scope.resolve_session(
        grant.session_id, token_label="desktop-token"
    ).memory_scope.character_id == "character_a"

    with pytest.raises(session_scope.SessionScopeError) as denied:
        session_scope.resolve_session(grant.session_id, token_label="mobile-token")
    assert (denied.value.status_code, denied.value.code) == (403, "character_not_authorized")

    monkeypatch.setattr(session_scope, "_owner_id", lambda: "different-owner")
    with pytest.raises(session_scope.SessionScopeError) as revoked:
        session_scope.resolve_session(grant.session_id, token_label="desktop-token")
    assert (revoked.value.status_code, revoked.value.code) == (403, "character_revoked")


@pytest.mark.asyncio
async def test_request_id_replays_once_and_rejects_payload_conflict(monkeypatch):
    from core import session_scope

    grant = _grant(monkeypatch)
    calls = []

    async def execute():
        calls.append(True)
        return {"reply": "ok", "turn_id": "turn-a", "msg_id": "msg-a"}

    _rid, first = await session_scope.execute_request(
        grant=grant, request_id="request-1", payload={"message": "hello"}, executor=execute,
    )
    _rid, replay = await session_scope.execute_request(
        grant=grant, request_id="request-1", payload={"message": "hello"}, executor=execute,
    )
    assert calls == [True]
    assert replay == first
    assert first["request_id"] == "request-1"
    assert first["session_id"] == grant.session_id
    assert first["char_id"] == "character_a"

    with pytest.raises(session_scope.SessionScopeError) as conflict:
        await session_scope.execute_request(
            grant=grant, request_id="request-1", payload={"message": "changed"}, executor=execute,
        )
    assert (conflict.value.status_code, conflict.value.code) == (409, "request_payload_conflict")


@pytest.mark.asyncio
async def test_concurrent_retry_is_in_flight(monkeypatch):
    from core import session_scope

    grant = _grant(monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()

    async def execute():
        started.set()
        await release.wait()
        return {"reply": "done"}

    first = asyncio.create_task(session_scope.execute_request(
        grant=grant, request_id="request-2", payload={"message": "hello"}, executor=execute,
    ))
    await started.wait()
    with pytest.raises(session_scope.SessionScopeError) as inflight:
        await session_scope.execute_request(
            grant=grant, request_id="request-2", payload={"message": "hello"}, executor=execute,
        )
    assert (inflight.value.status_code, inflight.value.code) == (202, "in_flight")
    release.set()
    await first


@pytest.mark.asyncio
async def test_cancelled_wait_does_not_cancel_execution_and_retry_replays(monkeypatch):
    from core import session_scope

    grant = _grant(monkeypatch)
    started = asyncio.Event()
    release = asyncio.Event()

    async def execute():
        started.set()
        await release.wait()
        return {"reply": "late", "turn_id": "turn-late"}

    waiter = asyncio.create_task(session_scope.execute_request(
        grant=grant, request_id="request-timeout", payload={"message": "hello"}, executor=execute,
    ))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    with pytest.raises(session_scope.SessionScopeError) as inflight:
        await session_scope.execute_request(
            grant=grant, request_id="request-timeout", payload={"message": "hello"}, executor=execute,
        )
    assert inflight.value.code == "in_flight"
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    _rid, replay = await session_scope.execute_request(
        grant=grant, request_id="request-timeout", payload={"message": "hello"}, executor=execute,
    )
    assert replay["turn_id"] == "turn-late"


def test_two_clients_keep_distinct_character_grants_when_live_active_changes(monkeypatch):
    from core import session_scope

    monkeypatch.setattr(session_scope, "_owner_id", lambda: "owner")
    monkeypatch.setattr(session_scope, "_validate_character", lambda value: str(value))
    desktop = session_scope.create_session(token_label="desktop", char_id="character_a")
    mobile = session_scope.create_session(token_label="mobile", char_id="character_b")
    # No active-character read participates in resolution; a third live role
    # therefore cannot reassign either already-issued grant.
    assert session_scope.resolve_session(desktop.session_id, token_label="desktop").char_id == "character_a"
    assert session_scope.resolve_session(mobile.session_id, token_label="mobile").char_id == "character_b"


@pytest.mark.asyncio
async def test_owner_chat_passes_frozen_character_to_non_stream_llm(monkeypatch):
    from admin.routers import chat
    from core.memory.scope import MemoryScope

    llm_char_ids = []

    class Pipeline:
        character = SimpleNamespace(name="Active")

        async def fetch_context(self, *_args, **_kwargs):
            return {"_scoped_character": SimpleNamespace(name="Scoped")}

        def build_prompt(self, *_args, **_kwargs):
            return [], {}

        async def run_llm(self, _messages, *, char_id=None, is_proactive=False):
            llm_char_ids.append(char_id)
            return "scoped reply"

    scope = MemoryScope.reality_scope("owner", "character_b")
    monkeypatch.setattr("core.pipeline_registry.get", lambda: Pipeline())
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"scheduler": {"owner_id": "owner"}})
    monkeypatch.setattr("core.scheduler.loop.mark_user_active", lambda: None)
    monkeypatch.setattr("core.scheduler.state_machine.notify_owner_turn", lambda _uid: None)
    monkeypatch.setattr("core.scheduler.proactive_ledger.record_user_message", lambda _uid: None)
    monkeypatch.setattr("core.tool_dispatcher.tool_loop_active", lambda _uid: False)
    monkeypatch.setattr(chat, "_probe_and_execute_tools", lambda *_args, **_kwargs: asyncio.sleep(0, result=None))
    monkeypatch.setattr("channels.ui_push.any_connected", lambda: False)
    monkeypatch.setattr("channels.registry.get", lambda _name: None)
    monkeypatch.setattr("core.coplay.session.is_active", lambda *_args, **_kwargs: False)

    async def sink(**_kwargs):
        return SimpleNamespace(
            turn_id="turn-b", msg_id="msg-b", written_to_memory=True,
            artifacts=[], emotion="neutral",
        )

    monkeypatch.setattr("core.turn_sink.record_assistant_turn", sink)
    result = await chat.run_owner_chat_turn(
        "hello", "mobile", live_origin_channel="mobile", frozen_scope=scope,
        request_id="request-b",
    )
    assert llm_char_ids == ["character_b"]
    assert result["char_id"] == "character_b"
    assert result["request_id"] == "request-b"


@pytest.mark.asyncio
async def test_stream_start_carries_frozen_scope_and_request_id(monkeypatch):
    from channels import desktop_ws, device_ws

    desktop_frames = []
    device_frames = []
    monkeypatch.setattr(desktop_ws, "_send_json", lambda payload: asyncio.sleep(0, result=desktop_frames.append(payload) or True))
    monkeypatch.setattr(device_ws, "enqueue_json", lambda payload: device_frames.append(payload) or True)

    await desktop_ws.push_stream_start(
        "msg-1", char_id="character_a", domain="reality", request_id="request-1",
    )
    await device_ws.push_stream_start(
        "msg-1", char_id="character_a", domain="reality", request_id="request-1",
    )
    for frame in (desktop_frames[0], device_frames[0]):
        assert frame["msg_id"] == "msg-1"
        assert frame["char_id"] == "character_a"
        assert frame["domain"] == "reality"
        assert frame["request_id"] == "request-1"


def test_observability_is_metadata_only(monkeypatch):
    from core import session_scope

    _grant(monkeypatch)
    snapshot = session_scope.observability_snapshot()
    assert snapshot["capability"] == "v1"
    assert snapshot["effective"] is True
    assert snapshot["active_sessions"] == 1
    assert "message" not in repr(snapshot).lower()
