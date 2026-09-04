from __future__ import annotations

import asyncio
import json
import time

import pytest


class _ConfirmedSession:
    WAITING_CONFIRM = "waiting_confirm"
    status = WAITING_CONFIRM


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
    _script(root, "private.py", "print(open('project-data/private.txt').read())")
    private = root / "project-data"
    private.mkdir()
    (private / "private.txt").write_text("private", encoding="utf-8")
    denied = asyncio.run(run_process_task(principal, program="private.py", idempotency_key="private"))
    assert denied["receipt"]["status"] == "failed"
    assert "private\n" not in denied["stdout"]
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


def test_process_runner_cancel_resource_limit_and_restart_unknown(monkeypatch, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root)
    _script(root, "slow.py", "import time; time.sleep(5)")
    _script(root, "disk.py", "open('artifact.txt', 'w').write('x' * 4096)")
    from core.agent_runtime import TaskPrincipal, task_store
    from core.agent_runtime.process_runner import (
        PROCESS_CAPABILITY,
        ProcessLimits,
        create_process_task,
        run_process,
        run_process_task,
    )
    from core.agent_runtime.task_manager import claim_next, get_task, request_cancel

    principal = TaskPrincipal.reality("proc-owner", "proc-char")

    async def cancel_running():
        receipt, _ = create_process_task(principal, program="slow.py", idempotency_key="cancel")
        lease = claim_next(principal, task_id=receipt["task_id"], capabilities={PROCESS_CAPABILITY})
        running = asyncio.create_task(run_process(principal, program="slow.py", task_id=receipt["task_id"], lease=lease))
        await asyncio.sleep(0.2)
        request_cancel(principal, receipt["task_id"])
        return await running

    canceled = asyncio.run(cancel_running())
    assert canceled["receipt"]["status"] == "canceled"

    limited = asyncio.run(run_process_task(
        principal,
        program="disk.py",
        idempotency_key="disk-limit",
        limits=ProcessLimits(disk_bytes=1),
    ))
    assert limited["receipt"]["status"] == "failed"
    assert limited["receipt"]["error_code"] == "process_resource_limit"

    pending, _ = create_process_task(principal, program="slow.py", idempotency_key="unknown")
    assert claim_next(principal, task_id=pending["task_id"], capabilities={PROCESS_CAPABILITY}) is not None
    task_store.reset_process_instance_for_tests("replacement-process")
    try:
        recovered = get_task(principal, pending["task_id"])
        assert recovered["status"] == "outcome_unknown"
        assert recovered["error_code"] == "worker_process_lost"
    finally:
        task_store.reset_process_instance_for_tests()


@pytest.mark.asyncio
async def test_process_run_tool_uses_reality_scope(monkeypatch, tmp_path, sandbox):
    root = tmp_path / "workspace"
    root.mkdir()
    _config(monkeypatch, root)
    _script(root, "tool.py", "print('tool-ok')")
    sandbox.meta_mode().parent.mkdir(parents=True, exist_ok=True)
    sandbox.meta_mode().write_text(
        json.dumps({"mode": "danger", "expires_at": time.time() + 60}),
        encoding="utf-8",
    )
    from core import tool_dispatcher

    output, confirmation = await tool_dispatcher.execute(
        "process_run",
        {"program": "tool.py"},
        "proc-owner",
        "proc-owner",
        False,
        _ConfirmedSession(),
        origin="assistant_loop",
        char_id="proc-char",
    )
    assert confirmation is None
    assert "tool-ok" in output
