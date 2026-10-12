"""Explicit assignment of historical shared user facts; dry-run by default.

Original evidence is preserved. No active-character inference or overwrite.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def migrate(uid: str, char_id: str, *, apply: bool = False, artifact: str = "user_facts") -> dict:
    from core.sandbox import get_paths
    from core.memory import user_facts
    paths = get_paths()
    if artifact == "life_records":
        import os
        import sqlite3
        import uuid
        from contextlib import closing
        source = paths.legacy_life_records_db()
        target = paths.life_records_db(char_id=char_id)
        if not source.exists():
            return {"status": "source_absent", "applied": False}
        if target.exists():
            return {"status": "target_conflict", "applied": False}
        if apply:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".migration-{uuid.uuid4().hex}.sqlite3")
            try:
                with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as old:
                    with closing(sqlite3.connect(temporary)) as new:
                        old.backup(new)
                os.link(temporary, target)  # Atomic publication without overwriting.
            finally:
                temporary.unlink(missing_ok=True)
        return {"status": "imported" if apply else "dry_run", "applied": apply, "original_preserved": True}
    if artifact in {"lorebook", "jailbreak_entries"}:
        import yaml
        from core.safe_write import safe_write_text
        source = paths.legacy_prompt_entries(artifact)
        target = paths.lorebook(char_id=char_id) if artifact == "lorebook" else paths.jailbreak_entries(char_id=char_id)
        if not source.exists():
            return {"status": "source_absent", "applied": False}
        raw = source.read_text(encoding="utf-8")
        value = yaml.safe_load(raw) if artifact == "lorebook" else json.loads(raw)
        if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
            raise ValueError("legacy prompt entries must be a mapping with entries")
        if target.exists():
            old = target.read_text(encoding="utf-8")
            existing = yaml.safe_load(old) if artifact == "lorebook" else json.loads(old)
            return {"status": "already_imported" if existing == value else "target_conflict", "applied": False}
        if apply and not safe_write_text(target, raw):
            raise OSError("prompt migration write failed")
        return {"status": "imported" if apply else "dry_run", "applied": apply, "original_preserved": True}
    from core.memory import health_state
    if artifact not in {"user_facts", "health_state"}:
        raise ValueError("unsupported artifact")
    source = paths.legacy_user_facts(uid) if artifact == "user_facts" else paths.legacy_health_state(uid)
    target = paths.user_memory_root(uid, char_id=char_id) / f"{artifact}.json"
    if not source.exists():
        return {"status": "source_absent", "applied": False}
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("legacy facts must be a mapping")
    clean = ({key: item for key, item in value.items() if key in user_facts.ALLOWED_FIELDS}
             if artifact == "user_facts" else health_state._normalized_state(value))
    if target.exists():
        same = (user_facts.load_user_facts(uid, char_id=char_id) if artifact == "user_facts"
                else health_state.load(uid, char_id=char_id)) == clean
        return {"status": "already_imported" if same else "target_conflict", "applied": False}
    if apply:
        saved = (user_facts.save_user_facts(uid, clean, char_id=char_id, trigger_signal="explicit_legacy_assignment")
                 if artifact == "user_facts" else health_state.save(uid, clean, char_id=char_id))
        if not saved:
            raise OSError("migration write failed")
    return {"status": "imported" if apply else "dry_run", "applied": apply, "field_count": len(clean), "original_preserved": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uid", default="")
    parser.add_argument("--char-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--artifact", choices=["user_facts", "health_state", "lorebook", "jailbreak_entries", "life_records"], default="user_facts")
    args = parser.parse_args()
    if args.artifact in {"user_facts", "health_state"} and not args.uid:
        parser.error("--uid is required for user data")
    print(json.dumps(migrate(args.uid, args.char_id, apply=args.apply, artifact=args.artifact), ensure_ascii=False))
