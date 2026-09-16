from pathlib import Path
from types import SimpleNamespace
import hashlib
import zipfile

import pytest

import scripts.update_release as updater


def test_update_script_guards_running_service_dirty_tree_and_release_package():
    script = Path("AA更新.bat").read_text(encoding="utf-8")
    assert 'if not exist ".git" goto :release_package' in script
    assert 'findstr /I /C:"main.py"' in script
    assert "git status --porcelain" in script
    assert "输入 Y 继续" in script
    assert ":pull_failed" in script
    assert "data、config.yaml 或 secrets" in script
    assert '".venv\\Scripts\\python.exe" scripts\\update_release.py' in script
    assert script.index('findstr /I /C:"main.py"') < script.index('if not exist ".git" goto :release_package')


def test_release_package_protected_paths_are_never_overwritten():
    protected = {
        "data/runtime/state.json",
        "userdata/characters/cards/custom.json",
        "config.yaml",
        "secrets.local.yaml",
        ".venv/Scripts/python.exe",
        "tools/uv.exe",
        "tools/uv",
    }
    ordinary = {"main.py", "tools/helper.exe", "scripts/update_release.py"}

    assert all(updater.is_protected_relative_path(Path(path)) for path in protected)
    assert not any(updater.is_protected_relative_path(Path(path)) for path in ordinary)


def test_sha256_validation_and_release_menu_parsing(tmp_path):
    asset = tmp_path / "PresenceKit-v1.2.3-win64-setup.zip"
    asset.write_bytes(b"release payload")
    digest = updater.sha256_file(asset)
    checksum = tmp_path / "asset.sha256"
    checksum.write_text(f"{digest}  {asset.name}\n", encoding="utf-8")

    updater.verify_sha256(asset, checksum)
    checksum.write_text("0" * 64 + "  wrong.zip\n", encoding="utf-8")
    try:
        updater.verify_sha256(asset, checksum)
    except updater.UpdateError as exc:
        assert "SHA256" in str(exc)
    else:
        raise AssertionError("wrong digest must fail verification")

    releases = [
        {"tag_name": "v2.0.0", "assets": []},
        {"tag_name": "v1.9.0", "assets": []},
    ]
    assert updater.parse_release_choice("", releases) == 0
    assert updater.parse_release_choice("2", releases) == 1
    for invalid in ("0", "3", "hello"):
        try:
            updater.parse_release_choice(invalid, releases)
        except updater.UpdateError:
            pass
        else:
            raise AssertionError(f"{invalid!r} must not select a release")


def test_release_assets_prefer_host_platform_zip(monkeypatch):
    release = {
        "tag_name": "v1.1.0",
        "assets": [
            {"name": "PresenceKit-v1.1.0-win64-setup.zip"},
            {"name": "PresenceKit-v1.1.0-win64-setup.zip.sha256"},
            {"name": "PresenceKit-v1.1.0-macos-arm64-setup.zip"},
            {"name": "PresenceKit-v1.1.0-macos-arm64-setup.zip.sha256"},
            {"name": "PresenceKit-v1.1.0-macos-x64-setup.zip"},
            {"name": "PresenceKit-v1.1.0-macos-x64-setup.zip.sha256"},
        ],
    }
    monkeypatch.setattr(updater, "current_package_suffix", lambda: "macos-arm64-setup")
    zip_asset, checksum_asset = updater._release_assets(release)
    assert zip_asset["name"] == "PresenceKit-v1.1.0-macos-arm64-setup.zip"
    assert checksum_asset["name"] == "PresenceKit-v1.1.0-macos-arm64-setup.zip.sha256"

    monkeypatch.setattr(updater, "current_package_suffix", lambda: "win64-setup")
    zip_asset, checksum_asset = updater._release_assets(release)
    assert zip_asset["name"] == "PresenceKit-v1.1.0-win64-setup.zip"


def test_fetch_releases_uses_mocked_network_response(tmp_path, monkeypatch):
    payload = [
        {"tag_name": "v2.0.0", "draft": False, "assets": []},
        {"tag_name": "v1.0.0-draft", "draft": True, "assets": []},
    ]

    def fake_download(url, destination, opener):
        assert url == updater.RELEASES_URL
        destination.write_text(__import__("json").dumps(payload), encoding="utf-8")

    monkeypatch.setattr(updater, "download", fake_download)
    releases = updater.fetch_releases(tmp_path)

    assert [release["tag_name"] for release in releases] == ["v2.0.0"]


def test_apply_release_keeps_private_paths_and_backs_up_replaced_program_files(tmp_path):
    install = tmp_path / "PresenceKit"
    install.mkdir()
    (install / "VERSION").write_text("v1.0.0\n", encoding="utf-8")
    (install / "main.py").write_text("old program", encoding="utf-8")
    (install / "config.yaml").write_text("private config", encoding="utf-8")
    (install / "data").mkdir()
    (install / "data" / "state.json").write_text("private state", encoding="utf-8")
    (install / "userdata").mkdir()
    (install / "userdata" / "card.json").write_text("private card", encoding="utf-8")
    source = tmp_path / "staged-release"
    source.mkdir()
    (source / "VERSION").write_text("v2.0.0\n", encoding="utf-8")
    (source / "main.py").write_text("new program", encoding="utf-8")
    (source / "config.yaml").write_text("release config", encoding="utf-8")
    (source / "data").mkdir()
    (source / "data" / "state.json").write_text("release state", encoding="utf-8")

    backup = updater.apply_release(install, source, "v1.0.0")

    assert (install / "main.py").read_text(encoding="utf-8") == "new program"
    assert (install / "VERSION").read_text(encoding="utf-8") == "v2.0.0\n"
    assert (install / "config.yaml").read_text(encoding="utf-8") == "private config"
    assert (install / "data" / "state.json").read_text(encoding="utf-8") == "private state"
    assert (install / "userdata" / "card.json").read_text(encoding="utf-8") == "private card"
    assert (backup / "main.py").read_text(encoding="utf-8") == "old program"
    assert (backup / updater.BACKUP_MANIFEST_NAME).is_file()


def test_bundled_update_replaces_release_assets_without_touching_legacy_files(tmp_path):
    install = tmp_path / "PresenceKit"
    install.mkdir()
    (install / "characters").mkdir()
    (install / "characters" / "default.json").write_text("old default", encoding="utf-8")
    (install / "characters" / "private.json").write_text("keep me", encoding="utf-8")
    source = tmp_path / "staged-release"
    (source / "bundled").mkdir(parents=True)
    (source / "bundled" / "marker.txt").write_text("bundled", encoding="utf-8")

    backup = updater.apply_release(install, source, "v1.0.0")

    assert (install / "bundled" / "marker.txt").read_text(encoding="utf-8") == "bundled"
    assert (install / "characters" / "default.json").read_text(encoding="utf-8") == "old default"
    assert (install / "characters" / "private.json").read_text(encoding="utf-8") == "keep me"
    assert (backup / "characters" / "private.json").read_text(encoding="utf-8") == "keep me"


def test_bundled_update_removes_source_only_release_assets(tmp_path):
    install = tmp_path / "PresenceKit"
    (install / "bundled").mkdir(parents=True)
    (install / "bundled" / "source-only.txt").write_text("old", encoding="utf-8")
    source = tmp_path / "staged-release"
    (source / "bundled").mkdir(parents=True)
    (source / "bundled" / "target-only.txt").write_text("new", encoding="utf-8")

    backup = updater.apply_release(install, source, "v1.0.0")

    assert not (install / "bundled" / "source-only.txt").exists()
    assert (install / "bundled" / "target-only.txt").read_text(encoding="utf-8") == "new"
    assert (backup / "bundled" / "source-only.txt").read_text(encoding="utf-8") == "old"


def test_copy_failure_keeps_complete_backup_and_rolls_back_replaced_files(tmp_path):
    install = tmp_path / "PresenceKit"
    install.mkdir()
    (install / "VERSION").write_text("v1.0.0\n", encoding="utf-8")
    (install / "main.py").write_text("old program", encoding="utf-8")
    (install / "zzzz_injected_failure").mkdir()
    source = tmp_path / "staged-release"
    source.mkdir()
    (source / "main.py").write_text("new program", encoding="utf-8")
    # A file cannot atomically replace this source-only directory.  The name
    # sorts after main.py, proving rollback runs after at least one replacement.
    (source / "zzzz_injected_failure").write_text("trigger", encoding="utf-8")

    with pytest.raises(updater.UpdateError, match="_update_backup_v1.0.0"):
        updater.apply_release(install, source, "v1.0.0")

    assert (install / "main.py").read_text(encoding="utf-8") == "old program"
    assert (install / "zzzz_injected_failure").is_dir()
    assert (install / "_update_backup_v1.0.0" / "main.py").read_text(encoding="utf-8") == "old program"


def test_restore_keeps_protected_virtualenv_and_private_state(tmp_path):
    install = tmp_path / "PresenceKit"
    install.mkdir()
    (install / "main.py").write_text("target program", encoding="utf-8")
    (install / ".venv" / "Scripts").mkdir(parents=True)
    (install / ".venv" / "Scripts" / "python.exe").write_text("running python", encoding="utf-8")
    (install / "data").mkdir()
    (install / "data" / "state.json").write_text("private state", encoding="utf-8")
    backup = install / "_update_backup_v1.0.0"
    backup.mkdir()
    (backup / "main.py").write_text("source program", encoding="utf-8")
    (backup / ".venv" / "Scripts").mkdir(parents=True)
    (backup / ".venv" / "Scripts" / "python.exe").write_text("old python", encoding="utf-8")
    (backup / "data").mkdir()
    (backup / "data" / "state.json").write_text("old private state", encoding="utf-8")
    updater._write_backup_manifest(backup, "v1.0.0")
    (install / "_update_tmp" / "package").mkdir(parents=True)

    updater.restore_installation_from_backup(install, backup)

    assert (install / "main.py").read_text(encoding="utf-8") == "source program"
    assert (install / ".venv" / "Scripts" / "python.exe").read_text(encoding="utf-8") == "running python"
    assert (install / "data" / "state.json").read_text(encoding="utf-8") == "private state"
    assert not (install / "_update_tmp").exists()


def test_restore_rejects_incomplete_backup_before_touching_program_files(tmp_path):
    install = tmp_path / "PresenceKit"
    install.mkdir()
    (install / "main.py").write_text("target program", encoding="utf-8")
    backup = install / "_update_backup_v1.0.0"
    backup.mkdir()
    source = backup / "main.py"
    source.write_text("source program", encoding="utf-8")
    updater._write_backup_manifest(backup, "v1.0.0")
    source.unlink()

    with pytest.raises(updater.UpdateError, match="不完整"):
        updater.restore_installation_from_backup(install, backup)

    assert (install / "main.py").read_text(encoding="utf-8") == "target program"


def test_offline_release_rehearsal_updates_program_but_keeps_private_files(tmp_path, monkeypatch):
    install = tmp_path / "PresenceKit"
    install.mkdir()
    (install / "VERSION").write_text("v1.0.0\n", encoding="utf-8")
    (install / "main.py").write_text("old", encoding="utf-8")
    (install / "config.yaml").write_text("private", encoding="utf-8")
    (install / "data").mkdir()
    (install / "data" / "layout_version.json").write_text(
        '{"product_baseline":"v1","data_layout_schema_version":1,"first_initialized_version":"v1.0.0"}',
        encoding="utf-8",
    )
    (install / "data" / "history.json").write_text("keep", encoding="utf-8")
    archive = tmp_path / "PresenceKit-v1.1.0-win64-setup.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("VERSION", "v1.1.0\n")
        package.writestr("main.py", "new")
        package.writestr("config.yaml", "release config")
        package.writestr("data/history.json", "release state")
        package.writestr("scripts/update_release.py", "# packaged updater\n")
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n", encoding="utf-8")
    monkeypatch.setattr(updater, "_service_is_running", lambda: False)

    updater.update(
        install,
        SimpleNamespace(
            source_zip=str(archive), sha256_file=str(checksum), target_version="v1.1.0",
            yes=True, skip_sync=True,
        ),
    )

    assert (install / "VERSION").read_text(encoding="utf-8") == "v1.1.0\n"
    assert (install / "main.py").read_text(encoding="utf-8") == "new"
    assert (install / "config.yaml").read_text(encoding="utf-8") == "private"
    assert (install / "data" / "history.json").read_text(encoding="utf-8") == "keep"


def test_offline_rejections_do_not_create_staging_before_validation(tmp_path, monkeypatch):
    install = tmp_path / "PresenceKit"
    install.mkdir()
    (install / "VERSION").write_text("v0.9.9\n", encoding="utf-8")
    archive = tmp_path / "PresenceKit-v1.0.0-win64-setup.zip"
    archive.write_bytes(b"not inspected when source baseline is unsupported")
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{updater.sha256_file(archive)}  {archive.name}\n", encoding="utf-8")
    monkeypatch.setattr(updater, "_service_is_running", lambda: False)

    with pytest.raises(updater.UpdateError, match="v0"):
        updater.update(
            install,
            SimpleNamespace(
                source_zip=str(archive), sha256_file=str(checksum), target_version="v1.0.0",
                yes=True, skip_sync=True,
            ),
        )

    assert not (install / "_update_tmp").exists()


def test_offline_sha_failure_does_not_create_staging(tmp_path, monkeypatch):
    install = tmp_path / "PresenceKit"
    install.mkdir()
    (install / "VERSION").write_text("v1.0.0\n", encoding="utf-8")
    (install / "data").mkdir()
    (install / "data" / "layout_version.json").write_text(
        '{"product_baseline":"v1","data_layout_schema_version":1}', encoding="utf-8",
    )
    archive = tmp_path / "PresenceKit-v1.0.1-win64-setup.zip"
    archive.write_bytes(b"corrupt payload")
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text("0" * 64 + f"  {archive.name}\n", encoding="utf-8")
    monkeypatch.setattr(updater, "_service_is_running", lambda: False)

    with pytest.raises(updater.UpdateError, match="SHA256"):
        updater.update(
            install,
            SimpleNamespace(
                source_zip=str(archive), sha256_file=str(checksum), target_version="v1.0.1",
                yes=True, skip_sync=True,
            ),
        )

    assert not (install / "_update_tmp").exists()
