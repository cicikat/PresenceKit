"""Regression coverage for uid-global health and sensor persistence."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


def _manifest(root: Path) -> dict[str, tuple[int, int, str]]:
    return {
        item.relative_to(root).as_posix(): (
            item.stat().st_size, item.stat().st_mtime_ns,
            hashlib.sha256(item.read_bytes()).hexdigest(),
        )
        for item in sorted(root.rglob("*")) if item.is_file()
    }


def _health_path(paths, uid: str) -> Path:
    from core.data_paths import DEFAULT_CHAR_ID
    return paths.user_memory_root(uid, char_id=DEFAULT_CHAR_ID) / "health_state.json"


def test_health_state_is_character_scoped(sandbox):
    from core.memory import health_state, user_profile

    uid = "health-shared"
    user_profile.save(uid, {"name": "a"}, char_id="character_a")
    user_profile.save(uid, {"name": "b"}, char_id="character_b")
    health_state.save(uid, {"sleep_segments": [{"duration_minutes": 420}]})

    assert health_state.load(uid)["sleep_segments"] == [{"duration_minutes": 420}]
    assert user_profile.load(uid, char_id="character_a")["name"] == "a"
    assert user_profile.load(uid, char_id="character_b")["name"] == "b"
    assert _health_path(sandbox, uid).is_file()


def test_watch_sleep_writes_health_not_profile(sandbox, monkeypatch):
    from admin.routers import watch
    from core.memory import health_state, user_profile

    uid = "watch-owner"
    user_profile.save(uid, {"name": "profile-only"})
    profile_path = sandbox.user_memory_root(uid, char_id=user_profile.DEFAULT_CHAR_ID) / "profile.json"
    before = profile_path.read_bytes()
    watch._sleep_buffer[:] = [{"sleep_start": "00:10", "sleep_end_time": "07:10"}]

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    monkeypatch.setattr(watch, "get_config", lambda: {"scheduler": {"owner_id": uid}, "character": {"name": "Companion"}})
    monkeypatch.setattr("core.scheduler.on_watch_event", AsyncMock())
    asyncio.run(watch._flush_sleep_buffer())

    assert profile_path.read_bytes() == before
    assert health_state.load(uid)["sleep_segments"][-1]["duration_minutes"] == 420


def test_sensor_and_heart_rate_do_not_enter_profile(sandbox, monkeypatch):
    from admin.routers import sensor, watch
    from core.memory import health_state, user_profile

    uid = "objective-owner"
    user_profile.save(uid, {"name": "profile-only"})
    monkeypatch.setattr(sensor, "get_config", lambda: {"scheduler": {"owner_id": uid}})
    sensor._save_sensor_to_health_state({"steps": 5000, "battery": 80, "location": "City", "screen_sessions": 5})
    watch._append_heart_rate_event(uid, 92, triggered=False)

    profile, state = user_profile.load(uid), health_state.load(uid)
    assert not {"phone_sensor_log", "phone_sensor_today", "heart_rate_events"} & set(profile)
    assert state["phone_sensor_today"]["steps"] == 5000
    assert state["heart_rate_events"][-1]["value"] == 92


def test_watch_tool_and_sensor_today_read_health_state(sandbox, monkeypatch):
    from admin.routers import sensor
    from core.memory import health_state, user_profile
    from core.tools.watch_tool import read_watch_for_user

    uid = "reader-owner"
    user_profile.save(uid, {"sleep_segments": [{"duration_minutes": 1}]})
    health_state.save(uid, {
        "sleep_segments": [{"time": "2026-07-30", "duration_minutes": 420, "sleep_start": "00:10", "sleep_end_time": "07:10"}],
        "heart_rate_events": [{"time": "2026-07-30 08:00", "value": 88, "triggered": False}],
        "phone_sensor_today": {"steps": 5000},
    })
    monkeypatch.setattr(sensor, "get_config", lambda: {"scheduler": {"owner_id": uid}})

    assert "88" in read_watch_for_user(uid)
    assert asyncio.run(sensor.get_sensor_today()) == {"steps": 5000}


def test_prompt_reads_health_state(monkeypatch):
    from core import prompt_builder

    char = MagicMock()
    char.name = "Companion"
    char.system_prompt = char.description = char.personality = char.scenario = char.mes_example = ""
    char.jailbreak_entries = []
    today = __import__("datetime").date.today().isoformat()
    health = {
        "sleep_segments": [{"time": today, "duration_minutes": 420, "sleep_start": "00:10", "sleep_end_time": "07:10"}],
        "phone_sensor_today": {"date": today, "steps": 5000, "battery": 80, "location": "City"},
    }
    with (
        patch("core.prompt_builder._load_jailbreak", return_value=""),
        patch("core.prompt_builder._load_style_hint", return_value=""),
        patch("core.presence.get_last_seen_text", return_value=""),
        patch("core.author_note_rotator.get_current_note", return_value=""),
        patch("core.config_loader.get_config", return_value={"chat": {"style": "roleplay"}, "watch": {"fresh_days": 3}}),
        patch("core.memory.health_state.load", return_value=health),
        patch("core.memory.user_profile.load", return_value={}),
        patch("core.mood_text.get_mood_text", return_value=""),
    ):
        messages, _ = prompt_builder.build(
            character=char, user_id="prompt-owner", user_message="health", history=[],
            relation={"role": "friend"}, profile={}, group_context=[], tags={"topic.health"},
        )
    assert {"3.6_watch", "3.7_sensor"} <= {item.get("_layer") for item in messages}


def test_profile_clear_preserves_health_state(sandbox):
    from core.memory import health_state, user_profile

    health_state.save("clear-owner", {"heart_rate_events": [{"value": 88}]})
    user_profile.save("clear-owner", {"name": "before"})
    user_profile.clear("clear-owner")
    assert user_profile.load("clear-owner")["name"] is None
    assert health_state.load("clear-owner")["heart_rate_events"] == [{"value": 88}]


def test_mutation_atomic_failure_and_concurrency(sandbox, monkeypatch):
    from core.memory import health_state

    uid = "health-concurrent"
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda callback: health_state.mutate(uid, callback), (
            lambda state: state.__setitem__("sleep_segments", [{"duration_minutes": 420}]),
            lambda state: state.__setitem__("heart_rate_events", [{"value": 88}]),
        )))
    state = health_state.load(uid)
    assert state["sleep_segments"] == [{"duration_minutes": 420}]
    assert state["heart_rate_events"] == [{"value": 88}]

    path = _health_path(sandbox, uid)
    before = path.read_text(encoding="utf-8")
    monkeypatch.setattr(health_state, "safe_write_json", lambda *_args, **_kwargs: False)
    assert health_state.save(uid, {"phone_sensor_today": {"steps": 200}}) is False
    assert path.read_text(encoding="utf-8") == before
    assert json.loads(before)["heart_rate_events"] == [{"value": 88}]


def test_legacy_migration_is_idempotent_and_prefers_new_state(sandbox):
    from core.memory import health_state, user_profile

    uid = "legacy-owner"
    user_profile.save(uid, {"sleep_segments": [{"duration_minutes": 420}]})
    assert health_state.load(uid)["sleep_segments"] == [{"duration_minutes": 420}]
    user_profile.save(uid, {"sleep_segments": [{"duration_minutes": 300}]})
    assert health_state.load(uid)["sleep_segments"] == [{"duration_minutes": 420}]
    health_state.save(uid, {"sleep_segments": [{"duration_minutes": 480}]})
    assert health_state.load(uid)["sleep_segments"] == [{"duration_minutes": 480}]


def test_legacy_conflict_prefers_active_character(sandbox, monkeypatch, caplog):
    from core import pipeline_registry
    from core.memory import health_state, user_profile

    uid = "legacy-conflict"
    user_profile.save(uid, {"heart_rate_events": [{"value": 71}]}, char_id="character_a")
    user_profile.save(uid, {"heart_rate_events": [{"value": 92}]}, char_id="character_b")
    monkeypatch.setattr(pipeline_registry, "get", lambda: SimpleNamespace(_active_character_id="character_b"))
    assert health_state.load(uid)["heart_rate_events"] == [{"value": 92}]
    assert health_state.load(uid, char_id="character_a")["heart_rate_events"] == [{"value": 71}]
    assert health_state.load(uid, char_id="new_character")["heart_rate_events"] == []


def _paths_at(base: Path, *, mode: str):
    import core.sandbox as sandbox_mod

    paths = sandbox_mod.DataPaths(mode=mode, test_session_id="health-import-isolation")
    paths._base = base
    return paths


def test_preimport_rebinds_to_sandbox(monkeypatch, tmp_path):
    import core.sandbox as sandbox_mod

    actual_before = _manifest(Path("data").resolve())
    old_root, sandbox_root = tmp_path / "old-root", tmp_path / "sandbox-root"
    monkeypatch.setattr(sandbox_mod, "_instance", _paths_at(old_root, mode="production"))
    health_state = importlib.reload(importlib.import_module("core.memory.health_state"))
    monkeypatch.setattr(sandbox_mod, "_instance", _paths_at(sandbox_root, mode="test"))
    health_state.save("u1", {"heart_rate_events": [{"value": 88}]})
    assert not old_root.exists()
    assert _health_path(_paths_at(sandbox_root, mode="test"), "u1").is_file()
    assert _manifest(Path("data").resolve()) == actual_before


def test_target_operations_leave_production_data_unchanged(sandbox, monkeypatch):
    from admin.routers import sensor, watch
    from core.memory import health_state

    actual_data = Path("data").resolve()
    before = _manifest(actual_data)
    monkeypatch.setattr(sensor, "get_config", lambda: {"scheduler": {"owner_id": "owner1"}})
    sensor._save_sensor_to_health_state({"steps": 10})
    watch._append_heart_rate_event("u1", 77, triggered=False)
    health_state.mutate("test_char", lambda state: state.__setitem__("sleep_segments", [{"duration_minutes": 360}]))
    assert _manifest(actual_data) == before
    text = "\n".join(
        f"{path.relative_to(sandbox._base).as_posix()}\n{path.read_text(encoding='utf-8')}"
        for path in sandbox._base.rglob("*.json")
    )
    assert all(token in text for token in ("owner1", "u1", "test_char"))
