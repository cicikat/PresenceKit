"""256 E: dry-run inventory, freeze ownership, re-entrant import, rollback."""

from __future__ import annotations

import json
from pathlib import Path

from core.agent_runtime import task_store
from core.agent_runtime.models import TaskPrincipal
from core.agent_runtime.scheduler_capability import list_schedules
from core.reminder_migration import (
    SOURCE_RETENTION_DAYS,
    apply_legacy_reminder_import,
    freeze_legacy_reminder_owner,
    inventory_legacy_reminders,
    rollback_legacy_reminder_import,
)
from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID
import pytest


_UID = "legacy-owner"
_CHAR = TEST_CHAR_ID
_OTHER = TEST_PEER_CHAR_ID


@pytest.fixture(autouse=True)
def _fresh_process():
    task_store.reset_process_instance_for_tests("process-reminder-migration")
    yield
    task_store.reset_process_instance_for_tests()


def _seed_legacy(sandbox, uid=_UID, items=None):
    path = sandbox.user_memory_root(uid, char_id=_CHAR) / "reminders.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = items if items is not None else [
        {"id": "r1", "content": "交材料", "remind_at": "2099-05-25 12:00", "done": False},
        {"id": "r2", "content": "已完成", "remind_at": "2020-01-01 08:00", "done": True},
        {"id": "r3", "content": "没有时间", "done": False},
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _blob(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False)


def test_unclaimed_inventory_does_not_copy_to_all_characters(sandbox, monkeypatch):
    _seed_legacy(sandbox)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"character": {"default": _CHAR}},
    )
    report = inventory_legacy_reminders()
    assert report["mode"] == "dry_run"
    assert report["never_copy_to_all_characters"] is True
    assert report["never_guess_from_active"] is True
    assert report["owner"]["claimed"] is False
    assert report["actions"]["unclaimed"] == 1
    assert report["source_retention_days"] == SOURCE_RETENTION_DAYS
    assert str(sandbox.user_memory_root(_UID, char_id=_CHAR)) not in _blob(report)
    assert list_schedules(TaskPrincipal.reality(_UID, _CHAR)) == []
    assert list_schedules(TaskPrincipal.reality(_UID, _OTHER)) == []


def test_freeze_uses_configured_default_not_active(sandbox, monkeypatch):
    _seed_legacy(sandbox)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"character": {"default": _CHAR}, "owner_id": _UID},
    )
    first = freeze_legacy_reminder_owner()
    assert first["char_id"] == _CHAR
    assert first["uid"] == _UID
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"character": {"default": _OTHER}, "owner_id": "someone-else"},
    )
    again = freeze_legacy_reminder_owner()
    assert again["char_id"] == _CHAR
    assert again["uid"] == _UID
    report = inventory_legacy_reminders()
    assert report["owner"]["claimed"] is True
    assert report["owner"]["char_id"] == _CHAR
    assert report["actions"]["import"] == 1
    assert report["actions"]["archive"] == 2


def test_apply_is_reentrant_and_skips_completed(sandbox, monkeypatch):
    _seed_legacy(sandbox)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"character": {"default": _CHAR}, "owner_id": _UID},
    )
    first = apply_legacy_reminder_import()
    assert first["applied"] is True
    live = list_schedules(TaskPrincipal.reality(_UID, _CHAR), include_cancelled=True, include_completed=True)
    assert len(live) == 1
    assert live[0]["content"] == "交材料"
    assert list_schedules(TaskPrincipal.reality(_UID, _OTHER)) == []
    second = apply_legacy_reminder_import()
    again = list_schedules(TaskPrincipal.reality(_UID, _CHAR), include_cancelled=True, include_completed=True)
    assert len(again) == 1
    assert again[0]["schedule_id"] == live[0]["schedule_id"]
    assert second["actions"]["skip"] >= 1


def test_rollback_does_not_clobber_newer_runtime(sandbox, monkeypatch, tmp_path):
    path = _seed_legacy(sandbox)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"character": {"default": _CHAR}, "owner_id": _UID},
    )
    applied = apply_legacy_reminder_import()
    live = list_schedules(TaskPrincipal.reality(_UID, _CHAR))[0]
    from core.agent_runtime.scheduler_capability import update_schedule
    updated = update_schedule(
        TaskPrincipal.reality(_UID, _CHAR),
        live["schedule_id"],
        expected_revision=live["revision"],
        content="导入后改过",
    )
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "reminders.json").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    rolled = rollback_legacy_reminder_import(backup, overwrite_newer=True)
    assert rolled["overwrite_newer"] is False
    assert rolled["runtime_schedules_left_in_place"] is True
    current = list_schedules(TaskPrincipal.reality(_UID, _CHAR))[0]
    assert current["content"] == "导入后改过"
    assert current["revision"] == updated["revision"]
    assert applied["backup"]["files"]
