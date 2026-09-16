import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.tools import drinking
from core.sandbox import get_paths
from tests.fixtures.public_assets import TEST_CHAR_ID


@pytest.fixture(autouse=True)
def policy(monkeypatch, sandbox):
    monkeypatch.setattr("core.character_loader.load", lambda _: SimpleNamespace(presence_ext={}))
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"scheduler": {"owner_id": "owner"}})


@pytest.mark.asyncio
async def test_invitation_scope_and_timeout_child_task():
    for text, uid, group, proactive in [("你好", "owner", False, False),
                                      ("干杯", "guest", False, False),
                                      ("干杯", "owner", True, False),
                                      ("干杯", "owner", False, True)]:
        assert not drinking.bind_turn(text, uid=uid, char_id=TEST_CHAR_ID, is_group=group, is_proactive=proactive)
        assert "不执行" in await drinking.drink_with_user("sip", user_id=uid, char_id=TEST_CHAR_ID)
    assert drinking.bind_turn("一起喝酒", uid="owner", char_id=TEST_CHAR_ID)
    result = await asyncio.wait_for(drinking.drink_with_user("sip", user_id="owner", char_id=TEST_CHAR_ID), 1)
    assert "动作完成" in result
    drinking.end_turn()
    assert "不执行" in await drinking.drink_with_user("sip", user_id="owner", char_id=TEST_CHAR_ID)


@pytest.mark.asyncio
async def test_refusal_policy_and_intensity(monkeypatch):
    drinking.bind_turn("干杯", uid="owner", char_id=TEST_CHAR_ID)
    call = lambda action: drinking.drink_with_user(action, user_id="owner", char_id=TEST_CHAR_ID)
    assert "婉拒" in await call("refuse")
    assert drinking.snapshot(TEST_CHAR_ID)["intensity"] == 0
    await call("pour")
    assert drinking.snapshot(TEST_CHAR_ID)["intensity"] == 0
    for _ in range(4):
        await call("sip")
    assert "拒绝续杯" in await call("sip")
    monkeypatch.setattr("core.character_loader.load", lambda _: SimpleNamespace(presence_ext={"drinking": "no"}))
    assert "不喝酒" in await call("sip")
    assert drinking.prompt_hint(TEST_CHAR_ID) is None


def test_decay_and_soft_layer():
    path = get_paths().drinking_state(char_id=TEST_CHAR_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"intensity": 3, "updated_at": 100}), encoding="utf-8")
    assert drinking.snapshot(TEST_CHAR_ID, now=3700)["intensity"] == 2
    assert drinking.snapshot(TEST_CHAR_ID, now=11000)["intensity"] == 0
    assert drinking.prompt_hint(TEST_CHAR_ID) is None
    import time
    path.write_text(json.dumps({"intensity": 2, "updated_at": time.time()}), encoding="utf-8")
    assert drinking.prompt_hint(TEST_CHAR_ID)["_layer"] == "1.6_drinking"


def test_registry_not_probe_and_perform():
    from core.tool_dispatcher import _build_probe_prompt
    assert "drink_with_user" not in _build_probe_prompt(["info"], allowed_tool_names={"drink_with_user"})
    from core.perform_mapper import _map_with_rules
    result = _map_with_rules("撑桌，笑得慢", "好")
    assert result["posture"] == "lean_in"
    assert result["energy"] < 0.5


def test_observation_scope(monkeypatch):
    from admin.routers.observability import router
    from admin.auth import require_scopes
    monkeypatch.setattr("core.scheduler.loop._active_char_id_or_none", lambda: TEST_CHAR_ID)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/observability/drinking").status_code in (401, 403)
    route = next(r for r in app.routes if getattr(r, "path", "") == "/observability/drinking")
    app.dependency_overrides[route.dependant.dependencies[0].call] = lambda: {}
    result = client.get("/observability/drinking")
    assert result.status_code == 200
    assert result.json()["fictional"]
    assert "drink" not in result.json()
