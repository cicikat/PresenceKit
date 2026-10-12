"""Character-owned prompt bindings over the installation asset catalog."""
from __future__ import annotations

import json
from threading import RLock

from core.data_paths import safe_user_id
from core.sandbox import get_paths
from core.safe_write import safe_write_json

selection_lock = RLock()
FIELDS = ("enabled_lorebooks", "enabled_jailbreaks", "active_dream_preset")


def read_document() -> dict:
    value = json.loads(get_paths().active_prompt_assets().read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("prompt asset selection must be a mapping")
    return value


def active_character() -> str:
    value = read_document().get("active_character")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("active character is not configured")
    return safe_user_id(value)


def for_character(char_id: str, document: dict | None = None) -> dict:
    char_id = safe_user_id(char_id)
    document = read_document() if document is None else document
    bindings = document.get("by_character") or {}
    value = bindings.get(char_id) if isinstance(bindings, dict) else None
    if not isinstance(value, dict):
        # A legacy selection has an explicit active-character owner. It is
        # never inherited by any other character.
        value = document if document.get("active_character") == char_id else {}
    return {"enabled_lorebooks": list(value.get("enabled_lorebooks") or []),
            "enabled_jailbreaks": list(value.get("enabled_jailbreaks") or []),
            "active_dream_preset": value.get("active_dream_preset")}


def update(document: dict, patch: dict) -> dict:
    previous = document.get("active_character")
    target = safe_user_id(patch.get("active_character") or previous)
    bindings = dict(document.get("by_character") or {})
    if previous:
        bindings[previous] = for_character(previous, document)
    selection = for_character(target, {**document, "by_character": bindings})
    for field in FIELDS:
        if field in patch and patch[field] is not None:
            selection[field] = patch[field]
    bindings[target] = selection
    result = {**document, "active_character": target, "by_character": bindings, **selection}
    if not safe_write_json(get_paths().active_prompt_assets(), result):
        raise OSError("prompt asset selection save failed")
    return result
