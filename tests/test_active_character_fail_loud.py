from tests.fixtures.public_assets import TEST_CHAR_ID
"""
tests/test_active_character_fail_loud.py

P0-T07: active_character fail-loud 验收测试

Covers:
1.  active_prompt_assets.json exists but active_character is empty/missing
    → _refresh_character_if_needed raises ValueError
    → pipeline.character unchanged
    → no post-process / slow_queue enqueue

2.  active_character points to nonexistent character
    → _refresh_character_if_needed raises ValueError
    → pipeline.character keeps original
    → no fallback to yexuan, no fallback to Character(name="AI")

3.  PATCH /settings/prompt-assets with invalid active_character
    → returns 422
    → active_prompt_assets.json unchanged

4.  PUT /characters/active with invalid character id
    → returns 422
    → active_prompt_assets.json unchanged

5.  config.default=yexuan but active_prompt_assets.active_character=character_b
    → pipeline uses character_b
    → config.default does NOT override active_prompt_assets

6.  active_prompt_assets.json missing, config.default valid
    → auto-created with config.default value
    → config.default is used (not a hardcoded name)

7.  active_prompt_assets.json missing, config.default empty
    → RuntimeError raised (fail-loud, no silent creation)
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


def _make_pipeline(char_id: str, registry):
    """Helper: build a Pipeline with the given char_id loaded."""
    from core.character_loader import load as _load
    from core.pipeline import Pipeline
    char = _load(char_id)
    return Pipeline(char, lore_engine=None, active_character_id=char_id)


# ── 1. active_character missing/empty → ValueError, character unchanged ───────

def test_missing_active_character_field_raises(chars_tree, monkeypatch, sandbox, registry):
    """JSON has no active_character field at all → ValueError."""
    pipeline = _make_pipeline(TEST_CHAR_ID, registry)

    sandbox.active_prompt_assets().write_text(
        json.dumps({"enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="active_character"):
        pipeline._refresh_character_if_needed()

    assert pipeline.character.name == "Companion", "character must remain unchanged"
    assert pipeline._active_character_id == TEST_CHAR_ID


def test_empty_active_character_field_raises(chars_tree, monkeypatch, sandbox, registry):
    """active_character is explicit empty string → ValueError."""
    pipeline = _make_pipeline(TEST_CHAR_ID, registry)

    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "", "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="active_character"):
        pipeline._refresh_character_if_needed()

    assert pipeline.character.name == "Companion"
    assert pipeline._active_character_id == TEST_CHAR_ID


def test_empty_active_character_no_slow_queue(chars_tree, monkeypatch, sandbox, registry):
    """Empty active_character: build_prompt raises before post_process can enqueue."""
    from core.post_process import slow_queue

    pipeline = _make_pipeline(TEST_CHAR_ID, registry)

    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "", "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    enqueue_calls: list = []
    original_enqueue = slow_queue.enqueue

    def _spy_enqueue(*args, **kwargs):
        enqueue_calls.append((args, kwargs))
        return original_enqueue(*args, **kwargs)

    monkeypatch.setattr(slow_queue, "enqueue", _spy_enqueue)

    # build_prompt propagates the ValueError
    with pytest.raises(ValueError):
        pipeline.build_prompt(
            user_id="u1",
            content="test",
            context={
                "history": [],
                "profile": {},
                "relation": {},
                "group_context": "",
                "user_identity_text": "",
                "event_search_result": "",
                "lore_entries": [],
                "reminders": [],
                "diary_context": "",
                "episodic_result": "",
                "episodic_fallback_result": "",
                "mid_term": "",
                "dream_impression_text": "",
            },
        )

    assert enqueue_calls == [], "slow_queue must not be enqueued when character validation fails"


def test_empty_active_character_no_short_term_write(chars_tree, monkeypatch, sandbox, registry):
    """Empty active_character: short_term.append must never be called."""
    from core.memory import short_term

    pipeline = _make_pipeline(TEST_CHAR_ID, registry)

    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "", "enabled_lorebooks": [], "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    append_calls: list = []

    def _spy_append(*args, **kwargs):
        append_calls.append((args, kwargs))

    monkeypatch.setattr(short_term, "append", _spy_append)

    with pytest.raises(ValueError):
        pipeline.build_prompt(
            user_id="u1",
            content="test",
            context={
                "history": [],
                "profile": {},
                "relation": {},
                "group_context": "",
                "user_identity_text": "",
                "event_search_result": "",
                "lore_entries": [],
                "reminders": [],
                "diary_context": "",
                "episodic_result": "",
                "episodic_fallback_result": "",
                "mid_term": "",
                "dream_impression_text": "",
            },
        )

    assert append_calls == [], "short_term.append must not be called when character invalid"


# ── 2. active_character → nonexistent id → ValueError, character preserved ───

def test_unknown_active_character_raises_valueerror(chars_tree, monkeypatch, sandbox, registry):
    """Unknown active_character id → ValueError; original character preserved."""
    pipeline = _make_pipeline(TEST_CHAR_ID, registry)

    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "does_not_exist", "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        pipeline._refresh_character_if_needed()

    # Must not have swapped to AI fallback
    assert pipeline.character.name != "AI"
    # Must not have fallen back to yexuan (it WAS yexuan; the key point: id unchanged)
    assert pipeline._active_character_id == TEST_CHAR_ID, (
        f'active_character_id must remain {TEST_CHAR_ID}, not updated to the invalid id'
    )
    # The character object must be the original yexuan, not any new object
    assert pipeline.character.name == "Companion"


def test_unknown_active_character_no_ai_fallback(chars_tree, monkeypatch, sandbox, registry):
    """No Character(name='AI') fallback on unknown active_character."""
    pipeline = _make_pipeline("character_b", registry)

    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "ghost_123", "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        pipeline._refresh_character_if_needed()

    assert pipeline.character.name != "AI"
    assert pipeline.character.name == "DemoUser", "must keep character_b, not fallback to AI"
    assert pipeline._active_character_id == "character_b"


def test_unknown_active_character_no_yexuan_fallback(chars_tree, monkeypatch, sandbox, registry):
    """Starting from character_b, unknown active_character must NOT silently swap to yexuan."""
    pipeline = _make_pipeline("character_b", registry)

    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "ghost_char", "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        pipeline._refresh_character_if_needed()

    assert pipeline.character.name == "DemoUser", f'must stay as character_b, no fallback to {TEST_CHAR_ID}'
    assert pipeline._active_character_id == "character_b"


# ── 3. PATCH /settings/prompt-assets with invalid active_character → 422 ─────

def test_patch_invalid_active_character_returns_422(chars_tree, monkeypatch, sandbox, registry):
    """PATCH with unknown active_character returns 422, does not write to active_prompt_assets."""
    from fastapi import HTTPException
    from admin.routers.settings_prompt_assets import PromptAssetsUpdate, patch_prompt_assets

    # Seed with yexuan
    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": TEST_CHAR_ID, "enabled_lorebooks": [],
                    "enabled_jailbreaks": ["base"]}),
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            patch_prompt_assets(
                PromptAssetsUpdate(active_character="nonexistent_char"),
                auth="dummy",
            )
        )
    assert exc_info.value.status_code == 422

    # File must be unchanged
    after = json.loads(sandbox.active_prompt_assets().read_text(encoding="utf-8"))
    assert after["active_character"] == TEST_CHAR_ID, (
        "active_prompt_assets must not be modified after rejected PATCH"
    )


def test_patch_invalid_active_character_no_disk_write(chars_tree, monkeypatch, sandbox, registry):
    """PATCH with label string (not id) also rejects 422 and does not write."""
    from fastapi import HTTPException
    from admin.routers.settings_prompt_assets import PromptAssetsUpdate, patch_prompt_assets

    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": TEST_CHAR_ID, "enabled_lorebooks": [],
                    "enabled_jailbreaks": ["base"]}),
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            patch_prompt_assets(
                PromptAssetsUpdate(active_character="Companion"),  # label, not id
                auth="dummy",
            )
        )
    assert exc_info.value.status_code == 422

    after = json.loads(sandbox.active_prompt_assets().read_text(encoding="utf-8"))
    assert after["active_character"] == TEST_CHAR_ID


# ── 4. PUT /characters/active with invalid id → 422, pipeline not updated ────

def test_put_active_invalid_id_returns_422(chars_tree, monkeypatch, sandbox, registry):
    """PUT /characters/active with unknown id returns 422."""
    from fastapi import HTTPException
    from admin.routers.character import set_active_character

    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": TEST_CHAR_ID, "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            set_active_character({"id": "ghost_char"}, auth="dummy")
        )
    assert exc_info.value.status_code == 422


def test_put_active_invalid_id_no_disk_write(chars_tree, monkeypatch, sandbox, registry):
    """PUT /characters/active with unknown id does not modify active_prompt_assets.json."""
    from fastapi import HTTPException
    from admin.routers.character import set_active_character

    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": TEST_CHAR_ID, "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    with pytest.raises(HTTPException):
        asyncio.run(
            set_active_character({"id": "not_a_real_character"}, auth="dummy")
        )

    after = json.loads(sandbox.active_prompt_assets().read_text(encoding="utf-8"))
    assert after["active_character"] == TEST_CHAR_ID, (
        "active_prompt_assets must not change after rejected PUT /characters/active"
    )


def test_put_active_invalid_id_does_not_update_pipeline(
    chars_tree, monkeypatch, sandbox, registry
):
    """PUT /characters/active with unknown id must not update the running pipeline character."""
    from fastapi import HTTPException
    from admin.routers.character import set_active_character
    from core.pipeline import Pipeline
    import core.pipeline_registry as _preg

    pipeline = _make_pipeline(TEST_CHAR_ID, registry)
    monkeypatch.setattr(_preg, "_pipeline", pipeline)

    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": TEST_CHAR_ID, "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    with pytest.raises(HTTPException):
        asyncio.run(
            set_active_character({"id": "unknown_ghost"}, auth="dummy")
        )

    assert pipeline.character.name == "Companion", "Pipeline character must remain unchanged"
    assert pipeline._active_character_id == TEST_CHAR_ID


# ── 5. active_prompt_assets.active_character overrides config.default ─────────

def test_active_overrides_config_default_at_pipeline_refresh(
    chars_tree, monkeypatch, sandbox, registry
):
    """Pipeline refresh uses active_prompt_assets, not config.default."""
    from core.pipeline import Pipeline
    from core.character_loader import load as _load

    pipeline = Pipeline(_load(TEST_CHAR_ID), lore_engine=None, active_character_id=TEST_CHAR_ID)

    # active_prompt_assets says character_b
    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "character_b", "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    # config says yexuan (mock)
    with patch("core.config_loader.get_config", return_value={"character": {"default": TEST_CHAR_ID}}):
        pipeline._refresh_character_if_needed()

    assert pipeline.character.name == "DemoUser", (
        f'Pipeline must use active_prompt_assets (character_b), not config.default ({TEST_CHAR_ID})'
    )
    assert pipeline._active_character_id == "character_b"


# ── 6. File missing + valid config.default → auto-created correctly ───────────

def test_missing_file_valid_config_default_autocreates(tmp_path):
    """active_prompt_assets.json missing + config.default set → auto-created with config value."""
    import core.data_paths as _dp

    paths = _dp.DataPaths(mode="test", test_session_id="fail_loud_autocreate")
    paths._base = tmp_path / "data"

    # The real project config.yaml has character.default: yexuan
    p = paths.active_prompt_assets()
    assert p.exists(), "file must be auto-created"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "active_character" in data
    assert data["active_character"], "active_character must be non-empty after init"
    # Must use config.default, not any other hardcoded value
    assert data["active_character"] != "", "must not be an empty sentinel"


def test_missing_file_empty_config_default_raises(tmp_path, monkeypatch):
    """active_prompt_assets.json missing + config.default empty → RuntimeError."""
    import core.data_paths as _dp

    paths = _dp.DataPaths(mode="test", test_session_id="fail_loud_nodefault")
    paths._base = tmp_path / "data"

    # Patch _CONFIG_PATH to a non-existent file so cfg = {} and default = ""
    monkeypatch.setattr(_dp, "_CONFIG_PATH", tmp_path / "no_config.yaml")

    with pytest.raises(RuntimeError, match="character.default"):
        paths.active_prompt_assets()


# ── 7. fetch_context raises before reading memory when character invalid ───────

def test_fetch_context_blocks_on_empty_active_character(
    chars_tree, monkeypatch, sandbox, registry
):
    """fetch_context raises before reading any memory when active_character is empty."""
    from core.memory import short_term

    pipeline = _make_pipeline(TEST_CHAR_ID, registry)

    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "", "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    load_calls: list = []
    original_load = short_term.load_for_prompt

    def _spy_load(*args, **kwargs):
        load_calls.append(args)
        return original_load(*args, **kwargs)

    monkeypatch.setattr(short_term, "load_for_prompt", _spy_load)

    with pytest.raises(ValueError, match="active_character"):
        asyncio.run(pipeline.context.fetch(user_id="u1", content="hello"))

    assert load_calls == [], (
        "short_term.load_for_prompt must not be called when active_character is invalid"
    )


def test_fetch_context_blocks_on_unknown_active_character(
    chars_tree, monkeypatch, sandbox, registry
):
    """fetch_context raises before reading memory when active_character is unknown."""
    from core.memory import short_term

    pipeline = _make_pipeline(TEST_CHAR_ID, registry)

    sandbox.active_prompt_assets().write_text(
        json.dumps({"active_character": "ghost_99", "enabled_lorebooks": [],
                    "enabled_jailbreaks": []}),
        encoding="utf-8",
    )

    load_calls: list = []
    original_load = short_term.load_for_prompt

    def _spy_load(*args, **kwargs):
        load_calls.append(args)
        return original_load(*args, **kwargs)

    monkeypatch.setattr(short_term, "load_for_prompt", _spy_load)

    with pytest.raises(ValueError):
        asyncio.run(pipeline.context.fetch(user_id="u1", content="hello"))

    assert load_calls == [], (
        "short_term.load_for_prompt must not be called when active_character is invalid"
    )
