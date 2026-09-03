"""Controlled Reality workspace capability (Brief 233).

The workspace is an explicit, user-configured set of roots.  This module is
deliberately independent from ``fs_access``: the latter remains read-only and
continues to serve legacy callers, while this adapter owns all mutations.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Any

from core.safe_write import safe_write_bytes

_DENY_NAMES = frozenset({
    "secrets", ".env", ".git", "node_modules", "__pycache__", "config.yaml",
    "token", "credentials", "password", "cookies", "browser", "profiles",
})
_TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".csv", ".log",
    ".html", ".htm", ".css", ".xml", ".ini", ".cfg", ".conf",
})
_MAX_PATH_CHARS = 1024
_ACTIVE = 0
_ACTIVE_LOCK = threading.Lock()
_VERSION_LOCK = threading.Lock()
_VERSIONS: dict[str, list[dict[str, Any]]] = {}


class WorkspaceError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _cfg() -> dict[str, Any]:
    from core.config_loader import get_config
    return get_config().get("workspace_access", {}) or {}


def _remote() -> bool:
    from core.deployment_capabilities import is_remote_server
    return is_remote_server()


def _enabled() -> bool:
    return bool(_cfg().get("enabled", False)) and not _remote()


def _roots() -> list[Path]:
    result: list[Path] = []
    for raw in _cfg().get("roots") or _cfg().get("allow_roots") or []:
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            path = Path(raw).expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if path not in result:
            result.append(path)
    return result


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _deny(path: Path) -> None:
    names = _DENY_NAMES | {str(x).lower() for x in (_cfg().get("deny_names") or [])}
    for part in path.parts:
        lowered = part.lower()
        if any(item in lowered for item in names):
            raise WorkspaceError("sensitive_path_denied")


def _project_data() -> Path:
    from core.sandbox import get_paths
    return get_paths().root_dir().resolve()


def _resolve(raw_path: str, *, allow_missing: bool = False) -> tuple[Path, Path]:
    if not isinstance(raw_path, str) or not raw_path.strip() or len(raw_path) > _MAX_PATH_CHARS:
        raise WorkspaceError("invalid_workspace_path")
    roots = _roots()
    if not roots:
        raise WorkspaceError("workspace_not_configured")
    candidate = Path(raw_path).expanduser()
    # Relative paths are resolved against the first configured root only.  An
    # absolute path must still be inside one of the explicit roots.
    if not candidate.is_absolute():
        candidate = roots[0] / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceError("path_resolution_failed") from exc
    root = next((item for item in roots if _within(resolved, item)), None)
    if root is None:
        raise WorkspaceError("workspace_path_denied")
    if _within(resolved, _project_data()):
        raise WorkspaceError("project_data_denied")
    _deny(candidate)
    _deny(resolved)
    # Every existing path component must be a real directory/file.  This
    # rejects symlink escape and symlinks pointing inside the workspace too.
    cursor = resolved
    while _within(cursor, root):
        if cursor.exists() and cursor.is_symlink():
            raise WorkspaceError("symlink_denied")
        if cursor == root:
            break
        cursor = cursor.parent
    if not allow_missing and not resolved.exists():
        raise WorkspaceError("path_not_found")
    return resolved, root


def _permission(operation: str) -> None:
    if not _enabled():
        raise WorkspaceError("workspace_unavailable_remote_server" if _remote() else "workspace_disabled")
    permissions = _cfg().get("permissions") or {}
    # A configured capability must opt into each operation independently.
    if not bool(permissions.get(operation, False)):
        raise WorkspaceError(f"permission_denied_{operation}")


def _reserve() -> None:
    global _ACTIVE
    limit = int(_cfg().get("max_concurrent_tasks", 2) or 2)
    with _ACTIVE_LOCK:
        if _ACTIVE >= max(1, min(limit, 32)):
            raise WorkspaceError("concurrent_limit_exceeded")
        _ACTIVE += 1


def _release() -> None:
    global _ACTIVE
    with _ACTIVE_LOCK:
        _ACTIVE = max(0, _ACTIVE - 1)


def _check_name(path: Path) -> None:
    if path.suffix.lower() not in _TEXT_EXTENSIONS:
        raise WorkspaceError("unsupported_file_type")


def _check_size(path: Path, content: bytes | None = None) -> None:
    max_file = int(_cfg().get("max_file_bytes", 5 * 1024 * 1024) or 1)
    size = len(content) if content is not None else path.stat().st_size
    if size > max_file:
        raise WorkspaceError("file_size_limit_exceeded")
    total_limit = int(_cfg().get("max_total_bytes", 50 * 1024 * 1024) or 1)
    root = next((r for r in _roots() if _within(path, r)), None)
    if root is None:
        raise WorkspaceError("workspace_path_denied")
    total = 0
    for item in root.rglob("*"):
        if item.is_file() and not item.is_symlink():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    if content is not None and path.exists():
        total -= path.stat().st_size
    if total + size > total_limit:
        raise WorkspaceError("workspace_capacity_exceeded")


def _record_version(path: Path, *, operation: str, size: int, digest: str, previous: bytes | None = None) -> int:
    key = str(path)
    with _VERSION_LOCK:
        items = _VERSIONS.setdefault(key, [])
        version = (items[-1]["version"] + 1) if items else 1
        items.append({"version": version, "operation": operation, "size": size, "digest": digest, "ts": time.time(), "previous": previous})
        del items[:-10]
    return version


def _result(path: Path, *, operation: str, size: int = 0, digest: str = "", previous: bytes | None = None) -> dict[str, Any]:
    return {
        "operation": operation,
        "name": path.name,
        "size": size,
        "digest": digest,
        "version": _record_version(path, operation=operation, size=size, digest=digest, previous=previous),
    }


def list_workspace(path: str | None = None, depth: int = 1) -> dict[str, Any]:
    _permission("list")
    if path:
        target, _ = _resolve(path)
        if not target.is_dir():
            raise WorkspaceError("not_a_directory")
        roots = [target]
    else:
        roots = _roots()
    depth = 2 if depth == 2 else 1
    entries: list[dict[str, Any]] = []
    limit = min(int(_cfg().get("max_list_entries", 100) or 100), 500)
    for root in roots:
        for item in sorted(root.rglob("*") if depth == 2 else root.iterdir(), key=lambda p: p.name.lower()):
            if len(entries) >= limit:
                break
            if item.is_symlink():
                continue
            try:
                _deny(item)
            except WorkspaceError:
                continue
            entries.append({"name": item.name, "kind": "directory" if item.is_dir() else "file", "size": item.stat().st_size if item.is_file() else 0})
    return {"roots": len(roots), "entries": entries, "truncated": len(entries) >= limit}


def read_workspace(path: str) -> str:
    _permission("read")
    target, _ = _resolve(path)
    if not target.is_file():
        raise WorkspaceError("not_a_file")
    _check_name(target)
    _check_size(target)
    raw = target.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceError("text_decode_failed") from exc
    max_chars = min(int(_cfg().get("max_read_chars", 10000) or 1), 100000)
    return text[:max_chars]


def write_workspace(path: str, content: str, *, overwrite: bool = False, operation: str = "create") -> dict[str, Any]:
    _permission(operation)
    if not isinstance(content, str):
        raise WorkspaceError("invalid_content")
    target, _ = _resolve(path, allow_missing=True)
    _check_name(target)
    exists = target.exists()
    if operation == "create" and exists:
        raise WorkspaceError("already_exists")
    if operation == "update" and not exists:
        raise WorkspaceError("path_not_found")
    if exists and not overwrite and operation == "update":
        raise WorkspaceError("overwrite_confirmation_required")
    data = content.encode("utf-8")
    _check_size(target, data)
    previous = target.read_bytes() if exists and target.is_file() else None
    _reserve()
    try:
        if not safe_write_bytes(target, data):
            raise WorkspaceError("atomic_write_failed")
    finally:
        _release()
    return _result(target, operation=operation, size=len(data), digest=hashlib.sha256(data).hexdigest()[:16], previous=previous)


def create_workspace(path: str, content: str) -> dict[str, Any]:
    return write_workspace(path, content, operation="create")


def update_workspace(path: str, content: str, *, confirmed: bool = False) -> dict[str, Any]:
    return write_workspace(path, content, overwrite=confirmed, operation="update")


def delete_workspace(path: str, *, confirmed: bool = False) -> dict[str, Any]:
    _permission("delete")
    if not confirmed:
        raise WorkspaceError("delete_confirmation_required")
    target, _ = _resolve(path)
    if not target.is_file():
        raise WorkspaceError("delete_file_only")
    previous = target.read_bytes()
    _reserve()
    try:
        target.unlink()
    except OSError as exc:
        raise WorkspaceError("delete_failed") from exc
    finally:
        _release()
    return _result(target, operation="delete", previous=previous)


def undo_workspace(path: str, *, confirmed: bool = False) -> dict[str, Any]:
    """Restore the latest bounded in-process version of a workspace file."""
    _permission("update")
    if not confirmed:
        raise WorkspaceError("undo_confirmation_required")
    target, _ = _resolve(path, allow_missing=True)
    key = str(target)
    with _VERSION_LOCK:
        history = _VERSIONS.get(key) or []
        entry = history[-1] if history else None
    if not entry or entry.get("previous") is None:
        raise WorkspaceError("version_not_available")
    previous = entry["previous"]
    if not isinstance(previous, bytes):
        raise WorkspaceError("version_not_available")
    _check_size(target, previous)
    _reserve()
    try:
        if not safe_write_bytes(target, previous):
            raise WorkspaceError("atomic_write_failed")
    finally:
        _release()
    return _result(target, operation="undo", size=len(previous), digest=hashlib.sha256(previous).hexdigest()[:16])


def capability_snapshot() -> dict[str, Any]:
    cfg = _cfg()
    return {
        "enabled": _enabled(),
        "status": "disabled_remote_server" if _remote() else ("enabled" if _enabled() else "disabled"),
        "root_count": len(_roots()),
        "permissions": {name: bool((cfg.get("permissions") or {}).get(name, False)) for name in ("read", "list", "create", "update", "delete")},
        "limits": {"max_file_bytes": int(cfg.get("max_file_bytes", 5 * 1024 * 1024) or 1), "max_total_bytes": int(cfg.get("max_total_bytes", 50 * 1024 * 1024) or 1), "max_concurrent_tasks": int(cfg.get("max_concurrent_tasks", 2) or 2)},
    }


# Capability-oriented aliases used by adapters and tests.  The implementation
# remains centralized above so all entry points share identical guards.
workspace_list = list_workspace
workspace_read = read_workspace
workspace_create = create_workspace
workspace_update = update_workspace
workspace_delete = delete_workspace
workspace_undo = undo_workspace
