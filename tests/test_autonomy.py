from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _no_cross_test_user_activity(monkeypatch):
    """Autonomy unit tests control user activity explicitly when it matters."""
    monkeypatch.setattr(
        "core.autonomy.runner._user_became_active", lambda _uid: False
    )


def test_autonomy_state_is_durable_and_manual_enqueue_is_not_execution(sandbox):
    from core.autonomy import store
    job, status = store.enqueue("owner", "char", "manual", dedupe_key="test-job")
    assert status == "queued" and job is not None
    state = store.load("owner", "char")
    assert state["jobs"][0]["status"] == "pending"
    assert state["runs"] == []


def test_autonomy_origin_is_explicit_and_fail_closed_for_unknown(sandbox):
    from core import tool_dispatcher
    assert "autonomy_loop" in tool_dispatcher._EXECUTE_ALLOWED_ORIGINS
    result = asyncio.run(tool_dispatcher.execute("get_time", {}, "owner", "owner", False, object(), origin="unknown_autonomy", char_id="char"))
    assert result == (None, None)


def test_two_unanswered_messages_hide_talk_and_real_user_message_resets(sandbox, monkeypatch):
    from core.scheduler import proactive_ledger as ledger
    monkeypatch.setattr(ledger, "_cfg", lambda: {"owner_id": "owner", "global_proactive_min_gap_seconds": 1, "max_daily_proactive": 99})
    ledger.record_send("scheduler", uid="owner")
    ledger.record_send("wake", uid="owner")
    assert ledger.continuity_status("owner")["consecutive_unanswered_talks"] == 2
    allowed, reason = ledger.can_send("autonomy", uid="owner")
    assert not allowed and reason == "unanswered_cap"
    ledger.record_user_message("owner")
    assert ledger.continuity_status("owner")["consecutive_unanswered_talks"] == 0


def test_autonomy_tool_schema_never_contains_talk_when_cap_reached(sandbox, monkeypatch):
    from core.autonomy import talk_gate
    monkeypatch.setattr("core.scheduler.proactive_ledger.continuity_status", lambda uid: {"consecutive_unanswered_talks": 2})
    mode, reason = talk_gate.check("owner")
    assert mode == "hard" and reason == "suppressed_unanswered_cap"


def test_tools_only_run_never_imports_or_calls_turn_sink(sandbox, monkeypatch):
    from core.autonomy import runner, store
    from core.autonomy.models import Job
    state = store.load("owner", "char"); state["config"]["enabled"] = True
    monkeypatch.setattr(runner.policy, "admission", lambda *args: None)
    monkeypatch.setattr(runner.policy, "allowed_tools", lambda *args: [{"type": "function", "function": {"name": "safe_tool", "parameters": {}}}])
    monkeypatch.setattr(runner.talk_gate, "check", lambda *args, **kwargs: ("hard", "suppressed_unanswered_cap"))
    calls = iter([
        SimpleNamespace(tool_calls=[{"id": "one", "name": "safe_tool", "arguments": {}}], continuation_items=[], assistant_message={}),
        SimpleNamespace(tool_calls=[], continuation_items=[], assistant_message={}),
    ])
    async def chat_turn(*_args, **_kwargs): return next(calls)
    async def execute(*_args, **_kwargs): return "ok", "ok"
    def ledger_send(*_args, **_kwargs): raise AssertionError("silent tools must not count as proactive speech")
    monkeypatch.setattr("core.llm_client.chat_turn", chat_turn)
    monkeypatch.setattr(runner, "_execute_tool", execute)
    monkeypatch.setattr("core.scheduler.proactive_ledger.record_send", ledger_send)
    run = asyncio.run(runner._run_locked(Job(uid="owner", char_id="char", source="manual"), state, runner.Run(uid="owner", char_id="char", source="manual", job_id="job")))
    assert run.disposition == "completed_tools_only"
    assert run.talk_sent is False
    assert run.prompt_snapshot
    assert run.prompt_snapshot[-1]["role"] == "user"


def test_soft_block_allows_only_one_confirm_decision(sandbox, monkeypatch):
    from core.autonomy import runner, store
    from core.autonomy.models import Job, Run
    state = store.load("owner", "char"); state["config"]["enabled"] = True
    monkeypatch.setattr(runner.policy, "admission", lambda *args: None)
    monkeypatch.setattr(runner.policy, "allowed_tools", lambda *args: [])
    modes = iter([("soft", "gap_not_elapsed"), ("soft", "gap_not_elapsed")])
    monkeypatch.setattr(runner.talk_gate, "check", lambda *args, **kwargs: next(modes))
    calls = iter([
        SimpleNamespace(tool_calls=[{"id": "talk", "name": "talk_owner", "arguments": {"text": "hi", "reason": "test"}}], continuation_items=[], assistant_message={}),
        SimpleNamespace(tool_calls=[{"id": "confirm", "name": "confirm_talk", "arguments": {"action": "cancel"}}], continuation_items=[], assistant_message={}),
    ])
    exposed = []
    async def chat_turn(_messages, schemas, **_kwargs):
        exposed.append([((schema.get("function") or schema).get("name")) for schema in schemas])
        return next(calls)
    monkeypatch.setattr("core.llm_client.chat_turn", chat_turn)
    run = asyncio.run(runner._run_locked(Job(uid="owner", char_id="char", source="manual"), state, Run(uid="owner", char_id="char", source="manual", job_id="job")))
    assert run.disposition == "talk_soft_blocked_then_canceled"
    assert run.talk_soft_blocked is True
    assert exposed[1] == ["confirm_talk"]


def test_autonomy_tool_policy_rejects_memory_and_unsandboxed_writes():
    from core.autonomy.policy import tool_eligibility
    registry = {
        "water_garden": {"category": "info"},
        "revise_memory": {"category": "info"},
        "desktop_notify": {"category": "desktop"},
    }
    assert tool_eligibility("water_garden", {"enabled": True}, registry=registry, effect="write") == (True, "eligible")
    assert tool_eligibility("revise_memory", {"enabled": True}, registry=registry, effect="write") == (False, "write_not_sandboxed_for_autonomy")
    assert tool_eligibility("desktop_notify", {"enabled": True}, registry=registry, effect="actuate")[0] is False


def test_write_tool_budget_prevents_second_write(sandbox, monkeypatch):
    from core.autonomy import runner, store
    from core.autonomy.models import Job, Run
    state = store.load("owner", "char")
    state["config"].update({"enabled": True, "max_tools": 3, "max_write_tools": 1})
    monkeypatch.setattr(runner.policy, "admission", lambda *args: None)
    monkeypatch.setattr(runner.policy, "allowed_tools", lambda *args: [{"type": "function", "function": {"name": "write_a", "parameters": {}}}, {"type": "function", "function": {"name": "write_b", "parameters": {}}}])
    monkeypatch.setattr(runner.talk_gate, "check", lambda *args, **kwargs: ("hard", "suppressed_unanswered_cap"))
    monkeypatch.setattr(runner, "_is_write_tool", lambda _name: True)
    calls = iter([
        SimpleNamespace(tool_calls=[{"id": "one", "name": "write_a", "arguments": {}}, {"id": "two", "name": "write_b", "arguments": {}}], continuation_items=[], assistant_message={}),
        SimpleNamespace(tool_calls=[], continuation_items=[], assistant_message={}),
    ])
    invoked = []
    async def chat_turn(*_args, **_kwargs): return next(calls)
    async def execute(name, *_args, **_kwargs): invoked.append(name); return "ok", "ok"
    monkeypatch.setattr("core.llm_client.chat_turn", chat_turn)
    monkeypatch.setattr(runner, "_execute_tool", execute)
    run = asyncio.run(runner._run_locked(Job(uid="owner", char_id="char", source="manual"), state, Run(uid="owner", char_id="char", source="manual", job_id="job")))
    assert invoked == ["write_a"]
    assert run.tool_names == ["write_a"]


def test_self_capability_enables_mcp_for_a_later_call_in_the_same_run(sandbox, monkeypatch):
    from core.autonomy import runner, store
    from core.autonomy.models import Job, Run
    from core.self_management import policy as self_policy, store as self_store
    from core.self_management.service import user_grant
    from core.tool_dispatcher import _TOOL_REGISTRY

    tool_name = "mcp__test__status"
    capability_id = "mcp.use:test/status"
    called = []

    async def mcp_status(**_kwargs):
        called.append(True)
        return "connected"

    monkeypatch.setitem(_TOOL_REGISTRY, tool_name, {
        "func": mcp_status, "description": "test mcp status", "parameters": {"type": "object", "properties": {}},
        "category": "mcp", "mcp_server": "test", "mcp_tool": "status", "effect": "read", "dangerous": False,
    })
    assert user_grant("owner", "char", capability_id=capability_id, allowed=True, mutable_by_agent=True, constraints={}, reason="allow").ok
    capability_state = self_store.load("owner", "char")
    capability_state["agent_state"][capability_id] = False
    assert self_store.save("owner", "char", capability_state)
    state = store.load("owner", "char")
    state["config"].update({"enabled": True, "max_steps": 3, "max_tools": 2})

    def current_tools(uid, char_id, _state):
        if not self_policy.tool_allowed(uid, char_id, tool_name):
            return []
        return [{"type": "function", "function": {"name": tool_name, "parameters": {"type": "object", "properties": {}}}}]

    monkeypatch.setattr(runner.policy, "allowed_tools", current_tools)
    monkeypatch.setattr(runner.policy, "admission", lambda *args: None)
    monkeypatch.setattr(runner.talk_gate, "check", lambda *args, **kwargs: ("hard", "suppressed_unanswered_cap"))
    monkeypatch.setattr("core.growth.mcp_proficiency.is_tool_allowed", lambda *_args, **_kwargs: True)
    calls = iter([
        SimpleNamespace(tool_calls=[{"id": "enable", "name": "manage_self_capability", "arguments": {"action": "enable", "capability_id": capability_id, "reason": "needed", "expected_revision": 1, "action_id": "enable-mcp"}}], continuation_items=[], assistant_message={}),
        SimpleNamespace(tool_calls=[{"id": "mcp", "name": tool_name, "arguments": {}}], continuation_items=[], assistant_message={}),
        SimpleNamespace(tool_calls=[], continuation_items=[], assistant_message={}),
    ])
    exposed = []

    async def chat_turn(_messages, schemas, **_kwargs):
        exposed.append({(schema.get("function") or schema).get("name") for schema in schemas})
        return next(calls)

    monkeypatch.setattr("core.llm_client.chat_turn", chat_turn)
    run = asyncio.run(runner._run_locked(Job(uid="owner", char_id="char", source="manual", id="job-e2e"), state, Run(uid="owner", char_id="char", source="manual", job_id="job-e2e", id="run-e2e")))
    assert tool_name not in exposed[0] and tool_name in exposed[1]
    assert called == [True]
    assert run.disposition == "completed_tools_only"
    assert any(event["status"] == "self_capability_changed" for event in run.events)
    mcp_event = next(event for event in run.events if event.get("tool_name") == tool_name)
    assert mcp_event["mcp_audit_id"] == "autonomy:run-e2e:job-e2e"
    audit = self_store.read_audit("owner", "char", limit=10)
    assert any(row.get("action_id") == "enable-mcp" and row.get("run_id") == "run-e2e" and row.get("job_id") == "job-e2e" for row in audit)


def test_self_capability_disable_denies_following_call_before_dispatch(sandbox, monkeypatch):
    from core.autonomy import runner, store
    from core.autonomy.models import Job, Run
    from core.self_management.service import user_grant
    from core.tool_dispatcher import _TOOL_REGISTRY

    tool_name = "mcp__test__status"
    capability_id = "mcp.use:test/status"
    called = []

    async def mcp_status(**_kwargs):
        called.append(True)
        return "unexpected"

    monkeypatch.setitem(_TOOL_REGISTRY, tool_name, {
        "func": mcp_status, "description": "test mcp status", "parameters": {"type": "object", "properties": {}},
        "category": "mcp", "mcp_server": "test", "mcp_tool": "status", "effect": "read", "dangerous": False,
    })
    assert user_grant("owner", "char", capability_id=capability_id, allowed=True, mutable_by_agent=True, constraints={}, reason="allow").ok
    state = store.load("owner", "char")
    state["config"].update({"enabled": True, "max_steps": 2, "max_tools": 2})
    schema = {"type": "function", "function": {"name": tool_name, "parameters": {"type": "object", "properties": {}}}}
    monkeypatch.setattr(runner.policy, "allowed_tools", lambda uid, char_id, _state: [schema] if __import__("core.self_management.policy", fromlist=["tool_allowed"]).tool_allowed(uid, char_id, tool_name) else [])
    monkeypatch.setattr(runner.policy, "admission", lambda *args: None)
    monkeypatch.setattr(runner.talk_gate, "check", lambda *args, **kwargs: ("hard", "suppressed_unanswered_cap"))
    calls = iter([
        SimpleNamespace(tool_calls=[{"id": "disable", "name": "manage_self_capability", "arguments": {"action": "disable", "capability_id": capability_id, "reason": "stop", "expected_revision": 1, "action_id": "disable-mcp"}}], continuation_items=[], assistant_message={}),
        SimpleNamespace(tool_calls=[{"id": "mcp", "name": tool_name, "arguments": {}}], continuation_items=[], assistant_message={}),
    ])
    monkeypatch.setattr("core.llm_client.chat_turn", lambda *_args, **_kwargs: _async_next(calls))
    run = asyncio.run(runner._run_locked(Job(uid="owner", char_id="char", source="manual"), state, Run(uid="owner", char_id="char", source="manual", job_id="job")))
    assert called == []
    assert run.disposition == "tool_call_denied"
    assert run.events[-1] == {"status": "tool_call_denied", "tool_name": tool_name, "reason": "not_in_current_effective_allowlist"}


async def _async_next(values):
    return next(values)


def test_schedule_window_supports_local_cross_midnight():
    from core.autonomy.runner import _inside_schedule_window
    assert _inside_schedule_window(23, 30, ["22:00", "02:00"])
    assert _inside_schedule_window(1, 30, ["22:00", "02:00"])
    assert not _inside_schedule_window(12, 0, ["22:00", "02:00"])


def test_consecutive_failures_open_circuit_and_success_closes_it(sandbox):
    from core.autonomy import store
    from core.autonomy.models import Job, Run
    job = Job(uid="owner", char_id="char", source="manual")
    state = store.load("owner", "char")
    state["config"].update({"circuit_failure_threshold": 2, "circuit_cooldown_seconds": 60})
    store.save("owner", "char", state)
    for _ in range(2):
        store.enqueue("owner", "char", "manual", dedupe_key=__import__("uuid").uuid4().hex)
        claimed = store.claim_due("owner", "char")
        store.finish(claimed, Run(uid="owner", char_id="char", source="manual", job_id=claimed.id, disposition="llm_failed", finished_at=1))
    state = store.load("owner", "char")
    assert store.circuit_open(state)
    store.enqueue("owner", "char", "manual", dedupe_key=__import__("uuid").uuid4().hex)
    claimed = store.claim_due("owner", "char")
    store.finish(claimed, Run(uid="owner", char_id="char", source="manual", job_id=claimed.id, disposition="completed_no_op", finished_at=1))
    assert not store.circuit_open(store.load("owner", "char"))


def test_admin_enqueue_only_creates_job_not_llm_work(sandbox, monkeypatch):
    import admin.routers.autonomy as api
    monkeypatch.setattr(api, "_scope", lambda: ("owner", "char"))
    calls = []
    async def forbidden(*_args, **_kwargs): calls.append(True); raise AssertionError("LLM must not run in HTTP request")
    monkeypatch.setattr("core.autonomy.runner.run_job", forbidden)
    result = asyncio.run(api.test_enqueue({"source": "schedule"}, auth=None))
    assert result["status"] == "queued" and result["job_id"]
    assert calls == []


def test_admin_config_rejects_bad_timezone(sandbox, monkeypatch):
    import admin.routers.autonomy as api
    monkeypatch.setattr(api, "_scope", lambda: ("owner", "char"))
    import pytest
    with pytest.raises(Exception) as exc:
        asyncio.run(api.patch_config({"schedule": {"timezone": "Not/AZone"}}, auth=None))
    assert "timezone" in str(exc.value)


def test_admin_tool_surface_reports_the_effective_decision_matrix(sandbox, monkeypatch):
    import admin.routers.autonomy as api

    monkeypatch.setattr(api, "_scope", lambda: ("owner", "char"))
    data = asyncio.run(api.tools(auth=None))
    assert data["tools"]
    required = {
        "allowed", "decision_source", "global_enabled", "deployment", "self_capability",
        "mcp_policy", "autonomy_policy", "danger", "confirmation", "registered",
        "self_capability_granted", "agent_selected_state", "autonomy_allowlist",
        "effect", "dangerous", "require_confirm", "execution_allowed", "final_schema",
        "denial_reason",
    }
    assert required <= set(data["tools"][0])


def test_tool_eligibility_is_allowlist_admission_not_final_schema():
    from core.autonomy.policy import tool_eligibility
    registry = {
        "mcp__weather__forecast": {"category": "mcp", "dangerous": False, "require_confirm": False},
    }
    eligible, reason = tool_eligibility(
        "mcp__weather__forecast",
        {"enabled": False, "mcp_explicit": False, "outcome_unknown": "fail_closed"},
        registry=registry,
        effect="read",
    )
    assert eligible is False
    assert reason == "mcp_requires_explicit_enablement"


def test_connected_readonly_mcp_inherits_without_allowlist(sandbox, monkeypatch):
    from core.autonomy import policy, store
    from core.self_management.service import user_grant
    from core.tool_dispatcher import _TOOL_REGISTRY

    tool_name = "mcp__inherit__status"
    capability_id = "mcp.use:inherit/status"
    monkeypatch.setitem(_TOOL_REGISTRY, tool_name, {
        "func": lambda **_kwargs: "ok",
        "description": "inherited mcp read",
        "parameters": {"type": "object", "properties": {}},
        "category": "mcp",
        "mcp_server": "inherit",
        "mcp_tool": "status",
        "effect": "read",
        "dangerous": False,
        "require_confirm": False,
    })
    monkeypatch.setattr("core.tool_dispatcher._is_tool_enabled", lambda name: True)
    monkeypatch.setattr(
        "core.tool_dispatcher.get_tools_schema",
        lambda **_kwargs: [{"type": "function", "function": {"name": tool_name}}],
    )
    monkeypatch.setattr("core.mcp_client.server_runtime", lambda _server: {
        "connected": True,
        "registered_tools": [tool_name],
    })
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"mcp_servers": {}})
    assert user_grant("owner", "char", capability_id=capability_id, allowed=True, mutable_by_agent=True, constraints={}, reason="allow").ok
    state = store.load("owner", "char")
    row = next(item for item in policy.tool_decisions("owner", "char", state) if item["name"] == tool_name)
    assert row["eligible"] is False
    assert row["eligibility_reason"] == "mcp_requires_explicit_enablement"
    assert row["allowed"] is True
    assert row["final_schema"] is True
    assert row["execution_allowed"] is True
    assert row["decision_source"] == "global_read_inheritance"
    assert row["autonomy_policy"] == "global_read_inheritance"
    assert [item["function"]["name"] for item in policy.allowed_tools("owner", "char", state)] == [tool_name]


def test_allowlist_and_inheritance_share_one_denied_reason(sandbox, monkeypatch):
    from core.autonomy import policy, store
    from core.self_management.service import user_grant
    from core.tool_dispatcher import _TOOL_REGISTRY

    tool_name = "mcp__inherit__status"
    capability_id = "mcp.use:inherit/status"
    monkeypatch.setitem(_TOOL_REGISTRY, tool_name, {
        "func": lambda **_kwargs: "ok",
        "description": "inherited mcp read",
        "parameters": {"type": "object", "properties": {}},
        "category": "mcp",
        "mcp_server": "inherit",
        "mcp_tool": "status",
        "effect": "read",
        "dangerous": False,
        "require_confirm": False,
    })
    monkeypatch.setattr("core.tool_dispatcher._is_tool_enabled", lambda name: True)
    monkeypatch.setattr("core.tool_dispatcher.get_tools_schema", lambda **_kwargs: [])
    monkeypatch.setattr("core.mcp_client.server_runtime", lambda _server: {
        "connected": True,
        "registered_tools": [tool_name],
    })
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"mcp_servers": {}})
    assert user_grant("owner", "char", capability_id=capability_id, allowed=True, mutable_by_agent=True, constraints={}, reason="allow").ok
    state = store.load("owner", "char")
    row = next(item for item in policy.tool_decisions("owner", "char", state) if item["name"] == tool_name)
    assert row["allowed"] is False
    assert row["denial_reason"] == "schema_unavailable"
    assert row["decision_source"] == "global_read_inheritance"


def test_dangerous_and_confirm_tools_stay_ineligible():
    from core.autonomy.policy import tool_eligibility
    registry = {
        "device_shutdown": {"category": "system", "dangerous": True, "require_confirm": True},
        "desktop_notify": {"category": "desktop", "dangerous": False, "require_confirm": False},
    }
    eligible, reason = tool_eligibility(
        "device_shutdown", {"enabled": True}, registry=registry, effect="write"
    )
    assert eligible is False
    assert reason == "side_effect_or_confirmation_required"
    assert tool_eligibility("desktop_notify", {"enabled": True}, registry=registry, effect="actuate")[0] is False


def test_global_disable_blocks_inherited_mcp(sandbox, monkeypatch):
    from core.autonomy import policy, store
    from core.self_management.service import user_grant
    from core.tool_dispatcher import _TOOL_REGISTRY

    tool_name = "mcp__inherit__status"
    capability_id = "mcp.use:inherit/status"
    monkeypatch.setitem(_TOOL_REGISTRY, tool_name, {
        "func": lambda **_kwargs: "ok",
        "description": "inherited mcp read",
        "parameters": {"type": "object", "properties": {}},
        "category": "mcp",
        "mcp_server": "inherit",
        "mcp_tool": "status",
        "effect": "read",
        "dangerous": False,
        "require_confirm": False,
    })
    monkeypatch.setattr("core.tool_dispatcher._is_tool_enabled", lambda name: False)
    monkeypatch.setattr(
        "core.tool_dispatcher.get_tools_schema",
        lambda **_kwargs: [{"type": "function", "function": {"name": tool_name}}],
    )
    monkeypatch.setattr("core.mcp_client.server_runtime", lambda _server: {
        "connected": True,
        "registered_tools": [tool_name],
    })
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"mcp_servers": {}})
    assert user_grant("owner", "char", capability_id=capability_id, allowed=True, mutable_by_agent=True, constraints={}, reason="allow").ok
    row = next(item for item in policy.tool_decisions("owner", "char", store.load("owner", "char")) if item["name"] == tool_name)
    assert row["allowed"] is False
    assert row["denial_reason"] == "globally_disabled"


def test_deployment_gate_blocks_local_capability(sandbox, monkeypatch):
    from core.autonomy import policy, store

    monkeypatch.setattr("core.tool_dispatcher._is_tool_enabled", lambda name: True)
    monkeypatch.setattr(
        "core.tool_dispatcher.get_tools_schema",
        lambda **_kwargs: [{"type": "function", "function": {"name": "fs_list"}}],
    )
    monkeypatch.setattr(
        "core.deployment_capabilities.tool_allowed",
        lambda name, config=None: (False, "disabled_remote_server_local_capability") if name == "fs_list" else (True, None),
    )
    state = store.load("owner", "char")
    state["config"]["tools"] = {"fs_list": {"enabled": True, "mcp_explicit": False, "outcome_unknown": "fail_closed"}}
    row = next(item for item in policy.tool_decisions("owner", "char", state) if item["name"] == "fs_list")
    assert row["allowed"] is False
    assert row["deployment"] is False
    assert row["denial_reason"] == "disabled_remote_server_local_capability"


def test_self_capability_revoke_blocks_inherited_mcp(sandbox, monkeypatch):
    from core.autonomy import policy, store
    from core.self_management.service import user_grant
    from core.tool_dispatcher import _TOOL_REGISTRY

    tool_name = "mcp__inherit__status"
    capability_id = "mcp.use:inherit/status"
    monkeypatch.setitem(_TOOL_REGISTRY, tool_name, {
        "func": lambda **_kwargs: "ok",
        "description": "inherited mcp read",
        "parameters": {"type": "object", "properties": {}},
        "category": "mcp",
        "mcp_server": "inherit",
        "mcp_tool": "status",
        "effect": "read",
        "dangerous": False,
        "require_confirm": False,
    })
    monkeypatch.setattr("core.tool_dispatcher._is_tool_enabled", lambda name: True)
    monkeypatch.setattr(
        "core.tool_dispatcher.get_tools_schema",
        lambda **_kwargs: [{"type": "function", "function": {"name": tool_name}}],
    )
    monkeypatch.setattr("core.mcp_client.server_runtime", lambda _server: {
        "connected": True,
        "registered_tools": [tool_name],
    })
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"mcp_servers": {}})
    assert user_grant("owner", "char", capability_id=capability_id, allowed=False, mutable_by_agent=True, constraints={}, reason="revoke").ok
    row = next(item for item in policy.tool_decisions("owner", "char", store.load("owner", "char")) if item["name"] == tool_name)
    assert row["allowed"] is False
    assert row["self_capability"] is False
    assert row["denial_reason"] == "self_capability_disabled"


def test_require_confirm_mcp_does_not_inherit(sandbox, monkeypatch):
    from core.autonomy import policy, store
    from core.self_management.service import user_grant
    from core.tool_dispatcher import _TOOL_REGISTRY

    tool_name = "mcp__inherit__status"
    capability_id = "mcp.use:inherit/status"
    monkeypatch.setitem(_TOOL_REGISTRY, tool_name, {
        "func": lambda **_kwargs: "ok",
        "description": "confirm mcp",
        "parameters": {"type": "object", "properties": {}},
        "category": "mcp",
        "mcp_server": "inherit",
        "mcp_tool": "status",
        "effect": "read",
        "dangerous": False,
        "require_confirm": True,
    })
    monkeypatch.setattr("core.tool_dispatcher._is_tool_enabled", lambda name: True)
    monkeypatch.setattr(
        "core.tool_dispatcher.get_tools_schema",
        lambda **_kwargs: [{"type": "function", "function": {"name": tool_name}}],
    )
    monkeypatch.setattr("core.mcp_client.server_runtime", lambda _server: {
        "connected": True,
        "registered_tools": [tool_name],
    })
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"mcp_servers": {}})
    assert user_grant("owner", "char", capability_id=capability_id, allowed=True, mutable_by_agent=True, constraints={}, reason="allow").ok
    row = next(item for item in policy.tool_decisions("owner", "char", store.load("owner", "char")) if item["name"] == tool_name)
    assert row["eligible"] is False
    assert row["allowed"] is False
    assert row["confirmation"] is True
    assert row["denial_reason"] == "autonomy_allowlist_disabled"


def test_mcp_local_policy_blocks_allowlisted_mcp(sandbox, monkeypatch):
    from core.autonomy import policy, store
    from core.self_management.service import user_grant
    from core.tool_dispatcher import _TOOL_REGISTRY

    tool_name = "mcp__inherit__status"
    capability_id = "mcp.use:inherit/status"
    monkeypatch.setitem(_TOOL_REGISTRY, tool_name, {
        "func": lambda **_kwargs: "ok",
        "description": "policy mcp",
        "parameters": {"type": "object", "properties": {}},
        "category": "mcp",
        "mcp_server": "inherit",
        "mcp_tool": "status",
        "effect": "read",
        "dangerous": False,
        "require_confirm": False,
    })
    monkeypatch.setattr("core.tool_dispatcher._is_tool_enabled", lambda name: True)
    monkeypatch.setattr(
        "core.tool_dispatcher.get_tools_schema",
        lambda **_kwargs: [{"type": "function", "function": {"name": tool_name}}],
    )
    monkeypatch.setattr("core.mcp_client.server_runtime", lambda _server: {
        "connected": True,
        "registered_tools": [tool_name],
    })
    monkeypatch.setattr("core.config_loader.get_config", lambda: {
        "mcp_servers": {
            "require_local_policy": True,
            "servers": [{"name": "inherit", "allow_tools": [], "tool_policy": {}}],
        }
    })
    assert user_grant("owner", "char", capability_id=capability_id, allowed=True, mutable_by_agent=True, constraints={}, reason="allow").ok
    state = store.load("owner", "char")
    state["config"]["tools"] = {
        tool_name: {"enabled": True, "mcp_explicit": True, "outcome_unknown": "fail_closed"}
    }
    row = next(item for item in policy.tool_decisions("owner", "char", state) if item["name"] == tool_name)
    assert row["allowed"] is False
    assert row["mcp_policy"] == "mcp_local_policy_denied"
    assert row["denial_reason"] == "mcp_local_policy_denied"


def test_execution_recheck_denies_after_schema_exposure(sandbox, monkeypatch):
    from core.autonomy import runner, store
    from core.autonomy.models import Job, Run

    state = store.load("owner", "char")
    state["config"]["enabled"] = True
    schema = {"type": "function", "function": {"name": "safe_tool", "parameters": {}}}
    calls = iter([
        [schema],
        [],
    ])
    monkeypatch.setattr(runner.policy, "admission", lambda *args: None)
    monkeypatch.setattr(runner.policy, "allowed_tools", lambda *_args: next(calls))
    monkeypatch.setattr(runner.talk_gate, "check", lambda *args, **kwargs: ("hard", "suppressed_unanswered_cap"))
    invoked = []

    async def chat_turn(_messages, schemas, **_kwargs):
        return SimpleNamespace(
            tool_calls=[{"id": "one", "name": "safe_tool", "arguments": {}}],
            continuation_items=[],
            assistant_message={},
        )

    async def execute(*_args, **_kwargs):
        invoked.append(True)
        return "ok", "ok"

    monkeypatch.setattr("core.llm_client.chat_turn", chat_turn)
    monkeypatch.setattr(runner, "_execute_tool", execute)
    run = asyncio.run(runner._run_locked(
        Job(uid="owner", char_id="char", source="manual"),
        state,
        Run(uid="owner", char_id="char", source="manual", job_id="job"),
    ))
    assert invoked == []
    assert run.disposition == "tool_call_denied"
    assert run.events[-1]["status"] == "tool_call_denied"
    assert run.events[-1]["reason"] == "not_in_current_effective_allowlist"


def test_expired_lease_can_be_reclaimed_but_stale_finisher_cannot_overwrite(sandbox, monkeypatch):
    from core.autonomy import store
    from core.autonomy.models import Run
    job, _ = store.enqueue("owner", "char", "manual", dedupe_key="lease")
    first = store.claim_due("owner", "char")
    state = store.load("owner", "char")
    state["jobs"][0]["lease_until"] = 0
    store.save("owner", "char", state)
    second = store.claim_due("owner", "char")
    assert second.id == first.id and second.lease_token != first.lease_token
    store.finish(first, Run(uid="owner", char_id="char", source="manual", job_id=first.id, disposition="completed_no_op", finished_at=1))
    state = store.load("owner", "char")
    assert state["jobs"][0]["status"] == "processing"
    assert state.get("sources", {}).get("manual", {}).get("last_evaluated_at") is None


def test_talk_does_not_enter_turn_sink_without_a_delivery_channel(sandbox, monkeypatch):
    from core.autonomy import talk_gate
    monkeypatch.setattr(talk_gate, "check", lambda *_args, **_kwargs: ("allow", "ok"))
    monkeypatch.setattr("core.pipeline_registry.get", lambda: object())
    monkeypatch.setattr("channels.registry.get_active", lambda: [])
    invoked = []
    async def forbidden(**_kwargs): invoked.append(True)
    monkeypatch.setattr("core.turn_sink.record_assistant_turn", forbidden)
    sent, reason = asyncio.run(talk_gate.send("owner", "char", "可以聊一句。", source="manual", run_id="run"))
    assert not sent and reason == "no_delivery_channel"
    assert invoked == []


def test_temporary_retry_is_durably_backed_off(sandbox):
    from core.autonomy import store
    from core.autonomy.models import Run
    store.enqueue("owner", "char", "schedule", dedupe_key="retry")
    job = store.claim_due("owner", "char")
    store.finish(job, Run(uid="owner", char_id="char", source="schedule", job_id=job.id, disposition="blocked_dream", finished_at=1), retry=True)
    state = store.load("owner", "char")
    assert state["jobs"][0]["next_attempt_at"] > 0
    assert store.claim_due("owner", "char") is None


def test_successful_talk_uses_turn_sink_then_records_shared_ledger(sandbox, monkeypatch):
    from core.autonomy import talk_gate
    monkeypatch.setattr(talk_gate, "check", lambda *_args, **_kwargs: ("allow", "ok"))
    monkeypatch.setattr("core.pipeline_registry.get", lambda: object())
    monkeypatch.setattr("channels.registry.get_active", lambda: [object()])
    monkeypatch.setattr("core.response_processor.strip_render_tags", lambda text: text)
    monkeypatch.setattr("core.reality_output_scrubber.scrub_reality_output_text", lambda text: text)
    order = []
    async def turn_sink(**kwargs):
        order.append(("sink", kwargs)); return SimpleNamespace(fanout_targets=["desktop"])
    def ledger(*args, **kwargs): order.append(("ledger", kwargs))
    monkeypatch.setattr("core.turn_sink.record_assistant_turn", turn_sink)
    monkeypatch.setattr("core.scheduler.proactive_ledger.record_send", ledger)
    sent, reason = asyncio.run(talk_gate.send("owner", "char", "可以聊一句。", source="manual", run_id="run"))
    assert sent and reason == "sent"
    assert [item[0] for item in order] == ["sink", "ledger"]
    assert order[0][1]["bypass_gate"] is False
    assert order[0][1]["envelope"].can_write_memory is True
    assert order[1][1]["uid"] == "owner"


@pytest.mark.asyncio
async def test_run_job_does_not_hold_conversation_lock_during_evaluation(
    sandbox, monkeypatch
):
    from core.autonomy import runner
    from core.autonomy.models import Job, Run

    class ForbiddenLock:
        def locked(self):
            return False

        async def __aenter__(self):
            raise AssertionError("autonomy evaluation entered conversation_lock")

        async def __aexit__(self, *_args):
            return False

    job = Job(uid="owner", char_id="char", source="manual")
    state = {"config": {}}
    monkeypatch.setattr(runner.store, "load", lambda *_args: state)
    monkeypatch.setattr(runner.store, "renew", lambda *_args: True)
    monkeypatch.setattr(runner.policy, "admission", lambda *_args: None)
    monkeypatch.setattr(
        "core.conversation_gate.conversation_lock", lambda _uid: ForbiddenLock()
    )

    async def evaluate(_job, _state, run):
        await asyncio.sleep(0)
        run.disposition = "completed_no_op"
        return runner._finish(run)

    monkeypatch.setattr(runner, "_run_locked", evaluate)
    result = await asyncio.wait_for(runner.run_job(job), timeout=1)
    assert isinstance(result, Run)
    assert result.disposition == "completed_no_op"
