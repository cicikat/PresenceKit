"""Typed tasks through dispatcher/HTTP: no-write success and bounded bad planners."""
import asyncio
import json

import httpx
import pytest

from admin.admin_server import app
from core import llm_client, tool_audit
from core.agent_runtime import device_file_agent as service
from tests.test_tool_incident_312 import file_device, call


@pytest.mark.asyncio
async def test_ambiguous_target_pauses_same_task_without_dispatch(file_device, monkeypatch):
    """A planner's clarification request must stay paused, without a write or replacement task."""
    async def model(*args, **kwargs):
        return '{"waiting_user":true}'

    monkeypatch.setattr(llm_client, "chat", model)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": "Bearer fixture"}) as client:
        await client.post("/device-file-agent/poll", json=file_device)
        started = json.loads((await call("device_file_task_start", {
            "device_id": file_device["device_id"], "workspace_id": file_device["grants"][0]["workspace_id"],
            "goal": "Inspect the requested fixture; target is ambiguous", "operation_intent": "inspect",
        })).result)
        try:
            result = await asyncio.wait_for(call("device_file_task_status", {"task_id": started["task_id"]}), 3)
            body = json.loads(result.result)
            assert (body["status"], body["code"]) == ("paused", "target_needs_clarification")
            assert body["task_id"] == started["task_id"]
            assert body["changes"] == body["checks"] == []
            assert body["progress"]["actions_completed"] == 0
            assert "command" not in (await client.post("/device-file-agent/poll", json=file_device)).json()
        finally:
            await service.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("intent,action,evidence", [
    ("read", {"op": "read", "path": "fixture.html"}, {"content": "synthetic title", "sha256": "a" * 64}),
    ("search", {"op": "find", "query": "missing.html"}, {"files": [], "truncated": False}),
    ("inspect", {"op": "stat", "path": "fixture"}, {"type": "directory", "size": 0}),
])
async def test_readonly_task_succeeds_from_corresponding_evidence(file_device, monkeypatch, intent, action, evidence):
    """A read, metadata result, or confirmed empty search must not fail for zero writes."""
    plans = iter([
        '__TOOL_CALL__:' + json.dumps([{"name": "propose_file_action", "arguments": action}]),
        '```json\n{"done":true}\n```',
    ])

    async def model(*args, **kwargs):
        return next(plans)

    monkeypatch.setattr(llm_client, "chat", model)
    file_device["operations"] = ["stat"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": "Bearer fixture"}) as client:
        await client.post("/device-file-agent/poll", json=file_device)
        start = json.loads((await call("device_file_task_start", {
            "device_id": file_device["device_id"], "workspace_id": file_device["grants"][0]["workspace_id"],
            "goal": "Inspect the synthetic fixture", "operation_intent": intent,
        })).result)
        assert start["request_status"] == "accepted"
        from core.memory import action_trace
        started_trace = next(row for row in action_trace.query("owner", "fixture_char")
                             if row["tool"] == "device_file_task_start")
        assert started_trace["display_activity"]["status"] == "running"
        assert started_trace["status"] == "pending"
        pending = asyncio.create_task(call("device_file_task_status", {"task_id": start["task_id"]}))
        try:
            for _ in range(100):
                reply = (await client.post("/device-file-agent/poll", json=file_device)).json()
                if command := reply.get("command"):
                    break
                await asyncio.sleep(.01)
            else:
                pytest.fail("device command unavailable")
            assert command["action"]["op"] == action["op"]
            assert command["operation_intent"] == intent
            assert (await client.post("/device-file-agent/result", json={
                "ref": command["ref"], "code": "ok", "body": evidence,
            })).status_code == 200
            result = await asyncio.wait_for(pending, 3)
            body = json.loads(result.result)
            assert result.status == "tool_executed"
            assert body["task_status"] == "succeeded"
            assert body["operation_intent"] == intent
            assert body["completion_reason"] == "device_evidence_received"
            assert body["changes"] == body["checks"] == []
            assert body["evidence"] == evidence
            assert body["evidence_summary"]["writes"] == 0
            # Completion may precede the next tool call. Evidence must remain available,
            # then disappear immediately when its grant is revoked.
            later = json.loads((await call("device_file_task_status", {"task_id": start["task_id"]})).result)
            assert later["evidence"] == evidence
            await client.post("/device-file-agent/poll", json={**file_device, "grants": []})
            revoked = json.loads((await call("device_file_task_status", {"task_id": start["task_id"]})).result)
            assert "evidence" not in revoked
        finally:
            await service.shutdown()
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("response,expected", [
    ('{"action":{"op":"create","path":"fixture.html","after":"unsafe"}}', "operation_intent_violation"),
    ('{"done":true}', "read_evidence_required"),
    ('', "invalid_file_plan"),
    ('{"done":true,"action":{"op":"read"}}', "invalid_file_plan"),
    ('{"done":true,"done":true}', "invalid_file_plan"),
    ('__TOOL_CALL__:[{"name":"finish_file_plan","arguments":{}},{"name":"finish_file_plan","arguments":{}}]',
     "invalid_file_plan"),
])
async def test_invalid_read_plans_fail_without_device_side_effects(file_device, monkeypatch, response, expected):
    """Read intent cannot escalate into writing, claim evidence-free completion, or loop forever."""
    async def model(*args, **kwargs):
        return response

    monkeypatch.setattr(llm_client, "chat", model)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": "Bearer fixture"}) as client:
        await client.post("/device-file-agent/poll", json=file_device)
        started = json.loads((await call("device_file_task_start", {
            "device_id": file_device["device_id"], "workspace_id": file_device["grants"][0]["workspace_id"],
            "goal": "Read fixture", "operation_intent": "read",
        })).result)
        try:
            for _ in range(100):
                reply = (await client.post("/device-file-agent/poll", json=file_device)).json()
                assert "command" not in reply, "read-only task dispatched a forbidden device action"
                if started["task_id"] not in service._runs:
                    break
                await asyncio.sleep(.01)
            else:
                pytest.fail("planner did not terminate within its bounded repair budget")
        finally:
            await service.shutdown()
        result = await call("device_file_task_status", {"task_id": started["task_id"]})
        body = json.loads(result.result)
        assert result.status == "tool_failed"
        assert body["error_code"] == expected
        assert body["request_status"] != "completed"
        assert body["progress"]["actions_completed"] == 0
        assert "command" not in (await client.post("/device-file-agent/poll", json=file_device)).json()
        audit = tool_audit.query("owner", "fixture_char", tool="device_file_task_start")[0]
        assert audit["status"] == "pending"
