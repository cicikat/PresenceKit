"""Fail-open ledger for outbound API calls; never stores request bodies or secrets."""
from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timedelta

from core.safe_write import safe_append_jsonl
from core.sandbox import get_paths

_KEEP_N = 7


def _daily_path(base_path, ts: float) -> object:
    day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    return base_path.with_name(f"{base_path.stem}-{day}{base_path.suffix}")


def _prune_daily_logs(base_path, now: float) -> None:
    cutoff = (datetime.fromtimestamp(now).date() - timedelta(days=_KEEP_N - 1))
    pattern = f"{base_path.stem}-*{base_path.suffix}"
    for candidate in base_path.parent.glob(pattern):
        day = candidate.stem.removeprefix(f"{base_path.stem}-")
        try:
            if datetime.strptime(day, "%Y-%m-%d").date() < cutoff:
                candidate.unlink()
        except (OSError, ValueError):
            continue


def append(
    *,
    caller: str,
    purpose: str,
    provider: str,
    model: str,
    duration_ms: int,
    ok: bool,
    output_hint: str = "",
    request_id: str = "",
    audit_id: str = "",
    protocol: str = "",
    error_category: str = "",
    logical_call_id: str = "",
    attempt_id: str = "",
    route_role: str = "",
    switch_reason: str = "",
    skip_reason: str = "",
    logical_final: bool | None = None,
    sdk_retry_policy: str = "",
) -> None:
    try:
        now = time.time()
        path = _daily_path(get_paths().api_call_log(), now)
        row = {
            "ts": now,
            "caller": caller,
            "purpose": purpose,
            "provider": provider,
            "model": model,
            "duration_ms": max(0, int(duration_ms)),
            "ok": bool(ok),
            "output_hint": str(output_hint)[:120],
            "request_id": str(request_id)[:128],
            "audit_id": str(audit_id)[:128],
            "protocol": str(protocol)[:48],
            "error_category": str(error_category)[:64],
        }
        if logical_call_id:
            row["logical_call_id"] = str(logical_call_id)[:64]
        if attempt_id:
            row["attempt_id"] = str(attempt_id)[:80]
        if route_role:
            row["route_role"] = str(route_role)[:16]
        if switch_reason:
            row["switch_reason"] = str(switch_reason)[:64]
        if skip_reason:
            row["skip_reason"] = str(skip_reason)[:64]
        if logical_final is not None:
            row["logical_final"] = bool(logical_final)
        if sdk_retry_policy:
            row["sdk_retry_policy"] = str(sdk_retry_policy)[:16]
        safe_append_jsonl(path, row)
        _prune_daily_logs(get_paths().api_call_log(), now)
    except Exception:
        pass


def query(*, caller: str = "", provider: str = "", limit: int = 100) -> tuple[list[dict], dict[str, int]]:
    import json
    try:
        base_path = get_paths().api_call_log()
        paths = [base_path] + sorted(base_path.parent.glob(f"{base_path.stem}-*{base_path.suffix}"))
        paths = [path for path in paths if path.exists()]
        if not paths:
            return [], {}
        rows = [
            json.loads(line)
            for path in paths
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        rows = [
            r for r in rows
            if isinstance(r, dict)
            and (not caller or r.get("caller") == caller)
            and (not provider or r.get("provider") == provider)
        ]
        rows = sorted(rows, key=lambda row: float(row.get("ts") or 0), reverse=True)[:limit]
        return rows, dict(Counter(str(r.get("provider") or "unknown") for r in rows))
    except Exception:
        return [], {}


def _iter_rows(*, since_ts: float | None = None, until_ts: float | None = None) -> list[dict]:
    import json

    base_path = get_paths().api_call_log()
    paths = [base_path] + sorted(base_path.parent.glob(f"{base_path.stem}-*{base_path.suffix}"))
    rows: list[dict] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            ts = float(row.get("ts") or 0)
            if since_ts is not None and ts < since_ts:
                continue
            if until_ts is not None and ts > until_ts:
                continue
            rows.append(row)
    return rows


def failover_stats(*, window_hours: float = 24.0, now: float | None = None) -> dict:
    """Attempt / logical / fallback rates for the given window.

    Denominators:
      attempts — every ledger row that has an attempt_id, else every row
      logical  — unique logical_call_id (legacy rows without one count as their own call)
      fallback — logical calls that actually issued a fallback attempt
    Skip reasons are counted from any row of a logical call, never from prompt bodies.
    """
    now = time.time() if now is None else now
    hours = max(1.0, min(float(window_hours or 24.0), 24.0 * 7))
    since = now - hours * 3600
    try:
        rows = _iter_rows(since_ts=since, until_ts=now)
    except Exception:
        rows = []

    attempts = [r for r in rows if r.get("attempt_id") or r.get("logical_call_id") or True]
    attempt_total = len(attempts)
    attempt_fail = sum(1 for r in attempts if not r.get("ok"))

    groups: dict[str, list[dict]] = {}
    for row in rows:
        key = str(row.get("logical_call_id") or "") or f"legacy:{id(row)}"
        groups.setdefault(key, []).append(row)

    logical_total = len(groups)
    logical_fail = 0
    fallback_issued = 0
    fallback_ok = 0
    skip_counts: Counter[str] = Counter()
    switch_reason_counts: Counter[str] = Counter()
    by_purpose: dict[str, dict[str, int]] = {}
    last_switch: dict | None = None
    last_refusal: dict | None = None
    unread_files = 0

    for members in groups.values():
        purpose = str(members[0].get("purpose") or "unknown")
        bucket = by_purpose.setdefault(purpose, {
            "logical": 0, "logical_fail": 0, "fallback_issued": 0, "fallback_ok": 0,
        })
        bucket["logical"] += 1
        has_fallback = any(r.get("route_role") == "fallback" for r in members)
        fallback_success = any(r.get("route_role") == "fallback" and r.get("ok") for r in members)
        finals = [r for r in members if r.get("logical_final")]
        if finals:
            ok = any(r.get("ok") for r in finals)
        else:
            ok = any(r.get("ok") for r in members)
        if not ok:
            logical_fail += 1
            bucket["logical_fail"] += 1
        if has_fallback:
            fallback_issued += 1
            bucket["fallback_issued"] += 1
            if fallback_success:
                fallback_ok += 1
                bucket["fallback_ok"] += 1
            fallback_rows = [r for r in members if r.get("route_role") == "fallback"]
            switch_row = max(fallback_rows, key=lambda r: float(r.get("ts") or 0))
            reason = str(switch_row.get("switch_reason") or "").strip()
            if reason:
                switch_reason_counts[reason] += 1
            candidate = {
                "ts": float(switch_row.get("ts") or 0),
                "iso": datetime.fromtimestamp(float(switch_row.get("ts") or 0)).isoformat(timespec="seconds"),
                "reason": reason,
                "logical_id": str(switch_row.get("logical_call_id") or ""),
                "purpose": purpose,
                "ok": bool(fallback_success),
            }
            if last_switch is None or candidate["ts"] >= last_switch["ts"]:
                last_switch = candidate
        for row in members:
            skip = str(row.get("skip_reason") or "").strip()
            if skip:
                skip_counts[skip] += 1
                refusal = {
                    "ts": float(row.get("ts") or 0),
                    "iso": datetime.fromtimestamp(float(row.get("ts") or 0)).isoformat(timespec="seconds"),
                    "reason": skip,
                    "logical_id": str(row.get("logical_call_id") or ""),
                    "purpose": str(row.get("purpose") or purpose),
                }
                if last_refusal is None or refusal["ts"] >= last_refusal["ts"]:
                    last_refusal = refusal

    skip_top = [
        {"reason": reason, "count": count}
        for reason, count in skip_counts.most_common(8)
    ]
    return {
        "window_hours": hours,
        "since_ts": since,
        "until_ts": now,
        "attempts": {
            "total": attempt_total,
            "failed": attempt_fail,
            "failure_rate": (attempt_fail / attempt_total) if attempt_total else 0.0,
        },
        "logical": {
            "total": logical_total,
            "failed": logical_fail,
            "failure_rate": (logical_fail / logical_total) if logical_total else 0.0,
        },
        "fallback": {
            "issued": fallback_issued,
            "succeeded": fallback_ok,
            "success_rate": (fallback_ok / fallback_issued) if fallback_issued else 0.0,
            "denominator": "logical calls that issued a fallback attempt",
        },
        "skip_reasons": dict(skip_counts),
        "skip_top": skip_top,
        "switch_reasons": dict(switch_reason_counts),
        "last_switch": last_switch,
        "last_refusal": last_refusal,
        "by_purpose": by_purpose,
        "truncated": False,
        "unread_files": unread_files,
        "notes": {
            "attempt_denominator": "each ledger row is one application-layer attempt; SDK retries are not extra rows",
            "logical_denominator": "unique logical_call_id; rows without one count as their own call",
            "fallback_denominator": "logical calls that issued a fallback attempt",
            "bodies_stored": False,
        },
    }
