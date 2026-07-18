"""
tests/test_settings_setup_center.py — Brief 93 §1：管理面板「配置中心」后端接口

GET/PUT /settings/base-model   — 基础聊天模型连接（legacy llm: 块 / model_presets 主 preset 透明兼容）
GET/PUT /settings/embedding    — 语义 Embedding（长期记忆语义召回，缺失 fail-open 不阻塞）
GET     /settings/setup-status — 必填缺失判定（首启自动跳转 + 顶部横幅依据）
"""
import asyncio

import yaml

from admin.routers import settings_llm as mod


def _write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _patch(monkeypatch, path):
    monkeypatch.setattr(mod, "CONFIG_FILE", path)
    monkeypatch.setattr(mod, "get_config", lambda: yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    from core import config_loader, llm_client
    monkeypatch.setattr(config_loader, "reload_config", lambda: None)
    monkeypatch.setattr(llm_client, "reload_client", lambda: None)


# ── /settings/base-model — legacy llm: 块 ───────────────────────────────────

def test_base_model_legacy_placeholder_is_not_configured(tmp_path, monkeypatch):
    path = _write(tmp_path, "llm:\n  base_url: https://api.deepseek.com\n  api_key: YOUR_DEEPSEEK_API_KEY\n  model: deepseek-chat\n")
    _patch(monkeypatch, path)
    result = asyncio.run(mod.get_base_model(auth=None))
    assert result["mode"] == "legacy"
    assert result["configured"] is False
    assert result["api_key_set"] is False


def test_base_model_legacy_write_and_read_back_masked(tmp_path, monkeypatch):
    path = _write(tmp_path, "llm:\n  base_url: https://api.deepseek.com\n  api_key: YOUR_DEEPSEEK_API_KEY\n  model: deepseek-chat\n")
    _patch(monkeypatch, path)

    result = asyncio.run(mod.update_base_model(
        mod.BaseModelUpdate(api_key="sk-realsecretkeyvalue1234"), auth=None,
    ))
    assert result["configured"] is True
    assert result["api_key_set"] is True
    assert "sk-realsecretkeyvalue1234" not in result["api_key_masked"]

    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert cfg["llm"]["api_key"] == "sk-realsecretkeyvalue1234"
    # base_url / model 未传入，保持原值不被清空
    assert cfg["llm"]["base_url"] == "https://api.deepseek.com"
    assert cfg["llm"]["model"] == "deepseek-chat"


def test_base_model_update_rejects_empty_body(tmp_path, monkeypatch):
    import pytest
    from fastapi import HTTPException
    path = _write(tmp_path, "llm:\n  base_url: x\n")
    _patch(monkeypatch, path)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.update_base_model(mod.BaseModelUpdate(), auth=None))
    assert exc.value.status_code == 422


# ── /settings/base-model — model_presets 主 preset ──────────────────────────

def test_base_model_preset_mode_resolves_active_chat_preset(tmp_path, monkeypatch):
    path = _write(tmp_path, yaml.safe_dump({
        "model_presets": {
            "active_routing": "default",
            "presets": {
                "deepseek-default": {
                    "provider_kind": "deepseek",
                    "base_url": "https://api.deepseek.com",
                    "api_key": "YOUR_DEEPSEEK_API_KEY",
                    "model": "deepseek-chat",
                },
            },
            "routing_profiles": {"default": {"chat": "deepseek-default"}},
        },
    }, allow_unicode=True))
    _patch(monkeypatch, path)

    before = asyncio.run(mod.get_base_model(auth=None))
    assert before["mode"] == "preset"
    assert before["preset_name"] == "deepseek-default"
    assert before["configured"] is False

    after = asyncio.run(mod.update_base_model(
        mod.BaseModelUpdate(api_key="sk-realkey", model="deepseek-chat", base_url="https://api.deepseek.com"),
        auth=None,
    ))
    assert after["configured"] is True

    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    preset = cfg["model_presets"]["presets"]["deepseek-default"]
    assert preset["api_key"] == "sk-realkey"
    # provider_kind 等既有字段不受影响
    assert preset["provider_kind"] == "deepseek"


# ── /settings/embedding ──────────────────────────────────────────────────────

def test_embedding_placeholder_is_not_configured(tmp_path, monkeypatch):
    path = _write(tmp_path, "embedding:\n  base_url: https://x\n  api_key: YOUR_EMBEDDING_KEY\n  model: bge-m3\n  dim: 1024\n")
    _patch(monkeypatch, path)
    result = asyncio.run(mod.get_embedding_settings(auth=None))
    assert result["configured"] is False


def test_embedding_missing_block_is_not_configured(tmp_path, monkeypatch):
    path = _write(tmp_path, "other:\n  x: 1\n")
    _patch(monkeypatch, path)
    result = asyncio.run(mod.get_embedding_settings(auth=None))
    assert result["configured"] is False
    assert result["dim"] is None


def test_embedding_write_marks_configured(tmp_path, monkeypatch):
    path = _write(tmp_path, "embedding:\n  base_url: https://x\n  api_key: YOUR_EMBEDDING_KEY\n  model: bge-m3\n  dim: 1024\n")
    _patch(monkeypatch, path)
    result = asyncio.run(mod.update_embedding_settings(
        mod.EmbeddingSettingsUpdate(api_key="sk-realembedkey"), auth=None,
    ))
    assert result["configured"] is True
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert cfg["embedding"]["api_key"] == "sk-realembedkey"
    assert cfg["embedding"]["dim"] == 1024


def test_embedding_dim_out_of_range_rejected(tmp_path, monkeypatch):
    import pytest
    from fastapi import HTTPException
    path = _write(tmp_path, "embedding: {}\n")
    _patch(monkeypatch, path)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.update_embedding_settings(mod.EmbeddingSettingsUpdate(dim=0), auth=None))
    assert exc.value.status_code == 422


# ── /settings/setup-status ───────────────────────────────────────────────────

def test_setup_status_needs_setup_when_base_chat_missing(tmp_path, monkeypatch):
    path = _write(tmp_path, "llm:\n  base_url: x\n  api_key: YOUR_DEEPSEEK_API_KEY\n  model: deepseek-chat\n")
    _patch(monkeypatch, path)
    result = asyncio.run(mod.get_setup_status(auth=None))
    assert result["needs_setup"] is True
    assert result["base_chat"]["configured"] is False


def test_setup_status_needs_setup_when_owner_id_missing(tmp_path, monkeypatch):
    # Brief 95 §1：owner_id 升为必填②，即便基础聊天模型已配置，owner_id 缺失仍要 needs_setup=True
    path = _write(tmp_path, "llm:\n  base_url: https://api.deepseek.com\n  api_key: sk-real\n  model: deepseek-chat\n")
    _patch(monkeypatch, path)
    result = asyncio.run(mod.get_setup_status(auth=None))
    assert result["needs_setup"] is True
    assert result["base_chat"]["configured"] is True
    assert result["owner"]["configured"] is False


def test_setup_status_ready_when_base_chat_and_owner_configured(tmp_path, monkeypatch):
    path = _write(tmp_path, (
        "llm:\n  base_url: https://api.deepseek.com\n  api_key: sk-real\n  model: deepseek-chat\n"
        "scheduler:\n  owner_id: '123456'\n"
    ))
    _patch(monkeypatch, path)
    result = asyncio.run(mod.get_setup_status(auth=None))
    assert result["needs_setup"] is False
    assert result["base_chat"]["configured"] is True
    assert result["owner"]["configured"] is True
    assert result["owner"]["owner_id"] == "123456"
