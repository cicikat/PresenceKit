from tests.fixtures.public_assets import TEST_CHAR_ID
import asyncio


def _enabled_config(**overrides):
    cfg = {
        "presence_nag": True,
        "presence_nag_minutes": 60,
        "activity_level": "high",
    }
    cfg.update(overrides)
    return {"scheduler": cfg}


def _eligible(monkeypatch, *, mood="sad", last_owner_turn_ts=1_000.0, now_ts=5_000.0):
    from core.scheduler import loop
    from core.scheduler.triggers import presence_nag

    monkeypatch.setattr("core.config_loader.get_config", lambda: _enabled_config())
    monkeypatch.setattr(loop, "_is_ready", lambda name, **_kwargs: True)
    monkeypatch.setattr(loop, "_owner_id", lambda: "owner")
    monkeypatch.setattr(loop, "_active_char_id_or_none", lambda: TEST_CHAR_ID)
    monkeypatch.setattr(
        "core.scheduler.state_machine.snapshot",
        lambda uid: {"last_owner_turn_ts": last_owner_turn_ts},
    )
    monkeypatch.setattr(
        "core.memory.mood_state.load",
        lambda *, char_id: {"current": mood, "intensity": 0.7},
    )
    return presence_nag.propose({"now_ts": now_ts})


def test_presence_nag_default_off(monkeypatch):
    from core.scheduler.triggers import presence_nag

    monkeypatch.setattr("core.config_loader.get_config", lambda: {"scheduler": {}})
    assert presence_nag.propose({"uid": "owner", "char_id": TEST_CHAR_ID}) is None


def test_presence_nag_requires_high_activity_negative_mood_and_silence(monkeypatch):
    assert _eligible(monkeypatch) is not None

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: _enabled_config(activity_level="low"),
    )
    from core.scheduler.triggers import presence_nag
    assert presence_nag.propose({"uid": "owner", "char_id": TEST_CHAR_ID, "now_ts": 5_000.0}) is None


def test_presence_nag_rejects_positive_mood_recent_or_unknown_interaction(monkeypatch):
    assert _eligible(monkeypatch, mood="gentle") is None
    assert _eligible(monkeypatch, last_owner_turn_ts=4_900.0) is None
    assert _eligible(monkeypatch, last_owner_turn_ts=0.0) is None


def test_presence_nag_proposal_is_quiet_only(monkeypatch):
    from core.scheduler.state_machine import TriggerState

    proposal = _eligible(monkeypatch)

    assert proposal.trigger_name == "presence_nag"
    assert proposal.requires_state == [TriggerState.QUIET]
    assert proposal.char_id == TEST_CHAR_ID


def test_presence_nag_execute_aligns_action_name_and_uses_llm_reply(monkeypatch):
    from core.scheduler import execution
    from core.scheduler.triggers import presence_nag

    captured = {}

    async def fake_pipeline_send(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return "你终于肯看我一眼了吗？"

    monkeypatch.setattr("core.scheduler.loop._active_char_id_or_none", lambda: TEST_CHAR_ID)
    monkeypatch.setattr("core.scheduler.loop._pipeline_send", fake_pipeline_send)
    monkeypatch.setattr("core.scheduler.loop._mark", lambda *args, **kwargs: None)

    result = asyncio.run(presence_nag._make_execute(72.4, TEST_CHAR_ID)(dry_run=False))

    assert result.sent is True
    assert captured["fanout"] == ["desktop"]
    action = captured["behavior_factory"]("你终于肯看我一眼了吗？")
    assert action == {
        "action_type": "presence_nag",
        "params": {"text": "你终于肯看我一眼了吗？", "avatar": TEST_CHAR_ID},
    }
    assert "72 分钟" in captured["prompt"]
    assert "只说一句" in captured["prompt"]


def test_presence_nag_policy_drops_while_user_active():
    from core.scheduler.policy import POLICY_TABLE

    policy = POLICY_TABLE["presence_nag"]
    assert policy.active_window_behavior == "drop"
    assert policy.mark_on_drop is False


def test_scheduler_config_endpoint_can_toggle_presence_nag(monkeypatch):
    from admin.routers import scheduler

    saved = {}
    monkeypatch.setattr(scheduler, "_sched_cfg", lambda: {"enabled": True, "presence_nag": False})
    monkeypatch.setattr(scheduler, "_save_sched_cfg", lambda cfg: saved.update(cfg))

    result = asyncio.run(scheduler.put_sched_config({"presence_nag": True}, auth=None))

    assert saved["presence_nag"] is True
    assert result["config"]["presence_nag"] is True
