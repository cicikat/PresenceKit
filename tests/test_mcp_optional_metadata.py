from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.mcp_client as mc
import core.tool_dispatcher as td


class _FakeSession:
    def __init__(self, tools, call_result=None):
        self.tools_result = SimpleNamespace(tools=tools)
        self.call_result = call_result
        self.calls: list[tuple[str, dict, object]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        return None

    async def list_tools(self):
        return self.tools_result

    async def call_tool(self, name, arguments, *, meta=None):
        self.calls.append((name, arguments, meta))
        return self.call_result or SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")]
        )


async def _noop_transport(stack, server_cfg):
    return None, None


def _tool(name: str, *, meta=..., annotations=None):
    values = {
        "name": name,
        "description": "remote description must not become policy",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": annotations,
    }
    if meta is not ...:
        values["meta"] = meta
    return SimpleNamespace(**values)


def _mapping(namespace: str = "io.third-party/tool") -> dict:
    return {
        "namespace": namespace,
        "schema_versions": [1],
        "schema_version_field": "schema_version",
        "domains_field": "domains",
        "interaction_field": "interaction",
    }


def _patch_session(monkeypatch, session):
    import mcp

    monkeypatch.setattr(mc, "_open_transport", _noop_transport)
    monkeypatch.setattr(mcp, "ClientSession", lambda read, write: session)


@pytest.fixture(autouse=True)
async def _isolate(monkeypatch):
    monkeypatch.setattr(td, "_TOOL_REGISTRY", dict(td._TOOL_REGISTRY))
    monkeypatch.setattr(mc, "_require_local_policy", lambda: False)
    mc._servers.clear()
    mc._server_status.clear()
    mc._owners.clear()
    yield
    for handle in list(mc._servers.values()):
        await handle.stack.aclose()
    mc._servers.clear()
    mc._server_status.clear()
    mc._owners.clear()


@pytest.mark.asyncio
async def test_standard_server_without_meta_keeps_legacy_registration(monkeypatch):
    session = _FakeSession([_tool("plain_read")])
    _patch_session(monkeypatch, session)

    await mc._connect_server("plain", {
        "name": "plain", "transport": "stdio", "command": ["fake"],
    })

    entry = td._TOOL_REGISTRY["mcp__plain__plain_read"]
    assert entry["category"] == "mcp"
    assert entry["mcp_metadata_status"] == "absent"
    assert entry["mcp_domains"] == []


@pytest.mark.asyncio
async def test_arbitrary_namespace_metadata_is_recognized_and_bounded(monkeypatch):
    tool = _tool("lookup", meta={
        "io.third-party/tool": {
            "schema_version": 1,
            "domains": ["zeta", "alpha", "alpha", "bad\nvalue", "x" * 49],
            "interaction": "read",
        },
        "unrelated-secret-shaped-key": {"token": "must-not-survive"},
    })
    session = _FakeSession([tool])
    _patch_session(monkeypatch, session)

    await mc._connect_server("generic", {
        "name": "generic", "transport": "stdio", "command": ["fake"],
        "metadata_mapping": _mapping(),
    })

    entry = td._TOOL_REGISTRY["mcp__generic__lookup"]
    assert entry["mcp_metadata_status"] == "recognized"
    assert entry["mcp_remote_domains"] == ["alpha", "zeta"]
    assert entry["mcp_domains"] == ["alpha", "zeta"]
    assert entry["mcp_remote_interaction"] == "read"
    assert entry["mcp_metadata_schema_version"] == 1
    assert "token" not in repr(mc.server_runtime("generic"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("meta", "expected_status", "expected_version"),
    [
        ({"io.third-party/tool": "bad"}, "invalid", None),
        ({"io.third-party/tool": {
            "schema_version": 99, "domains": ["observe"], "interaction": "read",
        }}, "unrecognized", 99),
        ({"another.namespace": {
            "schema_version": 1, "domains": ["observe"], "interaction": "read",
        }}, "unrecognized", None),
    ],
)
async def test_bad_or_unrecognized_metadata_never_blocks_registration(
    monkeypatch, meta, expected_status, expected_version,
):
    session = _FakeSession([_tool("still_callable", meta=meta)])
    _patch_session(monkeypatch, session)

    await mc._connect_server("soft", {
        "name": "soft", "transport": "stdio", "command": ["fake"],
        "metadata_mapping": _mapping(),
    })

    entry = td._TOOL_REGISTRY["mcp__soft__still_callable"]
    assert entry["mcp_metadata_status"] == expected_status
    assert entry["mcp_metadata_schema_version"] == expected_version
    assert entry["mcp_domains"] == []
    assert await entry["func"]() == "ok"
    assert session.calls == [("still_callable", {}, None)]


@pytest.mark.asyncio
async def test_bad_tool_metadata_does_not_block_good_tool_in_same_server(monkeypatch):
    session = _FakeSession([
        _tool("bad", meta={"io.third-party/tool": []}),
        _tool("good", meta={"io.third-party/tool": {
            "schema_version": 1, "domains": ["observe"], "interaction": "read",
        }}),
    ])
    _patch_session(monkeypatch, session)

    await mc._connect_server("mixed", {
        "name": "mixed", "transport": "stdio", "command": ["fake"],
        "metadata_mapping": _mapping(),
    })

    assert td._TOOL_REGISTRY["mcp__mixed__bad"]["mcp_metadata_status"] == "invalid"
    assert td._TOOL_REGISTRY["mcp__mixed__good"]["mcp_domains"] == ["observe"]


def test_unknown_interaction_is_only_an_unknown_hint():
    summary = mc.summarize_tool_metadata(_tool("opaque", meta={
        "io.third-party/tool": {
            "schema_version": 1, "domains": ["observe"], "interaction": "sideways",
        },
    }), {"name": "generic", "metadata_mapping": _mapping()})

    assert summary["metadata_status"] == "recognized"
    assert summary["remote_interaction"] == "unknown"
    assert summary["final_domains"] == ["observe"]


@pytest.mark.asyncio
async def test_remote_read_claim_cannot_change_local_write_policy(monkeypatch):
    monkeypatch.setattr(mc, "_require_local_policy", lambda: True)
    session = _FakeSession([_tool("mutate", meta={"io.third-party/tool": {
        "schema_version": 1, "domains": ["observe"], "interaction": "read",
    }})])
    _patch_session(monkeypatch, session)

    await mc._connect_server("policy", {
        "name": "policy", "transport": "stdio", "command": ["fake"],
        "metadata_mapping": _mapping(),
        "allow_tools": ["mutate"],
        "tool_policy": {"mutate": {"effect": "write", "require_confirm": True}},
    })

    entry = td._TOOL_REGISTRY["mcp__policy__mutate"]
    assert entry["mcp_remote_interaction"] == "read"
    assert entry["effect"] == "write"
    assert entry["require_confirm"] is False


@pytest.mark.asyncio
async def test_metadata_never_grants_missing_allowlist_authorization(monkeypatch):
    monkeypatch.setattr(mc, "_require_local_policy", lambda: True)
    session = _FakeSession([_tool("remote_claim", meta={"io.third-party/tool": {
        "schema_version": 1, "domains": ["observe"], "interaction": "read",
    }})])
    _patch_session(monkeypatch, session)

    await mc._connect_server("denied", {
        "name": "denied", "transport": "stdio", "command": ["fake"],
        "metadata_mapping": _mapping(), "allow_tools": [], "tool_policy": {},
    })

    assert "mcp__denied__remote_claim" not in td._TOOL_REGISTRY
    assert mc.server_runtime("denied")["tools"][0]["metadata_status"] == "recognized"


def test_domain_selector_only_narrows_and_include_unclassified_preserves_compatibility(monkeypatch):
    registry = {
        "mcp__srv__match": {
            "category": "mcp", "mcp_server": "srv", "mcp_domains": ["observe"],
            "description": "", "parameters": {},
        },
        "mcp__srv__other": {
            "category": "mcp", "mcp_server": "srv", "mcp_domains": ["write"],
            "description": "", "parameters": {},
        },
        "mcp__srv__plain": {
            "category": "mcp", "mcp_server": "srv", "mcp_domains": [],
            "description": "", "parameters": {},
        },
    }
    monkeypatch.setattr(td, "_TOOL_REGISTRY", registry)
    cfg = {"mcp_servers": {"servers": [{
        "name": "srv",
        "domain_selector": {"domains": ["observe"], "include_unclassified": True},
    }]}}
    monkeypatch.setattr(td, "get_config", lambda: cfg)
    monkeypatch.setattr(td, "_is_tool_enabled", lambda name: True)

    schemas = td.get_tools_schema(categories=["mcp"])
    names = {(item.get("function") or item)["name"] for item in schemas}
    assert names == {"mcp__srv__match", "mcp__srv__plain"}

    cfg["mcp_servers"]["servers"][0]["domain_selector"]["include_unclassified"] = False
    schemas = td.get_tools_schema(categories=["mcp"])
    names = {(item.get("function") or item)["name"] for item in schemas}
    assert names == {"mcp__srv__match"}

    cfg["mcp_servers"]["servers"][0].pop("domain_selector")
    schemas = td.get_tools_schema(categories=["mcp"])
    names = {(item.get("function") or item)["name"] for item in schemas}
    assert names == set(registry)


def test_local_override_is_exact_name_and_reused_by_relay_schema(monkeypatch):
    mapped = _tool("old_name", meta={"io.any/tool": {
        "schema_version": 1, "domains": ["remote"], "interaction": "mixed",
    }})
    cfg = {
        "name": "srv",
        "metadata_mapping": _mapping("io.any/tool"),
        "metadata_overrides": {
            "old_name": {"mode": "override", "domains": ["local"]},
        },
    }
    assert mc.summarize_tool_metadata(mapped, cfg)["final_domains"] == ["local"]
    assert mc.summarize_tool_metadata(_tool("new_name", meta=mapped.meta), cfg)["final_domains"] == ["remote"]

    registry = {
        "mcp__srv__old_name": {
            "category": "mcp", "mcp_server": "srv", "mcp_domains": ["local"],
            "description": "old", "parameters": {},
        },
        "mcp__srv__new_name": {
            "category": "mcp", "mcp_server": "srv", "mcp_domains": ["remote"],
            "description": "new", "parameters": {},
        },
    }
    monkeypatch.setattr(td, "_TOOL_REGISTRY", registry)
    monkeypatch.setattr(td, "_is_tool_enabled", lambda name: True)
    monkeypatch.setattr(td, "get_config", lambda: {"mcp_servers": {"servers": [{
        "name": "srv", "domain_selector": {
            "domains": ["local"], "include_unclassified": False,
        },
    }]}})
    schemas = td.get_tools_schema(categories=["mcp"])
    allowed = {(item.get("function") or item)["name"] for item in schemas}
    relay_prompt = td.get_tool_loop_relay_prompt(["mcp"], allowed_tool_names=allowed)

    assert allowed == {"mcp__srv__old_name"}
    assert "mcp__srv__old_name" in relay_prompt
    assert "mcp__srv__new_name" not in relay_prompt


def test_admin_view_returns_safe_summary_and_three_independent_states(monkeypatch):
    from admin.routers import settings_mcp

    tool = {
        "name": "inspect",
        "suggestion": {"effect": "read", "source": "annotation.readOnlyHint"},
        "remote_domains": ["observe"],
        "remote_interaction": "read",
        "metadata_source": "remote",
        "metadata_status": "recognized",
        "metadata_schema_version": 1,
        "final_domains": ["observe"],
    }
    monkeypatch.setattr(mc, "server_runtime", lambda name: {
        "connected": True, "tools": [tool], "registered_tools": ["mcp__srv__inspect"],
    })
    monkeypatch.setitem(td._TOOL_REGISTRY, "mcp__srv__inspect", {
        "category": "mcp", "mcp_server": "srv", "mcp_tool": "inspect",
        "effect": "read", "parameters": {
            "type": "object",
            "properties": {"secret_argument": {"type": "string", "description": "raw secret docs"}},
            "required": ["secret_argument"],
            "x-raw-secret": "must-not-leak",
        },
    })
    view = settings_mcp._server_view(
        {
            "name": "srv", "allow_tools": ["inspect"],
            "tool_policy": {"inspect": {"effect": "read"}},
            "metadata_mapping": _mapping(),
        },
        require_local_policy=True,
        session_exposed_names={"mcp__srv__inspect"},
    )

    state = view["tool_states"][0]
    assert state["discovered"] is True
    assert state["authorized"] is True
    assert state["session_exposed"] is True
    assert state["parameter_summary"] == {
        "properties": [{"name": "secret_argument", "type": "string"}],
        "required": ["secret_argument"],
    }
    serialized = repr(view)
    assert "raw secret docs" not in serialized
    assert "must-not-leak" not in serialized
    assert "description" not in state
    assert "input_schema" not in state
