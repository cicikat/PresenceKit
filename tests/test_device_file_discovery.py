"""Atomic tools use the real dispatcher and authenticated device transport."""
import asyncio
import json

import httpx
import pytest

from admin.admin_server import app
from core import llm_client, tool_audit
from core.agent_runtime import device_file_agent as service
from tests.test_tool_incident_312 import file_device, call


@pytest.mark.asyncio
@pytest.mark.parametrize("name,op,evidence", [
    ("device_file_list_dir", "list_dir", {"entries": [], "truncated": False}),
    ("device_file_stat", "stat", {"type": "directory", "size": 0}),
    ("device_file_find_name", "find_name", {"entries": [{"relative_path": "fixture/config.json"}], "truncated": True}),
    ("device_file_search_text", "search_text", {"entries": [{"relative_path": "fixture/a.tsx", "line": 1,
                                                            "short_excerpt": "private excerpt"}], "truncated": False}),
    ("device_file_read", "read_page", {"content": "private excerpt", "sha256": "a" * 64,
                                      "start": 1, "next": 2, "has_more": False}),
])
async def test_atomic_read_uses_device_evidence_without_model_or_write(file_device, monkeypatch, name, op, evidence):
    """Empty results and a zero-write read succeed; excerpts never enter durable audit."""
    async def forbidden(*args, **kwargs):
        pytest.fail("atomic inspection called a planner")
    monkeypatch.setattr(llm_client, "chat", forbidden)
    file_device["operations"] = sorted(service.ATOMIC_READS)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": "Bearer fixture"}) as client:
        assert (await client.post("/device-file-agent/poll", json=file_device)).json()["online"]
        pending = asyncio.create_task(call(name, {
            "device_id": file_device["device_id"], "workspace_id": file_device["grants"][0]["workspace_id"],
            "path": "fixture", "query": "needle", "options": '{"content_search":true}',
        }))
        try:
            for _ in range(100):
                reply = (await client.post("/device-file-agent/poll", json=file_device)).json()
                if command := reply.get("command"):
                    break
                await asyncio.sleep(.01)
            else:
                pytest.fail("no device command")
            assert command["action"]["op"] == op
            assert (await client.post("/device-file-agent/result", json={
                "ref": command["ref"], "code": "ok", "body": evidence,
            })).status_code == 200
            result = await asyncio.wait_for(pending, 3)
            payload = json.loads(result.result)
            assert result.status == "tool_executed"
            assert payload["task_status"] == "succeeded"
            assert payload["changes"] == []
            assert payload["evidence"] == evidence
            persisted = service._path(service.principal(), payload["task_id"]).read_text(encoding="utf-8")
            assert "private excerpt" not in persisted
            assert "private excerpt" not in json.dumps(tool_audit.query("owner", "fixture_char"))
        finally:
            await service.shutdown()
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_old_desktop_rejects_new_operation_before_dispatch(file_device):
    """Legacy hello omits operations: fail explicitly instead of queueing a timeout."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": "Bearer fixture"}) as client:
        await client.post("/device-file-agent/poll", json=file_device)
        result = await call("device_file_list_dir", {
            "device_id": file_device["device_id"], "workspace_id": file_device["grants"][0]["workspace_id"],
            "path": "", "query": "", "options": "{}",
        })
        assert result.status == "tool_failed"
        assert json.loads(result.result)["code"] == "unsupported_operation"
        assert not service._runs


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["malformed", "revoked", "cancelled", "epoch"])
async def test_atomic_read_rejects_bad_or_stale_evidence(file_device, boundary):
    """A successful HTTP receipt cannot expose data after cancellation or grant/epoch changes."""
    file_device["operations"] = ["list_dir"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": "Bearer fixture"}) as client:
        await client.post("/device-file-agent/poll", json=file_device)
        pending = asyncio.create_task(call("device_file_list_dir", {
            "device_id": file_device["device_id"], "workspace_id": file_device["grants"][0]["workspace_id"],
            "path": "", "query": "", "options": "{}",
        }))
        try:
            for _ in range(100):
                reply = (await client.post("/device-file-agent/poll", json=file_device)).json()
                if command := reply.get("command"):
                    break
                await asyncio.sleep(.01)
            else:
                pytest.fail("no device command")
            if boundary == "revoked":
                await client.post("/device-file-agent/poll", json={**file_device, "grants": []})
            elif boundary == "epoch":
                from uuid import uuid4
                await client.post("/device-file-agent/poll", json={**file_device, "device_epoch": uuid4().hex})
            elif boundary == "cancelled":
                await call("device_file_task_cancel", {"task_id": command["ref"]["task_id"]})
            reply = await client.post("/device-file-agent/result", json={
                "ref": command["ref"], "code": "ok", "body": {},
            })
            if boundary == "malformed":
                result = await asyncio.wait_for(pending, 3)
                assert result.status == "tool_failed"
                assert json.loads(result.result)["code"] == "protocol_mismatch"
            else:
                assert reply.status_code == 409
                result = await asyncio.wait_for(pending, 3)
                assert result.status == "tool_failed"
            assert "evidence" not in json.loads(result.result)
        finally:
            await service.shutdown()
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
