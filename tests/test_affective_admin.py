import asyncio
import time

import pytest
import yaml

from core.memory import mood_state as ms
from tests.test_affective_engine import proposal, commit


def test_summary_omits_evidence_while_memory_endpoint_exposes_bounded_events(sandbox, monkeypatch):
    from admin.routers import mood
    monkeypatch.setattr(ms, "enabled", lambda: True)
    monkeypatch.setattr(mood, "resolve_requested_char_id", lambda char_id, **k: char_id)
    commit("turn-a", time.time()-1, proposal(anger=.8, concern=.7, strategy="suppress"), "happy")
    summary = asyncio.run(mood.get_mood_state(char_id="companion", auth=None))
    assert "affect_events" not in summary and "receipts" not in summary
    assert summary["current"] == "happy" and summary["affect"]["emotions"]["anger"] > .7
    assert summary["affect_prompt_eligible"]
    events = asyncio.run(mood.get_affect_events(char_id="companion", limit=1, auth=None))
    assert events["items"][0]["source_turn_id"] == "turn-a" and events["read_only"]


def test_flag_hot_reload_and_expression_only_fallback(tmp_path, sandbox, monkeypatch):
    from admin.routers import settings_feature_flags as flags
    from core import config_loader
    path = tmp_path / "config.yaml"
    path.write_text("{}", encoding="utf-8")
    read = lambda: yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    monkeypatch.setattr(flags, "CONFIG_FILE", path)
    monkeypatch.setattr(flags, "get_config", read)
    monkeypatch.setattr(config_loader, "get_config", read)
    monkeypatch.setattr("core.qq_service.get_config", read)
    monkeypatch.setattr(config_loader, "reload_config", lambda: None)
    monkeypatch.setattr("core.perception.screen_observation.state", lambda: {"enabled": False, "active_device": None})
    state = asyncio.run(flags.get_feature_flags(auth=None))["flags"]["affective_engine"]
    assert state["enabled"] and state["effective_state"] == "enabled"
    result = asyncio.run(flags.update_feature_flags(flags.FeatureFlagsUpdate(flags={"affective_engine": False}), auth=None))
    assert result["flags"]["affective_engine"]["effective_state"] == "expression-only"
    assert not ms.enabled()


def test_event_evidence_requires_memory_scope(sandbox, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin.routers.mood import router
    from admin import auth
    from tests.fixtures.public_assets import TEST_CHAR_ID
    token = auth.TokenInfo(label='fixture-state-reader', scopes=frozenset({'state.read'}), profile=None)
    monkeypatch.setattr(auth, 'resolve_token', lambda raw: token)
    app = FastAPI()
    app.include_router(router, prefix='/mood')
    client = TestClient(app)
    headers = {'Authorization':'Bearer fixture-state-token'}
    assert client.get('/mood/state', params={'char_id':TEST_CHAR_ID}, headers=headers).status_code == 200
    assert client.get('/mood/events', params={'char_id':TEST_CHAR_ID}, headers=headers).status_code == 403


@pytest.mark.asyncio
async def test_avatar_push_is_expression_only_and_scoped(monkeypatch):
    from core.pipeline import _maybe_push_avatar_directive
    from core import pipeline_registry
    from types import SimpleNamespace
    actions = []
    async def push(action):
        actions.append(action)
    monkeypatch.setattr("core.tool_dispatcher._push_desktop_action", push)
    monkeypatch.setattr(pipeline_registry, "get", lambda: SimpleNamespace(_active_character_id="companion"))
    state = {"current": "happy", "intensity": .6, "has_signal": True, "source_turn_id": "turn-a",
             "source_at": time.time(), "expires_at": time.time()+1800, "version": 12,
             "affect": {"dominant": "anger"}}
    await _maybe_push_avatar_directive(state, "other")
    assert actions == []
    await _maybe_push_avatar_directive(state, "companion")
    action = actions[0]
    assert action["expression"] == "happy" and action["char_id"] == "companion"
    assert action["version"] == 12 and action["source_turn_id"] == "turn-a"
    assert action["expires_at"] - time.time() <= 6
