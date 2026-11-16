import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient


_UID = "rpg_foundation_user"


def _enter():
    from core.data_paths import DEFAULT_CHAR_ID
    from core.dream.dream_pipeline import enter_dream
    return asyncio.run(enter_dream(_UID, char_id=DEFAULT_CHAR_ID, dream_mode="rpg", script_id="prison_demo"))


def test_rpg_requires_existing_script_and_writes_only_session_core(sandbox):
    from core.dream.dream_pipeline import enter_dream
    from core.data_paths import DEFAULT_CHAR_ID

    missing = asyncio.run(enter_dream(_UID, char_id=DEFAULT_CHAR_ID, dream_mode="rpg"))
    assert missing["ok"] is False
    assert "requires script_id" in missing["error"]

    result = _enter()
    assert result == {"ok": True, "dream_id": result["dream_id"], "dream_mode": "rpg", "script_id": "prison_demo"}
    path = sandbox.dream_rpg_session_path(_UID, result["dream_id"])
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["script_id"] == "prison_demo"
    assert "prompt" not in payload
    assert not (path.parent / "events.jsonl").exists()
    assert not (path.parent / "dice.jsonl").exists()
    assert not (path.parent / "transcript.jsonl").exists()


def test_rpg_blocks_mode_and_script_switch_and_ordinary_turn(sandbox):
    from core.data_paths import DEFAULT_CHAR_ID
    from core.dream.dream_pipeline import dream_turn, enter_dream

    _enter()
    changed_mode = asyncio.run(enter_dream(_UID, char_id=DEFAULT_CHAR_ID, dream_mode="sandbox"))
    assert changed_mode["ok"] is False and "cannot switch" in changed_mode["error"]
    changed_script = asyncio.run(enter_dream(_UID, char_id=DEFAULT_CHAR_ID, dream_mode="rpg", script_id="other_script"))
    assert changed_script["ok"] is False and "cannot replace" in changed_script["error"]
    assert asyncio.run(dream_turn(_UID, "advance"))["error"] == "RPG_ENDPOINT_REQUIRED"


def test_rpg_chat_endpoint_returns_structured_409(sandbox, monkeypatch):
    from admin.routers.dream import router
    from core.config_loader import get_config

    result = _enter()
    cfg = get_config()
    monkeypatch.setattr('core.config_loader.get_config', lambda: {**cfg, "scheduler": {**cfg.get("scheduler", {}), "owner_id": _UID}})
    app = FastAPI()
    app.include_router(router)
    for route in router.routes:
        for dependency in route.dependant.dependencies:
            if hasattr(dependency.call, "_required_scopes"):
                app.dependency_overrides[dependency.call] = lambda: True
    response = TestClient(app).post("/dream/chat", json={"message": "advance"})
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "RPG_ENDPOINT_REQUIRED",
        "message": "RPG Dream input must use the RPG round endpoint.",
        "retryable": False,
    }


def test_rpg_exit_is_llm_free_and_has_no_afterglow(sandbox, monkeypatch):
    from core.dream.dream_pipeline import force_exit_dream
    from core.dream.dream_state import read_state

    result = _enter()
    monkeypatch.setattr("core.dream.dream_pipeline._generate_summary_bg", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")))
    closed = asyncio.run(force_exit_dream(_UID))
    assert closed["closed_now"] is True
    assert closed["dream_mode"] == "rpg"
    state = read_state(_UID)
    assert state["status"] == "REALITY_CHAT"
    assert state["last_dream_id"] == result["dream_id"]
    assert state["forced_impression_rounds_left"] == 0


def test_rpg_corrupt_core_is_safe_uncertain_projection(sandbox, monkeypatch):
    from admin.routers import dream_rpg

    result = _enter()
    path = sandbox.dream_rpg_session_path(_UID, result["dream_id"])
    path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(dream_rpg, "_uid", lambda: _UID)
    projection = dream_rpg._current()
    assert projection["status"] == "uncertain"
    assert projection["last_error_code"] == "RPG_SESSION_INVALID"


def test_rpg_capabilities_match_mode_enum():
    from core.dream.dream_state import DreamMode
    from admin.routers.dream_rpg import DreamCapabilitiesResponse, RpgCapability

    response = DreamCapabilitiesResponse(supported_modes=[item.value for item in DreamMode], rpg=RpgCapability())
    assert response.supported_modes == ["sandbox", "scenario", "mirror", "rpg"]
