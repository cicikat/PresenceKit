"""256 D: dry-run inventory, freeze ownership, re-entrant import, rollback."""

from __future__ import annotations

import json
from core import character_self as self_mod
from core.character_self_migration import (
    SOURCE_RETENTION_DAYS,
    apply_legacy_toy_import,
    freeze_legacy_toy_owner,
    inventory_legacy_toys,
    rollback_legacy_toy_import,
)
from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID


_UID = "legacy-owner"
_CHAR = TEST_CHAR_ID
_OTHER = TEST_PEER_CHAR_ID


def _seed_archive(sandbox, *, diary="日记原文", wishlist="去海边", doodle="涂鸦"):
    archive = sandbox.very_formal_project_dir()
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "思考笔记.txt").write_text(diary, encoding="utf-8")
    (archive / "愿望清单.md").write_text(wishlist, encoding="utf-8")
    (archive / "涂鸦板.txt").write_text(doodle, encoding="utf-8")
    (archive / ".autogrow_state.json").write_text("{}", encoding="utf-8")
    return archive


def _blob(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False)


def test_unclaimed_inventory_does_not_copy_to_all_characters(sandbox, monkeypatch):
    _seed_archive(sandbox)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"character": {"default": _CHAR}},
    )
    report = inventory_legacy_toys()
    assert report["mode"] == "dry_run"
    assert report["never_copy_to_all_characters"] is True
    assert report["never_guess_from_active"] is True
    assert report["owner"]["claimed"] is False
    assert report["actions"]["unclaimed"] == 3
    assert report["source_retention_days"] == SOURCE_RETENTION_DAYS
    assert "very_formal_project" in report["archive"]
    assert str(sandbox.very_formal_project_dir()) not in _blob(report)
    assert not sandbox.character_self_root(_UID, char_id=_CHAR).exists()
    assert not sandbox.character_self_root(_UID, char_id=_OTHER).exists()


def test_freeze_uses_configured_default_not_active(sandbox, monkeypatch):
    _seed_archive(sandbox)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"character": {"default": _CHAR}, "owner_id": _UID},
    )
    first = freeze_legacy_toy_owner()
    assert first["char_id"] == _CHAR
    assert first["uid"] == _UID
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"character": {"default": _OTHER}, "owner_id": "someone-else"},
    )
    again = freeze_legacy_toy_owner()
    assert again["char_id"] == _CHAR
    assert again["uid"] == _UID
    report = inventory_legacy_toys()
    assert report["owner"]["claimed"] is True
    assert report["owner"]["char_id"] == _CHAR
    assert report["actions"]["import"] == 3


def test_apply_is_reentrant_and_does_not_copy_to_other_character(sandbox, monkeypatch):
    archive = _seed_archive(sandbox)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"character": {"default": _CHAR}, "owner_id": _UID},
    )
    first = apply_legacy_toy_import(uid=_UID, char_id=_CHAR)
    assert first["applied"] is True
    assert first["actions"]["import"] == 3
    diary = self_mod.read_self("notes/思考笔记.txt", user_id=_UID, char_id=_CHAR)
    assert diary["ok"] is True
    assert diary["content"] == "日记原文"
    peer = self_mod.read_self("notes/思考笔记.txt", user_id=_UID, char_id=_OTHER)
    assert peer["ok"] is False
    assert archive.joinpath("思考笔记.txt").is_file()

    second = apply_legacy_toy_import(uid=_UID, char_id=_CHAR)
    assert second["actions"]["skip"] == 3
    assert second["actions"]["import"] == 0
    again = self_mod.read_self("notes/思考笔记.txt", user_id=_UID, char_id=_CHAR)
    assert again["content"] == "日记原文"
    assert again["revision"] == diary["revision"]


def test_conflict_does_not_overwrite_newer_self_file(sandbox, monkeypatch):
    _seed_archive(sandbox, diary="旧共享日记")
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"character": {"default": _CHAR}, "owner_id": _UID},
    )
    created = self_mod.create_self(
        "notes/思考笔记.txt", "角色后来写的新版本", user_id=_UID, char_id=_CHAR,
    )
    assert created["ok"] is True
    report = inventory_legacy_toys(uid=_UID, char_id=_CHAR)
    diary_item = next(item for item in report["files"] if item["file_key"] == "diary")
    assert diary_item["action"] == "conflict"
    applied = apply_legacy_toy_import(uid=_UID, char_id=_CHAR)
    live = self_mod.read_self("notes/思考笔记.txt", user_id=_UID, char_id=_CHAR)
    assert live["content"] == "角色后来写的新版本"
    assert live["revision"] == created["revision"]
    diary_applied = next(item for item in applied["files"] if item["file_key"] == "diary")
    assert diary_applied["action"] == "conflict"
    assert diary_applied.get("imported") is not True


def test_rollback_restores_archive_and_leaves_newer_self(sandbox, monkeypatch, tmp_path):
    archive = _seed_archive(sandbox, diary="档案原文")
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"character": {"default": _CHAR}, "owner_id": _UID},
    )
    backup = tmp_path / "toy-backup"
    applied = apply_legacy_toy_import(uid=_UID, char_id=_CHAR, backup_dir=backup)
    assert applied["applied"] is True
    assert applied["backup"]["dir_name"] == backup.name
    assert "dir_name" in applied["backup"]
    assert str(backup) not in _blob(applied)

    live = self_mod.read_self("notes/思考笔记.txt", user_id=_UID, char_id=_CHAR)
    updated = self_mod.update_self(
        "notes/思考笔记.txt",
        "导入后继续改过",
        expected_revision=live["revision"],
        user_id=_UID,
        char_id=_CHAR,
    )
    assert updated["ok"] is True
    (archive / "思考笔记.txt").write_text("事后被改过的档案", encoding="utf-8")

    rolled = rollback_legacy_toy_import(backup, overwrite_newer=True)
    assert rolled["ok"] is True
    assert rolled["overwrite_newer"] is False
    assert "思考笔记.txt" in rolled["restored_archive"]
    assert "notes/思考笔记.txt" in rolled["self_files_left_in_place"]
    assert archive.joinpath("思考笔记.txt").read_text(encoding="utf-8") == "档案原文"
    still = self_mod.read_self("notes/思考笔记.txt", user_id=_UID, char_id=_CHAR)
    assert still["content"] == "导入后继续改过"


def test_crash_mid_apply_is_reentrant(sandbox, monkeypatch):
    _seed_archive(sandbox)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"character": {"default": _CHAR}, "owner_id": _UID},
    )
    first = apply_legacy_toy_import(uid=_UID, char_id=_CHAR)
    assert first["applied"] is True
    self_mod.delete_self(
        "notes/愿望清单.md",
        expected_revision=self_mod.read_self(
            "notes/愿望清单.md", user_id=_UID, char_id=_CHAR,
        )["revision"],
        user_id=_UID,
        char_id=_CHAR,
    )
    recovered = apply_legacy_toy_import(uid=_UID, char_id=_CHAR)
    actions = {item["file_key"]: item["action"] for item in recovered["files"]}
    assert actions["diary"] == "skip"
    assert actions["doodle"] == "skip"
    assert actions["wishlist"] in {"import", "skip"}
    wishlist = self_mod.read_self("notes/愿望清单.md", user_id=_UID, char_id=_CHAR)
    assert wishlist["ok"] is True
    assert wishlist["content"] == "去海边"


def test_observability_has_migration_metadata_without_bodies(sandbox, monkeypatch):
    _seed_archive(sandbox, diary="私有日记正文")
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"character": {"default": _CHAR}, "owner_id": _UID},
    )
    apply_legacy_toy_import(uid=_UID, char_id=_CHAR)
    obs = self_mod.observability_snapshot(_UID, _CHAR)
    blob = json.dumps(obs, ensure_ascii=False)
    assert obs["legacy_toy"]["owner_claimed"] is True
    assert obs["legacy_toy"]["owner_char_id"] == _CHAR
    assert obs["legacy_toy"]["source_retention_days"] == SOURCE_RETENTION_DAYS
    assert "私有日记正文" not in blob
    assert str(sandbox.very_formal_project_dir()) not in blob
    report_path = sandbox.legacy_toy_migration_report()
    stored = json.loads(report_path.read_text(encoding="utf-8"))
    assert stored["rollback"]
    assert stored["source_retention_days"] == SOURCE_RETENTION_DAYS
