"""Admin control surface for durable internal autonomy; requests only inspect/enqueue."""
from __future__ import annotations

import re
import time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException

from admin.auth import require_scopes

router = APIRouter()
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _scope() -> tuple[str, str]:
    from core.scheduler.loop import _active_char_id_or_none, _owner_id
    uid, char_id = _owner_id(), _active_char_id_or_none()
    if not uid or not char_id:
        raise HTTPException(status_code=409, detail="owner 或当前角色未配置")
    return str(uid), str(char_id)


@router.get("/admin/autonomy/status", summary="读取内置唤醒状态")
async def status(auth=Depends(require_scopes("state.read"))):
    from core.autonomy import store
    from core.autonomy.talk_gate import check
    from core.scheduler.proactive_ledger import continuity_status
    uid, char_id = _scope(); state = store.load(uid, char_id)
    talk_mode, talk_reason = check(uid)
    jobs = [j for j in state.get("jobs", []) if j.get("status") in {"pending", "processing"}]
    current = next((j for j in jobs if j.get("status") == "processing"), None)
    latest = max((float((value or {}).get("last_evaluated_at") or 0) for value in state.get("sources", {}).values()), default=0)
    now = time.time(); cfg = state["config"]
    if current: runtime_state = "运行"
    elif jobs: runtime_state = "排队"
    elif float((state.get("circuit") or {}).get("open_until") or 0) > now: runtime_state = "熔断"
    elif latest and now - latest < int(cfg.get("min_interval_seconds") or 0): runtime_state = "冷却"
    else: runtime_state = "空闲"
    interval = cfg.get("interval") or {}
    next_interval = ((state.get("sources", {}).get("interval", {}) or {}).get("next_due_at") or (store.source_last_evaluated(state, "interval") + int(interval.get("seconds") or 0))) if interval.get("enabled") else None
    outcome_counts: dict[str, int] = {}
    for run in state.get("runs", []):
        outcome = str(run.get("evaluation_status") or "evaluated")
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
    return {"uid": uid, "char_id": char_id, "config_enabled": cfg.get("enabled"), "runtime_state": runtime_state, "current_run_id": (current or {}).get("id", ""), "current_stage": (current or {}).get("status", ""), "next_due_at": next_interval, "daily": state.get("daily"), "sources": state.get("sources"), "circuit": state.get("circuit"), "queued_jobs": jobs, "queued_signals": _redact_pending_signals(state.get("pending_signals") or []), "delivery_correlation_count": len(state.get("delivered_correlations") or []), "last_run": (state.get("runs") or [None])[-1], "outcome_counts": outcome_counts, "talk": {"available": talk_mode == "allow" and cfg.get("talk_enabled"), "mode": talk_mode, "reason": talk_reason, **continuity_status(uid)}}


@router.get("/admin/autonomy/effective-state", summary="读取调度器与自主性的统一生效状态")
async def effective_state(auth=Depends(require_scopes("state.read"))):
    from core.autonomy.effective_state import build_effective_state

    uid, char_id = _scope()
    return build_effective_state(uid, char_id)


def _redact_pending_signals(rows: list[dict]) -> list[dict]:
    """Expose queue state without returning legacy prompt/template content."""
    result = []
    for row in rows[-50:]:
        signal = row.get("signal") if isinstance(row, dict) else None
        if not isinstance(signal, dict):
            continue
        result.append({
            "dedupe_key": row.get("dedupe_key", ""),
            "queued_at": row.get("queued_at", 0),
            "signal_id": signal.get("signal_id") or signal.get("id", ""),
            "source": signal.get("source", ""),
            "reason": signal.get("reason", ""),
            "evidence": signal.get("evidence") or [],
            "priority": signal.get("priority", 0),
            "urgency": signal.get("urgency", 0),
            "expires_at": signal.get("expires_at", signal.get("expiry", 0)),
        })
    return result


def _redact_dream_retry_events(rows: list[dict]) -> list[dict]:
    allowed_statuses = {
        "signal_terminal_one_shot",
        "signal_terminal_expired",
        "signal_terminal_invalid",
        "dream_retry_child_queued",
    }
    allowed_keys = {
        "status",
        "signal_id",
        "signal_index",
        "source",
        "outcome",
        "expires_at",
        "error",
        "child_job_id",
        "child_opportunity_id",
        "signal_ids",
        "signal_sources",
    }
    return [
        {key: value for key, value in event.items() if key in allowed_keys}
        for event in rows
        if isinstance(event, dict) and event.get("status") in allowed_statuses
    ]


@router.get("/admin/autonomy/config", summary="读取内置唤醒配置")
async def config(auth=Depends(require_scopes("state.read"))):
    from core.autonomy import store
    uid, char_id = _scope(); return store.load(uid, char_id)["config"]


@router.patch("/admin/autonomy/config", summary="更新内置唤醒配置")
async def patch_config(body: dict, auth=Depends(require_scopes("admin"))):
    from core.autonomy import store
    uid, char_id = _scope(); state = store.load(uid, char_id); cfg = state["config"]
    bool_fields = {"enabled", "talk_enabled"}
    int_limits = {
        "daily_evaluation_budget": (1, 100), "min_interval_seconds": (0, 86400),
        "max_steps": (1, 8), "max_tools": (0, 8), "max_write_tools": (0, 8),
        "total_timeout_seconds": (1, 600), "tool_timeout_seconds": (1, 120),
        "circuit_failure_threshold": (1, 20), "circuit_cooldown_seconds": (60, 86400),
    }
    for key in bool_fields:
        if key in body:
            if not isinstance(body[key], bool): raise HTTPException(status_code=422, detail=f"{key} 必须为布尔值")
            cfg[key] = body[key]
    for key, (lower, upper) in int_limits.items():
        if key in body:
            value = body[key]
            if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
                raise HTTPException(status_code=422, detail=f"{key} 非法")
            cfg[key] = value
    for key in int_limits:
        if not isinstance(cfg.get(key), int) or not int_limits[key][0] <= cfg[key] <= int_limits[key][1]:
            raise HTTPException(status_code=422, detail=f"{key} 非法")
    for name in ("interval", "overflow", "schedule"):
        if name in body:
            if not isinstance(body[name], dict): raise HTTPException(status_code=422, detail=f"{name} 必须为对象")
            cfg[name].update(body[name])
    if not _TIME_RE.match(str(cfg["schedule"].get("time") or "")): raise HTTPException(status_code=422, detail="schedule.time 必须是 HH:MM")
    weekdays = cfg["schedule"].get("weekdays")
    if not isinstance(weekdays, list) or any(isinstance(day, bool) or not isinstance(day, int) or not 0 <= day <= 6 for day in weekdays):
        raise HTTPException(status_code=422, detail="schedule.weekdays 必须是 0-6 的数组")
    timezone = str(cfg["schedule"].get("timezone") or "local")
    if timezone != "local":
        try: ZoneInfo(timezone)
        except Exception as exc: raise HTTPException(status_code=422, detail="schedule.timezone 非法") from exc
    window = cfg["schedule"].get("window") or []
    if window and (not isinstance(window, list) or len(window) != 2 or any(not _TIME_RE.match(str(value)) for value in window)):
        raise HTTPException(status_code=422, detail="schedule.window 必须是两个 HH:MM 时间")
    if cfg["schedule"].get("restart_miss_policy") not in {"skip", "catch_up_once"}:
        raise HTTPException(status_code=422, detail="schedule.restart_miss_policy 非法")
    interval_seconds = cfg["interval"].get("seconds")
    if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int) or not 60 <= interval_seconds <= 31 * 86400:
        raise HTTPException(status_code=422, detail="interval.seconds 需在 60 秒至 31 天之间")
    threshold = cfg["overflow"].get("threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 < float(threshold) <= 10:
        raise HTTPException(status_code=422, detail="overflow.threshold 需在 (0, 10] 内")
    if cfg["max_write_tools"] > cfg["max_tools"]:
        raise HTTPException(status_code=422, detail="max_write_tools 不能超过 max_tools")
    store.replace_config(uid, char_id, cfg); return cfg


@router.get("/admin/autonomy/runs", summary="读取最近内置唤醒运行")
async def runs(limit: int = 30, auth=Depends(require_scopes("state.read"))):
    from core.autonomy import store
    uid, char_id = _scope(); data = store.load(uid, char_id).get("runs", [])
    rows = list(reversed(data[-max(1, min(limit, 100)):]))
    return {"runs": [{key: value for key, value in row.items() if key != "prompt_snapshot"} for row in rows]}


@router.get("/observability/autonomy-opportunities", summary="Read proactive opportunity lifecycle")
async def opportunities(limit: int = 50, auth=Depends(require_scopes("state.read"))):
    """Read a redacted lifecycle view: queued, silent, tools-only, sent, or canceled."""
    from core.autonomy import store

    uid, char_id = _scope()
    state = store.load(uid, char_id)
    entries = []
    for signal in _redact_pending_signals(state.get("pending_signals") or []):
        entries.append({
            "kind": "signal",
            "status": "unevaluated",
            **signal,
        })
    for job in state.get("jobs", []):
        opportunity = job.get("opportunity") or {}
        signals = []
        for signal in opportunity.get("signals") or []:
            if not isinstance(signal, dict):
                continue
            signals.append({
                "signal_id": signal.get("signal_id") or signal.get("id", ""),
                "source": signal.get("source", ""),
                "reason": signal.get("reason", ""),
                "evidence": signal.get("evidence") or [],
                "memory_query": signal.get("memory_query"),
                "priority": signal.get("priority", 0),
                "urgency": signal.get("urgency", 0),
                "confidence": signal.get("confidence", 0),
                "suggested_action": signal.get("suggested_action") or signal.get("action_mode", "none"),
                "created_at": signal.get("created_at", 0),
                "expires_at": signal.get("expires_at", signal.get("expiry", 0)),
            })
        entries.append({
            "kind": "opportunity",
            "status": "unevaluated" if job.get("status") in {"pending", "processing"} else "expired_or_finished",
            "job_id": job.get("id", ""),
            "opportunity_id": opportunity.get("id", ""),
            "source": job.get("source", ""),
            "signal_sources": job.get("signal_sources") or [],
            "signal_count": len(opportunity.get("signals") or []),
            "retry_parent_job_id": job.get("retry_parent_job_id", ""),
            "retry_parent_run_id": job.get("retry_parent_run_id", ""),
            "opportunity": {key: opportunity.get(key) for key in ("version", "priority", "reason", "expiry", "memory_query", "action_mode", "urgency", "confidence", "suggested_action")},
            "signals": signals,
            "created_at": job.get("created_at", 0),
        })
    for run in state.get("runs", []):
        entries.append({
            "kind": "run",
            "status": run.get("evaluation_status") or "evaluated",
            "run_id": run.get("id", ""),
            "job_id": run.get("job_id", ""),
            "opportunity_id": run.get("opportunity_id", ""),
            "source": run.get("source", ""),
            "signal_count": run.get("signal_count", 0),
            "disposition": run.get("disposition", ""),
            "talk_sent": bool(run.get("talk_sent")),
            "tool_names": run.get("tool_names") or [],
            "events": _redact_dream_retry_events(run.get("events") or []),
            "started_at": run.get("started_at", 0),
            "finished_at": run.get("finished_at", 0),
        })
    entries.sort(key=lambda item: float(item.get("finished_at") or item.get("created_at") or item.get("started_at") or 0), reverse=True)
    entries = entries[:max(1, min(int(limit), 100))]
    counts: dict[str, int] = {}
    for item in entries:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return {
        "uid": uid,
        "char_id": char_id,
        "entries": entries,
        "count": len(entries),
        "status_counts": counts,
        "funnel": _opportunity_funnel(state),
    }


_FUNNEL_DISPOSITIONS = (
    "no_candidate",
    "producer_matched",
    "signal_queued",
    "opportunity_created",
    "admission_allowed",
    "blocked_user_active",
    "daily_budget",
    "minimum_interval",
    "expired",
    "talk_unavailable",
    "evaluated_silent",
    "tools_only",
    "talk_gate_rejected",
    "talk_sent",
)


def _opportunity_funnel(state: dict, *, now: float | None = None) -> dict:
    """Aggregate durable, content-free delivery evidence for 24h and 7d."""
    now = time.time() if now is None else float(now)
    jobs = {str(row.get("id") or ""): row for row in state.get("jobs", []) if isinstance(row, dict)}
    return {
        "24h": _funnel_window(state, jobs, cutoff=now - 24 * 3600),
        "7d": _funnel_window(state, jobs, cutoff=now - 7 * 24 * 3600),
    }


def _funnel_window(state: dict, jobs: dict[str, dict], *, cutoff: float) -> dict:
    by_source: dict[str, dict[str, int]] = {}

    def add(source: str, disposition: str, count: int = 1) -> None:
        source = str(source or "unknown")[:80]
        row = by_source.setdefault(source, {key: 0 for key in _FUNNEL_DISPOSITIONS})
        row[disposition] = row.get(disposition, 0) + count

    for row in state.get("pending_signals", []):
        if not isinstance(row, dict) or float(row.get("queued_at") or 0) < cutoff:
            continue
        signal = row.get("signal") or {}
        if isinstance(signal, dict):
            source = signal.get("source", "unknown")
            add(source, "producer_matched")
            add(source, "signal_queued")

    for job in jobs.values():
        if float(job.get("created_at") or 0) < cutoff:
            continue
        sources = _job_signal_sources(job)
        for source in sources:
            add(source, "producer_matched")
            add(source, "opportunity_created")

    for run in state.get("runs", []):
        if not isinstance(run, dict) or float(run.get("finished_at") or run.get("started_at") or 0) < cutoff:
            continue
        job = jobs.get(str(run.get("job_id") or ""), {})
        sources = _job_signal_sources(job) or [str(run.get("source") or "autonomy")]
        disposition = _funnel_disposition(run)
        for source in sources:
            add(source, disposition)
            if disposition not in {"blocked_user_active", "daily_budget", "minimum_interval", "expired"}:
                add(source, "admission_allowed")

    totals = {key: sum(row.get(key, 0) for row in by_source.values()) for key in _FUNNEL_DISPOSITIONS}
    return {"by_source": by_source, "totals": totals}


def _job_signal_sources(job: dict) -> list[str]:
    sources = [str(value) for value in job.get("signal_sources", []) if str(value)]
    if sources:
        return sorted(set(sources))
    signals = (job.get("opportunity") or {}).get("signals") if isinstance(job.get("opportunity"), dict) else []
    return sorted({str(item.get("source") or "") for item in signals or [] if isinstance(item, dict) and item.get("source")})


def _funnel_disposition(run: dict) -> str:
    disposition = str(run.get("disposition") or "")
    evaluation = str(run.get("evaluation_status") or "")
    if bool(run.get("talk_sent")) or evaluation == "talk_sent":
        return "talk_sent"
    if evaluation == "evaluated_silent":
        return "evaluated_silent"
    if evaluation == "tools_completed_no_talk":
        return "tools_only"
    if disposition in {"blocked_user_active", "canceled_user_activity"}:
        return "blocked_user_active"
    if disposition == "suppressed_daily_budget":
        return "daily_budget"
    if disposition == "duplicate":
        return "minimum_interval"
    if disposition == "expired":
        return "expired"
    events = run.get("events") or []
    if any(isinstance(event, dict) and event.get("status") == "talk_unavailable" for event in events):
        return "talk_unavailable"
    return "talk_gate_rejected"


@router.get("/admin/autonomy/runs/{run_id}/prompt", summary="Read one autonomy prompt snapshot")
async def run_prompt(run_id: str, auth=Depends(require_scopes("admin"))):
    from core.autonomy import store
    uid, char_id = _scope()
    run = next((row for row in reversed(store.load(uid, char_id).get("runs", [])) if row.get("id") == run_id), None)
    if run is None:
        raise HTTPException(status_code=404, detail="autonomy run not found")
    return {"run_id": run_id, "messages": run.get("prompt_snapshot") or []}


@router.get("/admin/autonomy/tools", summary="读取自主工具 allowlist")
async def tools(auth=Depends(require_scopes("state.read"))):
    from core.autonomy import store
    from core.autonomy.policy import tool_decisions
    uid, char_id = _scope(); state = store.load(uid, char_id)
    return {"tools": tool_decisions(uid, char_id, state)}


@router.patch("/admin/autonomy/tools", summary="更新自主工具 allowlist")
async def patch_tools(body: dict, auth=Depends(require_scopes("admin"))):
    from core.autonomy import store
    from core.autonomy.policy import tool_eligibility
    from core.tool_dispatcher import _TOOL_REGISTRY, get_tool_effect, is_side_effect_tool
    uid, char_id = _scope(); name = str(body.get("name") or "")
    if name not in _TOOL_REGISTRY: raise HTTPException(status_code=404, detail="未知工具")
    info = _TOOL_REGISTRY[name]; effect = get_tool_effect(name) or ("write" if is_side_effect_tool(name) else "read")
    policy = {"enabled": bool(body.get("enabled")), "mcp_explicit": bool(body.get("mcp_explicit", False)), "outcome_unknown": str(body.get("outcome_unknown") or "fail_closed")}
    eligible, reason = tool_eligibility(name, policy, registry=_TOOL_REGISTRY, effect=effect)
    if policy["enabled"] and not eligible:
        raise HTTPException(status_code=422, detail=f"autonomy tool is not eligible: {reason}")
    if policy["outcome_unknown"] != "fail_closed":
        raise HTTPException(status_code=422, detail="outcome_unknown must be fail_closed")
    if effect not in {"read", "write"} or info.get("dangerous") or info.get("require_confirm"):
        raise HTTPException(status_code=422, detail="该工具不允许 autonomy")
    state = store.load(uid, char_id); state["config"].setdefault("tools", {})[name] = policy
    store.replace_config(uid, char_id, state["config"]); return {"ok": True}


@router.post("/admin/autonomy/tools/bulk", summary="批量开启/关闭主动时段可用工具")
async def bulk_tools(body: dict, auth=Depends(require_scopes("admin"))):
    """一键授权：只处理内置、非危险、无需确认且通过 tool_eligibility 的工具。

    MCP 工具需要逐个确认「结果未知」策略，不在批量范围内。
    """
    from core.autonomy import store
    from core.autonomy.policy import tool_eligibility
    from core.tool_dispatcher import _TOOL_REGISTRY, get_tool_effect, is_side_effect_tool
    uid, char_id = _scope()
    enabled = bool(body.get("enabled", True))
    names = body.get("names")
    if names is not None and not isinstance(names, list):
        raise HTTPException(status_code=422, detail="names 必须是列表")
    state = store.load(uid, char_id)
    tools_cfg = state["config"].setdefault("tools", {})
    changed: list[str] = []
    skipped: dict[str, str] = {}
    for name in (names if names is not None else list(_TOOL_REGISTRY)):
        info = _TOOL_REGISTRY.get(str(name))
        if info is None or info.get("self_management"):
            continue
        if info.get("category") == "mcp":
            skipped[str(name)] = "mcp_requires_explicit_enablement"
            continue
        effect = get_tool_effect(name) or ("write" if is_side_effect_tool(name) else "read")
        policy = {"enabled": enabled, "mcp_explicit": False, "outcome_unknown": "fail_closed"}
        eligible, reason = tool_eligibility(name, policy, registry=_TOOL_REGISTRY, effect=effect)
        if enabled and not eligible:
            if names is not None:
                skipped[str(name)] = reason
            continue
        if (tools_cfg.get(name) or {}).get("enabled") == enabled:
            continue
        tools_cfg[name] = policy
        changed.append(str(name))
    if changed:
        store.replace_config(uid, char_id, state["config"])
    return {"ok": True, "enabled": enabled, "changed": changed, "skipped": skipped}


@router.post("/admin/autonomy/test-enqueue", summary="排队一次内置唤醒测试")
async def test_enqueue(body: dict | None = None, auth=Depends(require_scopes("admin"))):
    from core.autonomy import store
    uid, char_id = _scope(); source = str((body or {}).get("source") or "manual")
    if source not in {"manual", "overflow", "schedule", "interval"}: raise HTTPException(status_code=422, detail="无效 source")
    job, status = store.enqueue(uid, char_id, source, dedupe_key=f"manual:{source}:{__import__('uuid').uuid4().hex}")
    return {
        "status": status,
        "job_id": job.id if job else "",
        "test_only": True,
        "direct_delivery": False,
        "runtime_admission_required": True,
    }
