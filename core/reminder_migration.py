"""Dry-run-first import of leftover reminder JSON into Runtime schedules.

Historical uid-only leftovers are never copied to every character and never
claimed from the live active character. Ownership is a one-time freeze of the
configured default at first inventory. Completed/cancelled items are not
rescheduled. Newer Runtime rows are never overwritten.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.agent_runtime.models import CausationRef, TaskPrincipal
from core.agent_runtime.scheduler_capability import create_schedule, import_index
from core.data_paths import DEFAULT_CHAR_ID, safe_user_id
from core.safe_write import safe_write_json
from core.sandbox import get_paths

SCHEMA = "character-reminder-migration.v1"
OWNER_SCHEMA = "character-reminder-legacy-owner.v1"
SOURCE_RETENTION_DAYS = 90
ROLLBACK_NOTE = (
    "Rollback restores archived leftover reminder JSON from the backup snapshot; "
    "it never overwrites a newer Runtime schedule that already diverged after import."
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _configured_default_char_id() -> str:
    from core.config_loader import get_config
    default = str((get_config().get("character") or {}).get("default") or "").strip()
    return default or DEFAULT_CHAR_ID


def _configured_owner_uid() -> str | None:
    from core.config_loader import get_config
    cfg = get_config()
    for key in ("owner_id", "user_id"):
        value = str(cfg.get(key) or "").strip()
        if value:
            try:
                return safe_user_id(value)
            except ValueError:
                return None
    qq = cfg.get("qq") or {}
    value = str(qq.get("owner_id") or qq.get("user_id") or "").strip()
    if value:
        try:
            return safe_user_id(value)
        except ValueError:
            return None
    return None


def _read_frozen_owner() -> dict[str, Any] | None:
    path = get_paths().legacy_reminder_owner_record()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("schema") != OWNER_SCHEMA:
        return None
    char_id = str(raw.get("char_id") or "").strip()
    uid = str(raw.get("uid") or "").strip()
    if not char_id:
        return None
    return {"char_id": char_id, "uid": uid, "frozen_at": raw.get("frozen_at"), "source": raw.get("source")}


def freeze_legacy_reminder_owner(*, char_id: str | None = None, uid: str | None = None) -> dict[str, Any]:
    """Persist historical ownership once. Never reassigns on later character switches."""
    existing = _read_frozen_owner()
    if existing:
        return existing
    cid = str(char_id or "").strip() or _configured_default_char_id()
    owner_uid = str(uid or "").strip() or (_configured_owner_uid() or "")
    if owner_uid:
        try:
            owner_uid = safe_user_id(owner_uid)
        except ValueError:
            owner_uid = ""
    record = {
        "schema": OWNER_SCHEMA,
        "char_id": cid,
        "uid": owner_uid,
        "frozen_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "configured_default_at_first_inventory",
    }
    path = get_paths().legacy_reminder_owner_record()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not safe_write_json(path, record, keep_bak=False):
        return record
    return record


def historical_legacy_reminder_char_id() -> str | None:
    owner = freeze_legacy_reminder_owner()
    return owner.get("char_id") or None


def _legacy_file(uid: str, char_id: str) -> Path:
    return get_paths().user_memory_root(uid, char_id=char_id) / "reminders.json"


def _legacy_candidates(uid: str, char_id: str) -> list[Path]:
    paths = [_legacy_file(uid, char_id)]
    # Historical uid-only leftovers sat next to the memory root before S6.
    uid_only = get_paths()._p("runtime", "memory", safe_user_id(uid), "reminders.json")
    if uid_only not in paths:
        paths.append(uid_only)
    return paths


def _parse_due(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return None


def _load_legacy_items(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return []
    return raw if isinstance(raw, list) else []


def inventory_legacy_reminders(*, char_id: str | None = None, uid: str | None = None) -> dict[str, Any]:
    """Dry-run inventory: ownership, hashes, targets, conflicts. Does not import."""
    owner = freeze_legacy_reminder_owner(char_id=char_id, uid=uid)
    claimed = bool(owner.get("char_id") and owner.get("uid"))
    items: list[dict[str, Any]] = []
    actions = {"import": 0, "skip": 0, "conflict": 0, "unclaimed": 0, "missing": 0, "archive": 0}
    sources: list[dict[str, Any]] = []
    if not claimed:
        report = {
            "schema": SCHEMA,
            "mode": "dry_run",
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "owner": {
                "char_id": owner.get("char_id") or "",
                "uid": owner.get("uid") or "",
                "claimed": False,
                "frozen_at": owner.get("frozen_at"),
                "source": owner.get("source"),
            },
            "items": items,
            "actions": {**actions, "unclaimed": 1},
            "sources": sources,
            "source_retention_days": SOURCE_RETENTION_DAYS,
            "rollback": ROLLBACK_NOTE,
            "never_copy_to_all_characters": True,
            "never_guess_from_active": True,
            "no_dual_authority_fallback": True,
        }
        _store_report(report)
        return report

    principal = TaskPrincipal.reality(owner["uid"], owner["char_id"])
    existing_legacy, existing_keys = import_index(principal)
    seen_ids: set[str] = set()
    for path in _legacy_candidates(owner["uid"], owner["char_id"]):
        payload = _load_legacy_items(path)
        sources.append({
            "exists": path.is_file(),
            "count": len(payload),
            "sha256": _sha256_bytes(path.read_bytes()) if path.is_file() else None,
        })
        if not payload:
            if not path.is_file():
                actions["missing"] += 1
            continue
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            legacy_id = str(raw.get("id") or raw.get("schedule_id") or "")
            content = str(raw.get("content") or "").strip()
            due_at = _parse_due(raw.get("remind_at") or raw.get("due_at"))
            done = bool(raw.get("done") or raw.get("canceled") or raw.get("cancelled"))
            item = {
                "legacy_id": legacy_id,
                "content_chars": len(content),
                "due_at": due_at,
                "done": done,
                "action": "missing",
            }
            if not content or due_at is None:
                item["action"] = "archive"
                actions["archive"] += 1
                items.append(item)
                continue
            if done:
                item["action"] = "archive"
                actions["archive"] += 1
                items.append(item)
                continue
            key = (content, float(due_at))
            if legacy_id and (legacy_id in seen_ids or legacy_id in existing_legacy):
                item["action"] = "skip"
                actions["skip"] += 1
            elif key in existing_keys:
                item["action"] = "skip"
                actions["skip"] += 1
            else:
                item["action"] = "import"
                item["content"] = content
                actions["import"] += 1
            if legacy_id:
                seen_ids.add(legacy_id)
            items.append(item)

    report = {
        "schema": SCHEMA,
        "mode": "dry_run",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "owner": {
            "char_id": owner.get("char_id") or "",
            "uid": owner.get("uid") or "",
            "claimed": True,
            "frozen_at": owner.get("frozen_at"),
            "source": owner.get("source"),
        },
        "items": [
            {k: v for k, v in item.items() if k != "content"}
            for item in items
        ],
        "actions": actions,
        "sources": [{"exists": s["exists"], "count": s["count"]} for s in sources],
        "source_retention_days": SOURCE_RETENTION_DAYS,
        "rollback": ROLLBACK_NOTE,
        "never_copy_to_all_characters": True,
        "never_guess_from_active": True,
        "no_dual_authority_fallback": True,
        "_import_payload": [item for item in items if item.get("action") == "import"],
    }
    public = {k: v for k, v in report.items() if k != "_import_payload"}
    _store_report(public)
    return report


def apply_legacy_reminder_import(
    *,
    char_id: str | None = None,
    uid: str | None = None,
    backup_dir: Path | None = None,
) -> dict[str, Any]:
    """Re-entrant import. Existing newer Runtime rows are never overwritten."""
    plan = inventory_legacy_reminders(char_id=char_id, uid=uid)
    owner = plan["owner"]
    if not owner.get("claimed"):
        plan["mode"] = "apply"
        plan["applied"] = False
        plan["error"] = "unclaimed"
        _store_report({k: v for k, v in plan.items() if not str(k).startswith("_")})
        return {k: v for k, v in plan.items() if not str(k).startswith("_")}
    backup_meta = _backup_sources(owner["uid"], owner["char_id"], backup_dir)
    principal = TaskPrincipal.reality(owner["uid"], owner["char_id"])
    results: list[dict[str, Any]] = []
    for item in plan.get("_import_payload") or []:
        row = {k: v for k, v in item.items() if k != "content"}
        try:
            created = create_schedule(
                principal,
                content=str(item.get("content") or ""),
                due_at=float(item["due_at"]),
                legacy_id=str(item.get("legacy_id") or "") or None,
                idempotency_key=f"legacy-reminder:{owner['uid']}:{item.get('legacy_id') or item.get('due_at')}",
                causation_ref=CausationRef("admin_action", f"legacy-reminder:{item.get('legacy_id') or 'anon'}"),
            )
            row["imported"] = bool(created.get("ok"))
            row["schedule_id"] = created.get("schedule_id")
            row["revision"] = created.get("revision")
            if created.get("created") is False:
                row["action"] = "skip"
        except Exception as exc:
            row["imported"] = False
            row["action"] = "conflict"
            row["code"] = getattr(exc, "code", "quota_exhausted")
        results.append(row)
    public_items = list(plan.get("items") or [])
    imported_ids = {str(item.get("legacy_id") or "") for item in results}
    merged = []
    for item in public_items:
        if item.get("action") == "import" and str(item.get("legacy_id") or "") in imported_ids:
            match = next(
                (row for row in results if str(row.get("legacy_id") or "") == str(item.get("legacy_id") or "")),
                item,
            )
            merged.append(match)
        else:
            merged.append(item)
    applied = {
        **{k: v for k, v in plan.items() if not str(k).startswith("_")},
        "mode": "apply",
        "applied": True,
        "items": merged,
        "backup": backup_meta,
        "actions": _recount(merged),
    }
    _store_report(applied)
    return applied


def rollback_legacy_reminder_import(backup_dir: Path, *, overwrite_newer: bool = False) -> dict[str, Any]:
    """Restore leftover JSON archives. Refuses to clobber newer Runtime schedules."""
    backup = Path(backup_dir)
    if not backup.is_dir():
        return {"ok": False, "error": "backup_missing", "rollback": ROLLBACK_NOTE, "overwrite_newer": False}
    owner = _read_frozen_owner() or {}
    restored: list[str] = []
    if owner.get("uid") and owner.get("char_id"):
        dest = _legacy_file(owner["uid"], owner["char_id"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = backup / "reminders.json"
        if src.is_file():
            shutil.copy2(src, dest)
            restored.append("reminders.json")
    return {
        "ok": True,
        "restored_archive": restored,
        "runtime_schedules_left_in_place": True,
        "overwrite_newer": False,
        "rollback": ROLLBACK_NOTE,
    }


def _backup_sources(uid: str, char_id: str, backup_dir: Path | None) -> dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = Path(backup_dir) if backup_dir is not None else (
        get_paths().agent_runtime_schedules_root().parent / f"legacy_reminder_backup_{stamp}"
    )
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for path in _legacy_candidates(uid, char_id):
        if path.is_file():
            target = dest / "reminders.json"
            shutil.copy2(path, target)
            copied.append("reminders.json")
            break
    return {"dir_name": dest.name, "files": copied, "created_at": stamp}


def _recount(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"import": 0, "skip": 0, "conflict": 0, "unclaimed": 0, "missing": 0, "archive": 0}
    for item in items:
        action = str(item.get("action") or "missing")
        if action not in counts:
            action = "conflict"
        counts[action] += 1
    return counts


def _store_report(report: dict[str, Any]) -> None:
    path = get_paths().legacy_reminder_migration_report()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(report)
    payload["stored_at"] = time.time()
    safe_write_json(path, payload, keep_bak=False)


def observability_projection() -> dict[str, Any]:
    """Metadata-only migration status. No reminder bodies or absolute paths."""
    owner = _read_frozen_owner() or {}
    report_path = get_paths().legacy_reminder_migration_report()
    report: dict[str, Any] = {}
    if report_path.exists():
        try:
            raw = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                report = {
                    "mode": raw.get("mode"),
                    "applied": raw.get("applied"),
                    "actions": raw.get("actions"),
                    "source_retention_days": raw.get("source_retention_days"),
                    "generated_at": raw.get("generated_at"),
                }
        except (OSError, UnicodeDecodeError, ValueError):
            report = {"status": "corrupt"}
    return {
        "legacy_reminder": {
            "owner_claimed": bool(owner.get("char_id") and owner.get("uid")),
            "owner_char_id": owner.get("char_id") or "",
            "frozen_at": owner.get("frozen_at") or "",
            "report": report,
            "source_retention_days": SOURCE_RETENTION_DAYS,
            "rollback": ROLLBACK_NOTE,
        }
    }
