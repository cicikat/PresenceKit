"""Brief 110: MCP 管理 API 不接真实网络，覆盖导入、白名单与热重载。"""
from __future__ import annotations

import asyncio

import pytest
import yaml
from fastapi import HTTPException

from admin.routers import settings_mcp as mod


def _write(tmp_path, text: str):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _patch_config(monkeypatch, path):
    monkeypatch.setattr(mod, "CONFIG_FILE", path)
    read = lambda: yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    monkeypatch.setattr(mod, "get_config", read)
    from core import config_loader
    monkeypatch.setattr(config_loader, "reload_config", lambda: read())


def _draft(**overrides):
    data = {
        "name": "cedar_toy",
        "url": "https://example.test/mcp",
        "headers": {"Authorization": "Bearer ${CEDAR_TOY_TOKEN}"},
        "allow_tools": ["toy_status"],
    }
    data.update(overrides)
    return mod.McpServerDraft(**data)


def test_import_tests_before_write_but_does_not_hot_reload(tmp_path, monkeypatch):
    """止血（2026-07-23）：跨 task 关闭已有 AsyncExitStack 会把整个 uvicorn 主循环一起
    取消带崩进程（观察到的真实 crash），在改造成常驻 task 生命周期之前，导入接口只
    探测 + 写配置，不再调用 reload_server_from_config。"""
    path = _write(tmp_path, "mcp_servers:\n  enabled: true\n  servers: []\n")
    _patch_config(monkeypatch, path)
    from core import mcp_client
    calls = []

    async def probe(cfg):
        calls.append(("probe", cfg))
        return [{"name": "toy_status", "description": "status"}]

    async def reload(name):
        calls.append(("reload", name))

    monkeypatch.setattr(mcp_client, "test_server_config", probe)
    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda name: {"connected": True, "tools": []})

    result = asyncio.run(mod.import_mcp_server(_draft(), _auth=None))

    assert result["tools"][0]["name"] == "toy_status"
    assert result["server"]["headers"]["Authorization"] == "Bearer ${CEDAR_TOY_TOKEN}"
    assert calls == [("probe", mod._validate_draft(_draft()))]
    assert "重启" in result["message"]
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert cfg["mcp_servers"]["servers"][0]["headers"]["Authorization"] == "Bearer ${CEDAR_TOY_TOKEN}"


def test_import_rejects_unknown_whitelist_without_writing(tmp_path, monkeypatch):
    path = _write(tmp_path, "mcp_servers:\n  enabled: false\n  servers: []\n")
    before = path.read_text(encoding="utf-8")
    _patch_config(monkeypatch, path)
    from core import mcp_client

    async def probe(_cfg):
        return [{"name": "known", "description": ""}]

    monkeypatch.setattr(mcp_client, "test_server_config", probe)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.import_mcp_server(_draft(allow_tools=["unknown"]), _auth=None))
    assert exc.value.status_code == 422
    assert path.read_text(encoding="utf-8") == before


def test_global_toggle_writes_config_without_hot_sync(tmp_path, monkeypatch):
    """止血：总开关只写配置，不再热同步（同上，跨 task 关闭连接会带崩整个进程）。"""
    path = _write(tmp_path, "mcp_servers:\n  enabled: false\n  servers: []\n")
    _patch_config(monkeypatch, path)
    from core import mcp_client
    calls = []

    async def sync():
        calls.append("sync")

    monkeypatch.setattr(mcp_client, "sync_mcp_servers", sync)
    result = asyncio.run(mod.update_mcp_settings(mod.McpSettingsUpdate(enabled=True), _auth=None))
    assert result["enabled"] is True
    assert calls == []
    assert "重启" in result["message"]
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert cfg["mcp_servers"]["enabled"] is True


def test_update_server_whitelist_writes_config_without_hot_reload(tmp_path, monkeypatch):
    """止血：单 server 更新只写配置，不再热重载（同上）。"""
    path = _write(tmp_path, "mcp_servers:\n  enabled: true\n  servers:\n    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n      allow_tools: []\n")
    _patch_config(monkeypatch, path)
    from core import mcp_client
    calls = []

    async def reload(name):
        calls.append(name)

    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda name: {"connected": False, "tools": []})
    result = asyncio.run(mod.update_mcp_server(
        "cedar_toy", mod.McpServerUpdate(allow_tools=["toy_status"]), _auth=None,
    ))
    assert result["server"]["allow_tools"] == ["toy_status"]
    assert calls == []
    assert "重启" in result["message"]
