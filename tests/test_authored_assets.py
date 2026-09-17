"""
tests/test_authored_assets.py

Existence + loadability checks for authored assets that are tracked in git.
All files listed here must be present and parseable on a clean checkout.

If this test fails, someone deleted or corrupted a tracked authored asset.
Fix: git checkout -- <path>  or restore from backup.
"""

import json
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).parent.parent


# ── Files that must exist (tracked in git) ────────────────────────────────────

TRACKED_JSON = [
    "bundled/seeds/reality/jailbreak_entries.json",
    "bundled/templates/character_template.json",
]

TRACKED_YAML = [
    "bundled/seeds/reality/lorebook.yaml",
    "bundled/seeds/reality/relations.yaml",
    "bundled/seeds/reality/blacklist.yaml",
]

TRACKED_TEXT = [
    "bundled/seeds/dream/worlds/_default/ruleset.md",
    "bundled/seeds/dream/worlds/_default/mes_example.md",
    "bundled/seeds/dream/presets/default.md",
]


@pytest.mark.parametrize("rel", TRACKED_JSON)
def test_tracked_json_exists_and_parses(rel: str):
    path = _ROOT / rel
    assert path.exists(), f"tracked authored asset missing: {rel} — run: git checkout -- {rel}"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{rel} must be a JSON object, got {type(data).__name__}"


@pytest.mark.parametrize("rel", TRACKED_YAML)
def test_tracked_yaml_exists_and_parses(rel: str):
    path = _ROOT / rel
    assert path.exists(), f"tracked authored asset missing: {rel} — run: git checkout -- {rel}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    # yaml.safe_load returns None for empty files; treat as valid (empty seed)
    assert data is None or isinstance(data, dict), (
        f"{rel} must parse as a YAML mapping or empty, got {type(data).__name__}"
    )


@pytest.mark.parametrize("rel", TRACKED_TEXT)
def test_tracked_text_exists_and_nonempty(rel: str):
    path = _ROOT / rel
    assert path.exists(), f"tracked authored asset missing: {rel} — run: git checkout -- {rel}"
    assert path.read_text(encoding="utf-8").strip(), f"{rel} must not be empty"


# ── Structural checks ─────────────────────────────────────────────────────────

def test_jailbreak_entries_schema():
    """Public jailbreak seed must have an 'entries' list."""
    path = _ROOT / "bundled/seeds/reality/jailbreak_entries.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "entries" in data, "jailbreak_entries.json must have top-level 'entries' key"
    assert isinstance(data["entries"], list)


def test_lorebook_schema():
    """Public lorebook seed must have an 'entries' list."""
    path = _ROOT / "bundled/seeds/reality/lorebook.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert "entries" in data, "lorebook.yaml must have top-level 'entries' key"
    assert isinstance(data["entries"], list)


def test_character_template_has_name_field():
    """The bundled character template must have a 'name' field."""
    path = _ROOT / "bundled/templates/character_template.json"
    assert path.exists(), "character_template.json missing from bundled/templates/"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "name" in data, "character_template.json must have a 'name' field"


def test_legacy_public_roots_are_absent_from_a_clean_checkout():
    assert not any((_ROOT / root).exists() for root in ("characters", "content", "defaults", "examples"))
