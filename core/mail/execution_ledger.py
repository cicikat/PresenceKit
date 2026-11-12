"""Sanitized forensic ledger for scheduler-driven character letters."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from core.safe_write import safe_append_jsonl
from core.sandbox import get_paths

FAILURE_CODES = frozenset({
    "generation_error", "empty_content", "quality_rejected", "smtp_auth_error",
    "smtp_connection_error", "smtp_timeout", "smtp_rejected", "smtp_unknown_error",
    "outcome_unknown",
})


def append(*, execution_id: str, uid: str, char_id: str, stage: str, result: str,
           failure_code: str = "", exception_type: str = "", smtp_status_code: int | None = None,
           retry_count: int = 0, duration_ms: int = 0, trigger: str = "letter_writer",
           source: str = "scheduler") -> None:
    """Persist metadata only; never accept letter text, prompts, addresses, or exception text."""
    try:
        safe_append_jsonl(get_paths().mail_execution_log(), {
            "execution_id": str(execution_id), "uid": str(uid), "char_id": str(char_id),
            "trigger": str(trigger), "source": str(source), "stage": str(stage),
            "result": str(result), "failure_code": failure_code if failure_code in FAILURE_CODES else "gmail_delivery_error" if failure_code.startswith("gmail_") else "",
            "exception_type": str(exception_type)[:80], "smtp_status_code": smtp_status_code,
            "retry_count": max(0, int(retry_count)), "duration_ms": max(0, int(duration_ms)),
            "timestamp": time.time(),
        })
    except Exception:
        pass


def query(*, uid: str = "", char_id: str = "", execution_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
    try:
        path: Path = get_paths().mail_execution_log()
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        rows = [row for row in rows if isinstance(row, dict)]
        rows = [row for row in rows if (not uid or row.get("uid") == uid) and (not char_id or row.get("char_id") == char_id) and (not execution_id or row.get("execution_id") == execution_id)]
        return sorted(rows, key=lambda row: float(row.get("timestamp") or 0), reverse=True)[:limit]
    except Exception:
        return []
