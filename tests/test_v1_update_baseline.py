"""v1-only update and data-layout baseline contracts."""
from __future__ import annotations

import hashlib
import json
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.layout_baseline import (
    DATA_LAYOUT_SCHEMA_VERSION,
    LayoutBaselineError,
    ensure_v1_layout_baseline,
)
import scripts.update_release as updater


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_project_declares_the_v1_baseline():
    with Path("pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]
    assert version.startswith("1."), version


def _installation_digest(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.relative_to(root).parts[0].startswith("_update_backup_")
    }


def _write_layout(install: Path, schema: int = DATA_LAYOUT_SCHEMA_VERSION) -> None:
    marker = install / "data" / "layout_version.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "product_baseline": "v1",
        "data_layout_schema_version": schema,
        "first_initialized_version": "v1.0.0",
    }), encoding="utf-8")


def _v1_install(tmp_path: Path, version: str = "v1.0.0") -> Path:
    install = tmp_path / "PresenceKit"
    install.mkdir()
    (install / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (install / "main.py").write_text("old program", encoding="utf-8")
    (install / "bundled" / "asset.txt").parent.mkdir(parents=True)
    (install / "bundled" / "asset.txt").write_text("old bundled", encoding="utf-8")
    (install / "userdata" / "characters").mkdir(parents=True)
    (install / "userdata" / "characters" / "card.json").write_text('{"private": true}', encoding="utf-8")
    (install / "config.yaml").write_text("private: config", encoding="utf-8")
    (install / "secrets.local.yaml").write_text("private: secret", encoding="utf-8")
    _write_layout(install)
    (install / "data" / "state.json").write_text('{"private": true}', encoding="utf-8")
    return install


def _release(tmp_path: Path, version: str) -> tuple[Path, Path]:
    archive = tmp_path / f"PresenceKit-{version}-win64-setup.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("VERSION", f"{version}\n")
        package.writestr("main.py", f"program {version}")
        package.writestr("bundled/asset.txt", f"bundled {version}")
        package.writestr("scripts/update_release.py", "# packaged updater\n")
        package.writestr("data/layout_version.json", "release must not overwrite private data")
        package.writestr("userdata/characters/card.json", "release must not overwrite private assets")
        package.writestr("config.yaml", "release must not overwrite config")
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{updater.sha256_file(archive)}  {archive.name}\n", encoding="utf-8")
    return archive, checksum


def _run_update(install: Path, archive: Path, checksum: Path, target: str) -> None:
    updater.update(install, SimpleNamespace(
        source_zip=str(archive), sha256_file=str(checksum), target_version=target,
        yes=True, skip_sync=True,
    ))


def test_fresh_v1_initialization_creates_one_baseline_marker(tmp_path, monkeypatch):
    import core.sandbox as sandbox

    paths = sandbox.DataPaths(mode="test", test_session_id="v1_baseline")
    paths._base = tmp_path / "data"
    monkeypatch.setattr(sandbox, "_instance", paths)
    (tmp_path / "VERSION").write_text("v1.0.0\n", encoding="utf-8")

    assert ensure_v1_layout_baseline(installation_root=tmp_path) is True
    marker = json.loads(paths.layout_version().read_text(encoding="utf-8"))
    assert marker == {
        "product_baseline": "v1",
        "data_layout_schema_version": DATA_LAYOUT_SCHEMA_VERSION,
        "first_initialized_version": "v1.0.0",
    }
    assert ensure_v1_layout_baseline(installation_root=tmp_path) is False
    assert json.loads(paths.layout_version().read_text(encoding="utf-8")) == marker


@pytest.mark.parametrize("target", ["v1.0.1", "v1.1.0"])
def test_v1_forward_update_keeps_protected_data_and_replaces_bundled(tmp_path, monkeypatch, target):
    install = _v1_install(tmp_path)
    protected_before = {
        name: _tree_digest(install / name) if (install / name).is_dir()
        else hashlib.sha256((install / name).read_bytes()).hexdigest()
        for name in ("data", "userdata", "config.yaml", "secrets.local.yaml")
    }
    archive, checksum = _release(tmp_path, target)
    monkeypatch.setattr(updater, "_service_is_running", lambda: False)

    _run_update(install, archive, checksum, target)

    assert (install / "VERSION").read_text(encoding="utf-8") == f"{target}\n"
    assert (install / "bundled" / "asset.txt").read_text(encoding="utf-8") == f"bundled {target}"
    protected_after = {
        name: _tree_digest(install / name) if (install / name).is_dir()
        else hashlib.sha256((install / name).read_bytes()).hexdigest()
        for name in protected_before
    }
    assert protected_after == protected_before
    assert (install / "_update_backup_v1.0.0").is_dir()


def test_repeat_v1_update_is_idempotent(tmp_path, monkeypatch):
    install = _v1_install(tmp_path)
    archive, checksum = _release(tmp_path, "v1.0.1")
    monkeypatch.setattr(updater, "_service_is_running", lambda: False)

    _run_update(install, archive, checksum, "v1.0.1")
    before_repeat = _tree_digest(install)
    _run_update(install, archive, checksum, "v1.0.1")

    assert _tree_digest(install) == before_repeat


def test_restore_returns_the_pre_update_snapshot(tmp_path, monkeypatch):
    install = _v1_install(tmp_path)
    before = _installation_digest(install)
    archive, checksum = _release(tmp_path, "v1.1.0")
    monkeypatch.setattr(updater, "_service_is_running", lambda: False)

    _run_update(install, archive, checksum, "v1.1.0")
    updater.restore_installation_from_backup(install, install / "_update_backup_v1.0.0")

    assert _installation_digest(install) == before


@pytest.mark.parametrize("source", ["v0.2.2", "unknown", ""])
def test_pre_v1_and_unknown_sources_are_rejected_before_writes(tmp_path, source):
    install = _v1_install(tmp_path)
    if source != "unknown":
        (install / "VERSION").write_text(f"{source}\n", encoding="utf-8")
    before = _tree_digest(install)

    with pytest.raises(updater.UpdateError):
        updater.validate_v1_update(install, source, "v1.0.1")

    assert _tree_digest(install) == before


def test_higher_schema_and_downgrade_are_rejected_before_writes(tmp_path):
    install = _v1_install(tmp_path, version="v1.1.0")
    _write_layout(install, schema=DATA_LAYOUT_SCHEMA_VERSION + 1)
    before = _tree_digest(install)

    with pytest.raises(updater.UpdateError, match="schema 高于"):
        updater.validate_v1_update(install, "v1.1.0", "v1.1.1")
    assert _tree_digest(install) == before

    _write_layout(install)
    supported_schema_before = _tree_digest(install)
    with pytest.raises(updater.UpdateError, match="downgrade"):
        updater.validate_v1_update(install, "v1.1.0", "v1.0.1")
    assert _tree_digest(install) == supported_schema_before


def test_invalid_existing_baseline_refuses_runtime_initialization(tmp_path, monkeypatch):
    import core.sandbox as sandbox

    paths = sandbox.DataPaths(mode="test", test_session_id="bad_baseline")
    paths._base = tmp_path / "data"
    paths.layout_version().parent.mkdir(parents=True)
    paths.layout_version().write_text('{"product_baseline": "v1", "data_layout_schema_version": 99}', encoding="utf-8")
    monkeypatch.setattr(sandbox, "_instance", paths)

    with pytest.raises(LayoutBaselineError):
        ensure_v1_layout_baseline(installation_root=tmp_path)
