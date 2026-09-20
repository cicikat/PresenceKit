"""Contract tests for offline private-state backup snapshots."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from core import backup_state as backup


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _install(tmp_path: Path) -> Path:
    root = tmp_path / "install"
    _write(root / "VERSION", "v1.2.3\n")
    _write(root / "config.yaml", "api_key: test-token-should-never-appear\n")
    _write(
        root / "data" / "layout_version.json",
        json.dumps({"product_baseline": "v1", "data_layout_schema_version": 1, "first_initialized_version": "v1.0.0"}),
    )
    _write(root / "data" / "runtime" / "memory" / "character" / "owner" / "history.json", "private memory body")
    _write(root / "data" / "runtime" / "memory" / "character" / "owner" / "vector_store.db", "derived")
    _write(root / "data" / "logs" / "error.log", "ordinary log")
    _write(root / "userdata" / "characters" / "cards" / "private.json", '{"name":"private"}')
    _write(root / "characters" / "legacy.json", '{"private":true}')
    _write(root / "characters" / "default.json", '{"public":true}')
    _write(root / "main.py", "source must not be selected")
    return root


def _offline(_: Path) -> backup.ServiceState:
    return backup.ServiceState.OFFLINE


def _create(root: Path, tmp_path: Path) -> Path:
    target = tmp_path / "backups" / "snapshot"
    target.parent.mkdir(parents=True)
    result = backup.create_snapshot(root, target, protection_mode="protected_volume", get_service_state=_offline)
    assert result["ok"] is True
    return target


def _rewrite_manifest_checksum(snapshot: Path) -> None:
    digest = hashlib.sha256((snapshot / backup.MANIFEST_NAME).read_bytes()).hexdigest()
    (snapshot / backup.MANIFEST_CHECKSUM_NAME).write_text(digest + "\n", encoding="ascii")


def test_create_offline_snapshot_and_manifest_excludes_content_and_caches(tmp_path: Path):
    root = _install(tmp_path)
    target = _create(root, tmp_path)

    manifest_text = (target / backup.MANIFEST_NAME).read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    paths = {entry["path"] for entry in manifest["files"]}
    assert manifest["manifest_version"] == 1
    assert manifest["product_version"] == "v1.2.3"
    assert manifest["protection_mode"] == "protected_volume"
    assert "data/runtime/memory/character/owner/history.json" in paths
    assert "userdata/characters/cards/private.json" in paths
    assert "characters/legacy.json" in paths
    assert "characters/default.json" not in paths
    assert "main.py" not in paths
    assert "data/runtime/memory/character/owner/vector_store.db" not in paths
    assert "data/logs/error.log" not in paths
    assert "test-token-should-never-appear" not in manifest_text
    assert "private memory body" not in manifest_text
    assert backup.verify_snapshot(target) == {"ok": True, "errors": []}


@pytest.mark.parametrize("state, code", [(backup.ServiceState.RUNNING, "service_running"), (backup.ServiceState.UNKNOWN, "service_state_unknown")])
def test_create_rejects_running_or_unknown_service(tmp_path: Path, state: backup.ServiceState, code: str):
    root = _install(tmp_path)
    target = tmp_path / "backups" / "snapshot"
    target.parent.mkdir()
    with pytest.raises(backup.BackupError) as raised:
        backup.create_snapshot(root, target, protection_mode="protected_volume", get_service_state=lambda _: state)
    assert raised.value.code == code
    assert not target.exists()


def test_windows_marker_pid_alive_is_running_not_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    marker = tmp_path / "service_state.json"
    marker.write_text(
        json.dumps({"pid": os.getpid(), "installation_root": str(tmp_path.resolve())}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        backup,
        "paths_for_installation",
        lambda _installation: SimpleNamespace(service_state=lambda: marker),
    )
    monkeypatch.setattr(backup.os, "name", "nt")
    monkeypatch.setattr(backup, "_pid_is_alive", lambda pid: True if pid == os.getpid() else None)

    assert backup.service_state(tmp_path) is backup.ServiceState.RUNNING


def test_windows_stale_marker_falls_through_to_offline_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    marker = tmp_path / "service_state.json"
    marker.write_text(
        json.dumps({"pid": 1, "installation_root": str(tmp_path.resolve())}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        backup,
        "paths_for_installation",
        lambda _installation: SimpleNamespace(service_state=lambda: marker),
    )
    monkeypatch.setattr(backup.os, "name", "nt")
    monkeypatch.setattr(backup, "_pid_is_alive", lambda _pid: False)

    class FakeCompleted:
        returncode = 0
        stdout = "[]"
        stderr = ""

    monkeypatch.setattr(backup.subprocess, "run", lambda *args, **kwargs: FakeCompleted())
    assert backup.service_state(tmp_path) is backup.ServiceState.OFFLINE


def test_linux_service_scan_ignores_backup_process_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    current = SimpleNamespace(
        pid=os.getpid(),
        info={"name": "python", "cmdline": ["python", "main.py", "backup-state", "create"]},
    )
    fake_psutil = SimpleNamespace(
        process_iter=lambda _attrs: [current],
        AccessDenied=RuntimeError,
        NoSuchProcess=RuntimeError,
        Error=RuntimeError,
    )
    monkeypatch.setattr(
        backup,
        "paths_for_installation",
        lambda _installation: SimpleNamespace(service_state=lambda: tmp_path / "missing-marker"),
    )
    monkeypatch.setattr(backup.os, "name", "posix")
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert backup.service_state(tmp_path) is backup.ServiceState.OFFLINE


def test_linux_service_scan_keeps_other_relative_main_process_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    other = SimpleNamespace(
        pid=os.getpid() + 1,
        info={"name": "python", "cmdline": ["python", "main.py"]},
    )
    fake_psutil = SimpleNamespace(
        process_iter=lambda _attrs: [other],
        AccessDenied=RuntimeError,
        NoSuchProcess=RuntimeError,
        Error=RuntimeError,
    )
    monkeypatch.setattr(
        backup,
        "paths_for_installation",
        lambda _installation: SimpleNamespace(service_state=lambda: tmp_path / "missing-marker"),
    )
    monkeypatch.setattr(backup.os, "name", "posix")
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert backup.service_state(tmp_path) is backup.ServiceState.UNKNOWN


def test_missing_required_root_and_optional_files(tmp_path: Path):
    root = _install(tmp_path)
    (root / "data").rename(root / "missing-data")
    target = tmp_path / "backups" / "snapshot"
    target.parent.mkdir(parents=True)
    with pytest.raises(backup.BackupError) as raised:
        backup.create_snapshot(root, target, protection_mode="protected_volume", get_service_state=_offline)
    assert raised.value.code == "missing_required_root"

    root = _install(tmp_path / "optional")
    target = _create(root, tmp_path / "optional")
    manifest = json.loads((target / backup.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert "secrets.local.yaml" in manifest["optional_missing_files"]


@pytest.mark.parametrize("relative", ["inside", "data/inside", "userdata/inside"])
def test_output_cannot_be_inside_installation_or_protected_roots(tmp_path: Path, relative: str):
    root = _install(tmp_path)
    with pytest.raises(backup.BackupError) as raised:
        backup.create_snapshot(root, root / relative, protection_mode="protected_volume", get_service_state=_offline)
    assert raised.value.code == "output_inside_installation"


def test_verify_detects_file_and_manifest_tampering(tmp_path: Path):
    root = _install(tmp_path)
    target = _create(root, tmp_path)
    (target / "userdata" / "characters" / "cards" / "private.json").write_text("changed", encoding="utf-8")
    assert backup.verify_snapshot(target)["errors"][0]["code"] in {"size_mismatch", "hash_mismatch"}

    target = _create(root, tmp_path / "second")
    manifest_path = target / backup.MANIFEST_NAME
    manifest_path.write_text(manifest_path.read_text(encoding="utf-8").replace("v1.2.3", "v9.9.9"), encoding="utf-8")
    assert backup.verify_snapshot(target)["errors"][0]["code"] == "hash_mismatch"


def test_verify_rejects_path_traversal_and_extra_file(tmp_path: Path):
    root = _install(tmp_path)
    target = _create(root, tmp_path)
    manifest_path = target / backup.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../outside"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _rewrite_manifest_checksum(target)
    assert backup.verify_snapshot(target)["errors"][0]["code"] == "unsafe_path"

    target = _create(root, tmp_path / "second")
    _write(target / "unexpected.txt", "extra")
    assert backup.verify_snapshot(target)["errors"][0]["code"] == "manifest_invalid"


def test_create_rejects_reparse_points_in_protected_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _install(tmp_path)
    target = tmp_path / "backups" / "snapshot"
    target.parent.mkdir(parents=True)
    original = backup._is_reparse_point
    monkeypatch.setattr(backup, "_is_reparse_point", lambda path: path.name == "private.json" or original(path))
    with pytest.raises(backup.BackupError) as raised:
        backup.create_snapshot(root, target, protection_mode="protected_volume", get_service_state=_offline)
    assert raised.value.code == "unsafe_link"


def test_unclassified_root_and_copy_failure_do_not_publish_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _install(tmp_path)
    target = tmp_path / "backups" / "snapshot"
    target.parent.mkdir(parents=True)
    monkeypatch.setattr(backup, "UNCLASSIFIED_PRIVATE_ROOTS", ("future_private_root",))
    with pytest.raises(backup.BackupError) as raised:
        backup.create_snapshot(root, target, protection_mode="protected_volume", get_service_state=_offline)
    assert raised.value.code == "unclassified_private_root"
    monkeypatch.setattr(backup, "UNCLASSIFIED_PRIVATE_ROOTS", ())
    def broken_copy(*_args, **_kwargs):
        raise OSError("copy failed")
    monkeypatch.setattr(backup.shutil, "copy2", broken_copy)
    with pytest.raises(OSError):
        backup.create_snapshot(root, target, protection_mode="protected_volume", get_service_state=_offline)
    assert not target.exists()
    assert not list(target.parent.glob(".snapshot.tmp-*"))


def test_create_runs_internal_verify_and_requires_explicit_protected_volume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = _install(tmp_path)
    target = tmp_path / "backups" / "snapshot"
    target.parent.mkdir(parents=True)
    with pytest.raises(backup.BackupError) as raised:
        backup.create_snapshot(root, target, protection_mode="", get_service_state=_offline)
    assert raised.value.code == "encryption_required"

    called = False
    original = backup.verify_snapshot
    def checked(path: Path):
        nonlocal called
        called = True
        return original(path)
    monkeypatch.setattr(backup, "verify_snapshot", checked)
    backup.create_snapshot(root, target, protection_mode="protected_volume", get_service_state=_offline)
    assert called is True
