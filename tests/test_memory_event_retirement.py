from __future__ import annotations

import asyncio

from tests.fixtures.public_assets import TEST_CHAR_ID


def _ok_record(*, coverage: float = 1.0, mapped: int = 2, old_results: int = 2, unmapped: int = 0, rejected: int = 0, status: str = "ok") -> dict:
    return {
        "status": status,
        "event_coverage": coverage,
        "old_mapped_event_count": mapped,
        "old_result_count": old_results,
        "old_unmapped_count": unmapped,
        "comparison_scope_rejections": rejected,
    }


def test_residual_unmapped_excludes_expected_scope_rejections():
    from core.memory.event_retirement import residual_unmapped

    assert residual_unmapped({"old_unmapped_count": 3, "comparison_scope_rejections": 2}) == 1
    assert residual_unmapped({"old_unmapped_count": 1, "comparison_scope_rejections": 4}) == 0


def test_shadow_coverage_denominator_is_mapped_old_events_not_new_or_all_old(sandbox):
    from core.memory.event_shadow_recall import compare_legacy_results
    from core.memory.event_store import append_event
    from core.memory.scope import MemoryScope

    scope = MemoryScope.reality_scope("retirement-coverage", TEST_CHAR_ID)
    assert append_event(scope, {
        "event_id": "mapped-user", "turn_id": "turn-a", "occurred_at": 1,
        "realm": "reality", "kind": "owner_chat", "actor": "user",
    }).ok
    compared = compare_legacy_results(
        {"new_event_ids": ["mapped-user"], "new_turn_ids": ["turn-a"], "new_event_turns": {"mapped-user": "turn-a"}},
        [
            {"source_event_ids": ["mapped-user"], "scope": {"uid": scope.uid, "char_id": TEST_CHAR_ID, "realm": "reality"}},
            {"id": "episodic-opaque-id"},
            {"source": "web", "event_id": "web-isolated"},
        ],
        scope=scope,
    )
    assert compared["old_result_count"] == 3
    assert compared["old_mapped_event_count"] == 1
    assert compared["event_coverage"] == 1.0
    assert compared["old_unmapped_count"] == 2


def test_shadow_retirement_draft_stays_pending_even_when_numeric_ready():
    from core.memory.event_retirement import (
        SHADOW_MIN_COMPLETED,
        SHADOW_WINDOW_DAYS,
        shadow_retirement_draft,
    )

    records = {
        f"2026-09-{day:02d}": [_ok_record(), _ok_record()]
        for day in range(1, SHADOW_WINDOW_DAYS + 1)
    }
    assert sum(len(rows) for rows in records.values()) >= SHADOW_MIN_COMPLETED
    draft = shadow_retirement_draft(
        records,
        effective_state="enabled-and-running",
        schema_health="ok",
        scope_enabled=True,
    )
    assert draft["status"] == "pending_approval"
    assert draft["used_as_gate"] is False
    assert draft["meets_draft"] is False
    assert draft["numeric_ready"] is True
    assert draft["denominators"]["shadow_coverage"] == "old_mapped_event_count"
    assert draft["denominators"]["unmapped_legacy"] == "old_result_count"
    assert draft["denominators"]["fallback_hit_rate"] == "non_disabled_calls"
    assert draft["open_reasons"] == ["thresholds_pending_approval"]
    assert draft["rollback"]["keep_legacy_recall"] is True


def test_shadow_retirement_draft_stays_open_without_window_or_rollout():
    from core.memory.event_retirement import shadow_retirement_draft

    draft = shadow_retirement_draft(
        {"2026-09-17": [_ok_record(status="timeout"), _ok_record()]},
        effective_state="disabled",
        schema_health="ok",
        scope_enabled=False,
    )
    assert draft["numeric_ready"] is False
    assert draft["meets_draft"] is False
    assert "observation_window_unmet" in draft["open_reasons"]
    assert "shadow_not_in_rollout" in draft["open_reasons"]
    assert "fallback_hit_rate_unmet" in draft["open_reasons"]


def test_migration_retirement_draft_does_not_treat_duplicate_storage_as_delete():
    from core.memory.event_retirement import migration_retirement_draft

    draft = migration_retirement_draft({
        "status": "completed",
        "total": 4,
        "next_offset": 4,
        "written": 2,
        "already_live": 1,
        "duplicate": 1,
        "legacy_unknown": 0,
        "failed": 0,
        "conflict": 0,
        "would_write": 0,
        "indeterminate": False,
        "comparison_status": "ok",
        "artifacts": {
            "short_term": {"mode": "inventory_only"},
            "episodic": {"mode": "inventory_only"},
        },
    })
    assert draft["numeric_ready"] is True
    assert draft["meets_draft"] is False
    assert draft["used_as_gate"] is False
    assert draft["open_reasons"] == ["thresholds_pending_approval"]
    assert draft["rollback"]["do_not_delete_old_recall_for_duplicate_storage"] is True
    assert draft["rollback"]["keep_markdown_fallback"] is True


def test_observability_ownership_map_covers_existing_ledgers_without_a_universal_store():
    from core.memory.event_retirement import OBSERVABILITY_OWNERSHIP

    ledgers = {row["ledger"] for row in OBSERVABILITY_OWNERSHIP}
    assert ledgers >= {
        "owner_receipt", "agent_runtime_task", "agent_runtime_work_session",
        "agent_runtime_process", "autonomy", "proactive", "event_evidence",
        "action_trace", "mail_execution", "api_calls",
    }
    assert all(row["endpoint"] for row in OBSERVABILITY_OWNERSHIP)
    assert "universal_ledger" not in ledgers


def test_shadow_observability_projects_retirement_draft_without_bodies(sandbox, monkeypatch):
    from admin.routers.observability import memory_event_shadow_recall
    from core.recall_trace import write_trace

    uid = "retirement-observe"
    write_trace(uid, TEST_CHAR_ID, {
        "query": "private query must stay out",
        "event_shadow_recall": {
            "status": "ok", "enabled": True, "event_coverage": 0.9,
            "old_mapped_event_count": 4, "old_result_count": 4,
            "old_unmapped_count": 0, "comparison_scope_rejections": 0,
        },
    })
    monkeypatch.setattr("core.memory.event_shadow_recall.config", lambda: {
        "enabled": True, "uids": [], "char_ids": [],
        "timeout_ms": 120, "sqlite_timeout_ms": 40,
    })
    result = asyncio.run(memory_event_shadow_recall(uid, TEST_CHAR_ID, _auth=None))
    draft = result["retirement_draft"]
    assert draft["used_as_gate"] is False
    assert draft["meets_draft"] is False
    assert "thresholds_pending_approval" in draft["open_reasons"]
    assert "private query must stay out" not in str(result)
    assert "event_id" not in result["records"][0]


def test_prompt_builder_still_has_no_shadow_or_proposal_parameter():
    import inspect
    from core.prompt_builder import build

    params = inspect.signature(build).parameters
    assert "event_shadow_recall" not in params
    assert "event_edge_proposal" not in params
    assert "retirement_draft" not in params
