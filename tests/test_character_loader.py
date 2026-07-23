"""
tests/test_character_loader.py

Fail-loud character loading tests (P1.5).

Covers:
- character.default: yexuan  →  loads yexuan.json
- character.default: yexuan.json  →  legacy normalize → loads yexuan.json
- character.default: Companion  →  legacy normalize (Chinese label) → loads yexuan.json
- unknown character id  →  ValueError
- registry points to filename that is missing on disk  →  FileNotFoundError
- corrupt character JSON  →  json.JSONDecodeError
- Character(name="AI") is never returned by load()
- runtime active_prompt_assets missing  →  auto-created (allowed)
- authored asset missing ≠ runtime missing
"""

import json
import logging
from pathlib import Path

import pytest

import core.asset_registry as _reg_mod
import core.character_loader as _cl_mod
from core.asset_registry import AssetRegistry
from core.character_loader import Character, load


# ── Fixture: minimal characters/ tree ────────────────────────────────────────

@pytest.fixture
def chars_dir(tmp_path):
    """
    Populate tmp_path/characters/ with a real character card and return
    the tmp_path.  Tests should monkeypatch.chdir(tmp_path) so that
    relative Path("characters") resolves correctly.
    """
    d = tmp_path / "characters"
    d.mkdir()
    (d / "yexuan.json").write_text(
        json.dumps({
            "name": "Companion",
            "description": "测试描述",
            "personality": "温柔",
            "scenario": "",
            "mes_example": "",
            "first_mes": "",
            "system_prompt": "",
            "world_book": [],
        }),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def registry_from(chars_dir, monkeypatch):
    """chdir to chars_dir root and return a fresh registry over it."""
    monkeypatch.chdir(chars_dir)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)
    return reg


# ── 1. Normal: id "yexuan" loads yexuan.json ─────────────────────────────────

def test_load_by_id(registry_from):
    char = load("yexuan")
    assert char.name == "Companion"
    assert isinstance(char, Character)


def test_load_caches_unchanged_card_but_reloads_after_mtime_change(registry_from, monkeypatch):
    import core.character_loader as loader

    read_count = 0
    original_load = json.load

    def _counting_load(*args, **kwargs):
        nonlocal read_count
        read_count += 1
        return original_load(*args, **kwargs)

    monkeypatch.setattr(loader.json, "load", _counting_load)
    assert loader.load("yexuan").personality == "温柔"
    assert loader.load("yexuan").personality == "温柔"
    assert read_count == 1

    card = Path("characters/yexuan.json")
    card.write_text(json.dumps({"name": "Companion", "personality": "克制"}), encoding="utf-8")
    assert loader.load("yexuan").personality == "克制"
    assert read_count == 2


def test_load_logs_only_the_active_character_not_routed_cards(registry_from, monkeypatch, caplog):
    """Repeated/routed card loads must not become INFO just because names alternate."""
    import core.pipeline_registry as pipeline_registry

    class _Pipeline:
        _active_character_id = "other_character"

    monkeypatch.setattr(pipeline_registry, "get", lambda: _Pipeline())
    monkeypatch.setattr(_cl_mod, "_last_logged_active_asset_id", None)
    monkeypatch.setattr(_cl_mod, "_last_logged_active_signature", None)

    with caplog.at_level(logging.DEBUG, logger="core.character_loader"):
        load("yexuan")
        load("yexuan")

    records = [
        record for record in caplog.records
        if record.name == "core.character_loader" and "加载成功" in record.getMessage()
    ]
    assert records
    assert all(record.levelno == logging.DEBUG for record in records)


def test_load_logs_active_character_once_until_its_card_changes(registry_from, monkeypatch, caplog):
    import core.pipeline_registry as pipeline_registry

    class _Pipeline:
        _active_character_id = "yexuan"

    monkeypatch.setattr(pipeline_registry, "get", lambda: _Pipeline())
    monkeypatch.setattr(_cl_mod, "_last_logged_active_asset_id", None)
    monkeypatch.setattr(_cl_mod, "_last_logged_active_signature", None)

    with caplog.at_level(logging.DEBUG, logger="core.character_loader"):
        load("yexuan")
        load("yexuan")

    records = [
        record for record in caplog.records
        if record.name == "core.character_loader" and "加载成功" in record.getMessage()
    ]
    assert [record.levelno for record in records] == [logging.INFO, logging.DEBUG]


# ── 2. Legacy: filename "yexuan.json" normalizes to id and loads ──────────────

def test_load_by_legacy_filename(registry_from):
    char = load("yexuan.json")
    assert char.name == "Companion"


# ── 3. Legacy: Chinese label "Companion" normalizes to id and loads ───────────────

def test_load_by_chinese_label(registry_from):
    char = load("Companion")
    assert char.name == "Companion"


# ── 4. Unknown id → ValueError (fail-loud) ───────────────────────────────────

def test_unknown_id_raises_value_error(registry_from):
    with pytest.raises(ValueError, match="unknown character"):
        load("does_not_exist")


# ── 5. File missing on disk → FileNotFoundError (fail-loud) ──────────────────

def test_file_missing_raises_file_not_found(chars_dir, monkeypatch):
    """Registry registers the entry but the file is deleted before load()."""
    monkeypatch.chdir(chars_dir)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)

    # Delete the file after the registry scanned it
    (chars_dir / "characters" / "yexuan.json").unlink()

    with pytest.raises(FileNotFoundError, match="yexuan"):
        load("yexuan")


# ── 6. Corrupt JSON → json.JSONDecodeError (fail-loud) ───────────────────────

def test_corrupt_json_raises_decode_error(chars_dir, monkeypatch):
    monkeypatch.chdir(chars_dir)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)

    (chars_dir / "characters" / "yexuan.json").write_text(
        "{broken json", encoding="utf-8"
    )

    with pytest.raises(json.JSONDecodeError):
        load("yexuan")


# ── 7. load() never returns Character(name="AI") silently ────────────────────

def test_load_never_returns_ai_fallback(registry_from):
    """load() must raise, not return Character(name='AI'), when id is unknown."""
    try:
        result = load("nonexistent_character_xyz")
    except (ValueError, FileNotFoundError):
        return  # correct: raised instead of returning AI fallback
    # If we get here, check that we did NOT get the silent AI fallback
    assert result.name != "AI", (
        "load() silently returned Character(name='AI') instead of raising "
        "— this is the dangerous fallback that P1.5 must eliminate"
    )
    pytest.fail("load() returned a result for an unknown id without raising")


# ── 8. .txt character card loads without AI fallback ─────────────────────────

def test_load_txt_character(chars_dir, monkeypatch):
    (chars_dir / "characters" / "simple.txt").write_text(
        "简单角色描述", encoding="utf-8"
    )
    monkeypatch.chdir(chars_dir)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)

    char = load("simple")
    assert char.name == "simple"
    assert char.description == "简单角色描述"
    assert char.name != "AI"


# ── 9. Runtime active_prompt_assets absent → auto-created (allowed) ──────────

def test_runtime_active_prompt_assets_autocreated(tmp_path):
    """
    data_paths.active_prompt_assets() auto-creates the file when missing.
    This is a runtime file, not an authored asset — auto-creation is allowed.
    """
    import core.data_paths as _dp
    paths = _dp.DataPaths(mode="test", test_session_id="test_autocreate")
    paths._base = tmp_path / "data"
    # File should not exist yet
    p = paths.active_prompt_assets()
    assert p.exists(), "runtime active_prompt_assets must be auto-created on first access"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "active_character" in data


# ── 10. Authored asset missing ≠ runtime file missing ────────────────────────

def test_authored_asset_missing_raises_not_silently_skipped(chars_dir, monkeypatch):
    """
    If a character id is registered in registry but file is absent,
    load() raises FileNotFoundError — distinct from runtime file missing.
    Runtime file missing (test 9) auto-creates; authored asset missing fails loud.
    """
    monkeypatch.chdir(chars_dir)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)

    # Remove the authored asset after registry scan
    (chars_dir / "characters" / "yexuan.json").unlink()

    with pytest.raises(FileNotFoundError):
        load("yexuan")
    # Contrast: runtime file missing (test 9) returns a path, not raises


# ── 11. Chinese label with .json extension normalizes correctly ───────────────

def test_chinese_label_with_extension(registry_from):
    char = load("Companion.json")
    assert char.name == "Companion"


# ── 12. World-book field is a list ────────────────────────────────────────────

def test_world_book_field_is_list(registry_from):
    char = load("yexuan")
    assert isinstance(char.world_book, list)
