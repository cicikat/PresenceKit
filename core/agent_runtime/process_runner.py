"""Bounded Reality process capability (Brief 234).

This adapter is intentionally separate from the coplay subprocess helpers.  A
caller must provide a Reality task lease and a program which is already inside
an explicitly configured workspace.  No shell is ever involved and process
output is returned to the caller only; task receipts contain metadata only.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import signal
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from core.agent_runtime.models import TaskLease, TaskPrincipal
from core.agent_runtime.task_manager import (
    CausationRef,
    RetryPolicy,
    TaskManagerError,
    claim_next,
    complete_task,
    create_task,
    fail_task,
    get_task,
    request_cancel,
    acknowledge_cancel,
)


PROCESS_CAPABILITY = "process.run"
PROCESS_SCHEMA_VERSION = "agent-runtime-process-runner.v1"
MAX_ARGS = 64
MAX_ARG_CHARS = 2048
MAX_PROGRAM_CHARS = 512
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_WALL_SECONDS = 15 * 60
MAX_CPU_SECONDS = 15 * 60
MAX_MEMORY_BYTES = 512 * 1024 * 1024
MAX_CHILD_PROCESSES = 16
MAX_DISK_BYTES = 100 * 1024 * 1024

_PYTHON_BOOTSTRAP = r'''
import os, socket, sys, sysconfig
root = os.path.realpath(sys.argv[1])
program = os.path.realpath(sys.argv[2])
sys.argv = [program, *sys.argv[3:]]
with open(program, "rb") as _source_file:
    _source = _source_file.read()

def within(path):
    try:
        return os.path.commonpath((root, os.path.realpath(os.fspath(path)))) == root
    except (TypeError, ValueError, OSError):
        return False

stdlib = os.path.realpath(sysconfig.get_paths().get("stdlib", sys.prefix))
def readable(path, mode):
    if within(path):
        return True
    try:
        return os.path.commonpath((stdlib, os.path.realpath(os.fspath(path)))) == stdlib and not any(flag in (mode or "r") for flag in ("w", "a", "+", "x"))
    except (TypeError, ValueError, OSError):
        return False

def audit(event, args):
    if event == "open":
        path = args[0] if args else None
        mode = args[1] if len(args) > 1 else "r"
        if isinstance(path, (str, bytes, os.PathLike)) and not readable(path, mode):
            raise PermissionError("process_workspace_boundary")
    if event in {"os.mkdir", "os.rmdir", "os.remove", "os.rename", "os.replace", "os.link", "os.symlink"}:
        paths = [item for item in (args or ()) if isinstance(item, (str, bytes, os.PathLike))]
        if any(not within(item) for item in paths):
            raise PermissionError("process_workspace_boundary")
    if event.startswith("socket."):
        raise PermissionError("process_network_disabled")
    if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.posix_spawnp", "ctypes.dlopen"}:
        raise PermissionError("process_child_denied")

sys.addaudithook(audit)
globals()["__file__"] = program
exec(compile(_source, program, "exec"), globals(), globals())
'''


class ProcessRunnerError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ProcessLimits:
    wall_seconds: int = 60
    cpu_seconds: int = 60
    memory_bytes: int = 256 * 1024 * 1024
    output_bytes: int = 256 * 1024
    max_processes: int = 4
    disk_bytes: int = 10 * 1024 * 1024

    def validate(self) -> "ProcessLimits":
        checks = (
            ("wall_seconds", self.wall_seconds, 1, MAX_WALL_SECONDS),
            ("cpu_seconds", self.cpu_seconds, 1, MAX_CPU_SECONDS),
            ("memory_bytes", self.memory_bytes, 1, MAX_MEMORY_BYTES),
            ("output_bytes", self.output_bytes, 1, MAX_OUTPUT_BYTES),
            ("max_processes", self.max_processes, 1, MAX_CHILD_PROCESSES),
            ("disk_bytes", self.disk_bytes, 1, MAX_DISK_BYTES),
        )
        for name, value, low, high in checks:
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise ProcessRunnerError(f"invalid_{name}")
        return self


def _cfg() -> dict[str, Any]:
    from core.config_loader import get_config

    value = get_config().get("process_runner", {})
    return value if isinstance(value, dict) else {}


def _workspace_cfg() -> dict[str, Any]:
    value = _cfg().get("workspace")
    if isinstance(value, dict):
        return value
    from core.config_loader import get_config

    value = get_config().get("workspace_access", {})
    return value if isinstance(value, dict) else {}


def _remote() -> bool:
    from core.deployment_capabilities import is_remote_server

    return is_remote_server()


def _enabled() -> bool:
    return bool(_cfg().get("enabled", False)) and not _remote()


def _limits(value: ProcessLimits | dict[str, Any] | None) -> ProcessLimits:
    if value is None:
        value = _cfg().get("limits") or {}
    if isinstance(value, ProcessLimits):
        return value.validate()
    if not isinstance(value, dict):
        raise ProcessRunnerError("invalid_limits")
    defaults = ProcessLimits()
    try:
        result = ProcessLimits(
            wall_seconds=int(value.get("wall_seconds", defaults.wall_seconds)),
            cpu_seconds=int(value.get("cpu_seconds", defaults.cpu_seconds)),
            memory_bytes=int(value.get("memory_bytes", defaults.memory_bytes)),
            output_bytes=int(value.get("output_bytes", defaults.output_bytes)),
            max_processes=int(value.get("max_processes", defaults.max_processes)),
            disk_bytes=int(value.get("disk_bytes", defaults.disk_bytes)),
        )
    except (TypeError, ValueError) as exc:
        raise ProcessRunnerError("invalid_limits") from exc
    return result.validate()


def _validate_principal(principal: TaskPrincipal) -> TaskPrincipal:
    if not isinstance(principal, TaskPrincipal) or principal.realm != "reality":
        raise ProcessRunnerError("realm_forbidden")
    return principal


def _validate_args(args: Iterable[str] | None) -> list[str]:
    if args is None:
        return []
    if isinstance(args, (str, bytes)):
        raise ProcessRunnerError("arguments_must_be_structured")
    try:
        values = list(args)
    except TypeError as exc:
        raise ProcessRunnerError("arguments_must_be_structured") from exc
    if len(values) > MAX_ARGS:
        raise ProcessRunnerError("argument_count_exceeded")
    clean: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value or len(value) > MAX_ARG_CHARS:
            raise ProcessRunnerError("invalid_argument")
        # Arguments are not parsed as paths by the runner.  Rejecting path
        # escapes still prevents the common accidental private-file access.
        candidate = Path(value)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ProcessRunnerError("argument_path_denied")
        clean.append(value)
    return clean


def _resolve_program(program: str) -> tuple[Path, Path]:
    if not isinstance(program, str) or not program.strip() or len(program) > MAX_PROGRAM_CHARS:
        raise ProcessRunnerError("invalid_program")
    if Path(program).is_absolute() or ".." in Path(program).parts:
        raise ProcessRunnerError("program_path_denied")
    if _remote():
        raise ProcessRunnerError("disabled_remote_server_local_capability")
    if not _enabled():
        raise ProcessRunnerError("process_runner_disabled")
    from core.agent_runtime import workspace

    try:
        workspace._permission("read")
        target, root = workspace._resolve(program)
    except Exception as exc:
        code = getattr(exc, "code", "program_path_denied")
        raise ProcessRunnerError(code) from exc
    if not target.is_file() or target.is_symlink() or target.suffix.lower() not in _allowed_extensions():
        raise ProcessRunnerError("program_not_allowed")
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ProcessRunnerError("program_path_denied") from exc
    return target, root


def _allowed_extensions() -> frozenset[str]:
    raw = _cfg().get("program_extensions")
    if not raw:
        return frozenset({".py"})
    values = {str(item).lower() for item in raw if isinstance(item, str)}
    return frozenset(item if item.startswith(".") else f".{item}" for item in values) or frozenset({".py"})


def _interpreter(name: str) -> list[str]:
    if not isinstance(name, str) or not name:
        raise ProcessRunnerError("invalid_interpreter")
    allowed = _cfg().get("interpreters") or ["python"]
    if not isinstance(allowed, list) or name not in allowed:
        raise ProcessRunnerError("interpreter_not_allowed")
    if name in {"python", "python3"}:
        return [sys.executable]
    # Configured names resolve through PATH, never through an arbitrary path.
    if Path(name).is_absolute() or Path(name).name != name:
        raise ProcessRunnerError("interpreter_path_denied")
    resolved = shutil.which(name)
    if not resolved:
        raise ProcessRunnerError("interpreter_unavailable")
    return [resolved]


def _tree_size(root: Path) -> int:
    total = 0
    try:
        for item in root.rglob("*"):
            if item.is_file() and not item.is_symlink():
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


def _bounded_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _terminate_tree(process: Any) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "nt":
            import psutil

            parent = psutil.Process(process.pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except psutil.Error:
                    pass
            try:
                parent.kill()
            except psutil.Error:
                pass
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError, ImportError):
        try:
            process.kill()
        except OSError:
            pass


async def _read_bounded(stream: Any, limit: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    truncated = False
    while True:
        chunk = await stream.read(min(65536, limit + 1))
        if not chunk:
            break
        remaining = max(0, limit - total)
        if remaining:
            chunks.append(chunk[:remaining])
            total += min(len(chunk), remaining)
        if len(chunk) > remaining or total >= limit:
            truncated = True
            # Keep draining the pipe after the cap so the child cannot block
            # on a full pipe while the parent waits for process termination.
    return b"".join(chunks), truncated


async def _execute(
    program: Path,
    workspace_root: Path,
    args: list[str],
    interpreter: str,
    limits: ProcessLimits,
    cancel_check: Any = None,
) -> dict[str, Any]:
    try:
        import psutil  # noqa: F401
    except Exception as exc:
        raise ProcessRunnerError("resource_monitor_unavailable") from exc
    command = _interpreter(interpreter) + [
        "-I", "-S", "-c", _PYTHON_BOOTSTRAP,
        str(workspace_root), str(program), *args,
    ]
    env = {
        "PATH": str(Path(sys.executable).parent),
        "PYTHONNOUSERSITE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    started = time.monotonic()
    initial_disk_bytes = _tree_size(workspace_root)
    kwargs: dict[str, Any] = {
        "cwd": str(program.parent),
        "stdin": asyncio.subprocess.DEVNULL,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "env": env,
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    try:
        process = await asyncio.create_subprocess_exec(*command, **kwargs)
    except (OSError, ValueError) as exc:
        raise ProcessRunnerError("process_spawn_failed") from exc
    stdout_task = asyncio.create_task(_read_bounded(process.stdout, limits.output_bytes))
    stderr_task = asyncio.create_task(_read_bounded(process.stderr, limits.output_bytes))
    reason = ""
    deadline = time.monotonic() + limits.wall_seconds
    monitor = None
    try:
        import psutil

        monitor = psutil.Process(process.pid)
    except Exception:
        monitor = None
    try:
        while process.returncode is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = "timeout"
                _terminate_tree(process)
                break
            try:
                await asyncio.wait_for(process.wait(), timeout=min(0.1, remaining))
            except asyncio.TimeoutError:
                if cancel_check is not None:
                    try:
                        if cancel_check():
                            reason = "canceled"
                            _terminate_tree(process)
                            break
                    except Exception:
                        pass
                if monitor is None:
                    continue
                try:
                    children = monitor.children(recursive=True)
                    if len(children) + 1 > limits.max_processes:
                        reason = "resource_limit"
                    cpu = sum(monitor.cpu_times()[:2])
                    memory = monitor.memory_info().rss
                    if cpu > limits.cpu_seconds or memory > limits.memory_bytes:
                        reason = "resource_limit"
                    if _tree_size(workspace_root) - initial_disk_bytes > limits.disk_bytes:
                        reason = "resource_limit"
                    if reason:
                        _terminate_tree(process)
                        break
                except Exception:
                    # A disappearing process is handled by wait() and is not
                    # itself evidence of a resource violation.
                    continue
        if process.returncode is None:
            await process.wait()
    except asyncio.CancelledError:
        reason = "canceled"
        _terminate_tree(process)
        await process.wait()
        raise
    stdout, out_truncated = await stdout_task
    stderr, err_truncated = await stderr_task
    duration = max(0.0, time.monotonic() - started)
    truncated = out_truncated or err_truncated
    if _tree_size(workspace_root) - initial_disk_bytes > limits.disk_bytes and not reason:
        reason = "resource_limit"
    if truncated and not reason:
        reason = "output_limit"
    return {
        "returncode": process.returncode,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "truncated": truncated,
        "duration_seconds": round(duration, 3),
        "reason": reason,
        "succeeded": process.returncode == 0 and not reason,
    }


def _metadata(result: dict[str, Any]) -> dict[str, Any]:
    stdout = str(result.get("stdout") or "").encode("utf-8")
    stderr = str(result.get("stderr") or "").encode("utf-8")
    return {
        "outcome_code": "ok" if result.get("succeeded") else (result.get("reason") or "process_failed"),
        "counters": {
            "returncode": int(result.get("returncode") if result.get("returncode") is not None else -1),
            "stdout_bytes": len(stdout),
            "stderr_bytes": len(stderr),
            "duration_seconds": float(result.get("duration_seconds") or 0),
        },
        "truncated": bool(result.get("truncated")),
        "artifact_ids": [],
    }


async def run_process(
    principal: TaskPrincipal,
    *,
    program: str,
    args: Iterable[str] | None = None,
    interpreter: str = "python",
    task_id: str | None = None,
    lease: TaskLease | None = None,
    limits: ProcessLimits | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one leased process and return bounded output plus its receipt."""
    principal = _validate_principal(principal)
    target, workspace_root = _resolve_program(program)
    values = _validate_args(args)
    bounded = _limits(limits)
    if lease is None:
        if task_id is None:
            raise ProcessRunnerError("task_lease_required")
        raise ProcessRunnerError("task_lease_required")
    try:
        task = get_task(principal, lease.task_id)
        if task_id is not None and lease.task_id != task_id:
            raise ProcessRunnerError("task_lease_mismatch")
        result = await _execute(
            target,
            workspace_root,
            values,
            interpreter,
            bounded,
            cancel_check=lambda: bool(get_task(principal, lease.task_id).get("cancel_requested")),
        )
        if result["reason"] == "canceled":
            receipt = acknowledge_cancel(principal, lease)
            return {"schema_version": PROCESS_SCHEMA_VERSION, "receipt": receipt, **result}
        if result["reason"]:
            receipt = fail_task(
                principal,
                lease,
                error_code=f"process_{result['reason']}",
                result_metadata=_metadata(result),
            )
        elif result["succeeded"]:
            receipt = complete_task(principal, lease, result_metadata=_metadata(result))
        else:
            receipt = fail_task(principal, lease, error_code="process_failed", result_metadata=_metadata(result))
        return {"schema_version": PROCESS_SCHEMA_VERSION, "receipt": receipt, **result}
    except asyncio.CancelledError:
        try:
            request_cancel(principal, lease.task_id, reason_code="runner_canceled")
            acknowledge_cancel(principal, lease)
        except Exception:
            pass
        raise
    except TaskManagerError as exc:
        raise ProcessRunnerError(exc.code) from exc


def create_process_task(
    principal: TaskPrincipal,
    *,
    program: str,
    args: Iterable[str] | None = None,
    interpreter: str = "python",
    idempotency_key: str,
    ttl_seconds: int = 300,
    limits: ProcessLimits | dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    principal = _validate_principal(principal)
    target, _workspace_root = _resolve_program(program)
    values = _validate_args(args)
    bounded = _limits(limits)
    payload = "\0".join([str(target), interpreter, *values])
    idem = hashlib.sha256((idempotency_key + "\0" + payload).encode("utf-8")).hexdigest()
    return create_task(
        principal,
        capability=PROCESS_CAPABILITY,
        source="process_runner",
        idempotency_key=idem,
        ttl_seconds=ttl_seconds,
        retry_policy=RetryPolicy.NEVER.value,
        max_attempts=1,
        causation_ref=CausationRef("admin_action", idem),
    )


async def run_process_task(
    principal: TaskPrincipal,
    *,
    program: str,
    args: Iterable[str] | None = None,
    interpreter: str = "python",
    idempotency_key: str,
    ttl_seconds: int = 300,
    limits: ProcessLimits | dict[str, Any] | None = None,
) -> dict[str, Any]:
    task, _ = create_process_task(
        principal,
        program=program,
        args=args,
        interpreter=interpreter,
        idempotency_key=idempotency_key,
        ttl_seconds=ttl_seconds,
        limits=limits,
    )
    lease = claim_next(principal, task_id=task["task_id"], capabilities={PROCESS_CAPABILITY})
    if lease is None:
        return {"schema_version": PROCESS_SCHEMA_VERSION, "receipt": get_task(principal, task["task_id"]), "duplicate": True}
    return await run_process(
        principal,
        program=program,
        args=args,
        interpreter=interpreter,
        task_id=task["task_id"],
        lease=lease,
        limits=limits,
    )


def capability_snapshot() -> dict[str, Any]:
    cfg = _cfg()
    return {
        "schema_version": PROCESS_SCHEMA_VERSION,
        "enabled": _enabled(),
        "desired_enabled": bool(cfg.get("enabled", False)),
        "status": "disabled_remote_server" if _remote() else ("enabled" if _enabled() else "disabled"),
        "network": "disabled",
        "workspace_required": True,
        "allowlisted_extensions": sorted(_allowed_extensions()),
        "limits": {key: getattr(_limits(None), key) for key in ("wall_seconds", "cpu_seconds", "memory_bytes", "output_bytes", "max_processes", "disk_bytes")},
    }


def observability_snapshot(*, uid: str | None = None, char_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    from core.agent_runtime.task_manager import observability_snapshot as task_snapshot

    snapshot = task_snapshot(uid=uid, char_id=char_id, capability=PROCESS_CAPABILITY, limit=limit)
    return {"schema_version": PROCESS_SCHEMA_VERSION, "capability": capability_snapshot(), "tasks": snapshot}
