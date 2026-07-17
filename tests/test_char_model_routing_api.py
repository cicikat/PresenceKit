"""
tests/test_char_model_routing_api.py — Brief 87 §1: per-角色模型绑定 API

覆盖：
  GET   /character/{char_id}/model-routing
  PATCH /character/{char_id}/model-routing
  GET   /model-presets/routing-profiles
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException

_MP = {
    "active_routing": "default",
    "presets": {"ds": {}, "claude": {}},
    "routing_profiles": {
        "default": {"chat": "ds"},
        "claude-main": {"chat": "claude"},
    },
}


@pytest.fixture(autouse=True)
def _mp_config(monkeypatch):
    monkeypatch.setattr("core.model_registry._get_preset_config", lambda: _MP)


@pytest.fixture(autouse=True)
def _clear_pipeline_registry():
    from core import pipeline_registry
    pipeline_registry.register(None)
    yield
    pipeline_registry.register(None)


@pytest.fixture
def chars_tree(tmp_path):
    """最小 characters/ 目录：一个未声明路由的角色 + 一个已绑定 claude-main 的角色。"""
    chars = tmp_path / "characters"
    chars.mkdir()
    (chars / "yexuan.json").write_text(
        json.dumps({"name": "叶瑄", "presence_ext": {}, "world_book": []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (chars / "claude_bound.json").write_text(
        json.dumps(
            {"name": "小助手", "presence_ext": {"model_routing": "claude-main"}, "world_book": []},
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


# ── GET /character/{char_id}/model-routing ─────────────────────────────────

def test_get_unknown_char_404(registry):
    from admin.routers.character import get_character_model_routing
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_character_model_routing("ghost", auth="dummy"))
    assert exc.value.status_code == 404


def test_get_undeclared_char_falls_back_to_active_routing(registry):
    from admin.routers.character import get_character_model_routing
    result = asyncio.run(get_character_model_routing("yexuan", auth="dummy"))
    assert result["model_routing"] is None
    assert result["effective_profile"] == "default"
    assert result["resolved_chat_preset"] == "ds"


def test_get_declared_char_resolves_own_profile(registry):
    from admin.routers.character import get_character_model_routing
    result = asyncio.run(get_character_model_routing("claude_bound", auth="dummy"))
    assert result["model_routing"] == "claude-main"
    assert result["effective_profile"] == "claude-main"
    assert result["resolved_chat_preset"] == "claude"


# ── PATCH /character/{char_id}/model-routing ────────────────────────────────

def test_patch_valid_profile_binds_and_writes_file(registry, chars_tree):
    from admin.routers.character import ModelRoutingUpdate, set_character_model_routing

    result = asyncio.run(
        set_character_model_routing(
            "yexuan", ModelRoutingUpdate(model_routing="claude-main"), auth="dummy"
        )
    )
    assert result["model_routing"] == "claude-main"
    assert result["effective_profile"] == "claude-main"
    assert result["resolved_chat_preset"] == "claude"

    saved = json.loads((chars_tree / "characters" / "yexuan.json").read_text(encoding="utf-8"))
    assert saved["presence_ext"]["model_routing"] == "claude-main"
    assert saved["name"] == "叶瑄", "其余字段不受影响"


def test_patch_unknown_profile_rejected_422(registry, chars_tree):
    from admin.routers.character import ModelRoutingUpdate, set_character_model_routing

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            set_character_model_routing(
                "yexuan", ModelRoutingUpdate(model_routing="does-not-exist"), auth="dummy"
            )
        )
    assert exc.value.status_code == 422

    saved = json.loads((chars_tree / "characters" / "yexuan.json").read_text(encoding="utf-8"))
    assert saved["presence_ext"] == {}, "非法值不能写入磁盘（不能静默失效，也不能半写入）"


def test_patch_null_clears_binding(registry, chars_tree):
    from admin.routers.character import ModelRoutingUpdate, set_character_model_routing

    result = asyncio.run(
        set_character_model_routing(
            "claude_bound", ModelRoutingUpdate(model_routing=None), auth="dummy"
        )
    )
    assert result["model_routing"] is None
    assert result["effective_profile"] == "default"
    assert result["resolved_chat_preset"] == "ds"

    saved = json.loads((chars_tree / "characters" / "claude_bound.json").read_text(encoding="utf-8"))
    assert "model_routing" not in saved["presence_ext"]


def test_patch_unknown_char_404(registry):
    from admin.routers.character import ModelRoutingUpdate, set_character_model_routing

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            set_character_model_routing(
                "ghost", ModelRoutingUpdate(model_routing="claude-main"), auth="dummy"
            )
        )
    assert exc.value.status_code == 404


# ── GET /model-presets/routing-profiles ─────────────────────────────────────

def test_list_routing_profiles():
    from admin.routers.settings_llm import list_routing_profiles

    result = asyncio.run(list_routing_profiles(auth="dummy"))
    assert result["active_routing"] == "default"
    by_name = {p["name"]: p["categories"] for p in result["profiles"]}
    assert by_name == {"default": {"chat": "ds"}, "claude-main": {"chat": "claude"}}
