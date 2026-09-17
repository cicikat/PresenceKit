"""Regression coverage for the uid-global period reminder boundary."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _health_path(paths, uid: str) -> Path:
    return paths._p("runtime", "memory", "global", uid, "health_state.json")


def _manifest(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in root.rglob("*")
        if path.is_file() and "test_sandbox" not in path.parts
    }


def test_set_get_clear_and_reject_invalid_dates(sandbox):
    from core.memory import health_state

    assert health_state.get_period_info("u1") == {"last_period_date": None}
    assert health_state.set_period_date("u1", "2026-07-30") == {"last_period_date": "2026-07-30"}
    assert health_state.get_period_info("u1") == {"last_period_date": "2026-07-30"}
    assert health_state.clear_period_date("u1") == {"last_period_date": None}
    for invalid in ("", "2026-2-03", "2026-02-30", "2026/02/03", "not-a-date"):
        with pytest.raises(ValueError):
            health_state.set_period_date("u1", invalid)


def test_period_date_is_uid_global_and_profile_clear_does_not_change_it(sandbox):
    from core.memory import health_state, user_profile

    health_state.set_period_date("owner1", "2026-07-01")
    user_profile.save("owner1", {"name": "before"}, char_id="character_a")
    user_profile.clear("owner1", char_id="character_a")
    assert health_state.get_period_info("owner1") == {"last_period_date": "2026-07-01"}
    assert "last_period_date" not in user_profile.load("owner1", char_id="character_a")
    assert "last_period_date" not in user_profile.load("owner1", char_id="character_b")


def test_legacy_profile_migration_is_idempotent_and_new_state_wins(sandbox):
    from core.memory import health_state, user_profile

    uid = "legacy-owner"
    user_profile.save(uid, {"last_period_date": "2026-06-20"}, char_id="character_a")
    assert health_state.get_period_info(uid) == {"last_period_date": "2026-06-20"}
    user_profile.save(uid, {"last_period_date": "2026-06-21"}, char_id="character_a")
    assert health_state.get_period_info(uid) == {"last_period_date": "2026-06-20"}
    health_state.set_period_date(uid, "2026-06-22")
    assert health_state.get_period_info(uid) == {"last_period_date": "2026-06-22"}
    health_state.clear_period_date(uid)
    assert health_state.get_period_info(uid) == {"last_period_date": None}


def test_legacy_period_conflict_uses_active_character_and_warns(sandbox, monkeypatch, caplog):
    from core import pipeline_registry
    from core.memory import health_state, user_profile

    uid = "legacy-conflict"
    user_profile.save(uid, {"last_period_date": "2026-06-01"}, char_id="character_a")
    user_profile.save(uid, {"last_period_date": "2026-06-02"}, char_id="character_b")
    monkeypatch.setattr(pipeline_registry, "get", lambda: SimpleNamespace(_active_character_id="character_b"))
    with caplog.at_level(logging.WARNING):
        assert health_state.get_period_info(uid) == {"last_period_date": "2026-06-02"}
    assert "Legacy period state conflict" in caplog.text
    assert "2026-06-01" not in caplog.text
    assert "2026-06-02" not in caplog.text


def test_period_proposer_and_legacy_check_read_health_state(sandbox, monkeypatch, caplog):
    from core.memory import health_state
    from core.runtime_signal_observability import _reset_for_tests, snapshot
    from core.scheduler.triggers import period

    _reset_for_tests()
    with caplog.at_level(logging.INFO):
        assert period.propose({"uid": "missing-owner", "today": date.today()}) is None
    assert "missing_period_date" not in caplog.text
    signal = next(item for item in snapshot()["signals"] if item["code"] == "period_date_missing")
    assert signal["status"] == "attention" and signal["count"] == 1

    health_state.set_period_date("owner1", date.today().isoformat())
    assert period.propose({"uid": "owner1", "today": date.today()}) is not None
    assert not hasattr(period, "_check_period")


def test_manual_trigger_missing_input_returns_reason_without_send(sandbox, monkeypatch):
    from core.scheduler import loop

    async def unexpected_send(*_args, **_kwargs):
        raise AssertionError("manual period trigger must not send without an input date")

    monkeypatch.setattr(loop, "_owner_id", lambda: "owner1")
    monkeypatch.setattr(loop, "_pipeline_send", unexpected_send)
    assert asyncio.run(loop.manual_trigger("period_reminder")) == "missing_period_date"


def test_prompt_builder_reads_uid_global_period_state(sandbox, monkeypatch):
    from core.character_loader import Character
    from core.memory import health_state
    from core import prompt_builder

    health_state.set_period_date("owner1", date.today().isoformat())
    monkeypatch.setattr(prompt_builder, "_load_jailbreak", lambda layer=None: "")
    monkeypatch.setattr(prompt_builder, "_load_style_hint", lambda *, char_id="": "")
    monkeypatch.setattr(prompt_builder, "_load_activity_snapshot", lambda *, char_id="": "")
    monkeypatch.setattr(prompt_builder, "_format_afterglow_soft_hint", lambda uid, char_id="": "")
    monkeypatch.setattr("core.presence.get_last_seen_text", lambda uid: "")
    monkeypatch.setattr("core.author_note_rotator.get_current_note", lambda paths=None, char_id=None: "")
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"chat": {}})
    monkeypatch.setattr("core.mood_text.get_mood_text", lambda: "")
    monkeypatch.setattr("core.activity_manager.get_prompt_fragment", lambda *args, **kwargs: "")
    messages, _ = prompt_builder.build(
        character=Character(name="Companion"), user_id="owner1", user_message="pain",
        history=[], relation={}, profile={}, group_context=[], tags={"topic.body"}, char_id="character_b",
    )
    assert "3.5_period" in {message.get("_layer") for message in messages}


@pytest.fixture
def period_client(sandbox, monkeypatch):
    from admin.routers import period

    monkeypatch.setattr("admin.auth.get_admin_secret", lambda: "period-test-token")
    monkeypatch.setattr(period, "get_config", lambda: {"scheduler": {"owner_id": "owner1"}})
    app = FastAPI()
    app.include_router(period.router)
    return TestClient(app)


def test_period_api_auth_validation_and_owner_binding(period_client):
    auth = {"Authorization": "Bearer period-test-token"}
    assert period_client.get("/period").status_code == 401
    assert period_client.get("/period", headers=auth).json() == {"last_period_date": None, "period_reminder_input_ready": False}
    assert period_client.put("/period", headers=auth, json={"last_period_date": "invalid"}).status_code == 422
    assert period_client.put("/period", headers=auth, json={"last_period_date": "2026-07-30", "uid": "other"}).json() == {"last_period_date": "2026-07-30", "period_reminder_input_ready": True}
    assert period_client.delete("/period", headers=auth).json()["last_period_date"] is None


def _paths_at(base: Path, *, mode: str):
    import core.sandbox as sandbox_mod

    paths = sandbox_mod.DataPaths(mode=mode, test_session_id="period-import-isolation")
    paths._base = base
    return paths


def test_preimport_then_sandbox_switch_does_not_hold_old_path(monkeypatch, tmp_path):
    import core.sandbox as sandbox_mod

    actual_before = _manifest(Path("data").resolve())
    old_root, sandbox_root = tmp_path / "old", tmp_path / "sandbox"
    monkeypatch.setattr(sandbox_mod, "_instance", _paths_at(old_root, mode="production"))
    health_state = importlib.reload(importlib.import_module("core.memory.health_state"))
    monkeypatch.setattr(sandbox_mod, "_instance", _paths_at(sandbox_root, mode="test"))
    health_state.set_period_date("u1", "2026-07-30")
    assert not old_root.exists()
    assert _health_path(_paths_at(sandbox_root, mode="test"), "u1").is_file()
    assert _manifest(Path("data").resolve()) == actual_before


def test_period_operations_write_only_to_sandbox(sandbox):
    from core.memory import health_state

    production_data = Path("data").resolve()
    before = _manifest(production_data)
    health_state.set_period_date("u1", "2026-07-30")
    health_state.set_period_date("owner1", "2026-07-29")
    health_state.set_period_date("test_char", "2026-07-28")
    assert _manifest(production_data) == before
    sandbox_paths = {path.relative_to(sandbox._base).as_posix() for path in sandbox._base.rglob("health_state.json")}
    assert all(f"runtime/memory/global/{uid}/health_state.json" in sandbox_paths for uid in ("u1", "owner1", "test_char"))


def test_deprecated_profile_shims_forward_without_character_scope(sandbox, caplog):
    from core.memory import user_profile

    with caplog.at_level(logging.WARNING):
        user_profile.set_period_date("owner1", "2026-07-30")
        assert user_profile.get_period_info("owner1") == {"last_period_date": "2026-07-30"}
    assert "deprecated" in caplog.text
