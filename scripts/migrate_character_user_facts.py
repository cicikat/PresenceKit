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
    parser.add_argument("--uid", required=True)
    parser.add_argument("--char-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--artifact", choices=["user_facts", "health_state"], default="user_facts")
    args = parser.parse_args()
    print(json.dumps(migrate(args.uid, args.char_id, apply=args.apply, artifact=args.artifact), ensure_ascii=False))
