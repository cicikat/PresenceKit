from __future__ import annotations

import pytest


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
    from core.agent_runtime.workspace import list_workspace, read_workspace

    assert read_workspace("note.md") == "hello"
    assert any(item["name"] == "note.md" for item in list_workspace()["entries"])
    with pytest.raises(ValueError):
        read_workspace(str(tmp_path / "outside.md"))


def test_workspace_writes_require_independent_grants_and_confirmation(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root, create=True, update=True, delete=True)
    from core.agent_runtime.workspace import WorkspaceError, create_workspace, delete_workspace, update_workspace

    create_workspace("new.md", "one")
    with pytest.raises(WorkspaceError):
        update_workspace("new.md", "two")
    update_workspace("new.md", "two", confirmed=True)
    with pytest.raises(WorkspaceError):
        delete_workspace("new.md")
    delete_workspace("new.md", confirmed=True)


def test_workspace_rejects_sensitive_and_remote(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root, create=True)
    from core.agent_runtime.workspace import WorkspaceError, create_workspace

    with pytest.raises(WorkspaceError):
        create_workspace(".env", "x")
    monkeypatch.setattr("core.deployment_capabilities.is_remote_server", lambda: True)
    with pytest.raises(WorkspaceError):
        create_workspace("ok.md", "x")
