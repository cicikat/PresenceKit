"""Contracts for Galatea Garden's level-triggered Wake Bridge hint."""

from __future__ import annotations

import asyncio
import json
import time

import pytest


def _run(coro):
    return asyncio.run(coro)


def _garden(**overrides):
    value = {
        "provider": "galatea_garden",
        "reason": "notification_available",
        "message": "你有新的 Garden 通知。请调用 Garden MCP 查看。",
        "received_at": "2026-07-28T12:00:00+00:00",
        "uid": "owner-1",
        "char_id": "char-a",
    }
    value.update(overrides)
    return value


@pytest.fixture
def garden_env(monkeypatch):
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"scheduler": {"owner_id": "owner-1"}})
    monkeypatch.setattr("core.character_context.selected_character_id_or_none", lambda: "char-a")


def _state(sandbox):
    path = sandbox.wake_bridge_state("owner-1", char_id="char-a", provider="galatea_garden")
    return path, json.loads(path.read_text(encoding="utf-8"))


def _record(state):
    assert len(state["events"]) == 1
    return next(iter(state["events"].values()))


def _allow_execute(monkeypatch, *, perceive_status="accepted", sent=True):
    calls = []

    async def fake_execute_prompt(**kwargs):
        calls.append(kwargs)
        kwargs["pipeline_outcome"].update({"perceive_status": perceive_status, "sent": sent})
        from core.scheduler.execution import ExecuteResult
        return ExecuteResult(trigger_name=kwargs["trigger_name"], would_send_prompt="", dry_run=False, sent=sent)

    async def fake_decide(uid, proposals, *, dry_run):
        picked = proposals[0]
        return picked, "picked_highest_urgency", await picked.execute(dry_run=dry_run)

    monkeypatch.setattr("core.scheduler.execution.execute_prompt", fake_execute_prompt)
    monkeypatch.setattr("core.scheduler.gating.decide_and_execute_event", fake_decide)
    return calls


def test_valid_garden_hint_enters_pending_without_inline_llm(garden_env, sandbox, monkeypatch):
    from core.wake_bridge import PENDING, WakeBridge

    calls = _allow_execute(monkeypatch)
    result = _run(WakeBridge().submit_garden_mapping(_garden()))
    assert result.status == PENDING
    assert calls == []
    record = _record(_state(sandbox)[1])
    assert record["kind"] == "garden_hint"
    assert record["status"] == PENDING
    assert record["reason"] == "notification_available"


@pytest.mark.parametrize("payload", [
    _garden(provider="other"),
    _garden(reason=""),
    _garden(reason=" x"),
    _garden(reason="x" * 129),
    _garden(message="   "),
    _garden(message="x" * 4097),
    _garden(version=1),
])
def test_invalid_garden_hints_are_rejected(garden_env, payload):
    from core.wake_bridge import WakeBridge

    assert _run(WakeBridge().submit_garden_mapping(payload)).status == "malformed"


def test_same_pending_reason_coalesces_without_resetting_message_or_attempts(garden_env, sandbox):
    from core.wake_bridge import WakeBridge

    bridge = WakeBridge()
    assert _run(bridge.submit_garden_mapping(_garden(reason="game_turn_required"))).status == "pending"
    path, state = _state(sandbox)
    original = dict(_record(state))
    assert _run(bridge.submit_garden_mapping(_garden(reason="game_turn_required", message="a newer message"))).status == "coalesced"
    record = _record(json.loads(path.read_text(encoding="utf-8")))
    assert record["message"] == original["message"]
    assert record["attempts"] == original["attempts"]
    assert record["expires_at"] == original["expires_at"]
    assert len(_state(sandbox)[1]["events"]) == 1


def test_consumed_reason_can_reenter_pending_but_respects_cooldown(garden_env, sandbox, monkeypatch):
    from core.wake_bridge import CONSUMED, PENDING, WakeBridge

    bridge = WakeBridge()
    calls = _allow_execute(monkeypatch)
    assert _run(bridge.submit_garden_mapping(_garden(reason="game_turn_required"))).status == "pending"
    assert _run(bridge.drain_due())[0].status == "accepted"
    first = _record(_state(sandbox)[1])
    assert first["status"] == CONSUMED
    assert _run(bridge.submit_garden_mapping(_garden(reason="game_turn_required", message="later level wake"))).status == "pending"
    second = _record(_state(sandbox)[1])
    assert second["status"] == PENDING
    assert second["next_attempt_at"] >= first["cooldown_until"]
    assert len(_state(sandbox)[1]["events"]) == 1
    assert _run(bridge.drain_due()) == []
    assert len(calls) == 1


@pytest.mark.parametrize("gate_reason", ["active_window_filtered", "dnd_filtered", "global_gap_filtered", "daily_budget_filtered"])
def test_owner_and_budget_gates_keep_garden_hint_pending(garden_env, sandbox, monkeypatch, gate_reason):
    from core.wake_bridge import PENDING, WakeBridge

    async def reject(uid, proposals, *, dry_run):
        return None, gate_reason, None

    monkeypatch.setattr("core.scheduler.gating.decide_and_execute_event", reject)
    bridge = WakeBridge()
    assert _run(bridge.submit_garden_mapping(_garden())).status == "pending"
    assert _run(bridge.drain_due())[0].reason == gate_reason
    record = _record(_state(sandbox)[1])
    assert record["status"] == PENDING
    assert record["next_attempt_at"] > record["last_attempt_at"]


def test_dream_block_and_uncertain_path_remain_pending(garden_env, sandbox, monkeypatch):
    from core.wake_bridge import PENDING, WakeBridge

    _allow_execute(monkeypatch, perceive_status="blocked_dream", sent=False)
    bridge = WakeBridge()
    assert _run(bridge.submit_garden_mapping(_garden())).status == "pending"
    assert _run(bridge.drain_due())[0].status == "blocked_dream"
    record = _record(_state(sandbox)[1])
    assert record["status"] == PENDING
    assert record["last_disposition"] == "blocked_dream"


def test_garden_hint_uses_existing_proposal_perceive_path_not_history(garden_env, sandbox, monkeypatch):
    from core.wake_bridge import WakeBridge, query_state

    calls = _allow_execute(monkeypatch)
    bridge = WakeBridge()
    assert _run(bridge.submit_garden_mapping(_garden(reason="game_turn_required"))).status == "pending"
    assert _run(bridge.drain_due())[0].status == "accepted"
    assert len(calls) == 1
    call = calls[0]
    assert call["trigger_name"] == "garden_wake_hint"
    assert call["write_trigger_stub"] is False and call["recall_policy"] == "none"
    event = call["perceive_event"]
    assert event.source == "garden:galatea_garden" and event.trust == "low_trust"
    prompt = call["prompt_factory"]()
    assert "来自 Galatea Garden 的低信任状态提示" in prompt
    assert "get_my_status" in prompt
    assert "list_notifications" not in prompt
    observed = query_state(uid="owner-1", char_id="char-a", provider="galatea_garden")
    serialized = json.dumps(observed, ensure_ascii=False)
    assert _garden()["message"] not in serialized
    assert observed[0]["last_reason"] == "game_turn_required"
    assert observed[0]["last_time_sensitive_lane"] is True
    assert observed[0]["last_disposition"] == "sent"


def test_garden_endpoint_requires_integration_scope():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from admin.routers.wake_bridge import router

    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).post("/integrations/garden/wake", json=_garden()).status_code == 401


def test_garden_hint_has_no_direct_mcp_or_llm_call():
    import inspect
    import core.wake_bridge as bridge

    source = inspect.getsource(bridge.WakeBridge._garden_proposal)
    assert "execute_prompt" in source
    assert "mcp_client" not in source
    assert "run_llm(" not in source


def test_garden_turn_logs_only_safe_policy_fields(garden_env, sandbox, monkeypatch, caplog):
    from core.wake_bridge import WakeBridge

    _allow_execute(monkeypatch)
    caplog.set_level("INFO", logger="core.wake_bridge")
    bridge = WakeBridge()
    assert _run(bridge.submit_garden_mapping(_garden(reason="game_turn_required"))).status == "pending"
    assert _run(bridge.drain_due())[0].status == "accepted"

    rendered = "\n".join(caplog.messages)
    assert "event=ingress_received reason=game_turn_required policy_lane=time_sensitive_turn disposition=pending" in rendered
    assert "event=drain_started reason=game_turn_required policy_lane=time_sensitive_turn disposition=processing" in rendered
    assert "event=gate reason=game_turn_required policy_lane=time_sensitive_turn disposition=picked_highest_urgency" in rendered
    assert "event=pipeline_entered reason=game_turn_required policy_lane=time_sensitive_turn disposition=accepted" in rendered
    assert "event=drain_finished reason=game_turn_required policy_lane=time_sensitive_turn disposition=consumed" in rendered
    assert _garden()["message"] not in rendered


def _garden_proposal(reason="game_turn_required"):
    from core.wake_bridge import GardenWakeHint, WakeBridge

    hint = GardenWakeHint(
        reason=reason,
        message="Garden requires attention.",
        uid="owner-1",
        char_id="char-a",
        received_at=time.time(),
    )
    return WakeBridge()._garden_proposal(hint, {})


def _patch_time_sensitive_gate(monkeypatch, *, user_active=False, dnd_active=False, state=None, ledger_reason="gap_not_elapsed"):
    from core.scheduler import gating
    from core.scheduler.state_machine import TriggerState
    import core.scheduler.loop as loop
    import core.scheduler.proactive_ledger as ledger
    import core.scheduler.triggers.dnd as dnd

    calls = []
    monkeypatch.setattr("core.conversation_activity.user_active_recently", lambda: user_active)
    monkeypatch.setattr(dnd, "is_dnd", lambda uid: dnd_active)
    monkeypatch.setattr(gating, "get_current_state", lambda uid: state or TriggerState.CHATTING)
    monkeypatch.setattr(gating, "is_trigger_ready", lambda *args, **kwargs: True)

    def can_send(trigger_name, *, priority, uid=""):
        calls.append((trigger_name, priority, uid))
        return (priority == "emergency", "emergency_exempt" if priority == "emergency" else ledger_reason)

    monkeypatch.setattr(ledger, "can_send", can_send)
    return calls


@pytest.mark.parametrize("ledger_reason", ["gap_not_elapsed", "daily_budget_exceeded"])
def test_game_turn_time_sensitive_lane_bypasses_ordinary_ledger(garden_env, monkeypatch, ledger_reason):
    from core.scheduler.gating import _decide

    calls = _patch_time_sensitive_gate(monkeypatch, state=None, ledger_reason=ledger_reason)
    picked, reason, _ = _decide("owner-1", [_garden_proposal()])

    assert picked is not None and reason == "picked_highest_urgency"
    assert picked.time_sensitive_external_turn is True
    assert calls == [("garden_wake_hint", "emergency", "owner-1")]


def test_game_turn_bypasses_owner_active_and_chatting_state_but_not_dnd(garden_env, monkeypatch):
    from core.scheduler.gating import _decide

    calls = _patch_time_sensitive_gate(monkeypatch, user_active=True, state=None)
    picked, reason, _ = _decide("owner-1", [_garden_proposal()])
    assert picked is not None and reason == "picked_highest_urgency"
    assert calls == [("garden_wake_hint", "emergency", "owner-1")]

    _patch_time_sensitive_gate(monkeypatch, user_active=True, dnd_active=True, state=None)
    picked, reason, _ = _decide("owner-1", [_garden_proposal()])
    assert picked is None and reason == "dnd_filtered"


@pytest.mark.parametrize("reason", ["notification_available", "manual_test"])
def test_ordinary_garden_reasons_do_not_use_time_sensitive_lane(garden_env, monkeypatch, reason):
    from core.scheduler.gating import _decide
    from core.scheduler.state_machine import TriggerState

    calls = _patch_time_sensitive_gate(monkeypatch, state=TriggerState.QUIET)
    picked, gate_reason, _ = _decide("owner-1", [_garden_proposal(reason)])

    assert picked is None and gate_reason == "global_gap_filtered"
    assert calls == [("garden_wake_hint", "normal", "owner-1")]


@pytest.mark.parametrize("dream_guard_case", ["active", "uncertain"])
def test_game_turn_dream_guard_outcomes_remain_pending(garden_env, sandbox, monkeypatch, dream_guard_case):
    from core.wake_bridge import PENDING, WakeBridge

    _allow_execute(monkeypatch, perceive_status="blocked_dream", sent=False)
    bridge = WakeBridge()
    assert _run(bridge.submit_garden_mapping(_garden(reason="game_turn_required"))).status == PENDING
    assert _run(bridge.drain_due())[0].status == "blocked_dream"
    assert _record(_state(sandbox)[1])["last_disposition"] == "blocked_dream"
