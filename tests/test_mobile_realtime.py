import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core import mobile_realtime as realtime, session_scope
from core.memory import event_store
from core.memory.scope import MemoryScope
from admin.routers.mobile import router


@pytest.fixture(autouse=True)
def isolated(monkeypatch, sandbox):
    realtime._CONNECTIONS.clear()
    realtime._CLIENTS.clear()
    realtime._COUNTS.clear()
    realtime._METRICS.clear()
    monkeypatch.setattr(realtime, "enabled", lambda: True)
    monkeypatch.setattr(session_scope, "_owner_id", lambda: "owner")
    monkeypatch.setattr(session_scope, "_validate_character", lambda value: str(value))
    session_scope._SESSIONS.clear()
    yield
    realtime._CONNECTIONS.clear()
    realtime._CLIENTS.clear()


def final(turn="turn-a", char="character_a", timestamp=100):
    scope = MemoryScope.reality_scope("owner", char)
    assert event_store.append_event(scope, {
        "event_id": turn + ":assistant", "turn_id": turn, "actor": "assistant",
        "kind": "assistant_message", "channel": "mobile", "visible_text": "Hello\nHello",
        "occurred_at": timestamp,
    }).ok
    return realtime.remember_final(uid="owner", char_id=char, turn_id=turn,
        content="Hello\nHello", display_text="<hl>Hello</hl>\nHello", request_id="req-one",
        artifacts=[{"id": "artifact-one", "filename": "one.txt"}])


def test_history_survives_queue_cleanup_restart_and_cross_day_pages(monkeypatch):
    for i in range(4):
        assert final(f"turn-{i}", timestamp=100 + i * 86400)
    scope = MemoryScope.reality_scope("owner", "character_a")
    page = realtime.history(scope, limit=2)
    assert [m["id"] for m in page["messages"]] == ["turn-3", "turn-2"]
    assert all("seq" not in m for m in page["messages"])
    assert page["messages"][0]["artifacts"][0]["filename"] == "one.txt"
    assert page["messages"][0]["request_id"] == "req-one"
    assert page["messages"][0]["display_text"] == "<hl>Hello</hl>\nHello"
    realtime._COUNTS.clear()  # No receipt/global queue needed to recover identities.
    page2 = realtime.history(scope, before=page["next_before"], limit=2)
    assert [m["turn_id"] for m in page2["messages"]] == ["turn-1", "turn-0"]
    assert page2["next_before"] is None
    assert not realtime.history(MemoryScope.reality_scope("owner", "character_b"))["messages"]


async def test_actual_queue_ack_does_not_delete_history(monkeypatch):
    from channels.mobile import MobileChannel
    monkeypatch.setattr("core.client_channels.enabled", lambda name: True)
    message = final()
    channel = MobileChannel()
    await channel.send(message["content"], "owner", msg_id=message["msg_id"], char_id=message["char_id"])
    rows = await channel.poll()
    await channel.ack(rows[-1]["seq"])
    assert await channel.poll() == []
    recovered = realtime.history(MemoryScope.reality_scope("owner", "character_a"))["messages"]
    assert recovered[0]["msg_id"] == message["msg_id"]


def test_projection_is_idempotent_and_respects_tombstone():
    envelope = final()
    assert final() == envelope
    scope = MemoryScope.reality_scope("owner", "character_a")
    assert len(realtime.history(scope)["messages"]) == 1
    assert event_store.tombstone_event(scope, "turn-a:assistant").ok
    assert realtime.history(scope)["messages"] == []
    assert final() is None


def test_forgetting_newest_final_does_not_reuse_history_position():
    final("old")
    scope = MemoryScope.reality_scope("owner", "character_a")
    previous = realtime.history(scope)["messages"][0]["history_index"]
    assert event_store.tombstone_event(scope, "old:assistant").ok
    final("new")
    assert realtime.history(scope)["messages"][0]["history_index"] > previous


@pytest.mark.parametrize("body", ["<tool_call>secret</tool_call>", "ok<think>hidden", "<|system|>secret", "<analysis unfinished"])
def test_no_raw_internal_content_is_ever_published(body):
    assert realtime.remember_final(uid="owner", char_id="character_a", turn_id="t",
        content=body, display_text=body) is None
    assert not realtime._CONNECTIONS


def test_missing_or_failed_persistence_never_becomes_success(monkeypatch):
    assert realtime.remember_final(uid="owner", char_id="character_a", turn_id="missing",
        content="partial", display_text="partial") is None
    final()
    monkeypatch.setattr(realtime.sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    assert realtime.remember_final(uid="owner", char_id="character_a", turn_id="turn-a",
        content="partial", display_text="partial") is None


def ws_client(monkeypatch):
    valid = {"yes": True}
    monkeypatch.setattr("admin.auth.authenticate_ws", lambda ws, scope:
        SimpleNamespace(label="phone") if valid.get(ws.headers.get("authorization")) else None)
    grant = session_scope.create_session(token_label="phone", char_id="character_a")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), grant, valid


def headers(grant, client="client-a", token="yes"):
    return {"authorization": token, "x-presence-session": grant.session_id, "x-presence-client": client}


@pytest.mark.parametrize("changed", ["token", "owner", "character", "switch", "disabled"])
def test_revocation_at_send_rejects_queued_frames(monkeypatch, changed):
    client, grant, valid = ws_client(monkeypatch)
    with client.websocket_connect("/ws/mobile", headers=headers(grant)) as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["mode"] == "final_only"
        sub = next(iter(realtime._CONNECTIONS.values()))
        if changed == "token": valid["yes"] = False
        elif changed == "owner": monkeypatch.setattr(session_scope, "_owner_id", lambda: "other")
        elif changed == "character":
            def revoked(value): raise session_scope.SessionScopeError(403, "character_revoked")
            monkeypatch.setattr(session_scope, "_validate_character", revoked)
        elif changed == "switch": realtime._CLIENTS[("phone", "client-a")] = "new-generation"
        else: monkeypatch.setattr(realtime, "enabled", lambda: False)
        assert not sub.authorized()
        ws.send_json({"type": "pong", "generation": ready["generation"]})
        with pytest.raises(WebSocketDisconnect): ws.receive_json()


def test_parallel_phone_connections_and_rebind_do_not_touch_desktop(monkeypatch):
    client, grant, _ = ws_client(monkeypatch)
    with client:
        with client.websocket_connect("/ws/mobile", headers=headers(grant)) as first:
            a = first.receive_json()
            old = next(iter(realtime._CONNECTIONS.values()))
            with client.websocket_connect("/ws/mobile", headers=headers(grant, "client-b")) as second:
                b = second.receive_json()
                assert a["generation"] != b["generation"] and old.authorized()
                with client.websocket_connect("/ws/mobile", headers=headers(grant)) as rebound:
                    c = rebound.receive_json()
                    assert not old.authorized() and c["generation"] != a["generation"]
                    rebound.close()
                second.close()
            first.close()


def test_live_final_is_same_as_authorized_history(monkeypatch):
    message = final()
    client, grant, _ = ws_client(monkeypatch)
    with client:
        with client.websocket_connect("/ws/mobile", headers=headers(grant)) as ws:
            ready = ws.receive_json()
            client.portal.call(realtime.publish, message)
            frame = ws.receive_json()
            assert frame["type"] == "final" and frame["message"] == message
            assert frame["generation"] == ready["generation"]
            ws.close()


@pytest.mark.parametrize("kind", ["ack", "execute", "pong"])
def test_no_client_ack_execution_or_stale_generation_protocol(monkeypatch, kind):
    client, grant, _ = ws_client(monkeypatch)
    with client.websocket_connect("/ws/mobile", headers=headers(grant)) as ws:
        ws.receive_json()
        ws.send_json({"type": kind, "generation": "old"})
        with pytest.raises(WebSocketDisconnect): ws.receive_json()


def test_handshake_requires_credentials_session_and_client(monkeypatch):
    client, grant, _ = ws_client(monkeypatch)
    for candidate in ({}, headers(grant, token="no"), {"authorization": "yes"},
                      {**headers(grant), "x-presence-session": "wrong"}):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/mobile", headers=candidate): pass


async def test_publish_is_scoped_and_backpressure_is_bounded(monkeypatch):
    grant = session_scope.create_session(token_label="phone", char_id="character_a")
    sub = realtime.Subscription(None, grant, "phone")
    monkeypatch.setattr(sub, "authorized", lambda: True)
    realtime._CONNECTIONS[sub.generation] = sub
    realtime.publish({"user_id": "other", "char_id": grant.char_id, "domain": "reality"})
    assert sub.pending.empty()
    realtime.publish({"user_id": "owner", "char_id": grant.char_id, "domain": "dream"})
    assert sub.pending.empty()
    for _ in range(65):
        realtime.publish({"user_id": "owner", "char_id": grant.char_id, "domain": "reality"})
    assert sub.pending.qsize() == 1 and await sub.pending.get() is None
    assert "content" not in json.dumps(realtime.snapshot())


@pytest.mark.parametrize("written,channel,fanout,expected", [
    (True, "mobile", "all", 1), (True, "desktop", "all", 1),
    (False, "mobile", "all", 0), (True, "activity", "all", 0),
    (True, "mobile", [], 0),
])
async def test_existing_turn_sink_projects_once_without_extra_execution(monkeypatch, written, channel, fanout, expected):
    from unittest.mock import AsyncMock
    from core.turn_sink import record_assistant_turn, TurnSource
    scope = MemoryScope.reality_scope("owner", "character_a")
    final()
    critical = AsyncMock(return_value={"turn_id": "turn-a", "critical_written": written})
    monkeypatch.setattr("channels.registry._channels", {})
    published = []
    monkeypatch.setattr(realtime, "publish", published.append)
    await record_assistant_turn(assistant_text="Hello\nHello", uid="owner", source=TurnSource.USER_CHAT,
        user_text="question", fanout=fanout, frozen_scope=scope,
        pipeline=SimpleNamespace(post_process_critical=critical), schedule_slow=False,
        event_channel=channel, request_id="req-one")
    assert critical.await_count == 1
    assert len(published) == expected
    if published:
        assert published[0]["msg_id"] == published[0]["turn_id"] == "turn-a"


async def test_hot_transport_disable_preserves_http_and_config(monkeypatch):
    from admin.routers import settings_clients as settings
    cfg = {"client_channels": {"mobile": True}, "unchanged": {"value": True}}
    for name in ("get_config",):
        monkeypatch.setattr(settings, name, lambda: cfg)
    monkeypatch.setattr(settings, "read_config_file", lambda path: cfg)
    monkeypatch.setattr(settings, "write_config_file", lambda path, data: None)
    monkeypatch.setattr(settings, "reload_config", lambda: None)
    monkeypatch.setattr("core.config_loader.get_config", lambda: cfg)
    monkeypatch.setattr("core.client_channels.get_config", lambda: cfg)
    result = await settings.put_settings(settings.ClientSettings(mobile_realtime=False), None)
    assert cfg["client_channels"]["mobile"] is True
    assert cfg["unchanged"] == {"value": True}
    assert result["mobile_realtime"]["enabled"] is False


def test_metrics_reject_content_and_are_bounded():
    from admin.routers.mobile import MobileRealtimeMetric
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        MobileRealtimeMetric(kind="connect_ms", value=1, content="private")
    with pytest.raises(ValidationError):
        MobileRealtimeMetric(kind="request_id", value=1)
    for i in range(250):
        realtime.record_metric("connect_ms", i)
    assert realtime.snapshot()["client_metrics"]["connect_ms"]["samples"] == 200


async def test_day_history_retains_display_and_request_after_receipts_expire(monkeypatch):
    from admin.routers import chat_log
    from core.memory import event_log
    message = final()
    scope = MemoryScope.reality_scope("owner", "character_a")
    assert realtime.finals_for_turns(scope, ["turn-a"])["turn-a"] == message
    monkeypatch.setattr(chat_log, "_owner_qq", lambda: "owner")
    monkeypatch.setattr(chat_log, "_resolve_char_id", lambda value: "character_a")
    monkeypatch.setattr(chat_log, "_parse_day", lambda text: [
        {"time": "12:00", "user": "question", "assistant": "legacy", "turn_id": "turn-a"}])
    event_log.append("owner", "assistant", "legacy", turn_id="turn-a", char_id="character_a")
    dates = event_log.list_days("owner", char_id="character_a")
    session_scope._REQUESTS.clear()
    entries = (await chat_log.get_day(dates[0], x_presence_session=None))["entries"]
    assert entries[0]["assistant_display_text"] == message["display_text"]
    assert entries[0]["request_id"] == "req-one"
