"""
tests/test_asset_registry.py

Unit tests for core/asset_registry.py — P1 asset id/label/filename separation.

Tests cover:
1. yexuan id resolves to yexuan.json with label Companion
2. active config stores id, not label/filename
3. PATCH with label/filename is rejected
4. hidden/template/example assets excluded from UI list
5. unknown asset id fails loud
6. legacy config "Companion" / "Companion.json" / "yexuan.json" normalizes to "yexuan"
7. dream preset entries: ASCII ids visible; Chinese filenames get stable ASCII ids
8. dream preset legacy normalization: Chinese filename/label → ASCII id
"""

import json
import shutil
from pathlib import Path

import pytest

from core.asset_registry import AssetRegistry, AssetEntry


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_characters(tmp_path):
    """Create a minimal characters/ tree for registry scanning."""
    chars = tmp_path / "characters"
    chars.mkdir()

    # Real character card
    (chars / "yexuan.json").write_text(
        json.dumps({"name": "Companion", "description": "test"}), encoding="utf-8"
    )

    # Template / example — should be hidden
    (chars / "character_template.json").write_text(
        json.dumps({"name": "模板"}), encoding="utf-8"
    )
    (chars / "example_char.json").write_text(
        json.dumps({"name": "示例"}), encoding="utf-8"
    )

    # Lorebooks
    lb = chars / "reality" / "lorebooks"
    lb.mkdir(parents=True)
    (lb / "base.yaml").write_text("entries: []", encoding="utf-8")
    (lb / "relationship.yaml").write_text("entries: []", encoding="utf-8")
    (lb / "template_lb.yaml").write_text("entries: []", encoding="utf-8")  # hidden

    # Jailbreaks
    jb = chars / "reality" / "jailbreaks"
    jb.mkdir(parents=True)
    (jb / "base.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    (jb / "anti_assistant.json").write_text(json.dumps({"entries": []}), encoding="utf-8")

    # Dream presets
    dp = chars / "dream_presets"
    dp.mkdir()
    (dp / "default.md").write_text("default preset", encoding="utf-8")
    (dp / "custom.md").write_text("custom preset", encoding="utf-8")
    (dp / "审讯.md").write_text("interrogation", encoding="utf-8")   # non-ASCII → id: interrogation
    (dp / "多p.md").write_text("multi", encoding="utf-8")             # non-ASCII → id: multi
    (dp / "未知preset.md").write_text("unknown", encoding="utf-8")    # non-ASCII, no mapping → hidden

    return tmp_path


@pytest.fixture
def registry(fake_characters, monkeypatch):
    """Asset registry scanning fake_characters tree."""
    import core.asset_registry as _mod
    monkeypatch.chdir(fake_characters)
    reg = AssetRegistry()
    return reg


# ── 1. yexuan id resolves correctly ───────────────────────────────────────────

def test_yexuan_resolves_to_filename_and_label(registry):
    entry = registry.resolve("yexuan", "character")
    assert entry.filename == "yexuan.json"
    assert entry.label == "Companion"
    assert entry.id == "yexuan"
    assert entry.kind == "character"
    assert not entry.hidden


def test_yexuan_path(registry, fake_characters):
    entry = registry.resolve("yexuan", "character")
    assert entry.path() == Path("characters") / "yexuan.json"


# ── 2. Active config stores id, not label/filename ────────────────────────────

def test_active_config_uses_id(tmp_path):
    """active_prompt_assets.json default generator stores id 'yexuan', not filename."""
    import core.data_paths as _dp
    import importlib
    # Re-read the module to get the default generation code
    paths = _dp.DataPaths(mode="test", test_session_id="test_asset_id_default")
    paths._base = tmp_path
    p = paths.active_prompt_assets()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["active_character"] == "yexuan", (
        "active_prompt_assets.json:active_character must store id 'yexuan', not a filename"
    )
    assert "." not in data["active_character"], (
        "active_character must not contain a dot (would indicate filename, not id)"
    )


# ── 3. PATCH with label/filename is rejected ─────────────────────────────────

def test_validate_id_rejects_filename_with_extension(registry, monkeypatch):
    """Submitting 'yexuan.json' to PATCH should raise HTTPException (dot detected)."""
    from fastapi import HTTPException
    from admin.routers.settings_prompt_assets import _validate_id
    import core.asset_registry as _mod
    monkeypatch.setattr(_mod, "_registry", registry)

    with pytest.raises(HTTPException) as exc:
        _validate_id("yexuan.json", "character", "active_character")
    assert exc.value.status_code == 422
    assert "filename" in exc.value.detail.lower() or "扩展名" in exc.value.detail


def test_validate_id_rejects_path_separator(registry, monkeypatch):
    from fastapi import HTTPException
    from admin.routers.settings_prompt_assets import _validate_id
    import core.asset_registry as _mod
    monkeypatch.setattr(_mod, "_registry", registry)

    with pytest.raises(HTTPException):
        _validate_id("characters/yexuan", "character", "active_character")


def test_validate_id_accepts_known_id(registry, monkeypatch):
    from admin.routers.settings_prompt_assets import _validate_id
    import core.asset_registry as _mod
    monkeypatch.setattr(_mod, "_registry", registry)

    # Should not raise
    _validate_id("yexuan", "character", "active_character")


def test_validate_id_rejects_unknown_id(registry, monkeypatch):
    from fastapi import HTTPException
    from admin.routers.settings_prompt_assets import _validate_id
    import core.asset_registry as _mod
    monkeypatch.setattr(_mod, "_registry", registry)

    with pytest.raises(HTTPException) as exc:
        _validate_id("nonexistent", "character", "active_character")
    assert exc.value.status_code == 422


# ── 4. Hidden/template/example excluded from UI list ─────────────────────────

def test_template_character_is_hidden(registry):
    entry = registry.resolve("character_template", "character")
    assert entry.hidden, "character_template.json must be hidden"


def test_example_character_is_hidden(registry):
    entry = registry.resolve("example_char", "character")
    assert entry.hidden, "example_char.json must be hidden"


def test_list_ui_excludes_hidden_characters(registry):
    visible = [e.id for e in registry.list_ui("character")]
    assert "yexuan" in visible
    assert "character_template" not in visible
    assert "example_char" not in visible


def test_template_lorebook_is_hidden(registry):
    entry = registry.resolve("template_lb", "reality_lorebook")
    assert entry.hidden


def test_list_ui_lorebooks_no_template(registry):
    visible = [e.id for e in registry.list_ui("reality_lorebook")]
    assert "base" in visible
    assert "relationship" in visible
    assert "template_lb" not in visible


# ── 5. Unknown asset id fails loud ───────────────────────────────────────────

def test_resolve_unknown_character_raises(registry):
    with pytest.raises(ValueError, match="unknown character asset id"):
        registry.resolve("does_not_exist", "character")


def test_resolve_unknown_lorebook_raises(registry):
    with pytest.raises(ValueError):
        registry.resolve("does_not_exist", "reality_lorebook")


# ── 6. Legacy config normalization ───────────────────────────────────────────

def test_normalize_legacy_id_passthrough(registry):
    assert registry.normalize_legacy("yexuan", "character") == "yexuan"


def test_normalize_legacy_filename_to_id(registry):
    assert registry.normalize_legacy("yexuan.json", "character") == "yexuan"


def test_normalize_legacy_chinese_label_to_id(registry):
    assert registry.normalize_legacy("Companion", "character") == "yexuan"


def test_normalize_legacy_chinese_label_with_extension(registry):
    assert registry.normalize_legacy("Companion.json", "character") == "yexuan"


# ── 7. Dream preset: ASCII ids; Chinese filenames get stable ASCII ids ────────

def test_dream_preset_default_visible(registry):
    entry = registry.resolve("default", "dream_preset")
    assert not entry.hidden


def test_dream_preset_interrogation_resolves(registry):
    """审讯.md is accessible via stable ASCII id 'interrogation', not hidden."""
    entry = registry.resolve("interrogation", "dream_preset")
    assert entry.filename == "审讯.md"
    assert entry.label == "审讯"
    assert entry.id == "interrogation"
    assert not entry.hidden


def test_dream_preset_multi_resolves(registry):
    """多p.md is accessible via stable ASCII id 'multi', not hidden."""
    entry = registry.resolve("multi", "dream_preset")
    assert entry.filename == "多p.md"
    assert entry.label == "多p"
    assert entry.id == "multi"
    assert not entry.hidden


def test_dream_preset_chinese_stem_not_a_valid_id(registry):
    """Chinese stem '审讯' is not registered as an id — only 'interrogation' is."""
    with pytest.raises(ValueError):
        registry.resolve("审讯", "dream_preset")


def test_dream_preset_unmapped_chinese_is_hidden(registry):
    """A Chinese filename with no id mapping entry must be hidden."""
    entry = registry.resolve("未知preset", "dream_preset")
    assert entry.hidden, "unmapped non-ASCII dream preset must be hidden"


def test_dream_preset_list_ui_includes_chinese_presets(registry):
    """Chinese-filename presets with mapped ids appear in the UI list."""
    visible_ids = [e.id for e in registry.list_ui("dream_preset")]
    assert "default" in visible_ids
    assert "custom" in visible_ids
    assert "interrogation" in visible_ids
    assert "multi" in visible_ids
    # Raw Chinese stems must never appear as ids
    assert "审讯" not in visible_ids
    assert "多p" not in visible_ids
    # Unmapped preset is hidden
    assert "未知preset" not in visible_ids


def test_dream_preset_list_ui_labels(registry):
    """Labels for Chinese presets show the Chinese display name."""
    entries = {e.id: e for e in registry.list_ui("dream_preset")}
    assert entries["interrogation"].label == "审讯"
    assert entries["multi"].label == "多p"


# ── 8. Dream preset uses id, not Chinese name / filename ─────────────────────

def test_dream_preset_id_is_ascii_stem(registry):
    """All visible dream preset ids must match the ASCII regex."""
    import re
    _ASCII = re.compile(r"^[A-Za-z0-9_-]+$")
    for entry in registry.list_ui("dream_preset"):
        assert _ASCII.fullmatch(entry.id), (
            f"visible dream_preset id {entry.id!r} is not ASCII-safe"
        )


# ── 9. Dream preset legacy normalization ─────────────────────────────────────

def test_normalize_legacy_dream_preset_chinese_stem(registry):
    assert registry.normalize_legacy("审讯", "dream_preset") == "interrogation"


def test_normalize_legacy_dream_preset_chinese_filename(registry):
    assert registry.normalize_legacy("审讯.md", "dream_preset") == "interrogation"


def test_normalize_legacy_dream_preset_multi_stem(registry):
    assert registry.normalize_legacy("多p", "dream_preset") == "multi"


def test_normalize_legacy_dream_preset_multi_filename(registry):
    assert registry.normalize_legacy("多p.md", "dream_preset") == "multi"


def test_normalize_legacy_dream_preset_ascii_passthrough(registry):
    assert registry.normalize_legacy("default", "dream_preset") == "default"


def test_normalize_legacy_dream_preset_ascii_filename(registry):
    assert registry.normalize_legacy("default.md", "dream_preset") == "default"
