import json
import tempfile
from pathlib import Path


def _fresh_state_machine(monkeypatch, now=1_000.0):
    import core.sandbox as sandbox_mod
    from core.scheduler import state_machine as sm

    paths = sandbox_mod.DataPaths(mode="test", test_session_id="pytest_state_machine")
    base = Path("data") / "test_sandbox"
    base.mkdir(parents=True, exist_ok=True)
    tmp_guard = tempfile.TemporaryDirectory(dir=base)
    paths._base = Path(tmp_guard.name)
    paths._tmp_guard = tmp_guard
    monkeypatch.setattr(sandbox_mod, "_instance", paths)
    current = {"value": now}
    monkeypatch.setattr(sm, "_now", lambda: current["value"])
    sm._reset_for_tests()
    return sm, current, paths


def test_owner_turn_enters_chatting_immediately(monkeypatch):
    sm, _, _ = _fresh_state_machine(monkeypatch)

    sm.notify_owner_turn("u1")

    assert sm.get_state("u1") == sm.TriggerState.CHATTING
    assert sm.snapshot("u1")["session_turn_count"] == 1


def test_final_delay_examples_from_design_doc(monkeypatch):
    sm, _, _ = _fresh_state_machine(monkeypatch)

    assert sm.calculate_final_delay_seconds(25, 0.3) == 9 * 60
    assert sm.calculate_final_delay_seconds(5, 0.8) == 2.5 * 60


def test_sensor_rate_requires_persist_before_restless(monkeypatch):
    sm, current, _ = _fresh_state_machine(monkeypatch)

    sm.feed_sensor_tick("u1", 4)
    assert sm.get_state("u1") == sm.TriggerState.QUIET

    sm.feed_sensor_tick("u1", 1)
    assert sm.get_state("u1") == sm.TriggerState.QUIET

    current["value"] += sm.QUIET_TO_ACTIVE_PERSIST_SECONDS - 1
    sm.feed_sensor_tick("u1", 0)
    assert sm.get_state("u1") == sm.TriggerState.QUIET

    current["value"] += 2
    sm.feed_sensor_tick("u1", 0)
    assert sm.get_state("u1") == sm.TriggerState.RESTLESS


def test_restless_returns_to_quiet_after_sensor_silence(monkeypatch):
    sm, current, _ = _fresh_state_machine(monkeypatch)

    sm.feed_sensor_tick("u1", 5)
    current["value"] += sm.QUIET_TO_ACTIVE_PERSIST_SECONDS + 1
    sm.feed_sensor_tick("u1", 0)
    assert sm.get_state("u1") == sm.TriggerState.RESTLESS

    current["value"] += sm.ACTIVE_TO_QUIET_BASE_SECONDS - sm.QUIET_TO_ACTIVE_PERSIST_SECONDS - 2
    sm.feed_sensor_tick("u1", 0)
    assert sm.get_state("u1") == sm.TriggerState.RESTLESS

    current["value"] += 3
    sm.feed_sensor_tick("u1", 0)
    assert sm.get_state("u1") == sm.TriggerState.QUIET


def test_state_persists_inside_scheduler_user_state_without_clobbering(monkeypatch):
    sm, _, paths = _fresh_state_machine(monkeypatch)
    # cooldowns lives in a separate file — set up pre-existing data
    paths.scheduler_cooldowns().write_text(
        json.dumps({"triggers": {"random_message": 123.0}}), encoding="utf-8"
    )
    # pre-existing user_state (e.g. last_diary_share)
    paths.scheduler_user_state().parent.mkdir(parents=True, exist_ok=True)
    paths.scheduler_user_state().write_text(
        json.dumps({"last_diary_share": 456.0}), encoding="utf-8"
    )

    sm.notify_owner_turn("u1")
    raw = json.loads(paths.scheduler_user_state().read_text(encoding="utf-8"))

    assert raw["last_diary_share"] == 456.0
    assert raw["trigger_state"]["u1"]["state"] == "CHATTING"
    # cooldowns file must be untouched by state_machine
    cooldowns_raw = json.loads(paths.scheduler_cooldowns().read_text(encoding="utf-8"))
    assert cooldowns_raw["triggers"] == {"random_message": 123.0}

    sm._reset_for_tests()
    assert sm.get_state("u1") == sm.TriggerState.CHATTING
