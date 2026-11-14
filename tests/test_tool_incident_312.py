"""Regression coverage for false success, invalid plans and autonomy exposure."""
import asyncio
import json
import sys
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from admin import auth
from admin.admin_server import app
from core import llm_client, tool_audit, tool_dispatcher
from core.agent_runtime import computer_use, device_file_agent as service


@pytest.fixture
def file_device(monkeypatch, sandbox):
    config = {"device_file_agent": {"enabled": True}, "scheduler": {"owner_id": "owner"},
              "deployment": {"mode": "remote_server"}}
    monkeypatch.setattr(service.config_loader, "get_config", lambda: config)
    monkeypatch.setattr(computer_use.pipeline_registry, "get",
                        lambda: SimpleNamespace(_active_character_id="fixture_char", character=None))
    monkeypatch.setattr(service, "_devices", {})
    monkeypatch.setattr(service, "_runs", {})
    token = auth.TokenInfo("fixture-desktop", frozenset({"sensor.write", "ws.desktop", "state.read"}), "desktop")
    monkeypatch.setattr(auth, "resolve_token", lambda _: token)
    grant = {"workspace_id": uuid4().hex, "revision": uuid4().hex, "alias": "fixture", "write": True}
    return {"protocol": service.VERSION, "device_id": uuid4().hex,
            "device_epoch": uuid4().hex, "state": "enabled", "grants": [grant]}


async def call(name, args, **kwargs):
    return await tool_dispatcher.execute_structured(
        name, args, "owner", "owner", False, None, origin="assistant_loop",
        char_id="fixture_char", **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [
    ("json", "invalid_file_plan"), ("action", "invalid_file_action"),
    ("budget", "action_budget_exceeded"), ("no_write", "no_write_performed"),
    ("timeout", "plan_timeout"),
])
async def test_failed_file_work_is_failed_at_dispatch_audit_and_observation(file_device, monkeypatch, mode, expected):
    """Bad plans must not spend device actions; read-only evidence cannot complete an edit."""
    monkeypatch.setattr(service, "MAX_ACTIONS", 2)
    planned = 0

    async def model(*args, **kwargs):
        nonlocal planned
        planned += 1
        if mode == "json":
            return "private invalid model response"
        if mode == "action":
            return json.dumps({"action": {"op": "private-invalid-op", "private-field": "private text"}})
        if mode == "timeout":
            raise TimeoutError("private model error")
        if mode == "no_write" and planned > 1:
            return '{"done":true}'
        return json.dumps({"action": {"op": "read", "path": "中文目录/fixture.html"}})

    monkeypatch.setattr(llm_client, "chat", model)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": "Bearer fixture"}) as client:
        assert (await client.post("/device-file-agent/poll", json=file_device)).json()["online"]
        start = await call("device_file_task_start", {"device_id": file_device["device_id"],
                           "workspace_id": file_device["grants"][0]["workspace_id"], "goal": "Edit fixture"})
        task_id = json.loads(start.result)["task_id"]
        commands = 0
        for _ in range(150):
            reply = (await client.post("/device-file-agent/poll", json=file_device)).json()
            if command := reply.get("command"):
                commands += 1
                response = await client.post("/device-file-agent/result", json={
                    "ref": command["ref"], "code": "ok", "body": {"content": "private file text", "sha256": "a" * 64},
                })
                assert response.status_code == 200
            if task_id not in service._runs:
                break
            await asyncio.sleep(.01)
        await service.shutdown()
        result = await call("device_file_task_status", {"task_id": task_id})
        assert result.status == "tool_failed"
        body = json.loads(result.result)
        assert (body["status"], body["code"]) == ("failed", expected)
        assert body["changes"] == body["checks"] == []
        assert commands == {"json": 0, "action": 0, "budget": 2, "no_write": 1, "timeout": 0}[mode]
        assert body["progress"]["actions_completed"] == commands
        assert body["progress"]["stage"] == "failed"
        assert body["progress"]["failure_stage"] == ("validating" if mode == "action" else "planning")
        if commands:
            assert body["progress"]["last_action_at"] > 0
        observed = (await client.get("/observability/device-file-agent")).json()
        assert any(row["task_id"] == task_id and row["progress"]["stage"] == "failed"
                   for row in observed["tasks"])
        persisted = service._path(service.principal(), task_id).read_text(encoding="utf-8")
        assert all(secret not in persisted for secret in (
            "private text", "private-field", "private file text", "private model error", "中文目录"))
        audit = tool_audit.query("owner", "fixture_char", tool="device_file_task_status")[0]
        assert audit["status"] == "failed"
        assert audit["error_code"] == expected
        assert audit["task_id"] == task_id
        from core.memory import action_trace
        display = next(row["display_activity"] for row in action_trace.query("owner", "fixture_char")
                       if row["tool"] == "device_file_task_status" and row.get("display_activity"))
        assert display["status"] == "error"


@pytest.mark.asyncio
async def test_interrupted_dispatched_write_stays_unknown_without_replay(file_device, monkeypatch):
    """An interrupted write without its receipt must stop the tool chain and remain unreplayed."""
    async def model(*args, **kwargs):
        return json.dumps({"action": {"op": "create", "path": "中文目录/fixture.html", "after": "private text"}})

    monkeypatch.setattr(llm_client, "chat", model)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": "Bearer fixture"}) as client:
        assert (await client.post("/device-file-agent/poll", json=file_device)).json()["online"]
        started = await call("device_file_task_start", {"device_id": file_device["device_id"],
                             "workspace_id": file_device["grants"][0]["workspace_id"], "goal": "Create fixture"})
        task_id = json.loads(started.result)["task_id"]
        try:
            for _ in range(150):
                reply = (await client.post("/device-file-agent/poll", json=file_device)).json()
                if command := reply.get("command"):
                    break
                await asyncio.sleep(.01)
            else:
                pytest.fail("desktop command unavailable")
            assert command["action"]["op"] == "create"
        finally:
            await service.shutdown()
        outcome = await call("device_file_task_status", {"task_id": task_id})
        assert outcome.status == "outcome_unknown"
        body = json.loads(outcome.result)
        assert (body["status"], body["code"], body["receipt_pending"]) == (
            "outcome_unknown", "device_receipt_pending", True)
        assert body["changes"] == body["checks"] == []
        assert body["progress"]["failure_stage"] == "device_action"
        assert "command" not in (await client.post("/device-file-agent/poll", json=file_device)).json()
        audit = tool_audit.query("owner", "fixture_char", tool="device_file_task_status")[0]
        assert audit["status"] == "unknown"
        assert audit["task_id"] == task_id
        from core.memory import action_trace
        display = next(row["display_activity"] for row in action_trace.query("owner", "fixture_char")
                       if row["tool"] == "device_file_task_status" and row.get("display_activity"))
        assert display["status"] == "unknown"


@pytest.mark.asyncio
async def test_file_tools_hidden_from_autonomy_but_execution_stays_denied(file_device):
    """Owner authorization must not accidentally expose a forbidden autonomous action."""
    names = lambda **kw: {(s.get("function") or s)["name"] for s in tool_dispatcher.get_tools_schema(
        uid="owner", char_id="fixture_char", **kw)}
    assert "device_workspace_list" in names()
    from core.tools.device_file_tools import NAMES
    assert not (NAMES & names(autonomy=True))
    outcome = await tool_dispatcher.execute_structured(
        "device_workspace_list", {}, "owner", "owner", False, None,
        origin="autonomy_loop", char_id="fixture_char")
    assert outcome.status == "tool_failed"
    assert outcome.result == "device_file_denied"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [("error", "tool_failed"), ("timeout", "tool_failed"),
                                          ("empty", "tool_executed"), ("results", "tool_executed")])
async def test_web_search_failure_and_empty_are_distinct_at_real_dispatch(monkeypatch, sandbox, caplog, mode, expected):
    """Network failures must reach audit as failures; legitimate zero hits remain distinct."""
    class DDGS:
        def __init__(self, **kwargs):
            pass

        def text(self, query, **kwargs):
            if mode == "error":
                raise RuntimeError("private query must not appear")
            if mode == "timeout":
                raise TimeoutError("private query must not appear")
            return [] if mode == "empty" else [{"title": "fixture", "href": "", "body": "public result"}]

    monkeypatch.setitem(sys.modules, "ddgs", SimpleNamespace(DDGS=DDGS))
    from core import config_loader, error_log
    error_lines = []
    monkeypatch.setattr(error_log, "append", error_lines.append)
    monkeypatch.setattr(config_loader, "get_config", lambda: {"scheduler": {"owner_id": "owner"}})
    outcome = await call("web_search", {"query": "synthetic fixture"})
    assert outcome.status == expected
    assert "private query" not in outcome.result
    assert "private query" not in "".join(error_lines) + caplog.text
    audit = tool_audit.query("owner", "fixture_char", tool="web_search")[0]
    assert audit["status"] == ("failed" if expected == "tool_failed" else "success")
    from core.memory import action_trace
    display = next(row["display_activity"] for row in action_trace.query("owner", "fixture_char")
                   if row["tool"] == "web_search" and row.get("display_activity"))
    assert display["status"] == ("error" if expected == "tool_failed" else "success")
    if mode in {"error", "timeout"}:
        assert json.loads(outcome.result)["code"] == ("web_search_timeout" if mode == "timeout" else "web_search_failed")
    elif mode == "empty":
        assert json.loads(outcome.result)["status"] == "empty"
    else:
        assert "public result" in outcome.result
