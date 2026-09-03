"""Atomic, per-owner/character Reality task storage."""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from core.agent_runtime.models import STORE_SCHEMA_VERSION
from core.safe_write import safe_write_json
from core.sandbox import get_paths


class TaskStoreError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


_PROCESS_INSTANCE_ID = uuid.uuid4().hex
_LOCKS: dict[tuple[str, str, str], threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class _ScopeLock:
    """Thread and process lock for one durable task scope."""

    def __init__(self, uid: str, char_id: str):
        self.uid = str(uid)
        self.char_id = str(char_id)
        self._thread_lock = _thread_lock(self.uid, self.char_id)
        self._file = None

    def __enter__(self):
        self._thread_lock.acquire()
        try:
            path = state_path(self.uid, self.char_id).with_suffix(".lock")
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(path, "a+b")
            self._file.seek(0)
            if not self._file.read(1):
                self._file.write(b"0")
                self._file.flush()
            self._file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._file.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX)
            return self
        except Exception:
            self._close()
            self._thread_lock.release()
            raise

    def _close(self):
        if self._file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()
            self._file = None

    def __exit__(self, exc_type, exc, tb):
        try:
            self._close()
        finally:
            self._thread_lock.release()


def _thread_lock(uid: str, char_id: str) -> threading.RLock:
    root = str(get_paths().root_dir().resolve())
    key = (root, str(uid), str(char_id))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def process_instance_id() -> str:
    return _PROCESS_INSTANCE_ID


def reset_process_instance_for_tests(value: str | None = None) -> str:
    global _PROCESS_INSTANCE_ID
    _PROCESS_INSTANCE_ID = value or uuid.uuid4().hex
    return _PROCESS_INSTANCE_ID


def scope_lock(uid: str, char_id: str) -> _ScopeLock:
    return _ScopeLock(uid, char_id)


def state_path(uid: str, char_id: str) -> Path:
    return get_paths().agent_runtime_task_state(uid, char_id=char_id)


def empty_state() -> dict[str, Any]:
    return {"schema_version": STORE_SCHEMA_VERSION, "tasks": []}


def load_unlocked(uid: str, char_id: str) -> dict[str, Any]:
    path = state_path(uid, char_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_state()
    except (OSError, ValueError, TypeError) as exc:
        raise TaskStoreError("task_store_unreadable") from exc
    if (
        not isinstance(raw, dict)
        or raw.get("schema_version") != STORE_SCHEMA_VERSION
        or not isinstance(raw.get("tasks"), list)
    ):
        raise TaskStoreError("task_store_schema_invalid")
    return raw


def save_unlocked(uid: str, char_id: str, state: dict[str, Any]) -> None:
    # Task state is a single atomic snapshot; a .bak sidecar would create an
    # unnecessary second recovery surface for a lifecycle record.
    if not safe_write_json(state_path(uid, char_id), state, keep_bak=False):
        raise TaskStoreError("task_store_write_failed")


def iter_scope_paths() -> list[Path]:
    root = get_paths().agent_runtime_reality_root() / "tasks"
    if not root.exists():
        return []
    try:
        return sorted(path for path in root.glob("*/*/state.json") if path.is_file())
    except OSError:
        return []


def scope_from_path(path: Path) -> tuple[str, str] | None:
    try:
        relative = path.relative_to(get_paths().agent_runtime_reality_root() / "tasks")
        if len(relative.parts) != 3 or relative.parts[2] != "state.json":
            return None
        return relative.parts[1], relative.parts[0]
    except (OSError, ValueError):
        return None
