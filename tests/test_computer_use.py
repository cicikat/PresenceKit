"""Desktop leases, same-role task orchestration and no-replay transport."""
from types import SimpleNamespace
from uuid import uuid4
import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from admin import auth
from admin.routers import computer_use as routes
from core import llm_client, tool_dispatcher
from core.agent_runtime import computer_use as service
from core.agent_runtime.computer_use_contract import ActionModel, Poll, Result
from core.pipeline import Pipeline


@pytest.fixture
def fixture(monkeypatch):
    config = {"computer_use": {"enabled": True}, "scheduler": {"owner_id": "owner"},
              "deployment": {"mode": "remote_server"}}
    pipeline = SimpleNamespace(_active_character_id="fixture_char")
    monkeypatch.setattr(service.config_loader, "get_config", lambda: config)
    monkeypatch.setattr(service.pipeline_registry, "get", lambda: pipeline)
    monkeypatch.setattr(service, "_device", None)
    monkeypatch.setattr(service, "_run", None)
    monkeypatch.setattr(service, "_last", {})
    monkeypatch.setattr(service, "_retired", {})
    desktop = auth.TokenInfo("desktop", frozenset({"sensor.write", "ws.desktop", "state.read"}), "desktop")
    mobile = auth.TokenInfo("mobile", frozenset({"sensor.write", "state.read"}), "mobile")
    monkeypatch.setattr(auth, "resolve_token", lambda token: {"desktop": desktop, "other": desktop, "mobile": mobile}.get(token))
    body = {
        "device_id": uuid4().hex, "session_id": uuid4().hex, "char_id": "fixture_char",
        "scope": "window", "state": "ready", "remaining_ms": 60_000, "focus_ready": True,
    }
    return config, pipeline, body


def observation(seq):
    bounds = {"x": -500, "y": 5, "width": 800, "height": 600}
    return {
        "version": seq, "captured_at_ms": 10_000, "jpeg_width": 800, "jpeg_height": 600,
        "surface": {
            "window": {"handle": 10, "pid": 100, "thread": 100, "created": [1, 2]},
            "bounds": bounds, "desktop": bounds, "dpi": 144, "focused": 11,
        },
        "nodes": [{"id": 1, "name": "Test control", "control_type": 1, "bounds": bounds, "focused": True}],
    }


@pytest.mark.asyncio
async def test_remote_task_receipts_are_device_session_sequence_and_nonce_bound(fixture, monkeypatch):
    _, _, body = fixture
    app = FastAPI()
    app.include_router(routes.router)
    actions = iter([
        {"kind": "click", "target": {"kind": "element", "id": 1}},
        {"kind": "type_text", "text": "fixture private input"},
        {"kind": "press_key", "key": "Enter"},
        {"kind": "scroll", "target": {"kind": "element", "id": 1}, "ticks": -1},
        {"kind": "done", "evidence_id": 1},
    ])

    async def plan(*args):
        return next(actions)

    monkeypatch.setattr(service, "_plan", plan)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 headers={"Authorization": "Bearer desktop"}) as http:
        mobile = await http.post("/computer-use/poll", json=body, headers={"Authorization": "Bearer mobile"})
        assert mobile.status_code == 403
        poll = (await http.post("/computer-use/poll", json=body)).json()
        assert poll["accepted"]
        body["server_epoch"] = poll["server_epoch"]
        await http.post("/computer-use/poll", json={**body, "state": "paused"})
        assert service.start("owner", "fixture_char", "paused") == {
            "state": "paused", "reason": "desktop_paused",
        }
        assert (await http.post("/computer-use/poll", json=body)).json()["accepted"]
        assert service.start("other", "fixture_char", "ignored")["state"] == "unavailable"
        task_id = service.start("owner", "fixture_char", "fixture private goal")["task_id"]
        assert service.start("owner", "fixture_char", "another")["state"] == "busy"
        run = service._run
        for seq in range(1, 6):
            for _ in range(20):
                packet = (await http.post("/computer-use/poll", json=body)).json()
                if packet["step"]:
                    break
                await asyncio.sleep(.01)
            assert packet["step"]["seq"] == seq
            assert packet["step"]["frame"] == (seq - 1 if seq > 1 else None)
            replay = (await http.post("/computer-use/poll", json=body)).json()
            assert replay["step"] is None
            body_result = {
                "device_id": body["device_id"], "session_id": body["session_id"], "task_id": task_id,
                "server_epoch": body["server_epoch"], "nonce": packet["nonce"], "seq": seq,
                "receipt": {"status": "changed" if seq > 1 else "ok", "observation": observation(seq)},
            }
            denied = await http.post("/computer-use/result", json=body_result, headers={"Authorization": "Bearer other"})
            assert denied.status_code == 409
            wrong = await http.post("/computer-use/result", json={**body_result, "nonce": uuid4().hex})
            assert wrong.status_code == 409
            late = await http.post("/computer-use/result", json={
                **body_result, "receipt": {"status": "ok", "observation": observation(seq + 1)},
            })
            assert late.status_code == 409
            ok = await http.post("/computer-use/result", json=body_result)
            assert ok.status_code == 200
            duplicate = await http.post("/computer-use/result", json=body_result)
            assert duplicate.status_code == 409
        await asyncio.wait_for(run.worker, timeout=1)
        state = (await http.get("/computer-use/status")).json()
        assert state["state"] == "succeeded"
        assert state["steps"] == 5
        assert state["actions"] == 4
        assert "fixture private" not in json.dumps(state)
        assert "Test control" not in json.dumps(state)
        assert service.task_status("owner", "fixture_char", task_id)["state"] == "succeeded"
        assert service.task_status("owner", "fixture_char", "")["task_id"] == task_id


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["role", "disable", "disconnect", "close", "dream"])
async def test_authorization_changes_end_pending_task_and_reject_replay(fixture, monkeypatch, event):
    config, pipeline, body = fixture
    monkeypatch.setattr(service, "_permitted", lambda actor: (
        service.enabled() and service.principal() == actor and not config.get("fixture_dream")
    ))
    initial = Poll.model_validate(body)
    assert service.poll("trusted", initial)["accepted"]
    task_id = service.start("owner", "fixture_char", "fixture private goal")["task_id"]
    run = service._run
    await asyncio.sleep(0)
    packet = service.poll("trusted", initial)
    assert packet["step"]["seq"] == 1
    if event == "role":
        pipeline._active_character_id = "other_char"
    elif event == "disable":
        config["computer_use"]["enabled"] = False
    elif event == "disconnect":
        service._device.heartbeat -= 60
    elif event == "dream":
        config["fixture_dream"] = True
    else:
        initial = initial.model_copy(update={"state": "closed", "remaining_ms": 0})
    assert service.poll("trusted", initial)["accepted"] is False
    assert not service.accept("trusted", Result.model_validate({
        "device_id": body["device_id"], "session_id": body["session_id"], "task_id": task_id,
        "server_epoch": packet["server_epoch"], "nonce": packet["nonce"], "seq": 1,
        "receipt": {"status": "ok", "observation": observation(1)},
    }))
    await asyncio.gather(run.worker, return_exceptions=True)
    assert service._run is None
    assert not service.poll("trusted", Poll.model_validate(body))["accepted"]


def test_action_contract_disallows_model_supplied_coordinates_and_scope():
    for proposal in (
        {"kind": "click", "target": {"kind": "point", "x": .5, "y": .5}},
        {"kind": "observe", "session_id": "another"},
        {"kind": "run", "command": "test"},
    ):
        with pytest.raises(ValueError):
            ActionModel.validate_python(proposal)


@pytest.mark.asyncio
async def test_owner_probe_exposes_computer_use_and_explains_missing_desktop_grant(fixture, monkeypatch):
    from core.pretool_router import route_pretool
    from core.session_state import SessionState

    monkeypatch.setattr(tool_dispatcher, "get_active_char_name", lambda: "Fixture")
    seen = []

    async def probe(messages, schemas, char_id):
        names = {schema["function"]["name"] for schema in schemas}
        seen.append(names)
        if "computer_use_start" not in names:
            return ""
        assert "临时授权" in messages[0]["content"]
        return '__TOOL_CALL__:[{"name":"computer_use_start","arguments":{"goal":"在记事本里写一句话"}}]'

    monkeypatch.setattr("core.decision_probe.probe", probe)
    for uid, group in (("owner", False), ("owner", True), ("other", False)):
        result = await route_pretool(
            "帮我在记事本里写一句话", uid, "fixture_char", "desktop", uid, group,
            SessionState(), tool_loop_enabled=False, categories=["desktop"],
        )
        if uid == "owner" and not group:
            assert service.TOOL_NAMES <= seen[-1]
            assert result.selected_tool == "computer_use_start"
            assert "no_desktop_authorization" in result.prompt_tool_result
            assert "开启临时授权" in result.prompt_tool_result
            prompt = result.computer_use_prompt()
            assert prompt["_layer"] == "11.7_computer_use"
            assert "本轮工具选择已经结束" in prompt["content"]
        else:
            assert not (service.TOOL_NAMES & set(result.tools_available))
            assert result.computer_use_prompt() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("context", ["owner", "group", "proactive", "filtered", "disabled", "other_role", "dream"])
async def test_tool_loop_offers_scoped_definitions_and_prompt_on_first_turn(fixture, monkeypatch, context):
    from core.session_state import SessionState

    config, _, _ = fixture
    config["prompt_language"] = "en"
    config["self_management"] = {"enabled": False}
    if context == "disabled":
        config["computer_use"]["enabled"] = False
    if context == "dream":
        from core.dream.dream_state import DreamGuardStatus
        monkeypatch.setattr("core.dream.dream_state.get_reality_guard_status", lambda _: DreamGuardStatus.BLOCK_ACTIVE)
    monkeypatch.setattr("core.character_name_provider.get_char_name", lambda *args: "Fixture")
    monkeypatch.setattr("core.model_registry._get_preset_config", lambda: {"presets": {}})
    monkeypatch.setattr("core.model_registry._resolve_preset_name", lambda *args, **kwargs: "legacy")
    seen = []

    async def chat_turn(messages, tools, **kwargs):
        seen.append((messages, tools))
        return llm_client.ChatTurn(content="Acknowledged", tool_calls=[], assistant_message={
            "role": "assistant", "content": "Acknowledged",
        })

    monkeypatch.setattr(llm_client, "chat_turn", chat_turn)
    await Pipeline.__new__(Pipeline).run_agentic_loop(
        [{"role": "user", "content": "Please operate Notepad for me."}],
        uid="owner", char_id="other_char" if context == "other_role" else "fixture_char", session_state=SessionState(),
        is_group=context == "group", is_proactive=context == "proactive",
        allowed_tool_names=frozenset({"computer_use_status"}) if context == "filtered" else service.TOOL_NAMES,
    )
    messages, tools = seen[0]
    functions = {tool["function"]["name"]: tool["function"] for tool in tools}
    notes = [message["content"] for message in messages if message.get("_layer") == "11.7_computer_use"]
    if context == "owner":
        assert service.TOOL_NAMES <= functions.keys()
        assert functions["computer_use_start"]["description"].startswith("Submit an explicit request")
        assert "temporary access" in notes[0]
        assert "Succeeded means the goal was verified" in notes[0]
    elif context == "filtered":
        assert service.TOOL_NAMES & functions.keys() == {"computer_use_status"}
        assert "computer_use_status" in notes[0] and "computer_use_start" not in notes[0]
    else:
        assert not (service.TOOL_NAMES & functions.keys())
        assert not notes


def test_computer_use_cannot_be_exposed_via_autonomy_or_recipe():
    from core.autonomy.policy import tool_eligibility
    from core.self_tool_recipes import RecipeError, check_step_tool
    from core.tool_dispatcher import _TOOL_REGISTRY, get_tool_effect, is_side_effect_tool

    assert is_side_effect_tool("computer_use_start")
    assert is_side_effect_tool("computer_use_cancel")
    assert not is_side_effect_tool("computer_use_status")
    for name in service.TOOL_NAMES:
        assert tool_eligibility(name, {"enabled": True}, registry=_TOOL_REGISTRY,
                                effect=get_tool_effect(name)) == (False, "owner_live_turn_required")
        with pytest.raises(RecipeError, match="step_tool_forbidden"):
            check_step_tool(name, set(service.TOOL_NAMES))
