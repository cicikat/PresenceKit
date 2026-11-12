"""
tests/test_char_asset_bindings_api.py — 角色资产路由 API（茶茶 2026-07-25 反馈）

覆盖：
  GET   /character/{char_id}/asset-bindings
  PATCH /character/{char_id}/asset-bindings

与 test_char_model_routing_api.py 同构（复用同一套 chars_tree/registry fixture 思路）。
"""
from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import asyncio
import json

import pytest
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def _clear_pipeline_registry():
    from core import pipeline_registry
    pipeline_registry.register(None)
    yield
    pipeline_registry.register(None)


@pytest.fixture
def chars_tree(tmp_path):
    chars = tmp_path / "characters"
    chars.mkdir()
    (chars / f'{TEST_CHAR_ID}.json').write_text(
        json.dumps({"name": "叶瑄", "presence_ext": {}, "world_book": []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (chars / "bound.json").write_text(
        json.dumps(
            {
                "name": "小助手", "world_book": [],
                "presence_ext": {
                    "tts_preset": "cheerful", "sticker_pack": "cute",
                    "live2d_model": "assistant.model3.json", "model_3d": "assistant.glb",
                },
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def registry(chars_tree, monkeypatch):
    import core.asset_registry as _reg_mod
    monkeypatch.chdir(chars_tree)
    reg = _reg_mod.AssetRegistry()
    monkeypatch.setattr(_reg_mod, "_registry", reg)
    return reg


@pytest.fixture
def tts_presets(monkeypatch):
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"tts": {"presets": {"cheerful": {"ref_audio": "a.wav"}}}},
    )


# ── GET /character/{char_id}/asset-bindings ─────────────────────────────────

def test_get_unknown_char_404(registry):
    from admin.routers.character import get_character_asset_bindings
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_character_asset_bindings("ghost", auth="dummy"))
    assert exc.value.status_code == 404


def test_get_undeclared_char_returns_all_none(registry):
    from admin.routers.character import get_character_asset_bindings
    result = asyncio.run(get_character_asset_bindings(TEST_CHAR_ID, auth="dummy"))
    assert result == {
        "char_id": TEST_CHAR_ID,
        "tts_preset": None, "tts_preset_resolved": None,
        "sticker_pack": None, "live2d_model": None, "model_3d": None,
        "image_gen": None,
    }


def test_get_declared_char_returns_bindings(registry, tts_presets):
    from admin.routers.character import get_character_asset_bindings
    result = asyncio.run(get_character_asset_bindings("bound", auth="dummy"))
    assert result["tts_preset"] == "cheerful"
    assert result["tts_preset_resolved"] is True
    assert result["sticker_pack"] == "cute"
    assert result["live2d_model"] == "assistant.model3.json"
    assert result["model_3d"] == "assistant.glb"


def test_get_declared_but_missing_preset_flagged_unresolved(registry, monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"tts": {"presets": {}}})
    from admin.routers.character import get_character_asset_bindings
    result = asyncio.run(get_character_asset_bindings("bound", auth="dummy"))
    assert result["tts_preset"] == "cheerful"
    assert result["tts_preset_resolved"] is False


# ── PATCH /character/{char_id}/asset-bindings ───────────────────────────────

def test_patch_single_field_does_not_touch_others(registry, chars_tree, tts_presets):
    from admin.routers.character import AssetBindingsUpdate, set_character_asset_bindings

    result = asyncio.run(
        set_character_asset_bindings("bound", AssetBindingsUpdate(sticker_pack="new_pack"), auth="dummy")
    )
    assert result["sticker_pack"] == "new_pack"
    assert result["live2d_model"] == "assistant.model3.json", "未提及的字段不应被清掉"

    saved = json.loads((chars_tree / "userdata" / "characters" / "cards" / "bound.json").read_text(encoding="utf-8"))
    assert saved["presence_ext"]["sticker_pack"] == "new_pack"
    assert saved["presence_ext"]["live2d_model"] == "assistant.model3.json"
    assert saved["presence_ext"]["tts_preset"] == "cheerful"
    assert saved["name"] == "小助手", "其余字段不受影响"
    legacy = json.loads((chars_tree / "characters" / "bound.json").read_text(encoding="utf-8"))
    assert legacy["presence_ext"]["sticker_pack"] == "cute"


def test_patch_empty_string_clears_field(registry, chars_tree, tts_presets):
    from admin.routers.character import AssetBindingsUpdate, set_character_asset_bindings

    result = asyncio.run(
        set_character_asset_bindings("bound", AssetBindingsUpdate(sticker_pack=""), auth="dummy")
    )
    assert result["sticker_pack"] is None

    saved = json.loads((chars_tree / "userdata" / "characters" / "cards" / "bound.json").read_text(encoding="utf-8"))
    assert "sticker_pack" not in saved["presence_ext"]
    assert saved["presence_ext"]["tts_preset"] == "cheerful", "同一次请求里未提及的字段仍保留"


def test_patch_writes_new_bindings_onto_undeclared_char(registry, chars_tree):
    from admin.routers.character import AssetBindingsUpdate, set_character_asset_bindings

    asyncio.run(
        set_character_asset_bindings(
            TEST_CHAR_ID,
            AssetBindingsUpdate(live2d_model=f'{TEST_CHAR_ID}.model3.json', model_3d=f'{TEST_CHAR_ID}.glb'),
            auth="dummy",
        )
    )
    saved = json.loads((chars_tree / "userdata" / "characters" / "cards" / f'{TEST_CHAR_ID}.json').read_text(encoding="utf-8"))
    assert saved["presence_ext"]["live2d_model"] == f'{TEST_CHAR_ID}.model3.json'
    assert saved["presence_ext"]["model_3d"] == f'{TEST_CHAR_ID}.glb'
    assert "tts_preset" not in saved["presence_ext"]
    legacy = json.loads((chars_tree / "characters" / f'{TEST_CHAR_ID}.json').read_text(encoding="utf-8"))
    assert legacy["presence_ext"] == {}


def test_patch_unknown_char_404(registry):
    from admin.routers.character import AssetBindingsUpdate, set_character_asset_bindings
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            set_character_asset_bindings("ghost", AssetBindingsUpdate(sticker_pack="x"), auth="dummy")
        )
    assert exc.value.status_code == 404
