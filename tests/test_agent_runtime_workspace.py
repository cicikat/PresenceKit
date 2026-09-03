from __future__ import annotations

import hashlib

import pytest


class _Session:
    WAITING_CONFIRM = "waiting_confirm"
    IDLE = "idle"
    status = IDLE

    def set_waiting_confirm(self, _tool_name, _tool_args):
        self.status = self.WAITING_CONFIRM


def _config(monkeypatch, root, **permissions):
    from core.agent_runtime import workspace

    cfg = {
        "workspace_access": {
            "enabled": True,
            "roots": [str(root)],
            "permissions": {"read": True, "list": True, "create": False, "update": False, "delete": False, **permissions},
            "max_file_bytes": 100,
            "max_total_bytes": 1000,
            "max_concurrent_tasks": 1,
        }
    }
    monkeypatch.setattr("core.config_loader.get_config", lambda: cfg)
    monkeypatch.setattr(workspace, "_project_data", lambda: root / "project-data")


def test_workspace_read_and_list_are_scoped(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "note.md").write_text("hello", encoding="utf-8")
    _config(monkeypatch, root)
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.workspace import list_workspace, read_workspace
    principal = TaskPrincipal.reality("workspace-owner", "workspace-character")

    assert read_workspace(principal, "note.md") == "hello"
    assert any(item["name"] == "note.md" for item in list_workspace(principal)["entries"])
    with pytest.raises(ValueError):
        read_workspace(principal, str(tmp_path / "outside.md"))


def test_workspace_writes_require_independent_grants_and_confirmation(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root, create=True, update=True, delete=True)
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.workspace import WorkspaceError, create_workspace, delete_workspace, update_workspace
    principal = TaskPrincipal.reality("workspace-owner", "workspace-character")

    create_workspace(principal, "new.md", "one")
    with pytest.raises(WorkspaceError):
        update_workspace(principal, "new.md", "two")
    update_workspace(principal, "new.md", "two", confirmed=True)
    with pytest.raises(WorkspaceError):
        delete_workspace(principal, "new.md")
    delete_workspace(principal, "new.md", confirmed=True)


def test_workspace_delete_rejects_unsupported_or_oversized_files(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "binary.exe").write_bytes(b"x")
    (root / "large.md").write_text("x" * 101, encoding="utf-8")
    _config(monkeypatch, root, delete=True)
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.workspace import WorkspaceError, delete_workspace
    principal = TaskPrincipal.reality("workspace-owner", "workspace-character")

    with pytest.raises(WorkspaceError, match="unsupported_file_type"):
        delete_workspace(principal, "binary.exe", confirmed=True)
    with pytest.raises(WorkspaceError, match="file_size_limit_exceeded"):
        delete_workspace(principal, "large.md", confirmed=True)


def test_workspace_rejects_sensitive_and_remote(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root, create=True)
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.workspace import WorkspaceError, create_workspace
    principal = TaskPrincipal.reality("workspace-owner", "workspace-character")

    with pytest.raises(WorkspaceError):
        create_workspace(principal, ".env", "x")
    monkeypatch.setattr("core.deployment_capabilities.is_remote_server", lambda: True)
    with pytest.raises(WorkspaceError):
        create_workspace(principal, "ok.md", "x")


def test_workspace_enabled_without_roots_is_not_configured(monkeypatch):
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.workspace import WorkspaceError, capability_snapshot, read_workspace

    monkeypatch.setattr(
        "core.agent_runtime.workspace._cfg",
        lambda: {"enabled": True, "roots": [], "permissions": {"read": True}},
    )
    monkeypatch.setattr("core.agent_runtime.workspace._remote", lambda: False)
    snapshot = capability_snapshot()
    assert snapshot["enabled"] is False
    assert snapshot["desired_enabled"] is True
    assert snapshot["status"] == "not_configured"
    with pytest.raises(WorkspaceError, match="workspace_not_configured"):
        read_workspace(TaskPrincipal.reality("workspace-owner", "workspace-character"), "missing.txt")


def test_workspace_rejects_intermediate_symlink_and_dream(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    real = root / "real"
    real.mkdir()
    (real / "note.md").write_text("hidden", encoding="utf-8")
    link = root / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    _config(monkeypatch, root)
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.workspace import WorkspaceError, read_workspace

    with pytest.raises(WorkspaceError, match="symlink_denied"):
        read_workspace(TaskPrincipal.reality("owner", "character"), "link/note.md")
    with pytest.raises(WorkspaceError, match="realm_forbidden"):
        read_workspace(TaskPrincipal("owner", "character", "dream"), "real/note.md")


def test_workspace_versions_are_durable_and_undo_restores(monkeypatch, tmp_path, sandbox):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root, create=True, update=True)
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.workspace import create_workspace, undo_workspace, update_workspace
    principal = TaskPrincipal.reality("workspace-owner", "workspace-character")

    create_workspace(principal, "note.md", "one")
    update_workspace(principal, "note.md", "two", confirmed=True)
    version_root = sandbox.agent_runtime_workspace_versions_dir(principal.uid, char_id=principal.char_id)
    state = next(version_root.glob("*/state.json")).read_text(encoding="utf-8")
    assert str(root) not in state
    undo_workspace(principal, "note.md", confirmed=True)
    assert (root / "note.md").read_text(encoding="utf-8") == "one"


@pytest.mark.asyncio
async def test_workspace_tool_create_then_read_has_one_receipt(monkeypatch, tmp_path, sandbox):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root, create=True)
    from core import tool_dispatcher
    from core.agent_runtime.task_manager import observability_snapshot

    created, confirm = await tool_dispatcher.execute(
        "workspace_create", {"path": "result.md", "content": "finished"},
        "workspace-owner", "workspace-owner", False, _Session(),
        origin="assistant_loop", char_id="workspace-character",
    )
    assert confirm is None
    assert "result.md" in created
    read, _ = await tool_dispatcher.execute(
        "workspace_read", {"path": "result.md"},
        "workspace-owner", "workspace-owner", False, _Session(),
        origin="assistant_loop", char_id="workspace-character",
    )
    assert "finished" in read
    tasks = observability_snapshot(uid="workspace-owner", char_id="workspace-character")
    assert tasks["status_counts"] == {"succeeded": 1}
    assert tasks["entries"][0]["result_metadata"]["counters"]["version"] == 1


@pytest.mark.asyncio
async def test_workspace_duplicate_running_receipt_never_reexecutes(monkeypatch, tmp_path, sandbox):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root, create=True)
    from core import tool_dispatcher
    from core.agent_runtime import CausationRef, TaskPrincipal
    from core.agent_runtime.task_manager import claim_next, create_task

    principal = TaskPrincipal.reality("workspace-owner", "workspace-character")
    idem = hashlib.sha256("create\0pending.md\0content".encode("utf-8")).hexdigest()
    task, _ = create_task(
        principal, capability="workspace.create", source="tool", idempotency_key=idem,
        ttl_seconds=300, causation_ref=CausationRef("reality_turn", idem),
    )
    assert claim_next(principal, task_id=task["task_id"], capabilities={"workspace.create"}) is not None

    result, confirm = await tool_dispatcher.execute(
        "workspace_create", {"path": "pending.md", "content": "content"},
        "workspace-owner", "workspace-owner", False, _Session(),
        origin="assistant_loop", char_id="workspace-character",
    )
    assert confirm is None
    assert '"duplicate": true' in result
    assert not (root / "pending.md").exists()
