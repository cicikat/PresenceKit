"""Brief 110: MCP 管理 API 不接真实网络，覆盖导入、白名单与热重载。"""
from __future__ import annotations

import asyncio

import pytest
import yaml
from fastapi import HTTPException
from pydantic import ValidationError

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


def test_remote_transport_validation_defaults_to_streamable_http_and_accepts_sse():
    assert mod._validate_draft(_draft())["transport"] == "streamable-http"
    assert mod._validate_draft(_draft())["use_proxy"] is False
    assert mod._validate_draft(_draft(transport="sse"))["transport"] == "sse"


def test_remote_transport_validation_keeps_legacy_http_alias_and_rejects_unknown():
    assert mod._validate_draft(_draft(transport="http"))["transport"] == "http"
    invalid = _draft().model_dump()
    invalid["transport"] = "websocket"
    with pytest.raises(ValidationError):
        mod.McpServerDraft(**invalid)


def test_server_view_redacts_literal_url_path_but_keeps_variable_binding(monkeypatch):
    from core import mcp_client

    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {"connected": False, "tools": []})
    literal = mod._server_view({
        "name": "cedar_toy", "transport": "streamable-http",
        "url": "https://gateway.test/mcp/a-literal-path-credential?token=also-secret",
    })
    template = mod._server_view({
        "name": "cedar_toy", "transport": "streamable-http",
        "url": "https://gateway.test/mcp/${MCP_URL_TOKEN}",
    })

    assert literal["url"] == "https://gateway.test/••••已配置"
    assert "credential" not in literal["url"]
    assert template["url"] == "https://gateway.test/mcp/${MCP_URL_TOKEN}"


def test_metadata_mapping_override_and_selector_round_trip_and_can_be_removed(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        "mcp_servers:\n  enabled: true\n  servers:\n"
        "    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n",
    )
    _patch_config(monkeypatch, path)
    from core import mcp_client

    async def reload(_name):
        return True

    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {
        "connected": True,
        "tools": [{
            "name": "inspect",
            "suggestion": {"effect": "read"},
            "remote_domains": ["remote"],
            "remote_interaction": "read",
            "metadata_source": "remote",
            "metadata_status": "recognized",
            "metadata_schema_version": 1,
            "final_domains": ["local"],
        }],
    })

    result = asyncio.run(mod.update_mcp_server(
        "cedar_toy",
        mod.McpServerUpdate(
            metadata_mapping=mod.McpMetadataMapping(
                namespace="io.any/tool", schema_versions=[1],
            ),
            metadata_overrides={
                "inspect": mod.McpMetadataOverride(mode="override", domains=["local"]),
            },
            domain_selector=mod.McpDomainSelector(
                domains=["local"], include_unclassified=True,
            ),
        ),
        _auth=None,
    ))

    stored = yaml.safe_load(path.read_text(encoding="utf-8"))["mcp_servers"]["servers"][0]
    assert stored["metadata_mapping"]["namespace"] == "io.any/tool"
    assert stored["metadata_overrides"] == {
        "inspect": {"mode": "override", "domains": ["local"]},
    }
    assert stored["domain_selector"] == {
        "domains": ["local"], "include_unclassified": True,
    }
    assert result["server"]["metadata_overrides"]["inspect"]["domains"] == ["local"]

    asyncio.run(mod.update_mcp_server(
        "cedar_toy",
        mod.McpServerUpdate(
            metadata_mapping=None, metadata_overrides=None, domain_selector=None,
        ),
        _auth=None,
    ))
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))["mcp_servers"]["servers"][0]
    assert "metadata_mapping" not in stored
    assert "metadata_overrides" not in stored
    assert "domain_selector" not in stored


def test_import_tests_before_write_and_hot_reloads(tmp_path, monkeypatch):
    """Brief 115 根治：MCP 连接生命周期已改成专属常驻 task 持有、管理面只发信号，
    不再跨 task 直接碰 AsyncExitStack，导入接口的热重载可以安全恢复。"""
    path = _write(tmp_path, "mcp_servers:\n  enabled: true\n  servers: []\n")
    _patch_config(monkeypatch, path)
    from core import mcp_client
    calls = []

    async def probe(cfg):
        calls.append(("probe", cfg))
        return [{"name": "toy_status", "description": "status"}]

    async def reload(name):
        calls.append(("reload", name))
        return True

    monkeypatch.setattr(mcp_client, "test_server_config", probe)
    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda name: {"connected": True, "tools": []})

    result = asyncio.run(mod.import_mcp_server(_draft(), _auth=None))

    assert result["tools"][0]["name"] == "toy_status"
    assert result["server"]["headers"]["Authorization"] == "Bearer ${CEDAR_TOY_TOKEN}"
    assert calls == [("probe", mod._validate_draft(_draft())), ("reload", "cedar_toy")]
    assert "重启" not in result["message"]
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


def test_reimport_preserves_explicit_policy_and_fills_new_tool_default(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        "mcp_servers:\n  enabled: true\n  require_local_policy: true\n  servers:\n"
        "    - name: cedar_toy\n      transport: http\n      url: https://old.test/mcp\n"
        "      allow_tools: [toy_status]\n      tool_policy:\n"
        "        toy_status: {effect: read}\n",
    )
    _patch_config(monkeypatch, path)
    from core import mcp_client

    async def probe(_cfg):
        return [
            {"name": "toy_status", "description": "status"},
            {"name": "send_message", "description": "send a message"},
        ]

    async def reload(_name):
        return True

    monkeypatch.setattr(mcp_client, "test_server_config", probe)
    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {
        "connected": True,
        "tools": [
            {"name": "toy_status", "description": "status"},
            {"name": "send_message", "description": "send a message"},
        ],
    })
    result = asyncio.run(mod.import_mcp_server(
        _draft(allow_tools=["toy_status", "send_message"]), _auth=None,
    ))

    states = {item["name"]: item for item in result["server"]["tool_states"]}
    assert states["toy_status"]["policy_status"] == "confirmed"
    assert states["send_message"]["policy_status"] == "confirmed"
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))["mcp_servers"]["servers"][0]
    assert stored["tool_policy"] == {
        "toy_status": {"effect": "read"},
        "send_message": {"effect": "write", "require_confirm": False},
    }


def test_reenable_disconnected_legacy_allowlist_backfills_policy_before_strict_reload(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        "mcp_servers:\n  enabled: true\n  require_local_policy: true\n  servers:\n"
        "    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n"
        "      enabled: false\n      allow_tools: [list_threads, create_thread]\n",
    )
    _patch_config(monkeypatch, path)
    from core import mcp_client
    calls = []

    async def probe(cfg):
        calls.append(("probe", cfg["name"]))
        return [
            {"name": "list_threads", "description": "list threads", "suggestion": {"effect": "read"}},
            {"name": "create_thread", "description": "create a thread", "suggestion": {"effect": "write"}},
        ]

    async def reload(name):
        calls.append(("reload", name))
        return True

    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {"connected": False, "tools": []})
    monkeypatch.setattr(mcp_client, "test_server_config", probe)
    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)

    result = asyncio.run(mod.update_mcp_server(
        "cedar_toy", mod.McpServerUpdate(enabled=True), _auth=None,
    ))

    stored = yaml.safe_load(path.read_text(encoding="utf-8"))["mcp_servers"]["servers"][0]
    assert stored["tool_policy"] == {
        "list_threads": {"effect": "read", "require_confirm": False},
        "create_thread": {"effect": "write", "require_confirm": False},
    }
    assert calls == [("probe", "cedar_toy"), ("reload", "cedar_toy")]
    assert result["reload_status"] == "reloaded"


def test_existing_explicit_policy_is_read_as_confirmed_without_config_migration(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        "mcp_servers:\n  enabled: true\n  require_local_policy: true\n  servers:\n"
        "    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n"
        "      allow_tools: [toy_status]\n      tool_policy:\n"
        "        toy_status: {effect: read}\n",
    )
    before = path.read_text(encoding="utf-8")
    _patch_config(monkeypatch, path)
    from core import mcp_client

    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {
        "connected": True,
        "tools": [{"name": "toy_status", "description": "status"}],
    })
    result = asyncio.run(mod.get_mcp_settings(_auth=None))

    state = result["servers"][0]["tool_states"][0]
    assert state["policy_status"] == "confirmed"
    assert state["policy"] == {"effect": "read"}
    assert path.read_text(encoding="utf-8") == before


def test_strict_import_fills_default_policy_for_each_allowlisted_tool(tmp_path, monkeypatch):
    path = _write(tmp_path, "mcp_servers:\n  enabled: true\n  require_local_policy: true\n  servers: []\n")
    _patch_config(monkeypatch, path)
    from core import mcp_client

    async def probe(_cfg):
        return [{"name": "toy_status", "description": "status"}]

    async def reload(_name):
        return True

    monkeypatch.setattr(mcp_client, "test_server_config", probe)
    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {
        "connected": True,
        "tools": [{"name": "toy_status", "description": "status"}],
    })
    result = asyncio.run(mod.import_mcp_server(_draft(), _auth=None))

    assert result["reload_status"] == "reloaded"
    assert result["server"]["tool_states"][0]["policy_status"] == "confirmed"
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert stored["mcp_servers"]["servers"][0]["tool_policy"] == {
        "toy_status": {"effect": "read", "require_confirm": False},
    }


def test_strict_import_defaults_unknown_tool_to_direct_write(tmp_path, monkeypatch):
    path = _write(tmp_path, "mcp_servers:\n  enabled: true\n  require_local_policy: true\n  servers: []\n")
    _patch_config(monkeypatch, path)
    from core import mcp_client

    async def probe(_cfg):
        return [{"name": "request_access", "description": ""}]

    async def reload(_name):
        return True

    monkeypatch.setattr(mcp_client, "test_server_config", probe)
    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {
        "connected": True,
        "tools": [{"name": "request_access", "description": ""}],
    })

    result = asyncio.run(mod.import_mcp_server(_draft(allow_tools=["request_access"]), _auth=None))

    assert result["server"]["tool_states"][0]["policy_status"] == "confirmed"
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert stored["mcp_servers"]["servers"][0]["tool_policy"] == {
        "request_access": {"effect": "write", "require_confirm": False},
    }


def test_global_toggle_writes_config_and_hot_syncs(tmp_path, monkeypatch):
    """Brief 115 根治：总开关热同步已恢复，sync_mcp_servers 只发信号给专属常驻 task。"""
    path = _write(tmp_path, "mcp_servers:\n  enabled: false\n  servers: []\n")
    _patch_config(monkeypatch, path)
    from core import mcp_client
    calls = []

    async def sync(*, wait=True):
        calls.append(("sync", wait))

    monkeypatch.setattr(mcp_client, "sync_mcp_servers", sync)
    result = asyncio.run(mod.update_mcp_settings(mod.McpSettingsUpdate(enabled=True), _auth=None))
    assert result["enabled"] is True
    assert calls == [("sync", False)]
    assert "重启" not in result["message"]
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert cfg["mcp_servers"]["enabled"] is True


def test_mcp_admin_uses_runtime_config_path(monkeypatch, tmp_path):
    """The admin writer must follow PRESENCEKIT_CONFIG_PATH/runtime loader path."""
    from admin.routers import settings_mcp
    from core import config_loader

    configured = tmp_path / "server-config.yaml"
    monkeypatch.setattr(config_loader, "_CONFIG_PATH", configured)
    monkeypatch.setattr(settings_mcp, "CONFIG_FILE", None)
    assert settings_mcp._config_file() == configured


def test_update_server_whitelist_writes_config_and_hot_reloads(tmp_path, monkeypatch):
    """Brief 115 根治：单 server 更新的热重载已恢复（同上）。"""
    path = _write(tmp_path, "mcp_servers:\n  enabled: true\n  servers:\n    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n      allow_tools: []\n")
    _patch_config(monkeypatch, path)
    from core import mcp_client
    calls = []

    async def reload(name):
        calls.append(name)
        return True

    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda name: {"connected": False, "tools": []})
    result = asyncio.run(mod.update_mcp_server(
        "cedar_toy", mod.McpServerUpdate(allow_tools=["toy_status"], use_proxy=True), _auth=None,
    ))
    assert result["server"]["allow_tools"] == ["toy_status"]
    assert result["server"]["use_proxy"] is True
    assert calls == ["cedar_toy"]
    assert "重启" not in result["message"]
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["mcp_servers"]["servers"][0]["use_proxy"] is True


def test_update_server_persists_per_tool_timeout_and_returns_it(tmp_path, monkeypatch):
    path = _write(tmp_path, "mcp_servers:\n  enabled: true\n  servers:\n    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n")
    _patch_config(monkeypatch, path)
    from core import mcp_client

    async def reload(_name):
        return True

    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {"connected": False, "tools": []})
    result = asyncio.run(mod.update_mcp_server(
        "cedar_toy", mod.McpServerUpdate(tool_timeouts_s={"hardware_sequence": 660}), _auth=None,
    ))

    assert result["server"]["tool_timeouts_s"] == {"hardware_sequence": 660.0}
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert stored["mcp_servers"]["servers"][0]["tool_timeouts_s"] == {"hardware_sequence": 660.0}


def test_per_tool_timeout_rejects_values_above_660_seconds():
    assert mod._normalize_tool_timeouts({"hardware_sequence": 660}) == {"hardware_sequence": 660.0}
    with pytest.raises(HTTPException, match="1-660 秒"):
        mod._normalize_tool_timeouts({"hardware_sequence": 661})


def test_update_server_reports_connection_failed_when_owner_reload_returns_false(tmp_path, monkeypatch):
    path = _write(tmp_path, "mcp_servers:\n  enabled: true\n  servers:\n    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n      allow_tools: [toy_status]\n")
    _patch_config(monkeypatch, path)
    from core import mcp_client

    async def reload(_name):
        return False

    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {"connected": False, "tools": []})
    result = asyncio.run(mod.update_mcp_server(
        "cedar_toy", mod.McpServerUpdate(enabled=False), _auth=None,
    ))
    assert result["reload_status"] == "connection_failed"
    assert "未能连接" in result["message"]
    assert "重启" not in result["message"]


def test_strict_policy_whitelist_update_preserves_confirmed_entries_and_fills_new_tool_default(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        "mcp_servers:\n  enabled: true\n  require_local_policy: true\n  servers:\n"
        "    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n"
        "      allow_tools: [toy_status]\n      tool_policy:\n"
        "        toy_status: {effect: read}\n",
    )
    _patch_config(monkeypatch, path)
    from core import mcp_client

    async def reload(name):
        assert name == "cedar_toy"
        return True

    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {
        "connected": True,
        "tools": [
            {"name": "toy_status", "description": "status"},
            {"name": "delete_thread", "description": "delete thread"},
        ],
    })
    result = asyncio.run(mod.update_mcp_server(
        "cedar_toy", mod.McpServerUpdate(allow_tools=["toy_status", "delete_thread"]), _auth=None,
    ))
    states = {item["name"]: item for item in result["server"]["tool_states"]}
    assert states["toy_status"]["policy_status"] == "confirmed"
    assert states["delete_thread"]["policy_status"] == "confirmed"
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert stored["mcp_servers"]["servers"][0]["tool_policy"] == {
        "toy_status": {"effect": "read"},
        "delete_thread": {"effect": "write", "require_confirm": False},
    }


def test_bulk_default_authorization_uses_runtime_snapshot_and_preserves_explicit_policy(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        "mcp_servers:\n  enabled: true\n  require_local_policy: true\n  servers:\n"
        "    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n"
        "      allow_tools: [toy_status]\n      tool_policy:\n"
        "        toy_status: {effect: actuate, require_confirm: true}\n",
    )
    _patch_config(monkeypatch, path)
    from core import mcp_client
    calls = []
    snapshot = [
        {"name": "toy_status", "description": "", "suggestion": {"effect": "read"}},
        {"name": "opaque_action", "description": "", "suggestion": {"effect": None}},
    ]

    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {
        "connected": True, "tools": snapshot,
    })

    async def reload(name):
        calls.append(name)
        return True

    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    result = asyncio.run(mod.update_mcp_server(
        "cedar_toy", mod.McpServerUpdate(bulk_authorize="default"), _auth=None,
    ))

    assert result["action"] == "default"
    assert result["processed_count"] == 2
    assert result["allow_tools"] == ["toy_status", "opaque_action"]
    assert result["tool_policy"] == {
        "toy_status": {"effect": "actuate", "require_confirm": True},
        "opaque_action": {"effect": "write", "require_confirm": False},
    }
    assert calls == ["cedar_toy"]
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert stored["mcp_servers"]["servers"][0]["allow_tools"] == ["toy_status", "opaque_action"]


def test_bulk_unrestricted_authorization_overrides_all_policy_once(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        "mcp_servers:\n  enabled: true\n  require_local_policy: true\n  servers:\n"
        "    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n"
        "      allow_tools: [old_tool]\n      tool_policy:\n"
        "        old_tool: {effect: read}\n",
    )
    _patch_config(monkeypatch, path)
    from core import mcp_client
    calls = []
    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {
        "connected": True,
        "tools": [{"name": "read_tool"}, {"name": "write_tool"}],
    })

    async def reload(name):
        calls.append(name)
        return True

    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    result = asyncio.run(mod.update_mcp_server(
        "cedar_toy", mod.McpServerUpdate(bulk_authorize="unrestricted"), _auth=None,
    ))

    expected = {
        "read_tool": {"effect": "unrestricted", "idempotent": True, "require_confirm": False},
        "write_tool": {"effect": "unrestricted", "idempotent": True, "require_confirm": False},
    }
    assert result["tool_policy"] == expected
    assert result["allow_tools"] == ["read_tool", "write_tool"]
    assert result["reload_status"] == "reloaded"
    assert calls == ["cedar_toy"]
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["mcp_servers"]["servers"][0]["tool_policy"] == expected


def test_bulk_authorization_rejects_disconnected_server_without_writing(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        "mcp_servers:\n  enabled: true\n  servers:\n"
        "    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n",
    )
    before = path.read_text(encoding="utf-8")
    _patch_config(monkeypatch, path)
    from core import mcp_client
    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {
        "connected": False, "tools": [],
    })

    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.update_mcp_server(
            "cedar_toy", mod.McpServerUpdate(bulk_authorize="default"), _auth=None,
        ))

    assert exc.value.status_code == 409
    assert path.read_text(encoding="utf-8") == before


def test_bulk_authorization_rejects_empty_runtime_directory_without_writing(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        "mcp_servers:\n  enabled: true\n  require_local_policy: true\n  servers:\n"
        "    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n",
    )
    before = path.read_text(encoding="utf-8")
    _patch_config(monkeypatch, path)
    from core import mcp_client
    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {
        "connected": True, "tools": [],
    })

    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.update_mcp_server(
            "cedar_toy", mod.McpServerUpdate(bulk_authorize="default"), _auth=None,
        ))

    assert exc.value.status_code == 409
    assert path.read_text(encoding="utf-8") == before


def test_bulk_authorization_reports_connection_failed_after_reload_returns_false(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        "mcp_servers:\n  enabled: true\n  require_local_policy: true\n  servers:\n"
        "    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n",
    )
    _patch_config(monkeypatch, path)
    from core import mcp_client
    calls = []
    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {
        "connected": True, "tools": [{"name": "toy_status"}],
    })

    async def reload(name):
        calls.append(name)
        return False

    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    result = asyncio.run(mod.update_mcp_server(
        "cedar_toy", mod.McpServerUpdate(bulk_authorize="default"), _auth=None,
    ))

    assert result["reload_status"] == "connection_failed"
    assert result["processed_count"] == 1
    assert calls == ["cedar_toy"]
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert stored["mcp_servers"]["servers"][0]["allow_tools"] == ["toy_status"]


def test_update_server_reports_restart_required_when_reload_returns_none(tmp_path, monkeypatch):
    path = _write(tmp_path, "mcp_servers:\n  enabled: true\n  servers:\n    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n      allow_tools: [toy_status]\n")
    _patch_config(monkeypatch, path)
    from core import mcp_client

    async def reload(_name):
        return None

    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {"connected": False, "tools": []})
    result = asyncio.run(mod.update_mcp_server(
        "cedar_toy", mod.McpServerUpdate(enabled=True), _auth=None,
    ))
    assert result["reload_status"] == "restart_required"
    assert "重启" in result["message"]


def test_reconnect_server_reuses_hot_reload_and_reports_connection_failed(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        "mcp_servers:\n  enabled: true\n  servers:\n"
        "    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n",
    )
    _patch_config(monkeypatch, path)
    from core import mcp_client
    calls = []

    async def reload(name):
        calls.append(name)
        return False

    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {
        "connected": False, "tools": [], "last_init_error": "handshake timeout",
    })
    result = asyncio.run(mod.reconnect_mcp_server("cedar_toy", _auth=None))
    assert calls == ["cedar_toy"]
    assert result["reload_status"] == "connection_failed"
    assert "未能连上" in result["message"]
    assert "handshake timeout" in result["message"]
    assert result["last_init_error"] == "handshake timeout"
    assert "重启" not in result["message"]


def test_reconnect_missing_server_is_404(tmp_path, monkeypatch):
    path = _write(tmp_path, "mcp_servers:\n  enabled: true\n  servers: []\n")
    _patch_config(monkeypatch, path)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.reconnect_mcp_server("missing", _auth=None))
    assert exc.value.status_code == 404


def test_strict_policy_accepts_complete_whitelist_and_policy(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        "mcp_servers:\n  enabled: true\n  require_local_policy: true\n  servers:\n"
        "    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n"
        "      allow_tools: [toy_status]\n      tool_policy:\n"
        "        toy_status: {effect: read}\n",
    )
    _patch_config(monkeypatch, path)
    from core import mcp_client
    calls = []

    async def reload(name):
        calls.append(name)
        return True

    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda name: {"connected": True, "tools": []})
    result = asyncio.run(mod.update_mcp_server(
        "cedar_toy",
        mod.McpServerUpdate(
            allow_tools=["toy_status", "delete_thread"],
            tool_policy={
                "toy_status": mod.McpToolPolicy(effect="read"),
                "delete_thread": mod.McpToolPolicy(effect="write"),
            },
        ),
        _auth=None,
    ))

    assert result["server"]["tool_policy"]["delete_thread"] == {"effect": "write"}
    assert calls == ["cedar_toy"]


def test_tool_policy_ui_label_is_persisted_and_returned(tmp_path, monkeypatch):
    path = _write(
        tmp_path,
        "mcp_servers:\n  enabled: true\n  require_local_policy: true\n  servers:\n"
        "    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n"
        "      allow_tools: [toy_status]\n",
    )
    _patch_config(monkeypatch, path)
    from core import mcp_client

    async def reload(_name):
        return True

    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {
        "connected": True,
        "tools": [{"name": "toy_status", "description": "remote status"}],
    })
    result = asyncio.run(mod.update_mcp_server(
        "cedar_toy",
        mod.McpServerUpdate(tool_policy={
            "toy_status": mod.McpToolPolicy(effect="read", ui_label="查看设备状态"),
        }),
        _auth=None,
    ))

    assert result["server"]["tool_policy"]["toy_status"] == {
        "effect": "read", "ui_label": "查看设备状态",
    }
    stored = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert stored["mcp_servers"]["servers"][0]["tool_policy"]["toy_status"]["ui_label"] == "查看设备状态"


def test_selecting_named_tool_preset_updates_runtime_allowlist(tmp_path, monkeypatch):
    path = _write(tmp_path, "mcp_servers:\n  enabled: true\n  servers:\n    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n      allow_tools: [toy_status]\n      tool_presets:\n        - name: 只读\n          tools: [toy_status]\n        - name: 对局\n          tools: [toy_status, play]\n")
    _patch_config(monkeypatch, path)
    from core import mcp_client
    calls = []

    async def reload(name):
        calls.append(name)

    monkeypatch.setattr(mcp_client, "reload_server_from_config", reload)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda name: {"connected": True, "tools": []})
    result = asyncio.run(mod.update_mcp_server(
        "cedar_toy", mod.McpServerUpdate(active_tool_preset="对局"), _auth=None,
    ))

    assert result["server"]["active_tool_preset"] == "对局"
    assert result["server"]["allow_tools"] == ["toy_status", "play"]
    assert calls == ["cedar_toy"]


def test_delete_server_removes_config_and_syncs_its_runtime(tmp_path, monkeypatch):
    path = _write(tmp_path, "mcp_servers:\n  enabled: true\n  servers:\n    - name: cedar_toy\n      transport: http\n      url: https://example.test/mcp\n    - name: keep_me\n      transport: http\n      url: https://example.test/keep\n")
    _patch_config(monkeypatch, path)
    from core import mcp_client

    calls = []

    async def sync():
        calls.append("sync")

    monkeypatch.setattr(mcp_client, "sync_mcp_servers", sync)
    result = asyncio.run(mod.delete_mcp_server("cedar_toy", _auth=None))

    assert result == {"message": "MCP server 已删除并断开", "deleted": "cedar_toy"}
    assert calls == ["sync"]
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [item["name"] for item in cfg["mcp_servers"]["servers"]] == ["keep_me"]


def test_delete_missing_server_returns_not_found(tmp_path, monkeypatch):
    path = _write(tmp_path, "mcp_servers:\n  enabled: true\n  servers: []\n")
    _patch_config(monkeypatch, path)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.delete_mcp_server("missing", _auth=None))

    assert exc.value.status_code == 404


def _install_console_tool(monkeypatch, *, require_local_policy=False):
    from core import mcp_client, tool_dispatcher

    config = {
        "scheduler": {"owner_id": "owner"},
        "mcp_servers": {
            "enabled": True,
            "require_local_policy": require_local_policy,
            "servers": [{
                "name": "cedar_toy",
                "enabled": True,
                "allow_tools": ["toy_status"],
                "tool_policy": {"toy_status": {"effect": "read"}},
            }],
        },
    }
    monkeypatch.setattr(mod, "get_config", lambda: config)
    monkeypatch.setattr(mcp_client, "server_runtime", lambda _name: {"connected": True, "tools": []})
    monkeypatch.setitem(tool_dispatcher._TOOL_REGISTRY, "mcp__cedar_toy__toy_status", {
        "category": "mcp",
        "mcp_server": "cedar_toy",
        "mcp_tool": "toy_status",
        "parameters": {
            "type": "object",
            "properties": {"verbose": {"type": "boolean"}},
            "required": ["verbose"],
            "additionalProperties": False,
        },
        "effect": "read",
        "require_confirm": False,
    })
    return config


def test_console_rejects_non_allowlisted_tool(tmp_path, monkeypatch):
    _install_console_tool(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        mod._resolve_console_tool("cedar_toy", "other_tool")
    assert exc.value.status_code == 403


def test_console_rejects_pending_local_policy(monkeypatch):
    config = _install_console_tool(monkeypatch, require_local_policy=True)
    config["mcp_servers"]["servers"][0]["tool_policy"] = {}
    with pytest.raises(HTTPException) as exc:
        mod._resolve_console_tool("cedar_toy", "toy_status")
    assert exc.value.status_code == 409


def test_console_schema_validation_redacts_argument_value(monkeypatch):
    _install_console_tool(monkeypatch)
    _, info = mod._resolve_console_tool("cedar_toy", "toy_status")
    with pytest.raises(HTTPException) as exc:
        mod._validate_console_arguments({"verbose": "secret-value"}, info["parameters"])
    assert exc.value.status_code == 422
    assert "secret-value" not in str(exc.value.detail)


def test_console_confirmation_reuses_ticket_arguments_and_audit_id(monkeypatch):
    _install_console_tool(monkeypatch)
    mod._console_confirmations.clear()
    calls = []

    async def fake_run(*, registered_name, arguments, audit_id, confirmed):
        from core.tool_dispatcher import ToolExecutionOutcome
        calls.append((registered_name, arguments, audit_id, confirmed))
        if not confirmed:
            return ToolExecutionOutcome(
                status="confirmation_required",
                confirmation_request="确认后执行",
            )
        return ToolExecutionOutcome(
            status="tool_executed",
            result="工具已执行：mcp__cedar_toy__toy_status，结果：ok",
        )

    monkeypatch.setattr(mod, "_run_console_tool", fake_run)
    initial = asyncio.run(mod.invoke_mcp_console(
        mod.McpConsoleInvoke(server="cedar_toy", tool="toy_status", arguments={"verbose": True}),
        _auth=None,
    ))
    assert initial["status"] == "confirmation_required"
    result = asyncio.run(mod.confirm_mcp_console(
        mod.McpConsoleConfirm(confirmation_id=initial["confirmation_id"]), _auth=None,
    ))

    assert result == {
        "status": "completed",
        "audit_id": initial["audit_id"],
        "result": "工具已执行：mcp__cedar_toy__toy_status，结果：ok",
    }
    assert calls == [
        ("mcp__cedar_toy__toy_status", {"verbose": True}, initial["audit_id"], False),
        ("mcp__cedar_toy__toy_status", {"verbose": True}, initial["audit_id"], True),
    ]
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mod.confirm_mcp_console(
            mod.McpConsoleConfirm(confirmation_id=initial["confirmation_id"]), _auth=None,
        ))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_console_origin_runs_through_dispatcher(monkeypatch, sandbox):
    from core import tool_dispatcher as td

    async def tool_func(**_kwargs):
        return "ok"

    monkeypatch.setitem(td._TOOL_REGISTRY, "mcp__cedar_toy__toy_status", {
        "func": tool_func,
        "description": "", "dangerous": False, "category": "mcp",
        "parameters": {}, "effect": "read", "mcp_server": "cedar_toy", "mcp_tool": "toy_status",
    })
    monkeypatch.setattr("core.growth.mcp_proficiency.is_tool_allowed", lambda *args, **kwargs: False)
    monkeypatch.setattr("core.memory.action_trace.record", lambda *args, **kwargs: None)
    result = await td.execute_structured(
        "mcp__cedar_toy__toy_status", {}, "owner", "owner", False,
        mod._ConsoleSessionState(), origin="admin_console", char_id="char",
    )
    assert result.confirmation_request is None
    assert result.result == "工具已执行：mcp__cedar_toy__toy_status，结果：ok"


@pytest.mark.asyncio
async def test_console_runner_uses_dispatcher_confirmation(monkeypatch, sandbox):
    _install_console_tool(monkeypatch)
    from core import tool_dispatcher as td

    async def tool_func(**_kwargs):
        return "ok"

    monkeypatch.setitem(td._TOOL_REGISTRY, "mcp__cedar_toy__toy_status", {
        "func": tool_func,
        "description": "", "dangerous": True, "category": "mcp",
        "parameters": {}, "effect": "write", "mcp_server": "cedar_toy", "mcp_tool": "toy_status",
    })
    monkeypatch.setattr("core.memory.action_trace.record", lambda *args, **kwargs: None)
    pending = await mod._run_console_tool(
        registered_name="mcp__cedar_toy__toy_status", arguments={}, audit_id="audit", confirmed=False,
    )
    assert pending.result is None
    assert pending.confirmation_request is not None
    assert pending.status == "confirmation_required"
    completed = await mod._run_console_tool(
        registered_name="mcp__cedar_toy__toy_status", arguments={}, audit_id="audit", confirmed=True,
    )
    assert completed.confirmation_request is None
    assert completed.status == "tool_executed"
    assert completed.result == "工具已执行：mcp__cedar_toy__toy_status，结果：ok"
