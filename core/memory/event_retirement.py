"""Draft Memory Event retirement metrics.

These numbers are documentation and observability only.  They must not gate
recall, prompt injection, migration apply, or deletion.  Thresholds stay
``pending_approval`` until an explicit retirement decision reuses them.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

DRAFT_STATUS = "pending_approval"

SHADOW_COVERAGE_MIN = 0.80
UNMAPPED_RESIDUAL_MAX = 0.15
FALLBACK_HIT_MAX = 0.05
SHADOW_WINDOW_DAYS = 14
SHADOW_MIN_COMPLETED = 20
COVERAGE_DENOMINATOR = "old_mapped_event_count"
UNMAPPED_DENOMINATOR = "old_result_count"
FALLBACK_DENOMINATOR = "non_disabled_calls"
MIGRATION_DENOMINATOR = "plan_total"


def residual_unmapped(record: Mapping[str, Any]) -> int:
    """Unmapped legacy results that are not expected scope/source rejections."""
    unmapped = int(record.get("old_unmapped_count") or 0)
    rejected = int(record.get("comparison_scope_rejections") or 0)
    return max(0, unmapped - rejected)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    if not items:
        return None
    return round(sum(items) / len(items), 4)


def shadow_retirement_draft(
    records_by_day: Mapping[str, Iterable[Mapping[str, Any]]] | None,
    *,
    effective_state: str,
    schema_health: str,
    scope_enabled: bool,
) -> dict[str, Any]:
    """Content-free soak snapshot. ``meets_draft`` cannot retire the old path."""
    by_day = {
        str(day): [dict(item) for item in (rows or [])]
        for day, rows in dict(records_by_day or {}).items()
    }
    records = [item for rows in by_day.values() for item in rows]
    completed = [item for item in records if str(item.get("status") or "") == "ok"]
    coverage_values = [
        float(item.get("event_coverage") or 0.0)
        for item in completed
        if int(item.get("old_mapped_event_count") or 0) > 0
    ]
    old_results = sum(int(item.get("old_result_count") or 0) for item in completed)
    residual = sum(residual_unmapped(item) for item in completed)
    calls = len(records)
    fallback_hits = sum(
        1
        for item in records
        if str(item.get("status") or "") in {"timeout", "busy", "cancelled"}
    )
    sampled_days = len(by_day)
    average_coverage = _mean(coverage_values)
    residual_rate = _ratio(residual, old_results)
    fallback_rate = _ratio(fallback_hits, calls)
    numeric_ok = (
        sampled_days >= SHADOW_WINDOW_DAYS
        and len(completed) >= SHADOW_MIN_COMPLETED
        and average_coverage is not None
        and average_coverage >= SHADOW_COVERAGE_MIN
        and residual_rate is not None
        and residual_rate <= UNMAPPED_RESIDUAL_MAX
        and fallback_rate is not None
        and fallback_rate <= FALLBACK_HIT_MAX
        and bool(scope_enabled)
        and effective_state not in {"disabled", "enabled-but-no-scope"}
        and schema_health in {"ok", "missing"}
    )
    open_reasons = ["thresholds_pending_approval"]
    if sampled_days < SHADOW_WINDOW_DAYS:
        open_reasons.append("observation_window_unmet")
    if len(completed) < SHADOW_MIN_COMPLETED:
        open_reasons.append("completed_sample_unmet")
    if average_coverage is None or average_coverage < SHADOW_COVERAGE_MIN:
        open_reasons.append("shadow_coverage_unmet")
    if residual_rate is None or residual_rate > UNMAPPED_RESIDUAL_MAX:
        open_reasons.append("unmapped_legacy_unmet")
    if fallback_rate is None or fallback_rate > FALLBACK_HIT_MAX:
        open_reasons.append("fallback_hit_rate_unmet")
    if not scope_enabled or effective_state in {"disabled", "enabled-but-no-scope"}:
        open_reasons.append("shadow_not_in_rollout")
    if schema_health not in {"ok", "missing"}:
        open_reasons.append("schema_blocked")
    return {
        "status": DRAFT_STATUS,
        "used_as_gate": False,
        "window_days": SHADOW_WINDOW_DAYS,
        "min_completed_calls": SHADOW_MIN_COMPLETED,
        "sampled_days": sampled_days,
        "sampled_dates": sorted(by_day),
        "denominators": {
            "shadow_coverage": COVERAGE_DENOMINATOR,
            "unmapped_legacy": UNMAPPED_DENOMINATOR,
            "fallback_hit_rate": FALLBACK_DENOMINATOR,
        },
        "thresholds": {
            "shadow_coverage_min": SHADOW_COVERAGE_MIN,
            "unmapped_residual_max": UNMAPPED_RESIDUAL_MAX,
            "fallback_hit_rate_max": FALLBACK_HIT_MAX,
        },
        "metrics": {
            "completed": len(completed),
            "calls": calls,
            "average_coverage": average_coverage,
            "mapped_old_events": sum(
                int(item.get("old_mapped_event_count") or 0) for item in completed
            ),
            "residual_unmapped": residual,
            "old_result_count": old_results,
            "unmapped_residual_rate": residual_rate,
            "fallback_hits": fallback_hits,
            "fallback_hit_rate": fallback_rate,
        },
        "rollback": {
            "disable_flag_or_clear_allowlists": True,
            "fail_open_timeout_busy_cancelled": True,
            "keep_legacy_recall": True,
        },
        "numeric_ready": numeric_ok,
        "meets_draft": False,
        "open_reasons": open_reasons,
        "effective_state": effective_state,
        "schema_health": schema_health,
        "scope_enabled": bool(scope_enabled),
    }


def migration_retirement_draft(status: Mapping[str, Any] | None) -> dict[str, Any]:
    """Per-scope import completeness. Duplicate storage is not a delete signal."""
    state = dict(status or {})
    total = int(state.get("total") or 0)
    next_offset = int(state.get("next_offset") or 0)
    failed = int(state.get("failed") or 0)
    conflict = int(state.get("conflict") or 0)
    would_write = int(state.get("would_write") or 0)
    artifacts = state.get("artifacts") or {}
    inventory_only = True
    if isinstance(artifacts, dict):
        for item in artifacts.values():
            if not isinstance(item, dict):
                inventory_only = False
                break
            if str(item.get("mode") or "") != "inventory_only":
                inventory_only = False
                break
    else:
        inventory_only = False
    consumed = (
        int(state.get("written") or 0)
        + int(state.get("already_live") or 0)
        + int(state.get("duplicate") or 0)
        + int(state.get("legacy_unknown") or 0)
    )
    numeric_ok = (
        str(state.get("status") or "") == "completed"
        and total > 0
        and next_offset >= total
        and failed == 0
        and conflict == 0
        and not bool(state.get("indeterminate"))
        and would_write == 0
        and inventory_only
    )
    open_reasons = ["thresholds_pending_approval"]
    if str(state.get("status") or "") != "completed":
        open_reasons.append("migration_not_completed")
    if total <= 0:
        open_reasons.append("empty_plan")
    if next_offset < total:
        open_reasons.append("batch_incomplete")
    if failed:
        open_reasons.append("failed_rows")
    if conflict:
        open_reasons.append("conflict_rows")
    if bool(state.get("indeterminate")):
        open_reasons.append("indeterminate_comparison")
    if would_write:
        open_reasons.append("would_write_remaining")
    if not inventory_only:
        open_reasons.append("non_inventory_artifacts")
    return {
        "status": DRAFT_STATUS,
        "used_as_gate": False,
        "denominator": MIGRATION_DENOMINATOR,
        "metrics": {
            "plan_total": total,
            "next_offset": next_offset,
            "written": int(state.get("written") or 0),
            "already_live": int(state.get("already_live") or 0),
            "duplicate": int(state.get("duplicate") or 0),
            "legacy_unknown": int(state.get("legacy_unknown") or 0),
            "failed": failed,
            "conflict": conflict,
            "would_write": would_write,
            "consumed": consumed,
            "comparison_status": str(state.get("comparison_status") or ""),
            "indeterminate": bool(state.get("indeterminate")),
            "inventory_only_artifacts": inventory_only,
        },
        "rollback": {
            "keep_markdown_fallback": True,
            "dry_run_default": True,
            "tombstone_not_physical_delete": True,
            "do_not_delete_old_recall_for_duplicate_storage": True,
        },
        "numeric_ready": numeric_ok,
        "meets_draft": False,
        "open_reasons": open_reasons,
    }


OBSERVABILITY_OWNERSHIP: tuple[dict[str, str], ...] = (
    {
        "ledger": "owner_receipt",
        "authority": "core.owner_turn_receipts",
        "endpoint": "/observability/owner-turns",
        "association": "caller_label + client_turn_id + canonical_turn_id",
        "retention": "30d and 1000 receipts per caller; live running skipped",
    },
    {
        "ledger": "agent_runtime_task",
        "authority": "core.agent_runtime.task_manager",
        "endpoint": "/observability/agent-runtime-tasks",
        "association": "uid + char_id + task_id; causation_ref.kind/digest",
        "retention": "terminal 30d; max 1000 tasks per scope",
    },
    {
        "ledger": "agent_runtime_work_session",
        "authority": "core.agent_runtime.work_sessions",
        "endpoint": "/observability/agent-runtime-work-sessions",
        "association": "uid + char_id + work_session_id",
        "retention": "durable scoped state; observability page is bounded",
    },
    {
        "ledger": "agent_runtime_process",
        "authority": "core.agent_runtime.process_runner",
        "endpoint": "/observability/agent-runtime-processes",
        "association": "process.run task receipts under uid + char_id",
        "retention": "task terminal 30d; capability snapshot is live",
    },
    {
        "ledger": "agent_runtime_workspace",
        "authority": "core.agent_runtime.workspace",
        "endpoint": "/observability/agent-runtime-workspace",
        "association": "capability snapshot; mutating tools use tool_request fingerprint",
        "retention": "configured roots; no file content in observability",
    },
    {
        "ledger": "autonomy",
        "authority": "core.autonomy.store",
        "endpoint": "/observability/autonomy-opportunities",
        "association": "uid + char_id + job/run/signal_id",
        "retention": "60 jobs / 100 runs / 120 pending signals; funnel 24h and 7d",
    },
    {
        "ledger": "proactive",
        "authority": "core.scheduler.proactive_ledger",
        "endpoint": "/scheduler/proactive-ledger",
        "association": "uid gap/budget; speech cooldown is {char_id}:{name}",
        "retention": "daily budget plus last 3 sends; continuity_by_uid",
    },
    {
        "ledger": "event_evidence",
        "authority": "core.memory.event_store",
        "endpoint": "/observability/memory-event-ledger",
        "association": "uid + char_id + realm=reality + event_id/turn_id",
        "retention": "append-only; DELETE is tombstone pending owner policy",
    },
    {
        "ledger": "event_shadow_recall",
        "authority": "core.memory.event_shadow_recall",
        "endpoint": "/observability/memory-event-shadow-recall",
        "association": "recall_trace date + mapped event/turn IDs",
        "retention": "daily recall_trace files; observability scans 31d",
    },
    {
        "ledger": "event_migration",
        "authority": "core.memory.event_migration",
        "endpoint": "/observability/memory-event-migration",
        "association": "uid + char_id + event_migration_state.json",
        "retention": "resumable content-free progress; Markdown fallback remains",
    },
    {
        "ledger": "action_trace",
        "authority": "core.memory.action_trace",
        "endpoint": "/observability/tool-traces",
        "association": "uid + char_id + execute_structured traces",
        "retention": "ring of last 30",
    },
    {
        "ledger": "mail_execution",
        "authority": "core.mail.execution_ledger",
        "endpoint": "/observability/mail-executions",
        "association": "execution_id + uid + char_id + ISO week",
        "retention": "append-only jsonl; query is bounded",
    },
    {
        "ledger": "api_calls",
        "authority": "core.api_call_log",
        "endpoint": "/observability/api-calls",
        "association": "caller + provider + request_id",
        "retention": "7 daily jsonl files",
    },
)
