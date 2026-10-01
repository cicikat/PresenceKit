"""Read-only inspection of ``data/logs/dead_letter_queue`` (工单 G).

A DLQ entry is ``{ms_ts}_{task_type}.json`` holding ``{task, error, failed_at}`` for a slow-queue
job (memory fixation, reflection, ...) that exhausted its retries.  ``task`` carries the pending
memory data, so nothing from it is ever returned here: only task type, timing, a coarse failure
category and one redacted line of the error.

Shared by the admin runtime observation (``GET /observe/runtime``, ``memory.read``) and the daily
``dlq_monitor`` so both report the same breakdown.  Never mutates the directory.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SAMPLE_COUNT = 10
_MAX_ERROR_CHARS = 120

# Ordered: first match wins.  Coarse on purpose — enough to tell an upstream/model outage
# (retrying later may work) from a code defect (retrying never will).
_REASON_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("code_error", re.compile(r"\b(ImportError|ModuleNotFoundError|AttributeError|TypeError|KeyError|NameError|"
                              r"UnboundLocalError|AssertionError|IndexError|ValueError)\b")),
    ("upstream_blocked", re.compile(r"geo_blocked|PermissionDenied|Upstream access forbidden|Error code: 40[13]|quota|forbidden", re.I)),
    ("timeout", re.compile(r"TimeoutError|timed out|\btimeout\b", re.I)),
    ("connection", re.compile(r"APIConnectionError|ConnectError|Connection error|ConnectionReset|DNS", re.I)),
    ("upstream_error", re.compile(r"InternalServerError|BadGateway|ServiceUnavailable|Error code: 50\d|upstream", re.I)),
    ("bad_request", re.compile(r"BadRequestError|invalid_request|Error code: 400", re.I)),
    ("synthesis_failed", re.compile(r"LLM 合成失败")),
)


def classify_reason(error_line: str) -> str:
    text = str(error_line or "")
    for name, pattern in _REASON_RULES:
        if pattern.search(text):
            return name
    return "other"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds") if ts else ""


def _parse(path: Path) -> dict[str, Any]:
    from admin.log_filter import redact_log_text

    stem_ms, _, stem_type = path.stem.partition("_")
    row: dict[str, Any] = {
        "filename": path.name,
        "task_type": stem_type or "unknown",
        "failed_at_ts": int(stem_ms) / 1000 if stem_ms.isdigit() else 0.0,
        "reason": "unreadable",
        "error_line": "",
    }
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return row
    if not isinstance(data, dict):
        return row
    task_type = (data.get("task") or {}).get("task_type") if isinstance(data.get("task"), dict) else None
    if task_type:
        row["task_type"] = str(task_type)
    try:
        row["failed_at_ts"] = float(data.get("failed_at") or row["failed_at_ts"])
    except (TypeError, ValueError):
        pass
    lines = [line.strip() for line in str(data.get("error", "")).splitlines() if line.strip()]
    last = lines[-1] if lines else ""
    row["error_line"] = redact_log_text(re.sub(r"\s+", " ", last))[:_MAX_ERROR_CHARS]
    row["reason"] = classify_reason(last)
    return row


def scan(dlq_dir: Path | None = None, *, max_files: int | None = None) -> dict[str, Any]:
    """Summarise the DLQ: totals, per-task-type breakdown with reasons, oldest entry, newest samples."""
    if dlq_dir is None:
        from core.sandbox import get_paths
        dlq_dir = get_paths().dead_letter_queue()
    files = sorted(dlq_dir.glob("*.json")) if dlq_dir.exists() else []
    rows = [_parse(path) for path in files]
    by_type: dict[str, dict[str, Any]] = {}
    for row in rows:
        bucket = by_type.setdefault(row["task_type"], {"count": 0, "oldest_ts": 0.0, "newest_ts": 0.0, "reasons": Counter()})
        bucket["count"] += 1
        ts = row["failed_at_ts"]
        if ts:
            bucket["oldest_ts"] = min(bucket["oldest_ts"] or ts, ts)
            bucket["newest_ts"] = max(bucket["newest_ts"], ts)
        bucket["reasons"][row["reason"]] += 1
    stamps = [row["failed_at_ts"] for row in rows if row["failed_at_ts"]]
    newest = sorted(rows, key=lambda row: row["failed_at_ts"], reverse=True)[:_SAMPLE_COUNT]
    return {
        "count": len(rows),
        "cap": max_files,
        "oldest_failed_at": _iso(min(stamps)) if stamps else "",
        "newest_failed_at": _iso(max(stamps)) if stamps else "",
        "reasons": dict(Counter(row["reason"] for row in rows)),
        "by_task_type": {
            name: {
                "count": info["count"],
                "oldest_failed_at": _iso(info["oldest_ts"]),
                "newest_failed_at": _iso(info["newest_ts"]),
                "reasons": dict(info["reasons"]),
            }
            for name, info in sorted(by_type.items(), key=lambda item: -item[1]["count"])
        },
        "recent_samples": [
            {"filename": row["filename"], "task_type": row["task_type"],
             "failed_at": _iso(row["failed_at_ts"]), "reason": row["reason"], "error": row["error_line"]}
            for row in newest
        ],
        "unreadable": sum(1 for row in rows if row["reason"] == "unreadable"),
    }
