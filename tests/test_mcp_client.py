"""
tests/test_mcp_client.py — Brief 29 · 4 MCP 客户端

覆盖 cc-tasks/29-本我模式-角色卡扩展-MCP接入.md §7 第5项：
mock server（stub ClientSession）→ list_tools 注册、命名前缀、同名让位、
call_tool 超时/异常→重连一次→再失败按失败处理、断线重连成功、action_trace 不落参数
（不声明 trace_args）。

真实 mcp SDK 的 ClientSession / stdio_client 被替换为进程内 stub，不发起真实子进程或网络连接。
"""

from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import asyncio
import json
from contextlib import AsyncExitStack, asynccontextmanager
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

    async def call_tool(self, name, arguments, *, meta=None):
        self.call_log.append((name, arguments, meta))
        item = self._call_results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture(autouse=True)
async def _clean_registry_and_servers(monkeypatch):
    """隔离真实 _TOOL_REGISTRY / _servers / _owners，测试结束后还原并回收专属 task。"""
    monkeypatch.setattr(td, "_TOOL_REGISTRY", dict(td._TOOL_REGISTRY))
    # Existing MCP regression cases intentionally exercise legacy configs.
    # Strict local-policy cases override this inside their own tests.
    monkeypatch.setattr(mc, "_require_local_policy", lambda: False)
    mc._servers.clear()
    mc._server_status.clear()
    mc._owners.clear()
    yield
    for owner in list(mc._owners.values()):
        owner.task.cancel()
    for owner in list(mc._owners.values()):
        try:
            await owner.task
        except BaseException:
            pass
    mc._owners.clear()
    mc._servers.clear()
    mc._server_status.clear()


async def _noop_transport(stack, server_cfg):
    return None, None


@asynccontextmanager
async def _fake_transport_context(value, closed: list[str] | None = None, name: str = "transport"):
    try:
        yield value
    finally:
        if closed is not None:
            closed.append(name)


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

    async def test_preserves_read_only_annotation_for_generic_documentation_guidance(self, monkeypatch):
        session = _FakeSession()
        session.tools_result = SimpleNamespace(tools=[
            SimpleNamespace(
                name="lookup_action_spec", description="Explains action parameters and options",
                inputSchema={"type": "object", "properties": {}},
                annotations=SimpleNamespace(readOnlyHint=True),
            ),
        ])
        monkeypatch.setattr(mc, "_open_transport", _noop_transport)
        _patch_client_session(monkeypatch, session)

        await mc._connect_server("srv1", {"transport": "stdio", "command": ["fake"]})

        assert td._TOOL_REGISTRY["mcp__srv1__lookup_action_spec"]["mcp_read_only"] is True

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


@pytest.mark.parametrize("doc_exposed", [False, True])
def test_opaque_parameter_guidance_uses_registry_metadata_not_a_hardcoded_guide_name(monkeypatch, doc_exposed):
    registry = {
        "mcp__arcade__play": {
            "category": "mcp", "mcp_server": "arcade", "description": "play an action",
        },
        "mcp__arcade__lookup_action_spec": {
            "category": "mcp", "mcp_server": "arcade", "mcp_read_only": True,
            "description": "Explains action parameters and available options",
        },
    }
    monkeypatch.setattr(td, "_TOOL_REGISTRY", registry)
    note = td.format_mcp_opaque_params_note([
        {"type": "function", "function": {
            "name": "mcp__arcade__play",
            "parameters": {"type": "object", "properties": {
                "params": {"type": "object", "additionalProperties": True},
            }},
        }},
    ] + ([{"type": "function", "function": {"name": "mcp__arcade__lookup_action_spec", "parameters": {}}}] if doc_exposed else []))

    assert "mcp__arcade__play" in note
    assert ("mcp__arcade__lookup_action_spec" in note) is doc_exposed
    assert "不要根据工具名猜测" in note
    assert "get_guide" not in note


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

    def test_transport_url_expands_environment_variable_path_segment(self, monkeypatch):
        monkeypatch.setenv("MCP_URL_TOKEN", "path-secret")
        assert mc._transport_url(
            {"url": "https://gateway.test/mcp/${MCP_URL_TOKEN}"}, "streamable-http",
        ) == "https://gateway.test/mcp/path-secret"

    def test_transport_url_missing_environment_variable_fails_closed(self, monkeypatch):
        monkeypatch.delenv("MCP_URL_TOKEN", raising=False)
        with pytest.raises(ValueError, match="MCP_URL_TOKEN"):
            mc._transport_url(
                {"url": "https://gateway.test/mcp/${MCP_URL_TOKEN}"}, "streamable-http",
            )

    async def test_probe_lists_tools_without_registering_them(self, monkeypatch):
        session = _FakeSession()
        session.tools_result = SimpleNamespace(tools=[
            SimpleNamespace(name="inspect", description="inspect status", inputSchema={}),
        ])
        monkeypatch.setattr(mc, "_open_transport", _noop_transport)
        _patch_client_session(monkeypatch, session)

        tools = await mc.test_server_config({"name": "remote", "transport": "http", "url": "https://x/mcp"})

        assert tools == [{
            "name": "inspect",
            "suggestion": {
                "effect": "read",
                "source": "name_description",
                "status": "suggested",
                "high_risk": False,
                "require_confirm": False,
            },
            "remote_domains": [],
            "remote_interaction": "unknown",
            "metadata_source": "none",
            "metadata_status": "absent",
            "metadata_schema_version": None,
            "final_domains": [],
        }]
        assert "mcp__remote__inspect" not in td._TOOL_REGISTRY


class TestMcpProxyRouting:
    def test_loopback_urls_always_bypass_proxy(self, monkeypatch):
        monkeypatch.setattr("core.config_loader.get_config", lambda: {
            "proxy": {"enabled": True, "http": "http://proxy.test:8080"},
        })
        for url in ("http://localhost:3000/mcp", "http://tool.localhost/mcp", "http://127.0.0.1/mcp", "http://[::1]/mcp"):
            assert mc.is_local_mcp_url(url) is True
            assert mc._mcp_proxy_url({"use_proxy": True}, url) is None

    def test_remote_proxy_requires_explicit_opt_in_and_uses_matching_global_url(self, monkeypatch):
        config = {
            "proxy": {
                "enabled": True,
                "http": "http://http-proxy.test:8080",
                "https": "http://https-proxy.test:8080",
            },
        }
        monkeypatch.setattr("core.config_loader.get_config", lambda: config)
        assert mc._mcp_proxy_url({}, "https://remote.test/mcp") is None
        assert mc._mcp_proxy_url({"use_proxy": True}, "http://remote.test/mcp") == "http://http-proxy.test:8080"
        assert mc._mcp_proxy_url({"use_proxy": True}, "https://remote.test/mcp") == "http://https-proxy.test:8080"

    def test_proxy_client_never_inherits_environment_settings(self, monkeypatch):
        import httpx

        calls = {}
        monkeypatch.setattr("core.config_loader.get_config", lambda: {
            "proxy": {"enabled": True, "https": "http://proxy.test:8080"},
        })
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: calls.setdefault("kwargs", kwargs))

        client = mc._mcp_http_client_factory({"use_proxy": True}, "https://remote.test/mcp")(
            headers={"X-Test": "yes"},
        )

        assert client is calls["kwargs"]
        assert calls["kwargs"]["proxy"] == "http://proxy.test:8080"
        assert calls["kwargs"]["trust_env"] is False
        assert calls["kwargs"]["follow_redirects"] is True


# ─────────────────────────────────────────────────────────────────────────────
# transport: stdio / SSE / Streamable HTTP，以及旧 http alias
# ─────────────────────────────────────────────────────────────────────────────

class TestOpenTransport:
    async def test_stdio_opens_sdk_stdio_client_inside_exit_stack(self, monkeypatch):
        import mcp
        import mcp.client.stdio as stdio_module

        calls = {}
        closed: list[str] = []

        class _Params:
            def __init__(self, *, command, args):
                calls["params"] = {"command": command, "args": args}

        def _stdio_client(params):
            calls["params_from_client"] = params
            return _fake_transport_context(("stdio-read", "stdio-write"), closed, "stdio")

        monkeypatch.setattr(mcp, "StdioServerParameters", _Params)
        monkeypatch.setattr(stdio_module, "stdio_client", _stdio_client)

        async with AsyncExitStack() as stack:
            assert await mc._open_transport(stack, {"transport": "stdio", "command": ["server", "--flag"]}) == (
                "stdio-read", "stdio-write",
            )
        assert calls["params"] == {"command": "server", "args": ["--flag"]}
        assert closed == ["stdio"]

    async def test_sse_opens_sdk_sse_client_inside_exit_stack(self, monkeypatch):
        import mcp.client.sse as sse_module

        calls = {}
        closed: list[str] = []

        def _sse_client(url, *, headers, httpx_client_factory):
            calls.update(url=url, headers=headers, httpx_client_factory=httpx_client_factory)
            return _fake_transport_context(("sse-read", "sse-write"), closed, "sse")

        monkeypatch.setattr(sse_module, "sse_client", _sse_client)

        async with AsyncExitStack() as stack:
            assert await mc._open_transport(stack, {
                "transport": "sse", "url": "https://example.test/sse", "headers": {"X-Test": "yes"},
            }) == ("sse-read", "sse-write")
        assert calls["url"] == "https://example.test/sse"
        assert calls["headers"] == {"X-Test": "yes"}
        assert callable(calls["httpx_client_factory"])
        assert closed == ["sse"]

    async def test_streamable_http_opens_current_sdk_client_inside_exit_stack(self, monkeypatch):
        import mcp.client.streamable_http as http_module
        calls = {}
        closed: list[str] = []

        def _mcp_client_factory(server_cfg, url):
            calls["server_cfg"] = server_cfg
            calls["factory_url"] = url

            def _http_client_factory(*, headers):
                calls["factory_headers"] = headers
                return _fake_transport_context("http-client", closed, "http-client")

            return _http_client_factory

        def _streamable_http_client(url, *, http_client):
            calls.update(url=url, http_client=http_client)
            return _fake_transport_context(("http-read", "http-write", lambda: None), closed, "streamable-http")

        monkeypatch.setattr(mc, "_mcp_http_client_factory", _mcp_client_factory)
        monkeypatch.setattr(http_module, "streamable_http_client", _streamable_http_client, raising=False)

        async with AsyncExitStack() as stack:
            assert await mc._open_transport(stack, {
                "transport": "streamable-http", "url": "https://example.test/mcp", "headers": {"X-Test": "yes"},
            }) == ("http-read", "http-write")
        assert calls == {
            "server_cfg": {"transport": "streamable-http", "url": "https://example.test/mcp", "headers": {"X-Test": "yes"}},
            "factory_url": "https://example.test/mcp",
            "factory_headers": {"X-Test": "yes"},
            "url": "https://example.test/mcp",
            "http_client": "http-client",
        }
        assert closed == ["streamable-http", "http-client"]

    async def test_legacy_http_alias_uses_streamable_http_client(self, monkeypatch):
        import mcp.client.streamable_http as http_module
        calls = []

        def _mcp_client_factory(server_cfg, url):
            calls.append(("route", server_cfg, url))

            def _http_client_factory(*, headers):
                calls.append(("factory", headers))
                return _fake_transport_context("http-client")

            return _http_client_factory

        def _streamable_http_client(url, *, http_client):
            calls.append(("transport", url, http_client))
            return _fake_transport_context(("http-read", "http-write", lambda: None))

        monkeypatch.setattr(mc, "_mcp_http_client_factory", _mcp_client_factory)
        monkeypatch.setattr(http_module, "streamable_http_client", _streamable_http_client, raising=False)

        async with AsyncExitStack() as stack:
            assert await mc._open_transport(stack, {
                "transport": "http", "url": "https://example.test/mcp",
            }) == ("http-read", "http-write")
        assert calls == [
            ("route", {"transport": "http", "url": "https://example.test/mcp"}, "https://example.test/mcp"),
            ("factory", None),
            ("transport", "https://example.test/mcp", "http-client"),
        ]


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

        result = await mc._call_tool("srv1", "toolA", {}, 5, effect="read")
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

        result = await mc._call_tool("srv1", "toolA", {}, 5, effect="read")
        assert result == "line1\nline2"

    async def test_is_error_raises(self):
        session = _FakeSession(call_results=[
            SimpleNamespace(content=[SimpleNamespace(text="boom")], isError=True),
        ])
        self._install_handle("srv1", session)

        with pytest.raises(RuntimeError):
            await mc._call_tool("srv1", "toolA", {}, 5, effect="read")

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

        result = await mc._call_tool("srv1", "toolA", {}, 5, effect="read")
        assert result == "ok"

    async def test_reconnect_retry_reports_next_attempt_to_status_observer(self, monkeypatch):
        dead_session = _FakeSession(call_results=[RuntimeError("connection dead")])
        self._install_handle("srv1", dead_session, tool_names=["mcp__srv1__toolA"])
        new_session = _FakeSession(call_results=[
            SimpleNamespace(content=[SimpleNamespace(text="ok")], isError=False),
        ])

        async def _fake_connect_server(name, cfg):
            mc._servers[name] = mc._ServerHandle(
                name=name, cfg=cfg, stack=AsyncExitStack(), session=new_session,
                tool_names=["mcp__srv1__toolA"],
            )

        attempts = []
        monkeypatch.setattr(mc, "_connect_server", _fake_connect_server)
        result = await mc._call_tool(
            "srv1", "toolA", {}, 5, effect="read",
            status_observer=lambda kind, *, attempt: attempts.append((kind, attempt)),
        )

        assert result == "ok"
        assert attempts == [("waiting", 2)]

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
            await mc._call_tool("srv1", "toolA", {}, 5, effect="read")

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

        with mc.audit_context("audit-console"):
            assert await mc._call_tool("srv1", "toolA", {"secret": "never-log"}, 5) == "ok"
        assert rows[0]["caller"] == "mcp__srv1__toolA"
        assert "secret" not in str(rows[0])
        assert rows[0]["audit_id"] == "audit-console"
        assert mc.server_runtime("srv1")["last_call_ok"] is True

    async def test_actuate_timeout_does_not_retry_and_has_a_stable_request_id(self, monkeypatch):
        session = _FakeSession(call_results=[asyncio.TimeoutError()])
        self._install_handle("srv1", session)

        async def _unexpected_reconnect(_name):
            raise AssertionError("actuate timeout must not reconnect and replay")

        monkeypatch.setattr(mc, "_reconnect_server", _unexpected_reconnect)
        with pytest.raises(mc.McpOutcomeUnknown) as raised:
            await mc._call_tool("srv1", "hardware_sequence", {}, 5, effect="actuate")

        assert len(session.call_log) == 1
        request_id = session.call_log[0][2]["request_id"]
        assert raised.value.payload["request_id"] == request_id

    async def test_read_timeout_reconnects_and_retries_once(self, monkeypatch):
        dead_session = _FakeSession(call_results=[asyncio.TimeoutError()])
        self._install_handle("srv1", dead_session, tool_names=["mcp__srv1__toolA"])
        new_session = _FakeSession(call_results=[
            SimpleNamespace(content=[SimpleNamespace(text="recovered")], isError=False),
        ])

        async def _fake_connect_server(name, cfg):
            mc._servers[name] = mc._ServerHandle(
                name=name, cfg=cfg, stack=AsyncExitStack(), session=new_session,
                tool_names=["mcp__srv1__toolA"],
            )

        monkeypatch.setattr(mc, "_connect_server", _fake_connect_server)
        assert await mc._call_tool("srv1", "toolA", {}, 5, effect="read") == "recovered"
        assert len(dead_session.call_log) == 1
        assert len(new_session.call_log) == 1

    async def test_non_idempotent_write_timeout_does_not_retry(self, monkeypatch):
        session = _FakeSession(call_results=[asyncio.TimeoutError()])
        self._install_handle("srv1", session)

        async def _unexpected_reconnect(_name):
            raise AssertionError("non-idempotent write must not reconnect and replay")

        monkeypatch.setattr(mc, "_reconnect_server", _unexpected_reconnect)
        with pytest.raises(RuntimeError):
            await mc._call_tool("srv1", "write_once", {}, 5, effect="write")

        assert len(session.call_log) == 1

    async def test_idempotent_write_timeout_reconnects_and_retries_once(self, monkeypatch):
        dead_session = _FakeSession(call_results=[asyncio.TimeoutError()])
        self._install_handle("srv1", dead_session, tool_names=["mcp__srv1__write_once"])
        new_session = _FakeSession(call_results=[
            SimpleNamespace(content=[SimpleNamespace(text="written")], isError=False),
        ])

        async def _fake_connect_server(name, cfg):
            mc._servers[name] = mc._ServerHandle(
                name=name, cfg=cfg, stack=AsyncExitStack(), session=new_session,
                tool_names=["mcp__srv1__write_once"],
            )

        monkeypatch.setattr(mc, "_connect_server", _fake_connect_server)
        assert await mc._call_tool(
            "srv1", "write_once", {}, 5, effect="write", idempotent=True,
        ) == "written"
        assert len(dead_session.call_log) == 1
        assert len(new_session.call_log) == 1

    async def test_idempotent_hardware_stop_retries_with_the_same_request_id(self, monkeypatch):
        dead_session = _FakeSession(call_results=[asyncio.TimeoutError()])
        self._install_handle("srv1", dead_session, tool_names=["mcp__srv1__hardware_stop"])
        new_session = _FakeSession(call_results=[
            SimpleNamespace(content=[SimpleNamespace(text="stopped")], isError=False),
        ])

        async def _fake_connect_server(name, cfg):
            mc._servers[name] = mc._ServerHandle(
                name=name, cfg=cfg, stack=AsyncExitStack(), session=new_session,
                tool_names=["mcp__srv1__hardware_stop"],
            )

        monkeypatch.setattr(mc, "_connect_server", _fake_connect_server)
        assert await mc._call_tool(
            "srv1", "hardware_stop", {}, 5, effect="emergency", idempotent=True,
        ) == "stopped"
        assert dead_session.call_log[0][2]["request_id"] == new_session.call_log[0][2]["request_id"]

    async def test_unrestricted_idempotent_tool_retries_three_times_with_one_request_id(self, monkeypatch):
        sessions = [
            _FakeSession(call_results=[asyncio.TimeoutError()]),
            _FakeSession(call_results=[asyncio.TimeoutError()]),
            _FakeSession(call_results=[asyncio.TimeoutError()]),
            _FakeSession(call_results=[SimpleNamespace(
                content=[SimpleNamespace(text="recovered")], isError=False,
            )]),
        ]
        self._install_handle("srv1", sessions[0], tool_names=["mcp__srv1__toolA"])
        next_session = iter(sessions[1:])

        async def _fake_connect_server(name, cfg):
            mc._servers[name] = mc._ServerHandle(
                name=name, cfg=cfg, stack=AsyncExitStack(), session=next(next_session),
                tool_names=["mcp__srv1__toolA"],
            )

        monkeypatch.setattr(mc, "_connect_server", _fake_connect_server)
        assert await mc._call_tool(
            "srv1", "toolA", {}, 5, effect="unrestricted", idempotent=True,
        ) == "recovered"
        request_ids = [session.call_log[0][2]["request_id"] for session in sessions]
        assert len(set(request_ids)) == 1

    async def test_sequence_uses_its_per_tool_timeout(self, monkeypatch):
        session = _FakeSession()
        session.tools_result = SimpleNamespace(tools=[
            SimpleNamespace(name="hardware_sequence", description="", inputSchema={}),
        ])
        monkeypatch.setattr(mc, "_open_transport", _noop_transport)
        _patch_client_session(monkeypatch, session)
        monkeypatch.setattr(mc, "_require_local_policy", lambda: True)
        await mc._connect_server("srv1", {
            "name": "srv1", "transport": "stdio", "command": ["fake"],
            "allow_tools": ["hardware_sequence"],
            "tool_policy": {"hardware_sequence": {"effect": "actuate"}},
            "tool_timeout_s": 30,
            "tool_timeouts_s": {"hardware_sequence": 60},
        })
        calls = []

        async def _capture(*args, **kwargs):
            calls.append((args, kwargs))
            return "ok"

        monkeypatch.setattr(mc, "_call_tool", _capture)
        assert await td._TOOL_REGISTRY["mcp__srv1__hardware_sequence"]["func"]() == "ok"
        assert calls[0][0][3] == 60

    def test_per_tool_timeout_allows_660_seconds_and_rejects_larger_values(self):
        assert mc._tool_timeout_s({"hardware_sequence": 660}, "hardware_sequence", 30) == 660
        assert mc._tool_timeout_s({"hardware_sequence": 661}, "hardware_sequence", 30) == 30

    async def test_dispatcher_returns_structured_outcome_unknown(self, monkeypatch, sandbox):
        async def _unknown(**_kwargs):
            raise mc.McpOutcomeUnknown(tool_name="hardware_sequence", request_id="request-123")

        monkeypatch.setitem(td._TOOL_REGISTRY, "mcp__hardware__hardware_sequence", {
            "func": _unknown, "description": "", "dangerous": False,
            "category": "mcp", "parameters": {}, "effect": "actuate",
            "mcp_server": "hardware", "mcp_tool": "hardware_sequence",
        })
        monkeypatch.setattr("core.growth.mcp_proficiency.is_tool_allowed", lambda *args, **kwargs: True)
        monkeypatch.setattr("core.memory.action_trace.record", lambda *args, **kwargs: None)

        result = await td.execute_structured(
            "mcp__hardware__hardware_sequence", {}, "owner", "owner", False,
            _NoConfirmSession(), origin="assistant_loop", char_id="char",
        )
        assert result.confirmation_request is None
        assert result.result is not None
        payload = json.loads(result.result)
        assert payload["outcome"] == "outcome_unknown"
        assert payload["request_id"] == "request-123"


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
    async def test_shutdown_clears_all_servers(self, monkeypatch):
        session = _FakeSession()
        session.tools_result = SimpleNamespace(tools=[])
        monkeypatch.setattr(mc, "_open_transport", _noop_transport)
        _patch_client_session(monkeypatch, session)

        owner = mc._spawn_owner("srv1", {"transport": "stdio", "command": ["fake"]})
        await owner.ready.wait()
        assert "srv1" in mc._servers

        await mc.shutdown_mcp_servers()
        assert mc._servers == {}
        assert mc._owners == {}


# ─────────────────────────────────────────────────────────────────────────────
# Brief 115 根治验收 #1：跨 task 触发 reload/disconnect，open/close 必须仍留在
# server 专属的那一个常驻 task 里，调用方 task 身份不影响这一点。
# ─────────────────────────────────────────────────────────────────────────────

class TestOwnerTaskLifecycle:
    async def test_reload_from_a_different_task_stays_in_owner_task(self, monkeypatch):
        session = _FakeSession()
        session.tools_result = SimpleNamespace(tools=[])
        monkeypatch.setattr(mc, "_open_transport", _noop_transport)
        _patch_client_session(monkeypatch, session)

        connect_task_ids: list[int] = []
        close_task_ids: list[int] = []
        real_connect = mc._connect_server
        real_close = mc._close_server

        async def _tracking_connect(name, cfg):
            connect_task_ids.append(id(asyncio.current_task()))
            return await real_connect(name, cfg)

        async def _tracking_close(name):
            close_task_ids.append(id(asyncio.current_task()))
            return await real_close(name)

        monkeypatch.setattr(mc, "_connect_server", _tracking_connect)
        monkeypatch.setattr(mc, "_close_server", _tracking_close)

        owner = mc._spawn_owner("srv1", {"transport": "stdio", "command": ["fake"]})
        await owner.ready.wait()
        owner_task_id = id(owner.task)
        assert connect_task_ids == [owner_task_id]

        monkeypatch.setattr(mc, "_get_mcp_config", lambda: {
            "enabled": True,
            "servers": [{"name": "srv1", "transport": "stdio", "command": ["fake"]}],
        })

        # 从一个全新的、独立的 task 里触发 reload —— 模拟管理面 HTTP 请求的 task。
        caller_task = asyncio.create_task(mc.reload_server_from_config("srv1"))
        ok = await caller_task

        assert ok is True
        assert id(caller_task) != owner_task_id, "触发 reload 的调用方 task 不应是专属常驻 task 本身"
        assert close_task_ids == [owner_task_id], "关闭旧连接必须发生在打开它的那个专属 task 里"
        assert connect_task_ids == [owner_task_id, owner_task_id], "重连也必须发生在专属 task 里"
        assert "srv1" in mc._servers

    async def test_disconnect_from_a_different_task_does_not_raise(self, monkeypatch):
        session = _FakeSession()
        session.tools_result = SimpleNamespace(tools=[])
        monkeypatch.setattr(mc, "_open_transport", _noop_transport)
        _patch_client_session(monkeypatch, session)

        owner = mc._spawn_owner("srv1", {"transport": "stdio", "command": ["fake"]})
        await owner.ready.wait()
        assert "srv1" in mc._servers

        # disconnect_server 本身只发信号；这里用一个独立 task 触发，确认不会抛出
        # CancelledError / BaseExceptionGroup 之类跨 task 取消祖先 scope 才会出现的异常。
        caller_task = asyncio.create_task(mc.disconnect_server("srv1"))
        await caller_task

        assert "srv1" not in mc._servers

    async def test_sync_without_wait_returns_before_connect_finishes(self, monkeypatch):
        started = asyncio.Event()
        release = asyncio.Event()
        session = _FakeSession()
        session.tools_result = SimpleNamespace(tools=[])
        monkeypatch.setattr(mc, "_open_transport", _noop_transport)
        _patch_client_session(monkeypatch, session)
        real_connect = mc._connect_server

        async def _slow_connect(name, cfg):
            started.set()
            await release.wait()
            return await real_connect(name, cfg)

        monkeypatch.setattr(mc, "_connect_server", _slow_connect)
        monkeypatch.setattr(mc, "_get_mcp_config", lambda: {
            "enabled": True,
            "servers": [{"name": "srv1", "transport": "stdio", "command": ["fake"]}],
        })

        await asyncio.wait_for(mc.sync_mcp_servers(wait=False), timeout=1)
        await asyncio.wait_for(started.wait(), timeout=1)
        assert "srv1" not in mc._servers
        release.set()
        owner = mc._owners["srv1"]
        await asyncio.wait_for(owner.ready.wait(), timeout=1)
        assert "srv1" in mc._servers


# ── Brief 122：MCP 业务错误原话回填,而不是套通用兜底文案 ────────────────────
#
# 排查 cedartoy「command 参数必填」时发现：execute() 之前把任何异常统一转成
# "工具暂时不可用"，把服务器已经讲清楚"缺了什么"的具体错误吞掉了，模型没法
# 据此自我纠正重试。现在 _format_result() 因 result.isError 抛出的
# RuntimeError("MCP 工具返回错误: ...") 会被原话回填；其他 RuntimeError（连接/
# 重连失败）继续走通用兜底，不把基础设施细节暴露给模型。

class _NoConfirmSession:
    status = "idle"


class TestMcpServerErrorPassthrough:
    def _register_fake_tool(self, monkeypatch, *, raises: Exception):
        async def _func(**kwargs):
            raise raises

        monkeypatch.setitem(td._TOOL_REGISTRY, "mcp__cedar_toy__play", {
            "func": _func,
            "description": "测试用",
            "dangerous": False,
            "category": "mcp",
            "parameters": {"type": "object", "properties": {}},
            "mcp_server": "cedar_toy",
            "mcp_tool": "play",
        })

    async def test_server_business_error_passed_through_verbatim(self, monkeypatch, sandbox):
        self._register_fake_tool(
            monkeypatch,
            raises=RuntimeError("MCP 工具返回错误: 【cedartoy】command 参数必填"),
        )

        result = await td.execute_structured(
            "mcp__cedar_toy__play", {"game": "fishing", "action": "cast"},
            "owner", "owner", False, _NoConfirmSession(),
            origin="assistant_loop", char_id=TEST_CHAR_ID,
        )

        assert result.confirmation_request is None
        assert result.result == "工具调用未成功：【cedartoy】command 参数必填"

    async def test_infra_error_falls_back_to_generic_fallback(self, monkeypatch, sandbox):
        self._register_fake_tool(
            monkeypatch,
            raises=RuntimeError("MCP 工具调用失败且重连未恢复: 连接超时"),
        )

        result = await td.execute_structured(
            "mcp__cedar_toy__play", {"game": "fishing", "action": "cast"},
            "owner", "owner", False, _NoConfirmSession(),
            origin="assistant_loop", char_id=TEST_CHAR_ID,
        )

        assert result.confirmation_request is None
        # 基础设施故障没有可操作的具体原因，不应把内部异常文本暴露给模型。
        assert result.result == "工具暂时不可用"


class TestMcpServerReportedErrorHelper:
    def test_matches_format_result_prefix(self):
        e = RuntimeError("MCP 工具返回错误: 【cedartoy】command 参数必填")
        assert td._mcp_server_reported_error(e) == "【cedartoy】command 参数必填"

    def test_non_runtime_error_returns_none(self):
        e = ValueError("MCP 工具返回错误: 不算数")
        assert td._mcp_server_reported_error(e) is None

    def test_unrelated_runtime_error_returns_none(self):
        e = RuntimeError("MCP server 'cedar_toy' 未连接")
        assert td._mcp_server_reported_error(e) is None

    def test_empty_text_after_prefix_returns_none(self):
        e = RuntimeError("MCP 工具返回错误: ")
        assert td._mcp_server_reported_error(e) is None
