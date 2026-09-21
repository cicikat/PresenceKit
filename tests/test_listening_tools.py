"""Ticket 260 F: listening tools, music_playback stimulus, feedback-loop suppress."""
from __future__ import annotations

import json

import pytest

from core import tool_dispatcher
from core.audio_music_contract import (
    FEEDBACK_LOOP_COOLDOWN_S,
    PLANNED_TOOLS,
    SESSION_TALK_BUDGET,
)
from core.autonomy.policy import tool_eligibility
from core.listening_store import list_history, load_session, upsert_track
from core.player_adapter import FakePlayerHost
from tests.fixtures.public_assets import TEST_CHAR_ID


_UID = "owner"
_CHAR = TEST_CHAR_ID


class _Session:
    WAITING_CONFIRM = "waiting_confirm"
    IDLE = "idle"
    status = IDLE

    def set_waiting_confirm(self, tool_name, tool_args):
        self.status = self.WAITING_CONFIRM


def _payload(result: str | None) -> dict:
    text = result or ""
    marker = "结果："
    if marker in text:
        text = text.split(marker, 1)[1]
    return json.loads(text)


def _enable_control(monkeypatch):
    monkeypatch.setattr("core.player_adapter.music_control_enabled", lambda: True)
    monkeypatch.setattr("core.listening_tools.music_control_enabled", lambda: True)


def _enable_autonomy(monkeypatch):
    from core.autonomy import store

    monkeypatch.setattr("core.music_playback_stimulus.music_autonomy_enabled", lambda: True)
    monkeypatch.setattr("core.self_management.policy.autonomy_enabled", lambda *_args: True)
    state = store.load(_UID, _CHAR)
    state["config"]["enabled"] = True
    store.save(_UID, _CHAR, state)


def _allow_dream(monkeypatch):
    from core.dream import dream_state as ds

    class _Status:
        ALLOW = "allow"

    monkeypatch.setattr(ds, "get_reality_guard_status", lambda uid: _Status.ALLOW)
    monkeypatch.setattr(ds, "DreamGuardStatus", _Status)


def _block_dream(monkeypatch):
    from core.dream import dream_state as ds

    class _Status:
        ALLOW = "allow"
        BLOCK_ACTIVE = "block_active"
        BLOCK_UNCERTAIN = "block_uncertain"

    monkeypatch.setattr(ds, "get_reality_guard_status", lambda uid: _Status.BLOCK_ACTIVE)
    monkeypatch.setattr(ds, "DreamGuardStatus", _Status)


@pytest.fixture(autouse=True)
def _clear_perceive():
    from core.perceive_event import clear_dedup_registry_for_test

    clear_dedup_registry_for_test()
    yield
    clear_dedup_registry_for_test()


def test_music_control_keeps_listening_tools_off_by_default():
    for name in PLANNED_TOOLS:
        assert tool_dispatcher._is_tool_enabled(name) is False


@pytest.mark.asyncio
async def test_tools_are_discoverable_and_frozen_principal(sandbox, monkeypatch):
    _enable_control(monkeypatch)
    monkeypatch.setattr(tool_dispatcher, "_current_mode", lambda: "safe")
    schemas = tool_dispatcher.get_tools_schema(categories=["info"])
    names = {(item.get("function") or {}).get("name") for item in schemas}
    for name in PLANNED_TOOLS:
        spec = tool_dispatcher._TOOL_REGISTRY[name]
        assert spec["category"] == "info"
        assert spec["dangerous"] is False
        assert spec["examples"]
        assert spec["keywords"]
        assert name in names
        eligible, reason = tool_eligibility(
            name, {"enabled": True}, registry=tool_dispatcher._TOOL_REGISTRY,
            effect=spec["effect"],
        )
        assert eligible is True, (name, reason)

    upsert_track(_UID, provider="local", source_id="a", title="One", duration_s=40)
    injected = await tool_dispatcher.execute_structured(
        "get_listening_state",
        {"user_id": "other", "char_id": "other-char"},
        _UID, _UID, False, _Session(),
        origin="user_live", char_id=_CHAR,
    )
    assert "grant_principal_mismatch" in (injected.result or "")


@pytest.mark.asyncio
async def test_write_note_and_choose_next_need_session(sandbox, monkeypatch):
    _enable_control(monkeypatch)
    monkeypatch.setattr(tool_dispatcher, "_current_mode", lambda: "safe")
    track = upsert_track(_UID, provider="local", source_id="a", title="One")
    written = await tool_dispatcher.execute_structured(
        "write_track_note",
        {"track_id": track["track_id"], "body": "第一句"},
        _UID, _UID, False, _Session(),
        origin="user_live", char_id=_CHAR,
    )
    note = _payload(written.result)
    assert note["ok"] is True
    conflict = await tool_dispatcher.execute_structured(
        "write_track_note",
        {"track_id": track["track_id"], "body": "覆盖", "expected_revision": 0},
        _UID, _UID, False, _Session(),
        origin="user_live", char_id=_CHAR,
    )
    assert _payload(conflict.result)["reason"] == "revision_conflict"
    refused = await tool_dispatcher.execute_structured(
        "choose_next_track",
        {"track_id": track["track_id"], "expected_revision": 0},
        _UID, _UID, False, _Session(),
        origin="user_live", char_id=_CHAR,
    )
    assert _payload(refused.result)["reason"] == "no_session"
    assert _payload(refused.result).get("started_sound") is not True


@pytest.mark.asyncio
async def test_choose_next_mutates_queue_without_starting_sound(sandbox, monkeypatch):
    _enable_control(monkeypatch)
    monkeypatch.setattr(tool_dispatcher, "_current_mode", lambda: "safe")
    one = upsert_track(_UID, provider="local", source_id="a", title="One")
    two = upsert_track(_UID, provider="local", source_id="b", title="Two")
    host = FakePlayerHost(_UID)
    host.play(one["track_id"])
    session = load_session(_UID)
    chosen = await tool_dispatcher.execute_structured(
        "choose_next_track",
        {"track_id": two["track_id"], "expected_revision": session["revision"]},
        _UID, _UID, False, _Session(),
        origin="assistant_loop", char_id=_CHAR,
    )
    body = _payload(chosen.result)
    assert body["ok"] is True
    assert body["started_sound"] is False
    queued = load_session(_UID)["queue"]
    assert queued[0] == one["track_id"]
    assert queued[1] == two["track_id"]
    stale = await tool_dispatcher.execute_structured(
        "choose_next_track",
        {"track_id": two["track_id"], "expected_revision": session["revision"]},
        _UID, _UID, False, _Session(),
        origin="assistant_loop", char_id=_CHAR,
    )
    assert _payload(stale.result)["reason"] == "revision_conflict"


@pytest.mark.asyncio
async def test_dream_block_does_not_roll_back_ledger(sandbox, monkeypatch):
    _enable_control(monkeypatch)
    _enable_autonomy(monkeypatch)
    _block_dream(monkeypatch)
    track = upsert_track(_UID, provider="local", source_id="a", title="One", duration_s=40)
    host = FakePlayerHost(_UID)
    from core.listening_store import set_participant
    set_participant(_UID, _CHAR)
    result = await host.commit("started", track_id=track["track_id"])
    assert result["ok"] is True
    assert result["stimulus"]["queued"] is False
    assert result["stimulus"]["reason"] == "blocked_dream"
    assert result["stimulus"]["ledger_committed"] is True
    assert load_session(_UID)["state"] == "playing"
    assert list_history(_UID)[0]["track_id"] == track["track_id"]
    from core.autonomy import store
    assert store.load(_UID, _CHAR)["pending_signals"] == []


@pytest.mark.asyncio
async def test_choose_next_causation_is_cooled_for_600s(sandbox, monkeypatch):
    _enable_control(monkeypatch)
    _enable_autonomy(monkeypatch)
    _allow_dream(monkeypatch)
    monkeypatch.setattr(tool_dispatcher, "_current_mode", lambda: "safe")
    one = upsert_track(_UID, provider="local", source_id="a", title="One")
    two = upsert_track(_UID, provider="local", source_id="b", title="Two")
    host = FakePlayerHost(_UID)
    host.play(one["track_id"])
    session = load_session(_UID)
    chosen = await tool_dispatcher.execute_structured(
        "choose_next_track",
        {"track_id": two["track_id"], "expected_revision": session["revision"]},
        _UID, _UID, False, _Session(),
        origin="assistant_loop", char_id=_CHAR,
    )
    command_id = _payload(chosen.result)["command_id"]
    first = await host.commit(
        "changed", track_id=two["track_id"], causation_command_id=command_id,
    )
    assert first["ok"] is True
    assert first["stimulus"]["queued"] is False
    assert first["stimulus"]["reason"] == "choose_next_feedback"
    assert first["stimulus"]["ledger_committed"] is True
    later = load_session(_UID)["music_feedback"]["choose_cooldown_until"]
    assert later >= FEEDBACK_LOOP_COOLDOWN_S
    second = await host.commit("finished", causation_command_id=command_id)
    assert second["stimulus"]["reason"] == "choose_next_cooldown"
    from core.autonomy import store
    assert store.load(_UID, _CHAR)["pending_signals"] == []


@pytest.mark.asyncio
async def test_session_talk_budget_and_session_end_clear_feedback(sandbox, monkeypatch):
    from core.autonomy import store
    from core.player_adapter import register_host

    _enable_control(monkeypatch)
    _enable_autonomy(monkeypatch)
    _allow_dream(monkeypatch)
    track = upsert_track(_UID, provider="local", source_id="a", title="One", duration_s=40)
    host = FakePlayerHost(_UID)
    from core.listening_store import set_participant
    set_participant(_UID, _CHAR)
    started = await host.commit("started", track_id=track["track_id"])
    assert started["stimulus"]["queued"] is True
    host.progress(2, position_s=2)
    finished = await host.commit("finished")
    assert finished["stimulus"]["queued"] is True
    third = await host.commit("started", track_id=track["track_id"])
    assert third["ok"] is True
    assert third["stimulus"]["reason"] == "session_talk_budget"
    assert int(load_session(_UID)["music_feedback"]["session_talks"]) == SESSION_TALK_BUDGET
    assert len(store.load(_UID, _CHAR)["pending_signals"]) == 2
    register_host(_UID, host_id="new-host")
    assert load_session(_UID)["music_feedback"] == {}
    assert load_session(_UID)["choose_command_ids"] == []


@pytest.mark.asyncio
async def test_music_autonomy_off_and_revoked_control_keep_ledger_path(sandbox, monkeypatch):
    _enable_control(monkeypatch)
    _allow_dream(monkeypatch)
    track = upsert_track(_UID, provider="local", source_id="a", title="One")
    host = FakePlayerHost(_UID)
    from core.listening_store import set_participant
    set_participant(_UID, _CHAR)
    silent = await host.commit("started", track_id=track["track_id"])
    assert silent["ok"] is True
    assert silent["stimulus"]["reason"] == "music_autonomy_disabled"
    assert load_session(_UID)["state"] == "playing"
    monkeypatch.setattr("core.player_adapter.music_control_enabled", lambda: False)
    from core.player_adapter import ingest_host_event
    rejected = ingest_host_event(_UID, {
        "event_id": "after-revoke",
        "kind": "progress",
        "generation": load_session(_UID)["generation"],
        "session_id": load_session(_UID)["session_id"],
        "sequence": 99,
        "played_delta_s": 4,
    })
    assert rejected["reason"] == "music_control_disabled"
