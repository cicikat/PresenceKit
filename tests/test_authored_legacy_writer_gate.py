"""C1.1: legacy authored roots stay readable but never become writer targets."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from core.data_paths import DataPaths


def _run(coro):
    return asyncio.run(coro)


def _production_paths(tmp_path, monkeypatch) -> DataPaths:
    import core.sandbox as sandbox

    monkeypatch.chdir(tmp_path)
    paths = DataPaths(mode="production")
    monkeypatch.setattr(sandbox, "_instance", paths)
    return paths


def _seed_bundled_dream_template(tmp_path: Path) -> None:
    template = tmp_path / "bundled" / "seeds" / "dream" / "worlds" / "_default"
    template.mkdir(parents=True, exist_ok=True)
    for name, content in {
        "ruleset.md": "rules\n",
        "mes_example.md": "example\n",
        "vocab.json": "[]\n",
        "lorebook.yaml": "[]\n",
    }.items():
        (template / name).write_text(content, encoding="utf-8")


class _BodyRequest:
    def __init__(self, content: bytes):
        self._content = content

    async def body(self) -> bytes:
        return self._content


def test_legacy_character_save_materializes_userdata_override(tmp_path, monkeypatch):
    paths = _production_paths(tmp_path, monkeypatch)
    legacy = tmp_path / "characters" / "legacy_card.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"name": "Legacy", "description": "before"}), encoding="utf-8")

    import admin.routers.character as character

    monkeypatch.setattr(character, "CHARACTERS_DIR", paths.user_character_cards_dir())
    result = _run(character.save_character(
        "legacy_card.json", _BodyRequest(json.dumps({"name": "Legacy", "description": "after"}).encode()),
    ))

    canonical = tmp_path / "userdata" / "characters" / "cards" / "legacy_card.json"
    assert result["effective_read_source"] == "legacy"
    assert result["canonical_write_target"] == "user"
    assert json.loads(legacy.read_text(encoding="utf-8"))["description"] == "before"
    assert json.loads(canonical.read_text(encoding="utf-8"))["description"] == "after"

    from core.asset_registry import AssetRegistry

    assert AssetRegistry().resolve("legacy_card", "character").path() == Path("userdata/characters/cards/legacy_card.json")


def test_legacy_reality_edits_materialize_combined_userdata_files(tmp_path, monkeypatch):
    _production_paths(tmp_path, monkeypatch)
    legacy_dir = tmp_path / "characters" / "reality"
    legacy_dir.mkdir(parents=True)
    legacy_lore = legacy_dir / "lorebook.yaml"
    legacy_lore.write_text(yaml.dump({"entries": [{"id": "old", "content": "legacy"}]}), encoding="utf-8")
    legacy_jb = legacy_dir / "jailbreak_entries.json"
    legacy_jb.write_text(json.dumps({"entries": [{"id": "old", "content": "legacy"}]}), encoding="utf-8")

    from admin.routers import jailbreak_entries, lorebook

    from scripts.migrate_character_user_facts import migrate
    from core.data_paths import DEFAULT_CHAR_ID
    assert lorebook._read_lorebook(DEFAULT_CHAR_ID) == {"entries": []}
    migrate("", DEFAULT_CHAR_ID, artifact="lorebook", apply=True)
    migrate("", DEFAULT_CHAR_ID, artifact="jailbreak_entries", apply=True)
    lore = lorebook._read_lorebook(DEFAULT_CHAR_ID)
    lore["entries"][0]["content"] = "updated"
    lorebook._write_lorebook(lore)
    jb = jailbreak_entries._read()
    jb["entries"][0]["content"] = "updated"
    jailbreak_entries._write(jb)

    assert "legacy" in legacy_lore.read_text(encoding="utf-8")
    assert json.loads(legacy_jb.read_text(encoding="utf-8"))["entries"][0]["content"] == "legacy"
    assert "updated" in (tmp_path / "userdata" / "characters" / "authored" / DEFAULT_CHAR_ID / "lorebook.yaml").read_text(encoding="utf-8")
    assert json.loads((tmp_path / "userdata" / "characters" / "authored" / DEFAULT_CHAR_ID / "jailbreak_entries.json").read_text(encoding="utf-8"))["entries"][0]["content"] == "updated"


def test_legacy_dream_world_edit_materializes_package_and_delete_fails_loud(tmp_path, monkeypatch):
    _production_paths(tmp_path, monkeypatch)
    legacy = tmp_path / "characters" / "dream_worlds" / "legacy_world"
    legacy.mkdir(parents=True)
    (legacy / "lorebook.yaml").write_text("[]", encoding="utf-8")
    (legacy / "ruleset.md").write_text("legacy rules", encoding="utf-8")

    from admin.routers import dream

    _run(dream.add_dream_lore_entry("legacy_world", {"keywords": ["k"], "content": "new"}))
    user_world = tmp_path / "userdata" / "characters" / "dream" / "worlds" / "legacy_world"
    assert (legacy / "lorebook.yaml").read_text(encoding="utf-8") == "[]"
    assert (user_world / "ruleset.md").read_text(encoding="utf-8") == "legacy rules"
    assert "new" in (user_world / "lorebook.yaml").read_text(encoding="utf-8")

    # An independently legacy-only package cannot be deleted.
    only_legacy = tmp_path / "characters" / "dream_worlds" / "read_only_world"
    only_legacy.mkdir()
    with pytest.raises(HTTPException, match="只读"):
        _run(dream.delete_dream_world("read_only_world"))
    assert only_legacy.is_dir()


def test_legacy_dream_preset_edit_materializes_userdata_and_new_world_never_creates_legacy(tmp_path, monkeypatch):
    _production_paths(tmp_path, monkeypatch)
    _seed_bundled_dream_template(tmp_path)
    legacy_preset = tmp_path / "characters" / "dream_presets" / "legacy_preset.md"
    legacy_preset.parent.mkdir(parents=True)
    legacy_preset.write_text("legacy", encoding="utf-8")

    from admin.routers import dream

    _run(dream.put_standalone_dream_preset("legacy_preset", {"content": "updated"}))
    user_preset = tmp_path / "userdata" / "characters" / "dream" / "presets" / "legacy_preset.md"
    assert legacy_preset.read_text(encoding="utf-8") == "legacy"
    assert user_preset.read_text(encoding="utf-8") == "updated"

    _run(dream.create_dream_world({"world": "new_world"}))
    assert (tmp_path / "userdata" / "characters" / "dream" / "worlds" / "new_world").is_dir()
    assert not (tmp_path / "characters" / "dream_worlds").exists()


def test_import_default_is_userdata_and_explicit_legacy_output_is_rejected(tmp_path):
    from scripts.import_st_card import resolve_output_path

    assert resolve_output_path(char_id="new_card", explicit_out=None, repo_root=tmp_path) == (
        tmp_path / "userdata" / "characters" / "cards" / "new_card.json"
    )
    with pytest.raises(ValueError, match="legacy/public"):
        resolve_output_path(
            char_id="new_card",
            explicit_out=str(tmp_path / "characters" / "new_card.json"),
            repo_root=tmp_path,
        )
