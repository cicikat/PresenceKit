from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID
"""
tests/test_character_switch.py

Reality character-card asset-switching pipeline tests.

Covers:
1.  character_b.json registry: id="character_b", label="DemoUser", not hidden
2.  yexuanJ-5412.json registry: id=TEST_PEER_CHAR_ID, not hidden
3.  GET /settings/prompt-assets returns character_b in characters list
4.  PATCH active_character=character_b writes character_b to active_prompt_assets.json
5.  character_loader.load("character_b") loads the character_b character correctly
6.  Pipeline._refresh_character_if_needed() hot-swaps when active_prompt_assets changes
7.  config.default=yexuan but active_character=character_b → pipeline uses character_b at next turn
8.  Unknown active_character → ValueError raised (fail-loud, no silent AI fallback)
9.  active_prompt_assets empty active_character → pipeline raises ValueError (fail-loud)
10. active_prompt_assets missing → auto-created from config.default (non-empty value)
"""

import asyncio
import json
from pathlib import Path

import pytest

import core.asset_registry as _reg_mod
from core.asset_registry import AssetRegistry


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def chars_tree(tmp_path):
    """Minimal characters/ tree with yexuan + character_b + jailbreaks."""
    chars = tmp_path / "characters"
    chars.mkdir()

    (chars / f'{TEST_CHAR_ID}.json').write_text(
        json.dumps({"name": "Companion", "description": "test", "world_book": []}),
        encoding="utf-8",
    )
    (chars / "character_b.json").write_text(
        json.dumps({"name": "DemoUser", "description": "character_b test", "world_book": []}),
        encoding="utf-8",
    )
    # yexuanJ-5412 also present
    (chars / f'{TEST_PEER_CHAR_ID}.json').write_text(
        json.dumps({"name": "CompanionJ-5412", "description": "j5412 test", "world_book": []}),
        encoding="utf-8",
    )

    jb = chars / "reality" / "jailbreaks"
    jb.mkdir(parents=True)
    (jb / "base.json").write_text(json.dumps({"entries": []}), encoding="utf-8")

    return tmp_path


@pytest.fixture
def registry(chars_tree, monkeypatch):
    monkeypatch.chdir(chars_tree)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)
    return reg


# ── 1. character_b.json registry entry ───────────────────────────────────────────

def test_character_b_registry_id_label(registry):
    entry = registry.resolve("character_b", "character")
    assert entry.id == "character_b"
    assert entry.label == "DemoUser"
    assert entry.filename == "character_b.json"
    assert not entry.hidden


def test_character_b_appears_in_ui_list(registry):
    visible_ids = [e.id for e in registry.list_ui("character")]
    assert "character_b" in visible_ids


def test_character_b_label_is_chinese(registry):
    entry = registry.resolve("character_b", "character")
    assert entry.label == "DemoUser", "label must be DemoUser (from JSON name field)"


# ── 2. yexuanJ-5412 registry entry ───────────────────────────────────────────

def test_j5412_registry_entry(registry):
    entry = registry.resolve(TEST_PEER_CHAR_ID, "character")
    assert entry.id == TEST_PEER_CHAR_ID
    assert entry.label == "CompanionJ-5412"
    assert not entry.hidden


def test_j5412_appears_in_ui_list(registry):
    visible_ids = [e.id for e in registry.list_ui("character")]
    assert TEST_PEER_CHAR_ID in visible_ids


# ── 3. GET /settings/prompt-assets returns character_b ────────────────────────────

def test_get_prompt_assets_includes_character_b(chars_tree, monkeypatch, sandbox):
    """GET /settings/prompt-assets characters list includes character_b."""
    monkeypatch.chdir(chars_tree)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)

    from admin.routers.settings_prompt_assets import get_prompt_assets

    result = asyncio.run(get_prompt_assets(auth="dummy"))

    ids = [c["id"] for c in result["characters"]]
    labels = {c["id"]: c["label"] for c in result["characters"]}
    assert "character_b" in ids, f"character_b missing from characters list: {ids}"
    assert labels["character_b"] == "DemoUser"


# ── 4. PATCH saves active_character to active_prompt_assets.json ──────────────

def test_patch_active_character_writes_json(chars_tree, monkeypatch, sandbox):
    """PATCH active_character=character_b persists to active_prompt_assets.json."""
    monkeypatch.chdir(chars_tree)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)

    # Seed active_prompt_assets.json with yexuan
    assets_path = sandbox.active_prompt_assets()
    assets_path.write_text(
        json.dumps({"active_character": TEST_CHAR_ID, "enabled_lorebooks": [],
                    "enabled_jailbreaks": ["base"]}),
        encoding="utf-8",
    )

    from admin.routers.settings_prompt_assets import PromptAssetsUpdate, patch_prompt_assets

    asyncio.run(
        patch_prompt_assets(
            PromptAssetsUpdate(active_character="character_b"),
            auth="dummy",
        )
    )

    saved = json.loads(assets_path.read_text(encoding="utf-8"))
    assert saved["active_character"] == "character_b", (
        f"active_prompt_assets.json should store 'character_b', got {saved['active_character']!r}"
    )


def test_patch_active_character_rejects_unknown(chars_tree, monkeypatch, sandbox):
    """PATCH with unknown id must return 422, not write to active_prompt_assets.json."""
    from fastapi import HTTPException
    monkeypatch.chdir(chars_tree)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)

    assets_path = sandbox.active_prompt_assets()
    original = json.loads(assets_path.read_text(encoding="utf-8"))

    from admin.routers.settings_prompt_assets import PromptAssetsUpdate, patch_prompt_assets

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            patch_prompt_assets(
                PromptAssetsUpdate(active_character="does_not_exist"),
                auth="dummy",
            )
        )
    assert exc_info.value.status_code == 422

    # active_prompt_assets must remain unchanged
    after = json.loads(assets_path.read_text(encoding="utf-8"))
    assert after["active_character"] == original["active_character"]


# ── 5. character_loader.load("character_b") loads character_b.json ────────────────────

def test_load_character_b_by_id(chars_tree, monkeypatch, registry):
    from core.character_loader import load

    char = load("character_b")
    assert char.name == "DemoUser"


def test_load_character_b_never_returns_ai_fallback(chars_tree, monkeypatch, registry):
    from core.character_loader import load, Character

    char = load("character_b")
    assert char.name != "AI", "load() must not return Character(name='AI') for known id"
    assert isinstance(char, Character)


# ── 6. Pipeline._refresh_character_if_needed() hot-swaps character ────────────

def test_pipeline_refresh_swaps_character(chars_tree, monkeypatch, sandbox):
    """When active_prompt_assets.json changes active_character, build_prompt triggers reload."""
    monkeypatch.chdir(chars_tree)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)

    from core.character_loader import load as _load
    from core.pipeline import Pipeline

    # Start pipeline with yexuan
    yexuan = _load(TEST_CHAR_ID)
    pipeline = Pipeline(yexuan, lore_engine=None, active_character_id=TEST_CHAR_ID)
    assert pipeline.character.name == "Companion"
    assert pipeline._active_character_id == TEST_CHAR_ID

    # Switch active_prompt_assets.json to character_b
    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "character_b", "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    # Trigger refresh
    pipeline.character_state.refresh()

    assert pipeline._active_character_id == "character_b"
    assert pipeline.character.name == "DemoUser", (
        "Pipeline character must be DemoUser after refresh with active_character=character_b"
    )


def test_pipeline_refresh_no_swap_when_same(chars_tree, monkeypatch, sandbox):
    """_refresh_character_if_needed() is a no-op when active_character is unchanged."""
    monkeypatch.chdir(chars_tree)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)

    from core.character_loader import load as _load
    from core.pipeline import Pipeline

    yexuan = _load(TEST_CHAR_ID)
    pipeline = Pipeline(yexuan, lore_engine=None, active_character_id=TEST_CHAR_ID)
    original_char = pipeline.character

    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": TEST_CHAR_ID, "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    pipeline.character_state.refresh()

    assert pipeline.character is original_char, "Character object must not change when id unchanged"


# ── 7. config.default=yexuan but active_character=character_b → uses character_b ──────

def test_active_prompt_assets_overrides_config_default(chars_tree, monkeypatch, sandbox):
    """active_prompt_assets.json active_character takes precedence over config.yaml default."""
    monkeypatch.chdir(chars_tree)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)

    # active_prompt_assets.json says character_b
    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "character_b", "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    # config.yaml default says yexuan (mock)
    from unittest.mock import patch as _patch
    with _patch("core.config_loader.get_config", return_value={"character": {"default": TEST_CHAR_ID}}):
        from core.sandbox import get_paths
        import json as _json
        active_data = _json.loads(get_paths().active_prompt_assets().read_text(encoding="utf-8"))
        from core import config_loader as _cl
        cfg = _cl.get_config()
        # Replicate main.py priority logic
        char_ref = active_data.get("active_character") or cfg.get("character", {}).get("default", "")

    assert char_ref == "character_b", (
        f"active_prompt_assets.json active_character must override config.yaml default, "
        f"got {char_ref!r}"
    )

    from core.character_loader import load
    char = load(char_ref)
    assert char.name == "DemoUser", f"Should load character_b (DemoUser), got {char.name!r}"


def test_empty_active_character_raises_in_pipeline(
    chars_tree, monkeypatch, sandbox
):
    """active_prompt_assets.json with empty active_character must raise ValueError in pipeline.

    No silent fallback to yexuan or any other character is permitted.
    """
    monkeypatch.chdir(chars_tree)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)

    from core.character_loader import load as _load
    from core.pipeline import Pipeline

    yexuan = _load(TEST_CHAR_ID)
    pipeline = Pipeline(yexuan, lore_engine=None, active_character_id=TEST_CHAR_ID)

    # active_prompt_assets.json has empty active_character — invalid state
    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "", "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="active_character"):
        pipeline.character_state.refresh()

    # Pipeline character must not have changed
    assert pipeline.character.name == "Companion", "Character must remain unchanged on empty active_character"
    assert pipeline._active_character_id == TEST_CHAR_ID


# ── 8. Unknown active_character → fail-loud ───────────────────────────────────

def test_unknown_active_character_raises_not_ai_fallback(chars_tree, monkeypatch, registry):
    """character_loader.load() with unknown id raises ValueError, not returns Character(name='AI')."""
    from core.character_loader import load

    with pytest.raises(ValueError, match="unknown character"):
        load("this_id_does_not_exist")


def test_pipeline_refresh_fail_loud_on_unknown_id(chars_tree, monkeypatch, sandbox):
    """_refresh_character_if_needed() raises ValueError on unknown id; character preserved."""
    monkeypatch.chdir(chars_tree)
    reg = AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)

    from core.character_loader import load as _load
    from core.pipeline import Pipeline

    yexuan = _load(TEST_CHAR_ID)
    pipeline = Pipeline(yexuan, lore_engine=None, active_character_id=TEST_CHAR_ID)

    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "ghost_character", "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        pipeline.character_state.refresh()

    # Pipeline must not have swapped to AI fallback or yexuan-override
    assert pipeline.character.name != "AI", (
        "Pipeline must not silently fallback to Character(name='AI') on unknown active_character"
    )
    # The original character must remain (swap was aborted before assignment)
    assert pipeline.character.name == "Companion", (
        "Pipeline character must stay as original (Companion) after failed swap"
    )
    # active_character_id must NOT have been updated to the bad id
    assert pipeline._active_character_id == TEST_CHAR_ID


# ── 9. Startup: active_prompt_assets absent → auto-created ───────────────────

def test_active_prompt_assets_autocreated_on_first_access(tmp_path):
    """active_prompt_assets.json is auto-created from config.default on first run."""
    import core.data_paths as _dp
    paths = _dp.DataPaths(mode="test", test_session_id="test_switch_autocreate")
    paths._base = tmp_path / "data"

    p = paths.active_prompt_assets()
    assert p.exists(), "active_prompt_assets.json must be auto-created"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "active_character" in data
    # Value must come from config.default (currently TEST_CHAR_ID), never a hardcoded fallback
    assert data["active_character"], "active_character must be non-empty after init"
