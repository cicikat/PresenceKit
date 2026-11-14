"""Desktop protocol, real dispatcher and same-role work session boundaries."""
import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from admin import auth
from admin.admin_server import app
from core import llm_client, tool_dispatcher
from core.agent_runtime import device_file_agent as service, work_sessions


@pytest.mark.asyncio
async def test_path_c_edit_uses_one_device_and_requires_verified_readback(monkeypatch, sandbox):
    from core.agent_runtime import computer_use

    config = {"device_file_agent": {"enabled": True}, "scheduler": {"owner_id": "owner"},
              "deployment": {"mode": "remote_server"}}
    character = SimpleNamespace(_active_character_id="fixture_char")
    monkeypatch.setattr(service.config_loader, "get_config", lambda: config)
    monkeypatch.setattr(computer_use.pipeline_registry, "get", lambda: character)
    monkeypatch.setattr(service, "_devices", {})
    monkeypatch.setattr(service, "_runs", {})
    desktop = auth.TokenInfo("test-desktop", frozenset({"sensor.write", "ws.desktop", "state.read"}), "desktop")
    mobile = auth.TokenInfo("test-mobile", frozenset({"sensor.write", "state.read"}), "mobile")
    monkeypatch.setattr(auth, "resolve_token", lambda token: mobile if token == "mobile" else desktop)
    actions = iter([
        {"action": {"op": "read", "path": "eye.html"}},
        {"action": {"op": "patch", "path": "eye.html", "expected_sha256": "a" * 64,
                    "before": "offset = y", "after": "offset = -y"}},
        {"action": {"op": "verify", "path": "eye.html", "expected_sha256": "b" * 64}},
        {"done": True},
    ])

    async def model(messages, **kwargs):
        assert kwargs["char_id"] == character._active_character_id
        return json.dumps(next(actions))

    monkeypatch.setattr(llm_client, "chat", model)
    grant = {"workspace_id": uuid4().hex, "revision": uuid4().hex, "alias": "demo", "write": True}
    device = {"protocol": service.VERSION, "device_id": uuid4().hex,
              "device_epoch": uuid4().hex, "state": "enabled", "grants": [grant]}
    poll_url = "/device-file-agent/poll"
    result_url = "/device-file-agent/result"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": "Bearer desktop"}) as client:
        async def tool(name, args, user="owner", char="fixture_char", origin="assistant_loop"):
            outcome = await tool_dispatcher.execute_structured(
                name, args, user, user, False, None, origin=origin, char_id=char,
            )
            return outcome.result

        async def poll_command():
            for _ in range(40):
                response = await client.post(poll_url, json=device)
                if "command" in response.json():
                    return response.json()["command"]
                await asyncio.sleep(.01)
            pytest.fail("desktop command unavailable")

        assert (await client.post(poll_url, json=device, headers={"Authorization": "Bearer mobile"})).status_code == 403
        assert (await client.post(poll_url, content=b"x" * 65537)).status_code == 413
        assert (await client.post(poll_url, json=device)).json()["online"]
        assert (await client.post(poll_url, json=device, headers={"Authorization": "Bearer other"})).json()["online"] is False
        assert "device_file_denied" in await tool("device_workspace_list", {}, user="another")
        assert "device_file_denied" in await tool("device_workspace_list", {}, origin="user_live")
        assert "device_file_denied" in await tool("device_workspace_list", {}, char="another")
        listing = json.loads(await tool("device_workspace_list", {}))
        assert listing["devices"][0]["grants"] == [grant]
        started = json.loads(await tool("device_file_task_start", {
            "device_id": device["device_id"], "workspace_id": grant["workspace_id"], "goal": "Fix eye movement",
        }))
        assert started["status"] == "queued"
        for operation, body in [
            ("read", {"content": "offset = y", "sha256": "a" * 64}),
            ("patch", {"saved": True, "sha256": "b" * 64, "version_id": uuid4().hex, "diff": "private marker"}),
            ("verify", {"verified": True, "sha256": "b" * 64, "syntax_checked": "unknown"}),
        ]:
            command = await poll_command()
            assert command["action"]["op"] == operation
            assert "command" not in (await client.post(poll_url, json=device)).json()
            payload = {"ref": command["ref"], "code": "ok", "body": body}
            assert (await client.post(result_url, json=payload, headers={"Authorization": "Bearer other"})).status_code == 409
            assert (await client.post(result_url, json=payload)).status_code == 200
            assert (await client.post(result_url, json=payload)).status_code == 200
        await asyncio.gather(*tuple(service._workers))
        status = json.loads(await tool("device_file_task_status", {"task_id": started["task_id"]}))
        assert status["status"] == "succeeded"
        assert len(status["changes"]) == 1
        assert status["checks"][0]["visual_checked"] == "unknown"
        assert work_sessions.list_work_sessions(service.principal())[0]["status"] == "succeeded"
        observed = await client.get("/observability/device-file-agent")
        persisted = service._path(service.principal(), started["task_id"]).read_text(encoding="utf-8")
        for private in ("private marker", "eye.html", "offset = y"):
            assert private not in observed.text
            assert private not in persisted
        assert grant["workspace_id"] not in observed.text
