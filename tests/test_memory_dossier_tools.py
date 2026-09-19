from __future__ import annotations

import json
import uuid

import pytest

from tests.fixtures.public_assets import TEST_CHAR_ID


class _Session:
    WAITING_CONFIRM = "waiting_confirm"
    status = "idle"
    def set_waiting_confirm(self, *_args):
        raise AssertionError("dossier tools do not use the dangerous confirmation gate")


def _scope(uid="dossier-tool-owner"):
    from core.memory.scope import MemoryScope
    return MemoryScope.reality_scope(uid, TEST_CHAR_ID)


def _event(scope, event_id, text):
    from core.memory.event_store import append_event
    assert append_event(scope, {"event_id": event_id, "turn_id": event_id, "seq": 1,
        "occurred_at": 100.0, "ingested_at": 101.0, "uid": scope.uid,
        "char_id": scope.character_id, "realm": "reality", "kind": "owner_chat",
        "actor": "user", "channel": "desktop", "source": "fixture",
        "raw_text": text, "visible_text": text, "memory_text": text}).ok


def _seed(scope, title="coffee"):
    from core.memory.dossiers import apply_operations
    _event(scope, "seed-event", f"first {title}")
    dossier, occurrence = uuid.uuid4().hex, uuid.uuid4().hex
    apply_operations(scope, [
        {"action": "create_dossier", "dossier_id": dossier, "title": title},
        {"action": "create_occurrence", "occurrence_id": occurrence, "participants": ["owner"],
         "time_certainty": "exact", "occurred_from": 100, "occurred_to": 100,
         "assertion_kind": "user_stated", "evidence": [{"reference_kind": "event",
         "source_id": "seed-event", "source_revision": "1"}]},
        {"action": "attach_occurrence", "dossier_id": dossier, "occurrence_id": occurrence,
         "expected_revision": 1},
        {"action": "revise_understanding", "dossier_id": dossier, "expected_revision": 2,
         "summary": f"current {title} understanding", "conditions": [],
         "supporting_occurrence_ids": [occurrence], "counterexample_occurrence_ids": [],
         "confidence_reason": "one confirmed occurrence", "coverage_ingest_seq": 1},
    ], operation_id=uuid.uuid4().hex, actor="character", chain="owner_chat")
    return dossier, occurrence


def test_recall_is_bounded_includes_late_evidence_and_misses_wrong_topic(sandbox):
    from core.memory.dossiers import build_recall_context
    scope = _scope(); dossier, _ = _seed(scope)
    _event(scope, "late-event", "coffee preference changed today")

    recalled = build_recall_context(scope, "coffee", max_chars=1200)
    assert recalled["dossier_ids"] == [dossier]
    assert recalled["unreviewed"] is True
    assert "current coffee understanding" in recalled["text"]
    assert "late-event" in recalled["text"]
    assert len(recalled["text"]) <= 1200
    assert build_recall_context(scope, "unrelated-topic")["text"] == ""


def test_explicit_revision_replaces_old_understanding_immediately(sandbox):
    from core.memory.dossiers import apply_operations, build_recall_context, read
    scope = _scope(); dossier, occurrence = _seed(scope)
    apply_operations(scope, [{"action": "revise_understanding", "dossier_id": dossier,
        "expected_revision": 3, "summary": "corrected now", "conditions": [],
        "supporting_occurrence_ids": [], "counterexample_occurrence_ids": [occurrence],
        "confidence_reason": "explicit correction", "coverage_ingest_seq": 1}],
        operation_id=uuid.uuid4().hex, actor="character", chain="owner_chat")
    value = read(scope, dossier)
    assert value["understanding"]["summary"] == "corrected now"
    assert "current coffee understanding" not in build_recall_context(scope, "coffee")["text"]


def test_plans_cancellations_and_suggestions_do_not_count_as_occurrences(sandbox):
    from core.memory.dossiers import apply_operations, read
    scope = _scope(); dossier, _ = _seed(scope)
    for state in ("planned", "cancelled", "hypothetical", "assistant_suggestion"):
        event_id = f"state-{state}"; _event(scope, event_id, f"coffee {state}")
        occurrence = uuid.uuid4().hex
        current = read(scope, dossier)["revision"]
        apply_operations(scope, [
            {"action": "create_occurrence", "occurrence_id": occurrence,
             "participants": [], "time_certainty": "unknown", "assertion_kind": "user_stated",
             "experience_state": state, "evidence": [{"reference_kind": "event",
             "source_id": event_id, "source_revision": "1"}]},
            {"action": "attach_occurrence", "dossier_id": dossier,
             "occurrence_id": occurrence, "expected_revision": current},
        ], operation_id=uuid.uuid4().hex, actor="character", chain="owner_chat")
    assert read(scope, dossier)["confirmed_occurrence_count"] == 1


def test_tools_registered_with_discovery_metadata(monkeypatch):
    import core.tool_dispatcher as dispatcher
    monkeypatch.setattr(dispatcher, "_is_tool_enabled", lambda _name: True)
    monkeypatch.setattr("core.deployment_capabilities.tool_allowed", lambda _name: (True, ""))
    monkeypatch.setattr("core.self_management.policy.tool_allowed", lambda *_args: True)
    expected = {"search_memory_dossiers", "read_memory_dossier", "search_dossier_events",
                "update_memory_dossier", "get_memory_consolidation_status",
                "request_memory_consolidation"}
    names = {item["function"]["name"] for item in dispatcher.get_tools_schema(
        categories=["memory"], uid="owner", char_id=TEST_CHAR_ID)}
    assert expected <= names
    for name in expected:
        assert dispatcher._TOOL_REGISTRY[name]["examples"]
        assert dispatcher._TOOL_REGISTRY[name]["keywords"]


@pytest.mark.asyncio
async def test_dispatcher_freezes_scope_and_group_denies(monkeypatch, sandbox):
    import core.tool_dispatcher as dispatcher
    scope = _scope("owner"); _seed(scope)
    monkeypatch.setattr(dispatcher, "_is_tool_enabled", lambda _name: True)
    monkeypatch.setattr("core.self_management.policy.tool_allowed", lambda *_args: True)
    result = await dispatcher.execute_structured(
        "search_memory_dossiers", {"query": "coffee"}, "owner", "owner", False,
        _Session(), origin="assistant_loop", char_id=TEST_CHAR_ID)
    assert result.status == "tool_executed"
    assert "current coffee understanding" in result.result
    denied = await dispatcher.execute_structured(
        "update_memory_dossier", {"operation_id": uuid.uuid4().hex, "operations": []},
        "owner", "owner", True, _Session(), origin="assistant_loop", char_id=TEST_CHAR_ID)
    assert denied.status == "tool_failed"


@pytest.mark.asyncio
async def test_full_history_request_returns_durable_task_without_scanning(monkeypatch, sandbox):
    import core.tool_dispatcher as dispatcher
    from core.agent_runtime.models import TaskPrincipal
    from core.agent_runtime.task_manager import list_tasks
    monkeypatch.setattr(dispatcher, "_is_tool_enabled", lambda _name: True)
    monkeypatch.setattr("core.self_management.policy.tool_allowed", lambda *_args: True)
    request_id = uuid.uuid4().hex
    result = await dispatcher.execute_structured(
        "request_memory_consolidation", {"request_id": request_id, "scope_mode": "full_history"},
        "owner", "owner", False, _Session(), origin="assistant_loop", char_id=TEST_CHAR_ID)
    assert result.status == "tool_executed"
    tasks = list_tasks(TaskPrincipal.reality("owner", TEST_CHAR_ID))
    assert len(tasks) == 1 and tasks[0]["capability"] == "memory.consolidation"
    assert tasks[0]["status"] == "queued"


def test_prompt_layer_is_ablated_and_does_not_duplicate_legacy(sandbox, monkeypatch):
    from core.character_loader import Character
    import core.prompt_builder as builder
    import core.presence as presence
    import core.author_note_rotator as notes
    import core.config_loader as config
    from core.prompt_ablation import set_state

    monkeypatch.setattr(builder, "_load_jailbreak", lambda layer=None: "")
    monkeypatch.setattr(builder, "_load_style_hint", lambda *, char_id="": "")
    monkeypatch.setattr(builder, "_load_activity_snapshot", lambda *, char_id="": "")
    monkeypatch.setattr(builder, "_format_afterglow_soft_hint", lambda uid, char_id=TEST_CHAR_ID: "")
    monkeypatch.setattr(presence, "get_last_seen_text", lambda uid: "")
    monkeypatch.setattr(notes, "get_current_note", lambda paths=None, char_id=None: "")
    monkeypatch.setattr(config, "get_config", lambda: {"chat": {}})
    kwargs = dict(character=Character(name="Companion"), user_id="owner", user_message="coffee",
                  history=[], relation={}, profile={}, group_context=[],
                  memory_dossier_context="current dossier", event_search_result="legacy event",
                  episodic_result="- legacy episode")
    messages, _ = builder.build(**kwargs)
    layers = [item.get("_layer") for item in messages]
    assert "6b_memory_dossiers" in layers
    set_state(["6b_memory_dossiers"], False)
    messages, debug = builder.build(**kwargs)
    assert "6b_memory_dossiers" not in [item.get("_layer") for item in messages]
    assert "6b_memory_dossiers" in debug["ablated_layers"]
