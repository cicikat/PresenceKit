"""MCP Local Effect Policy P1: trusted local classification and confirmation."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.mcp_client as mc
import core.tool_dispatcher as td


class _FakeSession:
    def __init__(self, tools):
        self.tools_result = SimpleNamespace(tools=tools)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        return None

    async def list_tools(self):
        return self.tools_result


async def _noop_transport(stack, server_cfg):
    return None, None


def _patch_client_session(monkeypatch, session):
    import mcp
    monkeypatch.setattr(mcp, "ClientSession", lambda read, write: session)


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    monkeypatch.setattr(td, "_TOOL_REGISTRY", dict(td._TOOL_REGISTRY))
    mc._servers.clear()
    mc._server_status.clear()
    monkeypatch.setattr(mc, "_require_local_policy", lambda: True)


def _strict_server(*, allow_tools, tool_policy):
    return {
        "name": "srv1",
        "transport": "stdio",
        "command": ["fake"],
        "allow_tools": allow_tools,
        "tool_policy": tool_policy,
    }


@pytest.mark.asyncio
async def test_local_policy_registers_effects_and_ignores_unlisted_remote_tool(monkeypatch, caplog):
    tools = [
        SimpleNamespace(name="read_status", description="", inputSchema={}, annotations=SimpleNamespace(readOnlyHint=True)),
        SimpleNamespace(name="send_message", description="", inputSchema={}, annotations=SimpleNamespace(readOnlyHint=True)),
        SimpleNamespace(name="remote_extra", description="", inputSchema={}),
    ]
    monkeypatch.setattr(mc, "_open_transport", _noop_transport)
    _patch_client_session(monkeypatch, _FakeSession(tools))

    await mc._connect_server("srv1", _strict_server(
        allow_tools=["read_status", "send_message"],
        tool_policy={
            "read_status": {"effect": "read"},
            "send_message": {"effect": "write"},
        },
    ))

    read = td._TOOL_REGISTRY["mcp__srv1__read_status"]
    write = td._TOOL_REGISTRY["mcp__srv1__send_message"]
    assert read["effect"] == "read" and read["dangerous"] is False
    assert write["effect"] == "write" and write["dangerous"] is False
    assert write["mcp_claimed_read_only"] is True
    assert read["ui_label"] == "外部工具"
    assert "mcp__srv1__remote_extra" not in td._TOOL_REGISTRY
    assert "本地 effect 冲突" in caplog.text


@pytest.mark.asyncio
async def test_local_policy_confirmation_defaults_and_emergency_override(monkeypatch):
    tools = [
        SimpleNamespace(name="write", description="", inputSchema={}),
        SimpleNamespace(name="pulse", description="", inputSchema={}),
        SimpleNamespace(name="stop", description="", inputSchema={}),
    ]
    monkeypatch.setattr(mc, "_open_transport", _noop_transport)
    _patch_client_session(monkeypatch, _FakeSession(tools))

    await mc._connect_server("srv1", _strict_server(
        allow_tools=["write", "pulse", "stop"],
        tool_policy={
            "write": {"effect": "write"},
            "pulse": {"effect": "actuate"},
            "stop": {"effect": "emergency", "require_confirm": True},
        },
    ))

    assert td._TOOL_REGISTRY["mcp__srv1__write"]["require_confirm"] is False
    assert td._TOOL_REGISTRY["mcp__srv1__pulse"]["require_confirm"] is False
    assert td._TOOL_REGISTRY["mcp__srv1__stop"]["require_confirm"] is False


@pytest.mark.asyncio
async def test_unrestricted_policy_requires_idempotency_and_overrides_confirmation(monkeypatch):
    tools = [SimpleNamespace(
        name="trusted_action", description="", inputSchema={},
        annotations=SimpleNamespace(destructiveHint=True),
    )]
    monkeypatch.setattr(mc, "_open_transport", _noop_transport)
    _patch_client_session(monkeypatch, _FakeSession(tools))

    with pytest.raises(ValueError, match="必须显式 idempotent: true"):
        mc.validate_local_tool_policy(_strict_server(
            allow_tools=["trusted_action"],
            tool_policy={"trusted_action": {"effect": "unrestricted"}},
        ))

    await mc._connect_server("srv1", _strict_server(
        allow_tools=["trusted_action"],
        tool_policy={"trusted_action": {
            "effect": "unrestricted", "idempotent": True, "require_confirm": True,
        }},
    ))
    entry = td._TOOL_REGISTRY["mcp__srv1__trusted_action"]
    assert entry["effect"] == "unrestricted"
    assert entry["require_confirm"] is False
    assert entry["dangerous"] is False


@pytest.mark.asyncio
async def test_destructive_annotation_remains_high_risk_after_local_confirmation(monkeypatch):
    tools = [SimpleNamespace(
        name="delete_item", description="", inputSchema={},
        annotations=SimpleNamespace(destructiveHint=True),
    )]
    monkeypatch.setattr(mc, "_open_transport", _noop_transport)
    _patch_client_session(monkeypatch, _FakeSession(tools))

    await mc._connect_server("srv1", _strict_server(
        allow_tools=["delete_item"],
        tool_policy={"delete_item": {"effect": "write", "require_confirm": False}},
    ))

    entry = td._TOOL_REGISTRY["mcp__srv1__delete_item"]
    assert entry["mcp_high_risk"] is True
    assert entry["dangerous"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("allow_tools", "tool_policy", "error"),
    [
        ("not-a-list", {}, "allow_tools"),
        (["status"], {"status": {"effect": "unknown"}}, "effect 无效"),
    ],
)
async def test_strict_local_policy_fails_closed_before_transport(monkeypatch, allow_tools, tool_policy, error):
    opened = False

    async def _must_not_open(*args, **kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("strict policy must reject before transport")

    monkeypatch.setattr(mc, "_open_transport", _must_not_open)
    with pytest.raises(RuntimeError, match=error):
        await mc._connect_server("srv1", _strict_server(
            allow_tools=allow_tools, tool_policy=tool_policy,
        ))
    assert opened is False
    assert mc.server_runtime("srv1")["last_init_ok"] is False


@pytest.mark.asyncio
async def test_strict_empty_allowlist_is_zero_authorization(monkeypatch):
    tools = [SimpleNamespace(name="read_status", description="", inputSchema={})]
    monkeypatch.setattr(mc, "_open_transport", _noop_transport)
    _patch_client_session(monkeypatch, _FakeSession(tools))

    await mc._connect_server("srv1", _strict_server(allow_tools=[], tool_policy={}))

    runtime = mc.server_runtime("srv1")
    assert runtime["registered_tools"] == []
    assert runtime["pending_confirmation_tools"] == []
    assert "mcp__srv1__read_status" not in td._TOOL_REGISTRY


@pytest.mark.asyncio
async def test_strict_allowlisted_tool_without_confirmation_is_discovered_but_not_registered(monkeypatch):
    tools = [SimpleNamespace(name="mystery_action", description="", inputSchema={})]
    monkeypatch.setattr(mc, "_open_transport", _noop_transport)
    _patch_client_session(monkeypatch, _FakeSession(tools))

    await mc._connect_server("srv1", _strict_server(
        allow_tools=["mystery_action"], tool_policy={},
    ))

    runtime = mc.server_runtime("srv1")
    assert runtime["registered_tools"] == []
    assert runtime["pending_confirmation_tools"] == ["mystery_action"]
    assert "mcp__srv1__mystery_action" not in td._TOOL_REGISTRY


@pytest.mark.parametrize(
    ("name", "description", "annotations", "effect", "high_risk", "status"),
    [
        ("anything", "", {"readOnlyHint": True}, "read", False, "suggested"),
        ("anything", "", {"destructiveHint": True}, "write", True, "suggested"),
        ("send_message", "", None, "write", False, "suggested"),
        ("opaque", "does a thing", None, None, False, "confirmation_required"),
    ],
)
def test_policy_suggestion_uses_annotations_then_conservative_local_heuristic(
    name, description, annotations, effect, high_risk, status,
):
    suggestion = mc.suggest_tool_policy(name, description, annotations)
    assert suggestion["effect"] == effect
    assert suggestion["high_risk"] is high_risk
    assert suggestion["status"] == status


@pytest.mark.asyncio
async def test_legacy_unclassified_policy_preserves_unconfirmed_registration(monkeypatch, caplog):
    tools = [SimpleNamespace(name="send_message", description="", inputSchema={})]
    monkeypatch.setattr(mc, "_require_local_policy", lambda: False)
    monkeypatch.setattr(mc, "_open_transport", _noop_transport)
    _patch_client_session(monkeypatch, _FakeSession(tools))

    await mc._connect_server("srv1", {"transport": "stdio", "command": ["fake"]})

    entry = td._TOOL_REGISTRY["mcp__srv1__send_message"]
    assert entry["dangerous"] is False
    assert entry["mcp_policy_legacy"] is True
    assert "legacy unclassified policy" in caplog.text
    assert entry["ui_label"] == "外部工具"


@pytest.mark.asyncio
async def test_local_policy_ui_label_is_registered_without_using_remote_metadata(monkeypatch):
    tools = [SimpleNamespace(name="remote_name", description="untrusted remote description", inputSchema={})]
    monkeypatch.setattr(mc, "_open_transport", _noop_transport)
    _patch_client_session(monkeypatch, _FakeSession(tools))

    await mc._connect_server("srv1", _strict_server(
        allow_tools=["remote_name"],
        tool_policy={"remote_name": {"effect": "read", "ui_label": "读取设备摘要"}},
    ))

    entry = td._TOOL_REGISTRY["mcp__srv1__remote_name"]
    assert entry["ui_label"] == "读取设备摘要"


def test_categories_mcp_exposes_no_local_tools(monkeypatch):
    monkeypatch.setattr(td, "_is_tool_enabled", lambda name: True)
    td._TOOL_REGISTRY["mcp__srv1__read"] = {
        "description": "", "parameters": {}, "category": "mcp", "dangerous": False,
        "effect": "read",
    }
    schemas = td.get_tools_schema(categories=["mcp"])
    names = {(item.get("function") or item)["name"] for item in schemas}
    assert names == {"mcp__srv1__read"}


class _ConfirmState:
    WAITING_CONFIRM = "waiting_confirm"

    def __init__(self):
        self.status = "idle"
        self.waiting = None

    def set_waiting_confirm(self, tool_name, tool_args):
        self.status = self.WAITING_CONFIRM
        self.waiting = (tool_name, tool_args)


def _mcp_entry(func, *, effect: str, require_confirm: bool):
    return {
        "func": func,
        "description": "", "parameters": {}, "category": "mcp",
        "dangerous": require_confirm, "effect": effect,
        "require_confirm": require_confirm,
    }


@pytest.mark.asyncio
async def test_write_calls_without_confirmation_and_explicit_write_confirmation_uses_existing_state(monkeypatch):
    calls: list[str] = []

    async def _write():
        calls.append("write")
        return "sent"

    async def _confirmed_write():
        calls.append("confirmed")
        return "sent"

    td._TOOL_REGISTRY["mcp__srv__send_message"] = _mcp_entry(
        _write, effect="write", require_confirm=False,
    )
    td._TOOL_REGISTRY["mcp__srv__create_reply"] = _mcp_entry(
        _confirmed_write, effect="write", require_confirm=True,
    )
    monkeypatch.setattr("core.growth.mcp_proficiency.is_tool_allowed", lambda *args, **kwargs: True)
    monkeypatch.setattr("core.self_management.policy.tool_allowed", lambda *args, **kwargs: True)
    monkeypatch.setattr("core.memory.action_trace.record", lambda *args, **kwargs: None)

    result = await td.execute_structured(
        "mcp__srv__send_message", {}, "u1", "u1", False, _ConfirmState(),
        origin="assistant_loop", char_id="c1",
    )
    assert result.result and "sent" in result.result and result.confirmation_request is None and calls == ["write"]

    state = _ConfirmState()
    result = await td.execute_structured(
        "mcp__srv__create_reply", {}, "u1", "u1", False, state,
        origin="assistant_loop", char_id="c1",
    )
    assert result.result is None and result.confirmation_request is not None
    assert state.waiting == ("mcp__srv__create_reply", {})
    assert calls == ["write"]


@pytest.mark.asyncio
async def test_explicit_actuate_confirmation_and_emergency_override(monkeypatch):
    calls: list[str] = []

    async def _actuate():
        calls.append("actuate")
        return "pulsed"

    async def _stop():
        calls.append("stop")
        return "stopped"

    td._TOOL_REGISTRY["mcp__srv__hardware_pulse"] = _mcp_entry(
        _actuate, effect="actuate", require_confirm=True,
    )
    # Verify the dispatcher hard guard too: emergency remains immediate even
    # if a malformed dynamic entry accidentally carries dangerous=True.
    td._TOOL_REGISTRY["mcp__srv__hardware_stop"] = _mcp_entry(
        _stop, effect="emergency", require_confirm=True,
    )
    monkeypatch.setattr("core.growth.mcp_proficiency.is_tool_allowed", lambda *args, **kwargs: True)
    monkeypatch.setattr("core.self_management.policy.tool_allowed", lambda *args, **kwargs: True)
    monkeypatch.setattr("core.memory.action_trace.record", lambda *args, **kwargs: None)

    pulse_state = _ConfirmState()
    result = await td.execute_structured(
        "mcp__srv__hardware_pulse", {}, "u1", "u1", False, pulse_state,
        origin="assistant_loop", char_id="c1",
    )
    assert result.result is None and result.confirmation_request is not None and calls == []

    result = await td.execute_structured(
        "mcp__srv__hardware_stop", {}, "u1", "u1", False, _ConfirmState(),
        origin="assistant_loop", char_id="c1",
    )
    assert result.result and "stopped" in result.result and result.confirmation_request is None and calls == ["stop"]


def test_effect_drives_side_effect_classification_without_changing_local_fallback(monkeypatch):
    monkeypatch.setattr(td, "_TOOL_REGISTRY", {
        "mcp__srv__read": {"effect": "read", "dangerous": False},
        "mcp__srv__write": {"effect": "write", "dangerous": False},
        "mcp__srv__pulse": {"effect": "actuate", "dangerous": False},
        "mcp__srv__stop": {"effect": "emergency", "dangerous": False},
        "local_legacy": {"dangerous": True},
    })
    assert td.get_tool_effect("mcp__srv__read") == "read"
    assert td.is_side_effect_tool("mcp__srv__read") is False
    assert all(td.is_side_effect_tool(name) for name in (
        "mcp__srv__write", "mcp__srv__pulse", "mcp__srv__stop", "local_legacy",
    ))
