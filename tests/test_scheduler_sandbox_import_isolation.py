"""Scheduler persistence must bind to the sandbox selected after import."""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path


def _manifest(root: Path) -> dict[str, tuple[int, int, str]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _contains(root: Path, token: str) -> bool:
    return any(token in path.read_text(encoding="utf-8") for path in root.rglob("*") if path.is_file())


def _paths_at(base: Path, *, mode: str):
    import core.sandbox as sandbox_mod

    paths = sandbox_mod.DataPaths(mode=mode, test_session_id="scheduler-import-isolation")
    paths._base = base
    return paths


def test_scheduler_persistence_rebinds_after_import(monkeypatch, tmp_path):
    """A module imported before sandbox installation cannot write through its old root."""
    import core.sandbox as sandbox_mod

    # The fallback cwd is a private synthetic installation. A repository-wide
    # scan races other xdist workers' legitimate test_sandbox writes and would
    # also read private developer state during a local run.
    actual_data = tmp_path / "unbound-cwd" / "data"
    actual_data.mkdir(parents=True)
    (actual_data / "sentinel.json").write_text('{"synthetic":true}', encoding="utf-8")
    monkeypatch.chdir(actual_data.parent)
    actual_before = _manifest(actual_data)
    preimport_root = tmp_path / "preimport-production"
    sandbox_root = tmp_path / "sandbox-one"
    next_sandbox_root = tmp_path / "sandbox-two"

    monkeypatch.setattr(sandbox_mod, "_instance", _paths_at(preimport_root, mode="production"))
    loop = importlib.reload(importlib.import_module("core.scheduler.loop"))
    state_machine = importlib.reload(importlib.import_module("core.scheduler.state_machine"))
    ledger = importlib.reload(importlib.import_module("core.scheduler.proactive_ledger"))
    last_mentioned = importlib.reload(importlib.import_module("core.scheduler.last_mentioned"))

    assert not preimport_root.exists()

    monkeypatch.setattr(sandbox_mod, "_instance", _paths_at(sandbox_root, mode="test"))
    loop._mark("test_char")
    state_machine.notify_owner_turn("u1")
    last_mentioned.mark_topic_followed("owner1")
    ledger.record_send("test_char", gist="test_char")

    assert not preimport_root.exists()
    assert _contains(sandbox_root, "u1")
    assert _contains(sandbox_root, "owner1")
    assert _contains(sandbox_root, "test_char")
    assert not (sandbox_root / "runtime" / "proactive_recent.json").exists()
    assert not (sandbox_root / "runtime" / "proactive_recent.json.bak").exists()
    assert _manifest(actual_data) == actual_before

    monkeypatch.setattr(sandbox_mod, "_instance", _paths_at(next_sandbox_root, mode="test"))
    loop._mark("second_sandbox")
    state_machine.notify_owner_turn("u1")
    ledger.record_send("second_sandbox", gist="second_sandbox")

    assert not _contains(sandbox_root, "second_sandbox")
    assert _contains(next_sandbox_root, "second_sandbox")
    assert not (next_sandbox_root / "runtime" / "proactive_recent.json").exists()
    assert not (next_sandbox_root / "runtime" / "proactive_recent.json.bak").exists()
    assert _manifest(actual_data) == actual_before
