import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.data_paths import DEFAULT_CHAR_ID


UID = "rpg_api_user"


def _proposal(request_id: str):
    outcomes = ("critical_failure", "failure", "success_with_cost", "success", "critical_success")
    return {"request_id": request_id, "decision": "automatic_success", "check_type": "move", "reason_code": "obvious", "outcome_branches": {"success": {"projections": {"public": [{"fact_id": "door", "value": "open"}], "player": [], "character": [], "kp_private": []}}}}


def _app(monkeypatch):
    from admin.routers import dream, dream_rpg
    from core.config_loader import get_config
    from core.dream.dream_pipeline import enter_dream

    cfg = get_config()
    monkeypatch.setattr(dream_rpg, "_uid", lambda: UID)
    monkeypatch.setattr('admin.routers.dream_common._owner_uid', lambda: UID)
    result = asyncio.run(enter_dream(UID, char_id=DEFAULT_CHAR_ID, dream_mode="rpg", script_id="prison_demo"))
    assert result["ok"]

    async def fake_chat(messages, **kwargs):
        if kwargs.get("call_category") == "rpg_kp":
            return json.dumps(_proposal("server"))
        return "角色回应"

    monkeypatch.setattr("core.llm_client.chat", fake_chat)
    app = FastAPI()
    app.include_router(dream.router)
    app.include_router(dream_rpg.router)
    for route in [*dream.router.routes, *dream_rpg.router.routes]:
        for dependency in route.dependant.dependencies:
            if hasattr(dependency.call, "_required_scopes"):
                app.dependency_overrides[dependency.call] = lambda: True
    return app, result["dream_id"]


def test_rpg_turn_lanes_idempotency_and_archive(sandbox, monkeypatch):
    app, dream_id = _app(monkeypatch)
    with TestClient(app) as client:
        first = client.post("/dream/rpg/turn", json={"dream_id": dream_id, "request_id": "turn1", "lane": "character", "message": "open the door", "expected_scene_revision": 0})
        assert first.status_code == 200, first.text
        payload = first.json()
        assert payload["status"] == "completed"
        replay = client.post("/dream/rpg/turn", json={"dream_id": dream_id, "request_id": "turn1", "lane": "character", "message": "open the door", "expected_scene_revision": 0})
        assert replay.status_code == 200
        secret = client.post("/dream/rpg/turn", json={"dream_id": dream_id, "request_id": "turn2", "lane": "kp", "message": "secret check", "expected_scene_revision": 1})
        assert secret.status_code == 200
        transcript = client.get("/dream/rpg/transcript", params={"dream_id": dream_id})
        assert transcript.status_code == 200
        assert any(row["lane"] == "kp" for row in transcript.json()["items"])
        exited = client.post("/dream/exit")
        assert exited.status_code == 200
        replay = client.get(f"/dream/archive/{dream_id}")
        assert replay.status_code == 200
        assert replay.json()["metadata"]["dream_mode"] == "rpg"
