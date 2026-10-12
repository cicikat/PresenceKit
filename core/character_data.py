"""Character data inventory and a portable authored trait configuration."""
from __future__ import annotations

import json
import yaml

from core.sandbox import get_paths
from core.data_paths import DEFAULT_CHAR_ID, safe_user_id
from core.safe_write import safe_write_text


def validate_traits(value: object) -> list[dict]:
    if not isinstance(value, list) or len(value) > 128:
        raise ValueError("traits must be a list of at most 128 entries")
    seen = set()
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise ValueError("each trait needs an id")
        if item["id"] in seen:
            raise ValueError("duplicate trait id")
        seen.add(item["id"])
        words = item.get("keywords", [])
        if not isinstance(words, list) or any(not isinstance(word, str) or not word.strip() for word in words):
            raise ValueError("trait keywords must be nonempty strings")
    if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > 65536:
        raise ValueError("trait definition exceeds 64 KiB")
    return value


def load_traits(char_id: str) -> list[dict]:
    char_id = safe_user_id(char_id)
    path = get_paths().yexuan_traits(char_id=char_id)
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError("invalid trait YAML") from exc
    if not isinstance(data, dict):
        raise ValueError("trait definition must be a mapping")
    value = data.get("traits", data.get(f"{char_id}_traits"))
    if value is None:
        # Owned legacy default files may carry an old schema key. The path is
        # already scoped; no other character's file is used as a fallback.
        legacy = [v for key, v in data.items() if str(key).endswith("_traits")]
        if char_id == DEFAULT_CHAR_ID and len(legacy) == 1:
            value = legacy[0]
        else:
            raise ValueError("traits schema missing")
    return validate_traits(value)


def save_traits(char_id: str, traits: list[dict]) -> None:
    path = get_paths().user_authored_character_dir(char_id=safe_user_id(char_id)) / "traits.yaml"
    value = validate_traits(traits)
    if not safe_write_text(path, yaml.safe_dump({"traits": value}, allow_unicode=True, sort_keys=False)):
        raise OSError("trait save failed")


def inventory(char_id: str) -> dict:
    char_id = safe_user_id(char_id)
    paths = get_paths()
    entries = []
    # The cleanup contract enumerates scoped ownership, including self, memory,
    # authored data, dream records and activity records. Never traverse a
    # user-selected physical path or return absolute installation paths.
    for name, path in paths.character_cleanup_runtime_roots(char_id=char_id).items():
        entries.append({"id": name, "present": path.exists(), "kind": "directory" if path.is_dir() else "file"})
    for name, path in {
        "authored_assets": paths.user_authored_character_dir(char_id=char_id),
        "traits": paths.yexuan_traits(char_id=char_id),
        "trait_state": paths.trait_state(char_id=char_id),
        "author_notes": paths.author_notes_pool(char_id=char_id),
        "observations": paths.observations(char_id=char_id),
        "user_relations": paths.character_user_relations(char_id=char_id),
        "lorebook": paths.lorebook(char_id=char_id),
        "jailbreak_entries": paths.jailbreak_entries(char_id=char_id),
        "life_records": paths.life_records_db(char_id=char_id),
        "group_context": paths.group_context(char_id=char_id),
    }.items():
        entries.append({"id": name, "present": path.exists(), "kind": "directory" if name == "group_context" else "file"})
    root = paths.memory_char_root(char_id=char_id)
    users = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []
    from core.prompt_asset_selection import for_character
    return {"char_id": char_id, "entries": entries, "users": users,
            "prompt_bindings": for_character(char_id),
            "user_artifacts": ["history", "profile", "identity", "user_facts", "health_state", "episodic", "mid_term", "event_store", "relationship_facts", "memory_dossiers", "reminders", "self", "hidden_state", "afterglow_residue"]}
