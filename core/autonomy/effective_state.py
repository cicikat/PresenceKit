"""Canonical scheduler/autonomy switch resolution and read-only state view."""
from __future__ import annotations

import time
from typing import Any


CONTRACT_VERSION = "scheduler-autonomy-effective-state.v1"

RUNTIME_CONSUMERS = {
    "scheduler.enabled": "core.scheduler.loop._loop",
    "scheduler.source": "core.autonomy.signal_adapters.routine_trigger_enabled",
    "autonomy.enabled": "core.autonomy.runner.tick",
    "autonomy.talk_enabled": "core.autonomy.runner._run_locked",
    "autonomy.min_interval_seconds": "core.autonomy.policy.admission",
    "autonomy.daily_evaluation_budget": "core.autonomy.policy.admission",
    "scheduler.global_proactive_min_gap_seconds": "core.autonomy.talk_gate.check",
    "scheduler.max_daily_proactive": "core.autonomy.talk_gate.check",
}

_SCHEDULER_SOURCE_PATHS: dict[str, tuple[str, ...]] = {
    "morning_greeting": ("morning_greeting",),
    "night_reminder": ("night_reminder",),
    "random_message": ("random_message",),
    "daily_journal": ("daily_journal",),
    "period_reminder": ("period_reminder",),
    "diary_reminder": ("diary_reminder",),
    "presence_nag": ("presence_nag",),
    "timenode": ("timenode",),
    "festival": ("festival",),
    "holiday_boost": ("holiday_boost",),
    "sensor_aware": ("sensor_aware", "enabled"),
    "overflow": ("overflow_trigger",),
}

_AUTONOMY_SOURCE_PATHS: dict[str, tuple[str, ...]] = {
    "interval": ("interval", "enabled"),
    "schedule": ("schedule", "enabled"),
    "overflow_autonomy": ("overflow", "enabled"),
}


def _path_get(root: dict, path: tuple[str, ...], default: Any = None) -> Any:
    value: Any = root
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def scheduler_enabled(config: dict | None = None) -> bool:
    if config is None:
        from core.config_loader import get_config

        config = get_config().get("scheduler", {})
    return bool(config.get("enabled", True))


def scheduler_source_enabled(name: str, config: dict | None = None) -> bool:
    """Resolve one scheduler producer switch, including the global master."""
    if config is None:
        # Keep the legacy `_cfg()` seam observable for callers/tests that
        # provide a hot-reloaded scheduler view, while the control-plane
        # builder passes its already-resolved config explicitly.
        from core.scheduler.loop import _cfg

        config = _cfg()
    if not scheduler_enabled(config):
        return False
    path = _SCHEDULER_SOURCE_PATHS.get(str(name))
    return bool(_path_get(config, path, True)) if path else True


def autonomy_enabled(uid: str, char_id: str, state: dict) -> bool:
    from core.self_management.policy import autonomy_enabled as capability_enabled

    return capability_enabled(
        uid, char_id, bool((state.get("config") or {}).get("enabled", False))
    )


def autonomy_min_interval(uid: str, char_id: str, state: dict) -> int:
    from core.self_management.policy import autonomy_min_interval as capability_interval

    base = int((state.get("config") or {}).get("min_interval_seconds") or 0)
    return capability_interval(uid, char_id, base)


def autonomy_talk_enabled(uid: str, char_id: str, state: dict) -> bool:
    return autonomy_enabled(uid, char_id, state) and bool(
        (state.get("config") or {}).get("talk_enabled", True)
    )


def daily_evaluation_budget(state: dict) -> int:
    return max(0, int((state.get("config") or {}).get("daily_evaluation_budget") or 0))


def daily_evaluation_used(state: dict) -> int:
    from core.autonomy.store import roll_daily

    roll_daily(state)
    return max(0, int((state.get("daily") or {}).get("evaluations") or 0))


def cooldown_status(uid: str, char_id: str, state: dict, *, now: float | None = None) -> dict:
    now = time.time() if now is None else float(now)
    latest = max(
        (
            float((value or {}).get("last_evaluated_at") or 0)
            for value in (state.get("sources") or {}).values()
        ),
        default=0.0,
    )
    seconds = autonomy_min_interval(uid, char_id, state)
    remaining = max(0, int(latest + seconds - now)) if latest else 0
    configured = int((state.get("config") or {}).get("min_interval_seconds") or 0)
    return {
        "configured_value": configured,
        "effective_value": seconds,
        "override_source": "self_capability" if seconds != configured else "autonomy_config",
        "restart_required": False,
        "runtime_consumer": RUNTIME_CONSUMERS["autonomy.min_interval_seconds"],
        "last_evaluated_at": latest,
        "remaining_seconds": remaining,
        "blocked": remaining > 0,
    }


def _control(
    *, configured: Any, effective: Any, source: str, consumer: str, reason: str = ""
) -> dict:
    return {
        "configured_value": configured,
        "effective_value": effective,
        "override_source": source,
        "restart_required": False,
        "runtime_consumer": consumer,
        "reason": reason,
    }


def _scheduler_runtime() -> dict:
    from core.scheduler import loop

    task = loop._scheduler_task
    if task is None:
        status = "not_started"
    elif task.cancelled():
        status = "cancelled"
    elif task.done():
        status = "failed" if task.exception() is not None else "stopped"
    else:
        status = "running"
    return {"available": status == "running", "task_state": status}


def _self_capability_override(uid: str, char_id: str, capability_id: str) -> dict:
    from core.self_management import policy, registry, store

    state = store.load(uid, char_id)
    canonical = registry._canonical(capability_id)
    selected = (state.get("agent_state") or {}).get(canonical)
    grant = (state.get("grants") or {}).get(canonical)
    feature = policy.feature_enabled()
    active = bool(feature and (selected is not None or isinstance(grant, dict)))
    return {
        "capability_id": canonical,
        "feature_enabled": feature,
        "selected_value": selected,
        "granted": bool((grant or {}).get("allowed")) if isinstance(grant, dict) else None,
        "active": active,
    }


def _trigger_rows(scheduler_cfg: dict, autonomy_cfg: dict, scheduler_effective: bool) -> list[dict]:
    from core.scheduler.gating import TRIGGER_MIGRATION_STATUS

    rows: list[dict] = []
    for name, lifecycle in sorted(TRIGGER_MIGRATION_STATUS.items()):
        configured: Any = None
        effective = False if lifecycle == "retired" else scheduler_effective
        config_path = ""
        consumer = "core.scheduler.loop._loop"
        if name in _SCHEDULER_SOURCE_PATHS:
            path = _SCHEDULER_SOURCE_PATHS[name]
            configured = bool(_path_get(scheduler_cfg, path, True))
            effective = scheduler_source_enabled(name, scheduler_cfg)
            config_path = "scheduler." + ".".join(path)
            consumer = RUNTIME_CONSUMERS["scheduler.source"]
        elif name in _AUTONOMY_SOURCE_PATHS:
            path = _AUTONOMY_SOURCE_PATHS[name]
            configured = bool(_path_get(autonomy_cfg, path, False))
            effective = scheduler_effective and configured
            config_path = "autonomy." + ".".join(path)
            consumer = "core.autonomy.runner.tick"
        rows.append(
            {
                "name": name,
                "lifecycle": lifecycle,
                "config_path": config_path or None,
                "configured_value": configured,
                "effective_value": bool(effective),
                "override_source": "retired" if lifecycle == "retired" else (config_path or "scheduler.enabled"),
                "restart_required": False,
                "runtime_consumer": consumer,
            }
        )
    return rows


def build_effective_state(uid: str, char_id: str) -> dict:
    """Build the one safe, read-only scheduler/autonomy control-plane view."""
    from channels.registry import get_active
    from core import pipeline_registry
    from core.autonomy import store, talk_gate
    from core.scheduler.proactive_ledger import snapshot as ledger_snapshot

    from core.config_loader import get_config

    now = time.time()
    global_cfg = get_config()
    scheduler_cfg = global_cfg.get("scheduler") or {}
    state = store.load(uid, char_id)
    autonomy_cfg = state.get("config") or {}
    scheduler_configured = bool(scheduler_cfg.get("enabled", True))
    scheduler_effective = scheduler_enabled(scheduler_cfg)
    scheduler_runtime = _scheduler_runtime()
    autonomy_configured = bool(autonomy_cfg.get("enabled", False))
    autonomy_effective = autonomy_enabled(uid, char_id, state)
    autonomy_override = _self_capability_override(uid, char_id, "autonomy.enabled")
    autonomy_source = "self_capability" if autonomy_effective != autonomy_configured else "autonomy_config"
    cooldown = cooldown_status(uid, char_id, state, now=now)
    budget = daily_evaluation_budget(state)
    used = daily_evaluation_used(state)
    talks = max(0, int((state.get("daily") or {}).get("talks") or 0))
    # Mirror policy.admission: a zero-talk day is not muted by evaluation budget.
    budget_blocks = bool(budget > 0 and used >= budget and talks > 0)
    pending = [row for row in state.get("jobs", []) if row.get("status") in {"pending", "processing"}]
    processing = next((row for row in pending if row.get("status") == "processing"), None)
    circuit_until = float((state.get("circuit") or {}).get("open_until") or 0)
    talk_mode, talk_reason = talk_gate.check(uid)
    talk_configured = bool(autonomy_cfg.get("talk_enabled", True))
    pipeline_available = pipeline_registry.get() is not None
    delivery_channels = len(get_active())
    talk_effective = bool(
        scheduler_effective
        and scheduler_runtime["available"]
        and autonomy_effective
        and talk_configured
        and talk_mode == "allow"
        and pipeline_available
        and delivery_channels
    )

    if not scheduler_effective:
        proactive_state, reason = "disabled", "scheduler_disabled"
    elif not scheduler_runtime["available"]:
        proactive_state, reason = "unavailable", "scheduler_not_running"
    elif not autonomy_effective:
        proactive_state, reason = "disabled", "autonomy_disabled"
    elif processing is not None:
        proactive_state, reason = "running", "autonomy_job_processing"
    elif pending:
        proactive_state, reason = "queued", "autonomy_job_pending"
    elif circuit_until > now:
        proactive_state, reason = "blocked", "circuit_open"
    elif budget_blocks:
        proactive_state, reason = "blocked", "daily_evaluation_budget_exhausted"
    elif cooldown["blocked"]:
        proactive_state, reason = "cooled_down", "minimum_interval_not_elapsed"
    elif not talk_configured:
        proactive_state, reason = "blocked", "talk_disabled"
    elif talk_mode == "hard":
        proactive_state, reason = "blocked", talk_reason
    elif not pipeline_available:
        proactive_state, reason = "unavailable", "pipeline_unavailable"
    elif not delivery_channels:
        proactive_state, reason = "unavailable", "no_delivery_channel"
    else:
        proactive_state, reason = "enabled", (talk_reason if talk_mode == "soft" else "ready")

    ledger = ledger_snapshot(uid)
    talk_source = "autonomy_config" if talk_configured else "autonomy_config_disabled"
    return {
        "contract_version": CONTRACT_VERSION,
        "uid": str(uid),
        "char_id": str(char_id),
        "proactive": {
            "state": proactive_state,
            "reason": reason,
            "can_evaluate": bool(scheduler_effective and scheduler_runtime["available"] and autonomy_effective and not cooldown["blocked"] and not budget_blocks and circuit_until <= now),
            "can_talk": talk_effective,
        },
        "scheduler": {
            **_control(
                configured=scheduler_configured,
                effective=scheduler_effective,
                source="config.scheduler.enabled",
                consumer=RUNTIME_CONSUMERS["scheduler.enabled"],
            ),
            "runtime": scheduler_runtime,
        },
        "autonomy": {
            **_control(
                configured=autonomy_configured,
                effective=autonomy_effective,
                source=autonomy_source,
                consumer=RUNTIME_CONSUMERS["autonomy.enabled"],
                reason="self_capability_override" if autonomy_source == "self_capability" else "configured_value",
            ),
            "self_capability": autonomy_override,
            "runtime": {
                "queued_jobs": len(pending),
                "processing_job_id": str((processing or {}).get("id") or ""),
                "circuit_open_until": circuit_until,
            },
        },
        "talk": {
            **_control(
                configured=talk_configured,
                effective=talk_effective,
                source=talk_source,
                consumer=RUNTIME_CONSUMERS["autonomy.talk_enabled"],
                reason=("ok" if talk_effective else reason),
            ),
            "gate_mode": talk_mode,
            "gate_reason": talk_reason,
            "pipeline_available": pipeline_available,
            "delivery_channel_count": delivery_channels,
        },
        "cooldown": cooldown,
        "daily_evaluation_budget": {
            **_control(
                configured=budget,
                effective=budget,
                source="autonomy_config",
                consumer=RUNTIME_CONSUMERS["autonomy.daily_evaluation_budget"],
            ),
            "used": used,
            "talks": talks,
            "remaining": max(0, budget - used),
            "blocked": budget_blocks,
            "zero_talk_bypass": bool(budget > 0 and used >= budget and talks == 0),
        },
        "daily_talk_budget": {
            "configured_value": ledger["daily_budget"],
            "effective_value": ledger["daily_budget"],
            "used": ledger["daily_count"],
            "remaining": max(0, int(ledger["daily_budget"]) - int(ledger["daily_count"])),
            "blocked": int(ledger["daily_count"]) >= int(ledger["daily_budget"]),
            "override_source": "config.scheduler.max_daily_proactive",
            "restart_required": False,
            "runtime_consumer": RUNTIME_CONSUMERS["scheduler.max_daily_proactive"],
        },
        "global_talk_cooldown": {
            "configured_value": scheduler_cfg.get("global_proactive_min_gap_seconds", 90 * 60),
            "effective_value": ledger["effective_gap_seconds"],
            "remaining_seconds": ledger["next_allowed_in_seconds"],
            "blocked": ledger["next_allowed_in_seconds"] > 0,
            "override_source": "config.scheduler.global_proactive_min_gap_seconds",
            "restart_required": False,
            "runtime_consumer": RUNTIME_CONSUMERS["scheduler.global_proactive_min_gap_seconds"],
        },
        "trigger_sources": _trigger_rows(scheduler_cfg, autonomy_cfg, scheduler_effective),
        "manual_test_endpoints": [
            {"method": "POST", "path": "/scheduler/trigger/{name}", "test_only": True, "direct_delivery": False},
            {"method": "POST", "path": "/admin/autonomy/test-enqueue", "test_only": True, "direct_delivery": False},
        ],
    }
