"""C1.2 resource-level authored read precedence and no-directory-shadowing."""

from __future__ import annotations

from pathlib import Path

from core.authored_asset_resolver import resolve_layered_files
from core.data_paths import DataPaths


def _production_paths(tmp_path, monkeypatch) -> DataPaths:
    import core.sandbox as sandbox

    monkeypatch.chdir(tmp_path)
    paths = DataPaths(mode="production")
    monkeypatch.setattr(sandbox, "_instance", paths)
    return paths


def _write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_empty_user_modular_dirs_do_not_shadow_legacy_lorebook_or_jailbreak(tmp_path, monkeypatch):
    _production_paths(tmp_path, monkeypatch)
    (tmp_path / "userdata" / "characters" / "reality" / "lorebooks").mkdir(parents=True)
    (tmp_path / "userdata" / "characters" / "reality" / "jailbreaks").mkdir(parents=True)
    legacy_lore = _write(tmp_path / "characters" / "reality" / "lorebooks" / "legacy.yaml")
    legacy_jb = _write(tmp_path / "characters" / "reality" / "jailbreaks" / "legacy.json", "{}")

    from core.asset_registry import AssetRegistry

    registry = AssetRegistry()
    assert registry.resolve("legacy", "reality_lorebook").path() == Path("characters/reality/lorebooks/legacy.yaml")
    assert registry.resolve("legacy", "reality_jailbreak").path() == Path("characters/reality/jailbreaks/legacy.json")
    assert legacy_lore.is_file() and legacy_jb.is_file()


def test_modular_resources_merge_with_user_precedence_deterministically(tmp_path, monkeypatch):
    _production_paths(tmp_path, monkeypatch)
    user_lore = _write(tmp_path / "userdata" / "characters" / "reality" / "lorebooks" / "a.yaml", "user")
    _write(tmp_path / "characters" / "reality" / "lorebooks" / "a.yaml", "legacy")
    legacy_lore_b = _write(tmp_path / "characters" / "reality" / "lorebooks" / "b.yaml")
    user_jb = _write(tmp_path / "userdata" / "characters" / "reality" / "jailbreaks" / "a.json", "{}")
    _write(tmp_path / "characters" / "reality" / "jailbreaks" / "a.json", "{}")
    legacy_jb_b = _write(tmp_path / "characters" / "reality" / "jailbreaks" / "b.json", "{}")

    from core.asset_registry import AssetRegistry

    registry = AssetRegistry()
    assert registry.resolve("a", "reality_lorebook").path() == Path("userdata/characters/reality/lorebooks/a.yaml")
    assert registry.resolve("b", "reality_lorebook").path() == Path("characters/reality/lorebooks/b.yaml")
    assert registry.resolve("a", "reality_jailbreak").path() == Path("userdata/characters/reality/jailbreaks/a.json")
    assert registry.resolve("b", "reality_jailbreak").path() == Path("characters/reality/jailbreaks/b.json")
    assert user_lore.read_text(encoding="utf-8") == "user"
    assert legacy_lore_b.is_file() and user_jb.is_file() and legacy_jb_b.is_file()

    first = [entry.id for entry in registry.list_all("reality_lorebook")]
    second = [entry.id for entry in AssetRegistry().list_all("reality_lorebook")]
    assert first == second == ["a", "b"]


def test_letters_and_knowledge_merge_by_relative_path(tmp_path, monkeypatch):
    paths = _production_paths(tmp_path, monkeypatch)
    user_samples, legacy_samples = paths.letter_samples_read_dirs(char_id="companion")
    user_knowledge, legacy_knowledge = paths.letter_knowledge_read_dirs(char_id="companion")
    _write(legacy_samples / "shared.txt", "legacy")
    _write(legacy_samples / "legacy_only.txt", "legacy only")
    _write(user_samples / "shared.txt", "user")
    _write(user_samples / "user_only.txt", "user only")
    _write(legacy_knowledge / "facts.md", "legacy facts")
    _write(user_knowledge / "facts.md", "user facts")
    legacy_knowledge_only = _write(legacy_knowledge / "legacy_only.md", "legacy only")

    samples = resolve_layered_files(user_samples, legacy_samples, logical_asset="letter_sample", suffixes=(".txt",), recursive=True)
    knowledge = resolve_layered_files(user_knowledge, legacy_knowledge, logical_asset="letter_knowledge", suffixes=(".md",), recursive=True)
    assert {item.logical_id: item.source for item in samples} == {
        "legacy_only.txt": "legacy", "shared.txt": "user", "user_only.txt": "user"
    }
    assert {item.logical_id: item.source for item in knowledge} == {
        "facts.md": "user", "legacy_only.md": "legacy"
    }
    assert next(item for item in knowledge if item.logical_id == "facts.md").path.read_text(encoding="utf-8") == "user facts"
    assert legacy_knowledge_only.is_file()


def test_dream_presets_and_worlds_merge_without_cross_package_fields(tmp_path, monkeypatch):
    _production_paths(tmp_path, monkeypatch)
    _write(tmp_path / "userdata" / "characters" / "dream" / "presets" / "a.md", "user A")
    _write(tmp_path / "characters" / "dream_presets" / "a.md", "legacy A")
    _write(tmp_path / "characters" / "dream_presets" / "b.md", "legacy B")

    user_world = tmp_path / "userdata" / "characters" / "dream" / "worlds" / "a"
    legacy_world_a = tmp_path / "characters" / "dream_worlds" / "a"
    legacy_world_b = tmp_path / "characters" / "dream_worlds" / "b"
    _write(user_world / "ruleset.md", "user rules")
    _write(legacy_world_a / "ruleset.md", "legacy rules")
    _write(legacy_world_a / "lorebook.yaml", "- content: legacy-only-lore\n")
    _write(legacy_world_b / "ruleset.md", "legacy B rules")
    _write(legacy_world_b / "lorebook.yaml", "[]")

    from core.asset_registry import AssetRegistry
    from core.dream.world_loader import discover_worlds, load_dream_lore_entries, load_world, resolve_dream_world

    registry = AssetRegistry()
    assert registry.resolve("a", "dream_preset").path() == Path("userdata/characters/dream/presets/a.md")
    assert registry.resolve("b", "dream_preset").path() == Path("characters/dream_presets/b.md")
    assert {item.logical_id: item.path for item in [resolve_dream_world("a"), resolve_dream_world("b")]} == {
        "a": Path("userdata/characters/dream/worlds/a"),
        "b": Path("characters/dream_worlds/b"),
    }
    assert discover_worlds() == ["a", "b"]
    assert load_world("a").ruleset == "user rules"
    assert load_dream_lore_entries("a") == [], "selected user package must not borrow legacy A lorebook"


def test_empty_user_dream_roots_do_not_hide_legacy_resources(tmp_path, monkeypatch):
    _production_paths(tmp_path, monkeypatch)
    (tmp_path / "userdata" / "characters" / "dream" / "presets").mkdir(parents=True)
    (tmp_path / "userdata" / "characters" / "dream" / "worlds").mkdir(parents=True)
    _write(tmp_path / "characters" / "dream_presets" / "legacy.md", "legacy")
    _write(tmp_path / "characters" / "dream_worlds" / "legacy" / "ruleset.md", "legacy rules")

    from core.asset_registry import AssetRegistry
    from core.dream.world_loader import discover_worlds, load_world

    assert AssetRegistry().resolve("legacy", "dream_preset").path() == Path("characters/dream_presets/legacy.md")
    assert discover_worlds() == ["legacy"]
    assert load_world("legacy").ruleset == "legacy rules"


def test_fresh_user_only_resources_remain_visible_without_legacy(tmp_path, monkeypatch):
    _production_paths(tmp_path, monkeypatch)
    _write(tmp_path / "userdata" / "characters" / "dream" / "worlds" / "fresh" / "ruleset.md", "fresh")

    from core.dream.world_loader import discover_worlds, load_world

    assert discover_worlds() == ["fresh"]
    assert load_world("fresh").ruleset == "fresh"
