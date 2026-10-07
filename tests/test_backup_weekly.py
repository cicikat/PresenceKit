import zipfile

import pytest

from core import backup_state as backup
from scripts.backup_weekly import create_zip
from tests.test_backup_state import _install


def test_weekly_zip_is_restorable_snapshot(tmp_path, monkeypatch):
    root = _install(tmp_path)
    sandbox = root / "data" / "test_sandbox"
    sandbox.mkdir()
    (sandbox / "private-test.txt").write_text("not production")
    monkeypatch.setattr(backup, "service_state", lambda _: backup.ServiceState.OFFLINE)
    original = backup.create_snapshot
    monkeypatch.setattr(backup, "create_snapshot", lambda *a, **kw: original(*a, **kw, get_service_state=lambda _: backup.ServiceState.OFFLINE))
    output = create_zip(root)
    with zipfile.ZipFile(output) as archive:
        assert not any("test_sandbox" in name for name in archive.namelist())
        restored = tmp_path / "unpacked"
        archive.extractall(restored)
    assert backup.verify_snapshot(restored)["ok"]
    assert not list(root.glob("*.partial"))


def test_weekly_running_service_leaves_no_archive(tmp_path, monkeypatch):
    root = _install(tmp_path)
    def refuse(*args, **kwargs):
        raise backup.BackupError("service_running", "stop first")
    monkeypatch.setattr(backup, "create_snapshot", refuse)
    with pytest.raises(backup.BackupError, match="stop first"):
        create_zip(root)
    assert not list(root.glob("*.zip*"))
