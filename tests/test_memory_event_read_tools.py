from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock

import pytest

from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID


def _json(value):
    return json.loads(value.safe_summary if hasattr(value, "safe_summary") else value)


class _Session:
    WAITING_CONFIRM = "waiting_confirm"
    status = "idle"

    def set_waiting_confirm(self, *args):
        raise AssertionError("read-only event tools must not request confirmation")


def _seed(uid: str, sandbox, *, char_id: str = TEST_CHAR_ID) -> None:
    from core.memory import event_store
    from core.memory.scope import MemoryScope
    from core.memory.path_resolver import resolve_path

    scope = MemoryScope.reality_scope(uid, char_id)
    for seq, event_id in enumerate(("tool-event-1", "tool-event-2", "tool-event-3")):
        result = event_store.append_event(scope, {
            "event_id": event_id, "turn_id": "tool-turn", "seq": seq,
            "occurred_at": 1_700_000_000 + seq, "ingested_at": 1_700_000_100 + seq,
            "realm": "reality", "kind": "owner_chat",
            "actor": "user" if seq != 1 else "assistant", "channel": "desktop",
            "source": "fixture", "raw_text": f"raw {seq}",
            "visible_text": f"visible {seq}", "memory_text": f"evidence {seq}",
        })
        assert result.inserted
    with sqlite3.connect(resolve_path(scope, "event_store")) as connection:
        connection.execute(
            "INSERT INTO event_topics (uid, char_id, event_id, topic, score, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, char_id, "tool-event-2", "topic.fixture", 1.0, 1_700_000_000),
        )
        connection.execute(
            "INSERT INTO event_edges (uid, char_id, from_event_id, to_event_id, edge_type, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, char_id, "tool-event-2", "tool-event-1", "same_turn", 1_700_000_200),
        )


@pytest.mark.asyncio
async def test_event_read_tools_return_bounded_evidence_and_relations(sandbox):
    from core.tools.event_tools import expand_event_window_wrapper, get_related_events_wrapper, search_events_wrapper

    uid = "event-read-tools"
    _seed(uid, sandbox)
    expanded = _json(await expand_event_window_wrapper(uid, "tool-event-2", before=1, after=1, char_id=TEST_CHAR_ID))
    assert expanded["status"] == "ok"
    assert expanded["event"]["event_id"] == "tool-event-2"
    assert expanded["event"]["topic"] == ["topic.fixture"]
    assert expanded["event"]["turn_id"] == "tool-turn"
    assert expanded["before"][0]["relation"]["direction"] == "before"
    assert "raw_payload_json" not in json.dumps(expanded)

    related = _json(await get_related_events_wrapper(
        uid, "tool-event-2", relation_types=["same_turn"], limit=5, char_id=TEST_CHAR_ID,
    ))
    assert related["items"][0]["event_id"] == "tool-event-1"
    assert related["items"][0]["relation"]["type"] == "same_turn"

    searched = _json(await search_events_wrapper(uid, query="evidence", limit=5, char_id=TEST_CHAR_ID))
    assert [item["event_id"] for item in searched["items"]] == ["tool-event-1", "tool-event-2", "tool-event-3"]


@pytest.mark.asyncio
async def test_event_read_tools_are_reality_scoped_and_unknown_is_structured(sandbox):
    from core.tools.event_tools import expand_event_window_wrapper

    uid = "event-read-tool-scope"
    _seed(uid, sandbox)
    wrong_character = _json(await expand_event_window_wrapper(uid, "tool-event-1", char_id=TEST_PEER_CHAR_ID))
    assert wrong_character["status"] == "outcome_unknown"
    assert wrong_character["reason"] == "event_not_found"
    missing = _json(await expand_event_window_wrapper(uid, "not-present", char_id=TEST_CHAR_ID))
    assert missing == {"status": "outcome_unknown", "reason": "event_not_found", "event_id": "not-present"}
    too_many = _json(await expand_event_window_wrapper(uid, "tool-event-1", before=21, char_id=TEST_CHAR_ID))
    assert too_many["reason"] == "limit_exceeded"


def test_event_tools_are_registered_in_memory_schema(monkeypatch):
    import core.tool_dispatcher as dispatcher

    monkeypatch.setattr('core.tool_runtime.policy._is_tool_enabled', lambda _name: True)
    monkeypatch.setattr("core.deployment_capabilities.tool_allowed", lambda _name: (True, ""))
    monkeypatch.setattr("core.self_management.policy.tool_allowed", lambda *_args: True)
    names = {item["function"]["name"] for item in dispatcher.get_tools_schema(categories=["memory"], char_id=TEST_CHAR_ID, uid="owner")}
    assert {"search_events", "expand_event_window", "get_related_events"} <= names


@pytest.mark.asyncio
async def test_dispatcher_preserves_origin_gate_and_unknown_status(monkeypatch, sandbox):
    import core.tool_dispatcher as dispatcher
    from core.memory import action_trace

    records = []
    monkeypatch.setattr(action_trace, "record", lambda *args, **kwargs: records.append(kwargs))
    monkeypatch.setattr('core.tool_runtime.policy._is_tool_enabled', lambda _name: True)
    monkeypatch.setattr("core.self_management.policy.tool_allowed", lambda *_args: True)
    denied = await dispatcher.execute_structured(
        "expand_event_window", {"event_id": "not-present"}, "owner", "owner", False, _Session(),
        origin="not-an-origin", char_id=TEST_CHAR_ID,
    )
    assert denied.status == "tool_failed"
    assert not records

    group_denied = await dispatcher.execute_structured(
        "expand_event_window", {"event_id": "not-present"}, "owner", "owner", True, _Session(),
        origin="assistant_loop", char_id=TEST_CHAR_ID,
    )
    assert group_denied.status == "tool_failed"

    unknown = await dispatcher.execute_structured(
        "expand_event_window", {"event_id": "not-present"}, "owner", "owner", False, _Session(),
        origin="assistant_loop", char_id=TEST_CHAR_ID,
    )
    assert unknown.status == "outcome_unknown"
    assert json.loads(unknown.result)["status"] == "outcome_unknown"
    assert records[-1]["status"] == "outcome_unknown"
    assert records[-1]["scope"] == {"uid": "owner", "char_id": TEST_CHAR_ID, "realm": "reality"}
    assert records[-1]["failure_reason"] == "event_not_found"
    assert records[-1]["duration_ms"] >= 0
    assert records[-1]["truncated"] is False
    assert "not-present" not in records[-1]["result_digest"]
