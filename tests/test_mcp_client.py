"""
tests/test_mcp_client.py — Brief 29 · 4 MCP 客户端

覆盖 cc-tasks/29-本我模式-角色卡扩展-MCP接入.md §7 第5项：
mock server（stub ClientSession）→ list_tools 注册、命名前缀、同名让位、
call_tool 超时/异常→重连一次→再失败按失败处理、断线重连成功、action_trace 不落参数
（不声明 trace_args）。

真实 mcp SDK 的 ClientSession / stdio_client 被替换为进程内 stub，不发起真实子进程或网络连接。
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from types import SimpleNamespace

import pytest

import core.mcp_client as mc
import core.tool_dispatcher as td


class _FakeSession:
    """Duck-typed ClientSession stub：支持 async context manager + initialize/list_tools/call_tool。"""

    def __init__(self, call_results=None):
        self.initialized = False
        self.tools_result = SimpleNamespace(tools=[])
        self._call_results = list(call_results or [])
        self.call_log: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        self.initialized = True

    async def list_tools(self):
        return self.tools_result

    async def call_tool(self, name, arguments):
        self.call_log.append((name, arguments))
        item = self._call_results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture(autouse=True)
def _clean_registry_and_servers(monkeypatch):
    """隔离真实 _TOOL_REGISTRY / _servers，测试结束后还原。"""
    monkeypatch.setattr(td, "_TOOL_REGISTRY", dict(td._TOOL_REGISTRY))
    mc._servers.clear()
    mc._server_status.clear()
    yield
    mc._servers.clear()
    mc._server_status.clear()


async def _noop_transport(stack, server_cfg):
    return None, None


def _patch_client_session(monkeypatch, session_or_factory):
    import mcp
    if callable(session_or_factory) and not isinstance(session_or_factory, _FakeSession):
        monkeypatch.setattr(mcp, "ClientSession", session_or_factory)
    else:
        monkeypatch.setattr(mcp, "ClientSession", lambda read, write: session_or_factory)


# ─────────────────────────────────────────────────────────────────────────────
# 注册：list_tools → _TOOL_REGISTRY，命名前缀，category=mcp，不声明 trace_args
# ─────────────────────────────────────────────────────────────────────────────

class TestConnectServerRegistration:
    async def test_registers_tools_with_prefixed_name(self, monkeypatch):
        session = _FakeSession()
        session.tools_result = SimpleNamespace(tools=[
            SimpleNamespace(name="read_file", description="read a file",
                             inputSchema={"type": "object", "properties": {}}),
        ])
        monkeypatch.setattr(mc, "_open_transport", _noop_transport)
        _patch_client_session(monkeypatch, session)

        await mc._connect_server("srv1", {"transport": "stdio", "command": ["fake"], "tool_timeout_s": 5})

        assert "mcp__srv1__read_file" in td._TOOL_REGISTRY
        entry = td._TOOL_REGISTRY["mcp__srv1__read_file"]
        assert entry["category"] == "mcp"
        assert entry["dangerous"] is False
        assert "trace_args" not in entry, "MCP 工具不应声明 trace_args（参数不落 action_trace）"
        assert "srv1" in mc._servers
        assert mc._servers["srv1"].tool_names == ["mcp__srv1__read_file"]

    async def test_allow_tools_whitelist_filters(self, monkeypatch):
        session = _FakeSession()
        session.tools_result = SimpleNamespace(tools=[
            SimpleNamespace(name="read_file", description="", inputSchema={}),
            SimpleNamespace(name="write_file", description="", inputSchema={}),
        ])
        monkeypatch.setattr(mc, "_open_transport", _noop_transport)
        _patch_client_session(monkeypatch, session)

        await mc._connect_server("srv1", {
            "transport": "stdio", "command": ["fake"], "allow_tools": ["read_file"],
        })

        assert "mcp__srv1__read_file" in td._TOOL_REGISTRY
        assert "mcp__srv1__write_file" not in td._TOOL_REGISTRY

    async def test_name_collision_static_side_wins(self, monkeypatch):
        td._TOOL_REGISTRY["mcp__srv1__read_file"] = {"marker": "static"}
        session = _FakeSession()
        session.tools_result = SimpleNamespace(tools=[
            SimpleNamespace(name="read_file", description="", inputSchema={}),
        ])
        monkeypatch.setattr(mc, "_open_transport", _noop_transport)
        _patch_client_session(monkeypatch, session)

        await mc._connect_server("srv1", {"transport": "stdio", "command": ["fake"]})

        assert td._TOOL_REGISTRY["mcp__srv1__read_file"] == {"marker": "static"}
        assert mc._servers["srv1"].tool_names == []


# ─────────────────────────────────────────────────────────────────────────────
# init_mcp_servers: 总开关 + 单 server 失败隔离
# ─────────────────────────────────────────────────────────────────────────────

class TestInitMcpServers:
    async def test_disabled_is_noop(self, monkeypatch):
        monkeypatch.setattr("core.config_loader.get_config", lambda: {"mcp_servers": {"enabled": False}})
        await mc.init_mcp_servers()
        assert mc._servers == {}

    async def test_single_server_failure_isolated(self, monkeypatch):
        cfg = {"mcp_servers": {"enabled": True, "servers": [
            {"name": "bad", "transport": "unknown-transport"},
            {"name": "good", "transport": "stdio", "command": ["fake"]},
        ]}}
        monkeypatch.setattr("core.config_loader.get_config", lambda: cfg)

        good_session = _FakeSession()
        good_session.tools_result = SimpleNamespace(tools=[])

        async def _fake_open_transport(stack, server_cfg):
            if server_cfg.get("transport") == "unknown-transport":
                raise ValueError("unsupported transport")
            return None, None
        monkeypatch.setattr(mc, "_open_transport", _fake_open_transport)
        _patch_client_session(monkeypatch, good_session)

        await mc.init_mcp_servers()

        assert "bad" not in mc._servers
        assert "good" in mc._servers

    async def test_per_server_disabled_is_skipped(self, monkeypatch):
        monkeypatch.setattr("core.config_loader.get_config", lambda: {
            "mcp_servers": {"enabled": True, "servers": [
                {"name": "off", "enabled": False, "transport": "stdio", "command": ["fake"]},
            ]}
        })
        await mc.init_mcp_servers()
        assert mc._servers == {}


class TestHttpHeadersAndProbe:
    def test_headers_expand_environment_variables(self, monkeypatch):
        monkeypatch.setenv("MCP_TEST_TOKEN", "secret-value")
        assert mc._expand_headers({"Authorization": "Bearer ${MCP_TEST_TOKEN}"}) == {
            "Authorization": "Bearer secret-value",
        }

    def test_headers_missing_environment_variable_fails_closed(self, monkeypatch):
        monkeypatch.delenv("MCP_MISSING_TOKEN", raising=False)
        with pytest.raises(ValueError, match="MCP_MISSING_TOKEN"):
            mc._expand_headers({"Authorization": "Bearer ${MCP_MISSING_TOKEN}"})

    async def test_probe_lists_tools_without_registering_them(self, monkeypatch):
        session = _FakeSession()
        session.tools_result = SimpleNamespace(tools=[
            SimpleNamespace(name="inspect", description="inspect status", inputSchema={}),
        ])
        monkeypatch.setattr(mc, "_open_transport", _noop_transport)
        _patch_client_session(monkeypatch, session)

        tools = await mc.test_server_config({"name": "remote", "transport": "http", "url": "https://x/mcp"})

        assert tools == [{"name": "inspect", "description": "inspect status"}]
        assert "mcp__remote__inspect" not in td._TOOL_REGISTRY


# ─────────────────────────────────────────────────────────────────────────────
# _call_tool: 成功截断、isError 抛错、断线重连一次
# ─────────────────────────────────────────────────────────────────────────────

class TestCallTool:
    def _install_handle(self, name, session, tool_names=None):
        handle = mc._ServerHandle(
            name=name, cfg={"transport": "stdio", "command": ["fake"]},
            stack=AsyncExitStack(), session=session, tool_names=tool_names or [],
        )
        mc._servers[name] = handle
        return handle

    async def test_success_joins_and_truncates_at_2000(self):
        long_text = "x" * 3000
        session = _FakeSession(call_results=[
            SimpleNamespace(content=[SimpleNamespace(text=long_text)], isError=False),
        ])
        self._install_handle("srv1", session)

        result = await mc._call_tool("srv1", "toolA", {}, 5)
        assert len(result) == 2001
        assert result.endswith("…")

    async def test_multi_content_items_joined_with_newline(self):
        session = _FakeSession(call_results=[
            SimpleNamespace(content=[
                SimpleNamespace(text="line1"),
                SimpleNamespace(text="line2"),
            ], isError=False),
        ])
        self._install_handle("srv1", session)

        result = await mc._call_tool("srv1", "toolA", {}, 5)
        assert result == "line1\nline2"

    async def test_is_error_raises(self):
        session = _FakeSession(call_results=[
            SimpleNamespace(content=[SimpleNamespace(text="boom")], isError=True),
        ])
        self._install_handle("srv1", session)

        with pytest.raises(RuntimeError):
            await mc._call_tool("srv1", "toolA", {}, 5)

    async def test_reconnect_once_then_success(self, monkeypatch):
        dead_session = _FakeSession(call_results=[RuntimeError("connection dead")])
        self._install_handle("srv1", dead_session, tool_names=["mcp__srv1__toolA"])
        td._TOOL_REGISTRY["mcp__srv1__toolA"] = {"category": "mcp"}

        new_session = _FakeSession(call_results=[
            SimpleNamespace(content=[SimpleNamespace(text="ok")], isError=False),
        ])

        async def _fake_connect_server(name, cfg):
            mc._servers[name] = mc._ServerHandle(
                name=name, cfg=cfg, stack=AsyncExitStack(),
                session=new_session, tool_names=["mcp__srv1__toolA"],
            )
        monkeypatch.setattr(mc, "_connect_server", _fake_connect_server)

        result = await mc._call_tool("srv1", "toolA", {}, 5)
        assert result == "ok"

    async def test_reconnect_then_fail_raises(self, monkeypatch):
        dead_session = _FakeSession(call_results=[RuntimeError("dead once")])
        self._install_handle("srv1", dead_session, tool_names=["mcp__srv1__toolA"])

        async def _fake_connect_server(name, cfg):
            still_dead = _FakeSession(call_results=[RuntimeError("dead twice")])
            mc._servers[name] = mc._ServerHandle(
                name=name, cfg=cfg, stack=AsyncExitStack(),
                session=still_dead, tool_names=["mcp__srv1__toolA"],
            )
        monkeypatch.setattr(mc, "_connect_server", _fake_connect_server)

        with pytest.raises(RuntimeError):
            await mc._call_tool("srv1", "toolA", {}, 5)

    async def test_unknown_server_raises(self):
        with pytest.raises(RuntimeError):
            await mc._call_tool("does-not-exist", "toolA", {}, 5)

    async def test_call_is_written_to_api_ledger_without_arguments(self, monkeypatch):
        session = _FakeSession(call_results=[
            SimpleNamespace(content=[SimpleNamespace(text="ok")], isError=False),
        ])
        self._install_handle("srv1", session)
        rows = []
        monkeypatch.setattr("core.api_call_log.append", lambda **kwargs: rows.append(kwargs))

        assert await mc._call_tool("srv1", "toolA", {"secret": "never-log"}, 5) == "ok"
        assert rows[0]["caller"] == "mcp__srv1__toolA"
        assert "secret" not in str(rows[0])
        assert mc.server_runtime("srv1")["last_call_ok"] is True


# ─────────────────────────────────────────────────────────────────────────────
# _reconnect_server: 摘除旧注册条目再重连
# ─────────────────────────────────────────────────────────────────────────────

class TestReconnectServer:
    async def test_removes_old_registry_entries_before_reconnect(self, monkeypatch):
        td._TOOL_REGISTRY["mcp__srv1__toolA"] = {"marker": "old"}
        handle = mc._ServerHandle(
            name="srv1", cfg={"transport": "stdio", "command": ["fake"]},
            stack=AsyncExitStack(), session=_FakeSession(), tool_names=["mcp__srv1__toolA"],
        )
        mc._servers["srv1"] = handle

        called = {}

        async def _fake_connect_server(name, cfg):
            called["name"] = name
        monkeypatch.setattr(mc, "_connect_server", _fake_connect_server)

        await mc._reconnect_server("srv1")

        assert "mcp__srv1__toolA" not in td._TOOL_REGISTRY
        assert called.get("name") == "srv1"

    async def test_reconnect_unknown_server_is_noop(self):
        await mc._reconnect_server("ghost")  # 不应抛错


# ─────────────────────────────────────────────────────────────────────────────
# shutdown_mcp_servers
# ─────────────────────────────────────────────────────────────────────────────

class TestShutdown:
    async def test_shutdown_clears_all_servers(self):
        handle = mc._ServerHandle(
            name="srv1", cfg={}, stack=AsyncExitStack(), session=_FakeSession(), tool_names=[],
        )
        mc._servers["srv1"] = handle

        await mc.shutdown_mcp_servers()
        assert mc._servers == {}
