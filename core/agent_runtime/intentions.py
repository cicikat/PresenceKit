
"""Private Reality intentions and their existing-tool receipt bindings."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import time
import uuid

from core.agent_runtime.task_store import process_instance_id, scope_lock
from core.safe_write import safe_write_json
from core.sandbox import get_paths

ACTIVE_LIMIT = 32
HISTORY_LIMIT = 200
HISTORY_SECONDS = 30 * 86400
COOLDOWN_SECONDS = 7200
ACTIVE = frozenset({"proposed", "deferred", "ready", "acting", "blocked", "outcome_unknown"})
ACTIONS = {
    "write_track_note": "track_id",
    "gmail_mark_handled": "pending_id",
    "gmail_reply_message": "message_id",
    "start_agent_task": "request_id",
}


class IntentError(ValueError):
    pass


def enabled(state: dict) -> bool:
    return state.get("config", {}).get("character_intents_enabled") is True


def _path(uid: str, char_id: str):
    return get_paths().character_intentions(uid, char_id=char_id)


def _load(uid: str, char_id: str) -> dict:
    try:
        data = json.loads(_path(uid, char_id).read_text(encoding="utf-8"))
        if data["version"] != 1 or not isinstance(data["rows"], dict) or not isinstance(data["counts"], dict):
            raise ValueError
        for identity, row in data["rows"].items():
            if (row["id"], row["owner_uid"], row["char_id"]) != (identity, uid, char_id):
                raise ValueError
        return data
    except FileNotFoundError:
        return {"version": 1, "rows": {}, "counts": {}}
    except (KeyError, TypeError, ValueError, OSError):
        raise IntentError("intent_storage_error") from None


def _save(uid: str, char_id: str, data: dict) -> None:
    if not safe_write_json(_path(uid, char_id), data, keep_bak=False):
        raise IntentError("intent_storage_error")


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _count(data: dict, key: str) -> None:
    data["counts"][key] = int(data["counts"].get(key, 0)) + 1


def _update(data: dict, row: dict, status: str, now: float, **fields) -> None:
    row.update(status=status, updated_at=now, revision=row["revision"] + 1, **fields)
    _count(data, status)


def _open(row: dict) -> bool:
    if row["status"] in ACTIVE:
        return True
    evidence = row.get("result_evidence") or []
    return bool(row["status"] == "abandoned" and row.get("action_ref")
                and (not evidence or evidence[0]["kind"] == "agent_task_admitted"))


def _maintain(data: dict, now: float) -> bool:
    changed = False
    for row in data["rows"].values():
        if row["status"] == "acting" and not row.get("task_id") and (
            row.get("worker") != process_instance_id() or now >= row.get("lease_until", 0)
        ):
            _update(data, row, "outcome_unknown", now, block_reason="action_receipt_missing")
            changed = True
        if row["status"] in ACTIVE - {"acting", "outcome_unknown"} and row["expires_at"] <= now:
            _update(data, row, "abandoned", now, block_reason="intent_expired")
            changed = True
    completed = sorted(
        (row for row in data["rows"].values() if not _open(row)),
        key=lambda row: row["updated_at"], reverse=True,
    )
    for index, row in enumerate(completed):
        if index >= HISTORY_LIMIT or row["updated_at"] < now - HISTORY_SECONDS:
            del data["rows"][row["id"]]
            changed = True
    return changed


def _mutate(uid: str, char_id: str, change):
    now = time.time()
    with scope_lock(uid, char_id):
        data = _load(uid, char_id)
        maintained = _maintain(data, now)
        value, changed = change(data, now)
        if maintained or changed:
            _save(uid, char_id, data)
        return value


def _text(value: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise IntentError("invalid_intent_text")
    value = value.strip()
    if len(value) > maximum or (required and not value):
        raise IntentError("invalid_intent_text")
    from core.sensitive_redaction import RedactionError, redact_for_export
    try:
        return redact_for_export(value)
    except RedactionError as exc:
        raise IntentError("intent_text_redaction_failed") from exc


def _find(data: dict, identity: str, revision: int) -> dict:
    row = data["rows"].get(identity)
    if row is None:
        raise IntentError("intent_not_found")
    if row["revision"] != revision:
        raise IntentError("intent_revision_conflict")
    return row


def propose(uid: str, char_id: str, *, goal: str, motivation: str, anchors: list[dict],
            next_check_at: float | None = None, expires_at: float | None = None) -> dict:
    goal = _text(goal, 200, required=True)
    motivation = _text(motivation, 300)
    fingerprint = _hash([(item["kind"], item["id"]) for item in anchors] or ["subjective", goal.casefold()])

    def change(data, now):
        for row in data["rows"].values():
            if row["fingerprint"] == fingerprint or row["goal"].casefold() == goal.casefold():
                return {"intent": row, "duplicate": True}, False
        if sum(_open(row) for row in data["rows"].values()) >= ACTIVE_LIMIT:
            _count(data, "quota_exhausted")
            return {"error": "quota_exhausted"}, True
        expiry = _bounded_time(expires_at, now, now + HISTORY_SECONDS)
        due = _bounded_time(next_check_at, now, expiry) if next_check_at is not None else None
        identity = uuid.uuid4().hex
        row = {
            "id": identity, "owner_uid": uid, "char_id": char_id, "origin": "autonomy",
            "goal": goal, "motivation": motivation, "source_anchors": anchors,
            "fingerprint": fingerprint, "status": "deferred" if due else "proposed",
            "created_at": now, "updated_at": now, "next_check_at": due,
            "last_evaluated_at": now, "expires_at": expiry, "revision": 1,
            "preconditions": ["reality_scope", "effective_tool_permission"],
            "action_ref": None, "result_evidence": [],
        }
        data["rows"][identity] = row
        _count(data, "proposed")
        _count(data, "grounded" if anchors else "subjective")
        if due:
            _count(data, "deferred")
        return {"intent": row, "duplicate": False}, True

    result = _mutate(uid, char_id, change)
    if "error" in result:
        raise IntentError(result["error"])
    return result


def _bounded_time(value, now: float, maximum: float) -> float:
    if value is None:
        return maximum
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not now < value <= maximum:
        raise IntentError("invalid_intent_time")
    return float(value)


def revise(uid: str, char_id: str, *, identity: str, revision: int, action: str,
           run_id: str = "", tool: str = "", target: str = "", next_check_at: float | None = None,
           goal: str | None = None, motivation: str | None = None, block_reason: str = "") -> dict:
    def change(data, now):
        row = _find(data, identity, revision)
        previous = row["status"]
        if previous not in ACTIVE:
            raise IntentError("intent_terminal")
        if previous in {"acting", "outcome_unknown"} and action != "abandon":
            raise IntentError("intent_unresolved")
        if action == "ready" and (row.get("next_check_at") or 0) > now:
            raise IntentError("intent_not_due")
        fields = {"armed_run": "", "armed_tool": "", "armed_target": "", "last_evaluated_at": now}
        if goal is not None:
            fields["goal"] = _text(goal, 200, required=True)
        if motivation is not None:
            fields["motivation"] = _text(motivation, 300)
        if action == "ready":
            if tool not in ACTIONS or not isinstance(target, str) or not 1 <= len(target) <= 128 or not run_id:
                raise IntentError("invalid_intent_action")
            if any(other["id"] != identity and other["status"] == "ready"
                   and (other.get("armed_tool"), other.get("armed_target")) == (tool, target)
                   for other in data["rows"].values()):
                raise IntentError("intent_action_already_armed")
            fields.update(armed_run=run_id, armed_tool=tool, armed_target=target, next_check_at=None, block_reason="")
            if previous in {"deferred", "blocked"}:
                _count(data, "resumed")
            status = "ready"
        elif action == "defer":
            due = next_check_at if next_check_at is not None else min(now + COOLDOWN_SECONDS, row["expires_at"])
            fields.update(next_check_at=_bounded_time(due, now, row["expires_at"]), block_reason="")
            status = "deferred"
        elif action == "block":
            fields.update(next_check_at=min(now + COOLDOWN_SECONDS, row["expires_at"]), block_reason=block_reason)
            status = "blocked"
        elif action == "abandon":
            fields.update(next_check_at=None, block_reason=block_reason or "abandoned")
            status = "abandoned"
        else:
            raise IntentError("invalid_intent_action")
        _update(data, row, status, now, **fields)
        return row, True

    return _mutate(uid, char_id, change)


def _action_digest(tool: str, args: dict) -> str:
    return _hash([tool, {key: value for key, value in args.items() if key != "request_id"}])


def claim(uid: str, char_id: str, run_id: str, tool: str, args: dict, *, timeout: float) -> dict | None:
    if tool not in ACTIONS:
        return None
    fingerprint = _action_digest(tool, args)

    def change(data, now):
        for row in data["rows"].values():
            if (row["status"] in {"acting", "outcome_unknown", "abandoned"} and _open(row)
                    and row.get("action_fingerprint") == fingerprint):
                raise IntentError("intent_previous_outcome_unresolved")
        ready = [
            row for row in data["rows"].values()
            if row["status"] == "ready" and row.get("armed_tool") == tool
        ]
        if not ready:
            return None, False
        row = next((row for row in ready if row["armed_target"] == args.get(ACTIONS[tool])), None)
        if row is None:
            if any(candidate.get("armed_run") == run_id for candidate in ready):
                raise IntentError("intent_action_target_mismatch")
            return None, False
        reference = {"kind": "autonomy_tool_call", "run_id": run_id, "tool": tool, "id": uuid.uuid4().hex}
        _update(data, row, "acting", now, action_ref=reference, action_fingerprint=fingerprint,
                worker=process_instance_id(), lease_until=now + timeout + 30,
                result_evidence=[], task_id=None)
        _count(data, "execution_attempt")
        return row, True

    return _mutate(uid, char_id, change)


def finish(uid: str, char_id: str, claimed: dict, *, status: str, reason: str = "",
           evidence: dict | None = None, task_id: str | None = None) -> None:
    def change(data, now):
        row = data["rows"].get(claimed["id"])
        if not row or row["action_ref"] != claimed["action_ref"]:
            return None, False
        if row["status"] not in {"acting", "outcome_unknown", "abandoned"}:
            return None, False
        next_status = status if row["status"] != "abandoned" else "abandoned"
        fields = {"block_reason": reason, "result_evidence": [evidence] if evidence else []}
        if status == "blocked":
            fields["next_check_at"] = min(now + COOLDOWN_SECONDS, row["expires_at"])
        if task_id:
            fields["task_id"] = task_id
        _update(data, row, next_status, now, **fields)
        return None, True

    _mutate(uid, char_id, change)


def snapshot(uid: str, char_id: str) -> tuple[list[dict], dict]:
    return _mutate(uid, char_id, lambda data, _: ((list(data["rows"].values()), data["counts"]), False))


def observation(uid: str, char_id: str) -> dict:
    rows, counts = snapshot(uid, char_id)
    states = Counter(row["status"] for row in rows)
    return {
        "counts": counts, "states": dict(states), "active_limit": ACTIVE_LIMIT,
        "items": [
            {key: row.get(key) for key in (
                "id", "status", "revision", "updated_at", "next_check_at", "block_reason",
            )}
            for row in sorted(rows, key=lambda item: item["updated_at"], reverse=True)
            if row["status"] in ACTIVE
        ],
        "recent_outcomes": [
            {"status": row["status"], "at": row["updated_at"], "reason": row.get("block_reason", "")}
            for row in sorted(
                (item for item in rows if item["status"] in {"done", "blocked", "outcome_unknown", "abandoned"}),
                key=lambda item: item["updated_at"], reverse=True,
            )[:12]
        ],
    }


def clear_terminal(uid: str, char_id: str) -> int:
    def change(data, _):
        ids = [
            identity for identity, row in data["rows"].items()
            if not _open(row)
        ]
        for identity in ids:
            del data["rows"][identity]
        return len(ids), bool(ids)

    return _mutate(uid, char_id, change)
