from __future__ import annotations

import asyncio

import pytest


def _config(monkeypatch, root, *, enabled=True):
    cfg = {
        "process_runner": {"enabled": enabled},
        "workspace_access": {
            "enabled": True,
            "roots": [str(root)],
            "permissions": {"read": True, "list": True},
        },
    }
    monkeypatch.setattr("core.config_loader.get_config", lambda: cfg)
    monkeypatch.setattr("core.agent_runtime.workspace._project_data", lambda: root / "project-data")
    return cfg


def _script(root, name, body):
    path = root / name
    path.write_text(body, encoding="utf-8")
    return name


def test_process_runner_success_and_metadata_redaction(monkeypatch, tmp_path, sandbox):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root)
    name = _script(root, "ok.py", "print('hello')")
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.process_runner import run_process_task

    result = asyncio.run(run_process_task(TaskPrincipal.reality("proc-owner", "proc-char"), program=name, idempotency_key="ok"))
    assert result["receipt"]["status"] == "succeeded"
    assert result["stdout"].strip() == "hello"
    assert "hello" not in str(result["receipt"])


def test_process_runner_rejects_escape_network_and_remote(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root)
    _script(root, "net.py", "import socket; socket.socket()")
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.process_runner import ProcessRunnerError, run_process_task

    principal = TaskPrincipal.reality("proc-owner", "proc-char")
    with pytest.raises(ProcessRunnerError, match="program_path_denied"):
        asyncio.run(run_process_task(principal, program="../outside.py", idempotency_key="escape"))
    result = asyncio.run(run_process_task(principal, program="net.py", idempotency_key="net"))
    assert result["receipt"]["status"] == "failed"
    monkeypatch.setattr("core.deployment_capabilities.is_remote_server", lambda: True)
    with pytest.raises(ProcessRunnerError, match="disabled_remote_server"):
        asyncio.run(run_process_task(principal, program="net.py", idempotency_key="remote"))


def test_process_runner_timeout_and_output_truncation(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root)
    _script(root, "slow.py", "import time; time.sleep(2)")
    _script(root, "loud.py", "print('x' * 10000)")
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.process_runner import ProcessLimits, run_process_task

    principal = TaskPrincipal.reality("proc-owner", "proc-char")
    timed = asyncio.run(run_process_task(principal, program="slow.py", idempotency_key="slow", limits=ProcessLimits(wall_seconds=1)))
    assert timed["receipt"]["status"] == "failed"
    assert timed["receipt"]["error_code"] == "process_timeout"
    loud = asyncio.run(run_process_task(principal, program="loud.py", idempotency_key="loud", limits=ProcessLimits(output_bytes=32)))
    assert loud["truncated"] is True
    assert loud["receipt"]["result_metadata"]["truncated"] is True
