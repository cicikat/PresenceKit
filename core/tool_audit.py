"""Private, append-only execution receipts; never injected into a prompt by default."""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from core.safe_write import safe_append_jsonl
from core.sandbox import get_paths

logger = logging.getLogger(__name__)
_SAFE_ARG_KEYS = frozenset({
    "action", "capability_id", "expected_revision", "action_id", "category",
    "date_from", "date_to", "offset", "record_id", "time_range", "status",
})


def _scalar(value):
    if isinstance(value, (bool, int)) or value is None:
        return value
    if isinstance(value, str) and len(value) <= 128 and not any(
        marker in value.lower() for marker in ("://", "bearer ", "token", "secret", "password", "api_key", "header")
    ):
        return value
    return "<redacted>"


def record(uid: str, char_id: str, *, tool: str, args: dict, origin: str, outcome) -> None:
    """Record one dispatcher call without raw arguments, results or credentials."""
    try:
        now = datetime.now(timezone.utc)
        from core.tool_activity import current_call
        activity = current_call() or {}
        safe_args = {key: _scalar(value) for key, value in args.items()
                     if key in _SAFE_ARG_KEYS} if isinstance(args, dict) else {}
        fingerprint = hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False,
                                                 default=str).encode("utf-8")).hexdigest()[:16]
        status = "success" if outcome.status == "tool_executed" else (
            "unknown" if outcome.status == "outcome_unknown" else
            "pending" if outcome.status == "confirmation_required" else "failed"
        )
        receipt = {
            "time": now.isoformat(), "timestamp": now.timestamp(),
            "tool": str(tool)[:128], "origin": str(origin)[:64],
            "key_params": safe_args, "arguments_fingerprint": fingerprint,
            "status": status, "error_code": None if status == "success" else outcome.status,
            "before": None, "after": None,
            "request_id": str(activity.get("event_id") or uuid.uuid4().hex)[:128],
        }
        if tool == "manage_self_capability" and isinstance(args, dict):
            from core.self_management.store import read_audit
            action_id = args.get("action_id")
            matching = next((item for item in reversed(read_audit(uid, char_id, limit=200))
                             if item.get("actor") == "agent" and item.get("action_id") == action_id), None)
            if matching:
                receipt["status"] = "success" if matching.get("result") in {"applied", "idempotent", "unchanged"} else "failed"
                receipt["error_code"] = None if receipt["status"] == "success" else matching.get("result")
                receipt["before"] = _scalar(matching.get("old_effective_value"))
                receipt["after"] = _scalar(matching.get("new_effective_value"))
                receipt["key_params"]["requested_value"] = _scalar(matching.get("requested_value"))
        safe_append_jsonl(get_paths().tool_audit(uid, char_id=char_id, day=now.date().isoformat()), receipt)
    except Exception as exc:
        logger.warning("[tool_audit] record failed: %s", exc)


def query(uid: str, char_id: str, *, time_range: str = "24h", tool: str = "",
          status: str = "", limit: int = 50) -> list[dict]:
    seconds = {"24h": 86400, "7d": 604800, "30d": 2592000}.get(time_range, 86400)
    cutoff = time.time() - seconds
    today = datetime.now(timezone.utc).date()
    rows = []
    for days_ago in range((seconds // 86400) + 2):
        day = (today - timedelta(days=days_ago)).isoformat()
        try:
            path = get_paths().tool_audit(uid, char_id=char_id, day=day)
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    item = json.loads(line)
                    if not isinstance(item, dict) or float(item.get("timestamp") or 0) < cutoff:
                        continue
                    if tool and item.get("tool") != tool:
                        continue
                    if status and item.get("status") != status:
                        continue
                    rows.append(item)
        except FileNotFoundError:
            continue
        except Exception as exc:
            logger.warning("[tool_audit] query failed: %s", exc)
    rows.sort(key=lambda item: float(item.get("timestamp") or 0), reverse=True)
    return rows[:max(1, min(int(limit), 100))]


def is_recap_request(message: str) -> bool:
    text = str(message or "").lower()
    past = any(word in text for word in ("昨天", "昨晚", "今早", "早上", "刚才", "之前", "最近", "上次", "yesterday", "last night", "earlier", "previously"))
    action = any(word in text for word in ("做了", "干了", "调用", "工具", "失败", "改了", "修改", "能力", "权限", "did you", "changed", "failed", "tools"))
    return past and action
