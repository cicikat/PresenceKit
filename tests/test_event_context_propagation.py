from __future__ import annotations

import sqlite3

import pytest

from core.event_context import EventContext
from core.memory import event_store
from core.memory.fixation_pipeline import capture_turn
from core.memory.scope import MemoryScope
from core.write_envelope import stamp_user_chat


def test_capture_turn_preserves_evidence_ids_and_stores_ingress_provenance(sandbox):
    uid, char_id, turn_id = "owner-ctx", "char-ctx", "turn-ctx"
    context = EventContext.from_ingress(
        uid=uid, char_id=char_id, ingress_event_id="ingress-ctx",
        dedupe_key="dedupe-ctx", source="desktop_chat", channel="desktop",
        kind="user_message", actor="user",
        occurred_at=1000.0,
    )
    scope = MemoryScope.reality_scope(uid, char_id)
    assert event_store.initialize(scope).healthy

    assert capture_turn(
        uid, "hello", "reply", turn_id=turn_id, char_id=char_id,
        envelope=stamp_user_chat(), event_context=context,
    ) == turn_id

    with sqlite3.connect(event_store._path(scope)) as connection:
        rows = connection.execute(
            "SELECT event_id, ingress_event_id, causation_id FROM events ORDER BY seq"
        ).fetchall()
    assert rows == [
        ("turn-ctx:user", "ingress-ctx", "ingress-ctx"),
        ("turn-ctx:assistant", "ingress-ctx", "ingress-ctx"),
    ]
    with sqlite3.connect(event_store._path(scope)) as connection:
        times = dict(connection.execute('SELECT actor, occurred_at FROM events'))
    assert times['user'] == 1000.0
    assert times['assistant'] > times['user']


def test_capture_turn_rejects_cross_scope_context(sandbox):
    context = EventContext.from_ingress(
        uid="owner-a", char_id="char-a", ingress_event_id="ingress-a",
        dedupe_key="dedupe-a", source="desktop", channel="desktop", kind="user_message",
    )
    with pytest.raises(ValueError, match="scope"):
        capture_turn(
            "owner-b", "hello", "reply", turn_id="turn-b", char_id="char-b",
            envelope=stamp_user_chat(), event_context=context,
        )


def test_failed_ledger_append_is_not_reported_as_committed(sandbox, monkeypatch):
    from core.memory.event_store import AppendResult
    from core.event_context_observer import reset_for_tests, snapshot

    monkeypatch.setattr("core.event_context_observer.config", lambda: {"mode": "observe"})
    reset_for_tests()
    context = EventContext.from_ingress(
        uid="owner-fail", char_id="char-fail", ingress_event_id="ingress-fail",
        dedupe_key="dedupe-fail", source="desktop", channel="desktop", kind="user_message",
    )
    monkeypatch.setattr(
        event_store, "append_event",
        lambda _scope, event: AppendResult(False, False, event["event_id"], "busy"),
    )

    capture_turn(
        "owner-fail", "hello", "reply", turn_id="turn-fail", char_id="char-fail",
        envelope=stamp_user_chat(), event_context=context,
    )

    result = snapshot()
    assert result["counts"]["evidence:failed"] == 1
    assert "evidence:committed" not in result["counts"]
    assert result["errors"]["busy"] == 1
