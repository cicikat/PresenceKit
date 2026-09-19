"""Dry-run-first migration of the frozen shared toy archive into per-character self.

Historical ``data/very_formal_project/`` files are never copied to every
character and never claimed from the live active character. Ownership is a
one-time freeze of the configured default at first inventory, distinct from
later ``character.default`` / active switches. Unknown ownership stays
unclaimed.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from core.character_self import create_self, read_self
from core.data_paths import DEFAULT_CHAR_ID, safe_user_id
from core.safe_write import safe_write_json
from core.sandbox import get_paths

SCHEMA = "character-self-toy-migration.v1"
OWNER_SCHEMA = "character-self-legacy-toy-owner.v1"
SOURCE_RETENTION_DAYS = 90
ROLLBACK_NOTE = (
    "Rollback restores archived sources from the backup snapshot into "
    "very_formal_project/; it never overwrites a newer self file that already "
    "diverged after import."
)

_LEGACY_FILES: dict[str, str] = {
    "diary": "思考笔记.txt",
    "wishlist": "愿望清单.md",
    "doodle": "涂鸦板.txt",
}
_SELF_TARGETS: dict[str, str] = {
    "diary": "notes/思考笔记.txt",
    "wishlist": "notes/愿望清单.md",
    "doodle": "notes/涂鸦板.txt",
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str | None:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError:
        return None


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
    path = get_paths().legacy_toy_owner_record()
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


def freeze_legacy_toy_owner(*, char_id: str | None = None, uid: str | None = None) -> dict[str, Any]:
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
    path = get_paths().legacy_toy_owner_record()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not safe_write_json(path, record, keep_bak=False):
        return record
    return record


def historical_legacy_toy_char_id() -> str | None:
    owner = freeze_legacy_toy_owner()
    return owner.get("char_id") or None


def inventory_legacy_toys(*, char_id: str | None = None, uid: str | None = None) -> dict[str, Any]:
    """Dry-run inventory: ownership, hashes, targets, conflicts. Does not copy."""
    paths = get_paths()
    archive = paths.very_formal_project_dir()
    owner = freeze_legacy_toy_owner(char_id=char_id, uid=uid)
    claimed = bool(owner.get("char_id") and owner.get("uid"))
    files: list[dict[str, Any]] = []
    actions: dict[str, int] = {"import": 0, "skip": 0, "conflict": 0, "unclaimed": 0, "missing": 0}
    for key, filename in _LEGACY_FILES.items():
        source = archive / filename
        target = _SELF_TARGETS[key]
        item: dict[str, Any] = {
            "file_key": key,
            "source_name": filename,
            "target": target,
            "exists": source.is_file(),
            "sha256": None,
            "size": 0,
            "action": "missing",
        }
        if not source.is_file():
            actions["missing"] += 1
            files.append(item)
            continue
        try:
            data = source.read_bytes()
        except OSError:
            item["action"] = "missing"
            actions["missing"] += 1
            files.append(item)
            continue
        item["sha256"] = _sha256_bytes(data)
        item["size"] = len(data)
        try:
            source_text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            item["content_sha256"] = _sha256_bytes(source_text.encode("utf-8"))
        except UnicodeDecodeError:
            item["content_sha256"] = None
        if not claimed:
            item["action"] = "unclaimed"
            actions["unclaimed"] += 1
            files.append(item)
            continue
        existing = read_self(target, user_id=owner["uid"], char_id=owner["char_id"], origin="migration")
        if existing.get("ok"):
            dest_hash = _sha256_bytes(str(existing.get("content") or "").encode("utf-8"))
            if dest_hash == item.get("content_sha256"):
                item["action"] = "skip"
                actions["skip"] += 1
            else:
                item["action"] = "conflict"
                item["existing_revision"] = existing.get("revision")
                actions["conflict"] += 1
        elif existing.get("code") in {"path_not_found", "not_a_file"}:
            item["action"] = "import"
            actions["import"] += 1
        else:
            item["action"] = "conflict"
            item["code"] = existing.get("code")
            actions["conflict"] += 1
        files.append(item)

    autogrow_state = archive / ".autogrow_state.json"
    autogrow = {
        "exists": autogrow_state.is_file(),
        "sha256": _sha256_file(autogrow_state) if autogrow_state.is_file() else None,
        "note": "matching owner cooldown is migrated into scoped self metadata on apply",
    }
    report = {
        "schema": SCHEMA,
        "mode": "dry_run",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "owner": {
            "char_id": owner.get("char_id") or "",
            "uid": owner.get("uid") or "",
            "claimed": claimed,
            "frozen_at": owner.get("frozen_at"),
            "source": owner.get("source"),
        },
        "archive": "very_formal_project",
        "files": files,
        "actions": actions,
        "autogrow_state": autogrow,
        "source_retention_days": SOURCE_RETENTION_DAYS,
        "rollback": ROLLBACK_NOTE,
        "never_copy_to_all_characters": True,
        "never_guess_from_active": True,
    }
    _store_report(report)
    return report


def apply_legacy_toy_import(
    *,
    char_id: str | None = None,
    uid: str | None = None,
    backup_dir: Path | None = None,
) -> dict[str, Any]:
    """Re-entrant import. Existing newer self files are never overwritten."""
    plan = inventory_legacy_toys(char_id=char_id, uid=uid)
    owner = plan["owner"]
    if not owner.get("claimed"):
        plan["mode"] = "apply"
        plan["applied"] = False
        plan["error"] = "unclaimed"
        _store_report(plan)
        return plan
    archive = get_paths().very_formal_project_dir()
    backup_meta = _backup_archive(archive, backup_dir)
    results: list[dict[str, Any]] = []
    for item in plan["files"]:
        row = dict(item)
        if item["action"] not in {"import", "conflict"}:
            results.append(row)
            continue
        source = archive / item["source_name"]
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            row["action"] = "conflict"
            row["code"] = "unsupported_file_type"
            results.append(row)
            continue
        target = item["target"]
        if item["action"] == "conflict":
            target = _conflict_archive_target(item["target"], str(item.get("sha256") or ""))
            row["archive_target"] = target
        created = create_self(
            target,
            text,
            user_id=owner["uid"],
            char_id=owner["char_id"],
            origin="migration",
        )
        if created.get("ok"):
            row["imported"] = True
            row["revision"] = created.get("revision")
            if item["action"] == "conflict":
                row["action"] = "archive"
        elif created.get("code") == "already_exists":
            existing = read_self(
                target, user_id=owner["uid"], char_id=owner["char_id"], origin="migration",
            )
            dest_hash = _sha256_bytes(str(existing.get("content") or "").encode("utf-8"))
            source_hash = _sha256_bytes(text.encode("utf-8"))
            if dest_hash == source_hash:
                row["action"] = "archive" if item["action"] == "conflict" else "skip"
                row["imported"] = False
            else:
                row["action"] = "conflict"
                row["imported"] = False
                row["code"] = "already_exists"
        else:
            row["action"] = "conflict"
            row["imported"] = False
            row["code"] = created.get("code")
        results.append(row)
    autogrow = _migrate_autogrow_state(archive, owner)
    applied = {
        **plan,
        "mode": "apply",
        "applied": True,
        "files": results,
        "backup": backup_meta,
        "actions": _recount(results),
        "autogrow_state": autogrow,
    }
    _store_report(applied)
    return applied


def rollback_legacy_toy_import(backup_dir: Path, *, overwrite_newer: bool = False) -> dict[str, Any]:
    """Restore archived sources. Refuses to clobber a newer self file."""
    backup = Path(backup_dir)
    archive = get_paths().very_formal_project_dir()
    restored: list[str] = []
    if not backup.is_dir():
        return {"ok": False, "error": "backup_missing", "rollback": ROLLBACK_NOTE}
    archive.mkdir(parents=True, exist_ok=True)
    for filename in list(_LEGACY_FILES.values()) + [".autogrow_state.json"]:
        src = backup / filename
        dest = archive / filename
        if not src.is_file():
            continue
        shutil.copy2(src, dest)
        restored.append(filename)
    owner = _read_frozen_owner() or {}
    self_skipped: list[str] = []
    if owner.get("uid") and owner.get("char_id"):
        for _key, target in _SELF_TARGETS.items():
            existing = read_self(target, user_id=owner["uid"], char_id=owner["char_id"], origin="migration")
            if existing.get("ok"):
                self_skipped.append(target)
    report = {
        "ok": True,
        "restored_archive": restored,
        "self_files_left_in_place": self_skipped,
        "overwrite_newer": False,
        "rollback": ROLLBACK_NOTE,
    }
    return report


def _backup_archive(archive: Path, backup_dir: Path | None) -> dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = Path(backup_dir) if backup_dir is not None else archive.parent / f"very_formal_project_backup_{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    if archive.is_dir():
        for child in archive.iterdir():
            if child.is_file():
                shutil.copy2(child, dest / child.name)
                copied.append(child.name)
    return {"dir_name": dest.name, "files": copied, "created_at": stamp}


def _conflict_archive_target(target: str, sha256: str) -> str:
    path = PurePosixPath(target)
    suffix = path.suffix
    stem = path.name[:-len(suffix)] if suffix else path.name
    return str(PurePosixPath("notes", "legacy", f"{stem}.legacy-{sha256[:12]}{suffix}"))


def _migrate_autogrow_state(archive: Path, owner: dict[str, Any]) -> dict[str, Any]:
    source = archive / ".autogrow_state.json"
    result: dict[str, Any] = {
        "exists": source.is_file(),
        "sha256": _sha256_file(source) if source.is_file() else None,
        "action": "missing",
    }
    if not source.is_file():
        return result
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
        timestamp = float(raw[f"{owner['char_id']}:{owner['uid']}"])
    except (OSError, UnicodeDecodeError, ValueError, TypeError, KeyError):
        result["action"] = "archive_only"
        return result
    target = get_paths().character_self_meta_root(
        owner["uid"], char_id=owner["char_id"],
    ) / "toy_autogrow_state.json"
    current = 0.0
    try:
        current = float(json.loads(target.read_text(encoding="utf-8")).get("last_written_at") or 0)
    except (OSError, UnicodeDecodeError, ValueError, TypeError, AttributeError):
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    if safe_write_json(target, {"last_written_at": max(current, timestamp)}):
        result["action"] = "import"
    else:
        result["action"] = "conflict"
    return result


def _recount(files: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"import": 0, "archive": 0, "skip": 0, "conflict": 0, "unclaimed": 0, "missing": 0}
    for item in files:
        action = str(item.get("action") or "missing")
        if action not in counts:
            action = "conflict"
        counts[action] += 1
    return counts


def _store_report(report: dict[str, Any]) -> None:
    path = get_paths().legacy_toy_migration_report()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(report)
    payload["stored_at"] = time.time()
    safe_write_json(path, payload, keep_bak=False)


def observability_projection() -> dict[str, Any]:
    """Metadata-only migration status. No note bodies or absolute paths."""
    owner = _read_frozen_owner() or {}
    report_path = get_paths().legacy_toy_migration_report()
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
        "legacy_toy": {
            "owner_claimed": bool(owner.get("char_id") and owner.get("uid")),
            "owner_char_id": owner.get("char_id") or "",
            "frozen_at": owner.get("frozen_at") or "",
            "report": report,
            "source_retention_days": SOURCE_RETENTION_DAYS,
            "rollback": ROLLBACK_NOTE,
        }
    }
