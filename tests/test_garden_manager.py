import json
from concurrent.futures import ThreadPoolExecutor

import pytest


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_get_state_bootstrap_uses_safe_write_and_round_trips(sandbox, monkeypatch):
    from core.garden import manager

    calls = []
    real_safe_write_json = manager.safe_write_json

    def spy_safe_write_json(path, data):
        calls.append(path.name)
        return real_safe_write_json(path, data)

    monkeypatch.setattr(manager, "safe_write_json", spy_safe_write_json)

    state = manager.get_state(char_id="yexuan")

    assert len(state["slots"]) == len(manager.FLOWERS)
    assert state["harvest_count"] == 0
    assert state["vase_count"] == 0
    assert calls == ["plants.json", "storage.json"]

    plants = _read_json(sandbox.garden() / "plants.json")
    storage = _read_json(sandbox.garden() / "storage.json")
    assert plants["slots"]["calm"]["growth"] == 0
    assert storage == {"harvest": [], "vase": [], "history": []}


def test_save_raises_when_safe_write_fails(sandbox, monkeypatch):
    from core.garden import manager

    monkeypatch.setattr(manager, "safe_write_json", lambda path, data: False)

    with pytest.raises(OSError):
        manager._save(sandbox.garden() / "plants.json", {"slots": {}})


def test_water_and_daily_check_keep_existing_transitions(sandbox, monkeypatch):
    from core.garden import manager

    now = 1_000_000.0
    monkeypatch.setattr(manager.time, "time", lambda: now)

    for expected_growth in (10, 20, 30):
        result = manager.water("calm", reason="force", char_id="yexuan")
        assert result["ok"] is True
        assert result["slot_key"] == "calm"
        assert result["stage"] == "seed"
        assert result["growth"] == expected_growth
        assert result["events"] == []

    manager._save(
        manager._storage_path(),
        {
            "harvest": [{
                "flower_id": "daisy",
                "bloomed_at": now - 20 * 86400,
                "expires_at": now - 1,
                "status": "fresh",
            }],
            "vase": [{
                "flower_id": "rose",
                "placed_at": now - 8 * 86400,
                "wilts_at": now - 1,
            }],
            "history": [],
        },
    )

    events = manager.daily_check(char_id="yexuan")
    assert [event["type"] for event in events] == ["harvest_expired", "vase_wilted"]

    storage = _read_json(sandbox.garden() / "storage.json")
    assert storage["harvest"] == []
    assert storage["vase"] == []
    assert [item["status"] for item in storage["history"]] == ["expired", "wilted"]

    assert manager.daily_check(char_id="yexuan") == []


def test_repeated_concurrent_calls_leave_readable_json(sandbox):
    from core.garden import manager

    def run_once(i):
        if i % 3 == 0:
            return manager.daily_check(char_id="yexuan")
        if i % 3 == 1:
            return manager.get_state(char_id="yexuan")
        return manager.water("calm", reason="force", char_id="yexuan")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run_once, range(24)))

    assert len(results) == 24
    plants = _read_json(sandbox.garden() / "plants.json")
    storage = _read_json(sandbox.garden() / "storage.json")
    assert "slots" in plants
    assert set(storage) == {"harvest", "vase", "history"}
