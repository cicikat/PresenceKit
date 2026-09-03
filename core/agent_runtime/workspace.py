"""Controlled Reality workspace capability (Brief 233).

The workspace is an explicit, user-configured set of roots.  This module is
deliberately independent from ``fs_access``: the latter remains read-only and
continues to serve legacy callers, while this adapter owns all mutations.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from core.agent_runtime.models import TaskPrincipal
from core.safe_write import safe_write_bytes, safe_write_json
from core.sandbox import get_paths

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
_VERSION_LOCK = threading.RLock()
_VERSION_SCHEMA = "agent-runtime-workspace-version.v1"
_SNAPSHOT_RE = re.compile(r"^[1-9][0-9]*\.bin$")


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
    return bool(_cfg().get("enabled", False)) and bool(_roots()) and not _remote()


def _path_has_symlink(path: Path) -> bool:
    parts = path.parts
    if not parts:
        return False
    cursor = Path(parts[0])
    if cursor.exists() and cursor.is_symlink():
        return True
    for part in parts[1:]:
        cursor /= part
        if cursor.exists() and cursor.is_symlink():
            return True
    return False


def _roots() -> list[Path]:
    result: list[Path] = []
    for raw in _cfg().get("roots") or _cfg().get("allow_roots") or []:
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            lexical = Path(raw).expanduser().absolute()
            if _path_has_symlink(lexical):
                continue
            path = lexical.resolve(strict=False)
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
    candidate = candidate.absolute()
    if _path_has_symlink(candidate):
        raise WorkspaceError("symlink_denied")
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
    if not allow_missing and not resolved.exists():
        raise WorkspaceError("path_not_found")
    return resolved, root


def _permission(operation: str) -> None:
    if _remote():
        raise WorkspaceError("workspace_unavailable_remote_server")
    if not bool(_cfg().get("enabled", False)):
        raise WorkspaceError("workspace_disabled")
    if not _roots():
        raise WorkspaceError("workspace_not_configured")
    permissions = _cfg().get("permissions") or {}
    # A configured capability must opt into each operation independently.
    if not bool(permissions.get(operation, False)):
        raise WorkspaceError(f"permission_denied_{operation}")


def _principal(value: TaskPrincipal) -> TaskPrincipal:
    if not isinstance(value, TaskPrincipal) or value.realm != "reality":
        raise WorkspaceError("realm_forbidden")
    from core.data_paths import safe_user_id
    try:
        safe_user_id(value.uid)
        safe_user_id(value.char_id)
    except ValueError as exc:
        raise WorkspaceError("invalid_workspace_principal") from exc
    return value


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
    if content is None:
        return
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


def _version_paths(principal: TaskPrincipal, path: Path) -> tuple[Path, Path]:
    target_digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
    root = get_paths().agent_runtime_workspace_versions_dir(principal.uid, char_id=principal.char_id)
    directory = root / target_digest
    return directory, directory / "state.json"


def _load_versions(principal: TaskPrincipal, path: Path) -> tuple[Path, dict[str, Any]]:
    directory, state_path = _version_paths(principal, path)
    if not state_path.exists():
        return directory, {"schema_version": _VERSION_SCHEMA, "versions": []}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkspaceError("version_store_invalid") from exc
    items = state.get("versions")
    if state.get("schema_version") != _VERSION_SCHEMA or not isinstance(items, list):
        raise WorkspaceError("version_store_invalid")
    previous_version = 0
    for item in items:
        if not isinstance(item, dict):
            raise WorkspaceError("version_store_invalid")
        version = item.get("version")
        previous_exists = item.get("previous_exists")
        snapshot = item.get("snapshot")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version <= previous_version
            or item.get("operation") not in {"create", "update", "delete", "undo"}
            or not isinstance(previous_exists, bool)
            or not isinstance(snapshot, str)
            or (previous_exists and snapshot != f"{version}.bin")
            or (previous_exists and not _SNAPSHOT_RE.fullmatch(snapshot))
            or (not previous_exists and snapshot)
        ):
            raise WorkspaceError("version_store_invalid")
        previous_version = version
    return directory, state


def _record_version(principal: TaskPrincipal, path: Path, *, operation: str, previous: bytes | None, previous_exists: bool) -> int:
    with _VERSION_LOCK:
        directory, state = _load_versions(principal, path)
        items = state["versions"]
        version = int(items[-1]["version"] + 1) if items else 1
        snapshot_name = f"{version}.bin" if previous_exists else ""
        if previous_exists:
            directory.mkdir(parents=True, exist_ok=True)
            if previous is None or not safe_write_bytes(directory / snapshot_name, previous):
                raise WorkspaceError("version_write_failed")
        items.append({
            "version": version,
            "operation": operation,
            "previous_exists": previous_exists,
            "previous_size": len(previous or b""),
            "previous_digest": hashlib.sha256(previous or b"").hexdigest()[:16] if previous_exists else "",
            "snapshot": snapshot_name,
        })
        removed = items[:-10]
        state["versions"] = items[-10:]
        if not safe_write_json(directory / "state.json", state):
            raise WorkspaceError("version_write_failed")
        for item in removed:
            stale = directory / str(item.get("snapshot") or "")
            if item.get("snapshot") and stale.is_file():
                try:
                    stale.unlink()
                except OSError:
                    pass
    return version


def _result(path: Path, *, operation: str, version: int, size: int = 0, digest: str = "") -> dict[str, Any]:
    return {
        "operation": operation,
        "name": path.name,
        "size": size,
        "digest": digest,
        "version": version,
    }


def list_workspace(principal: TaskPrincipal, path: str | None = None, depth: int = 1) -> dict[str, Any]:
    _principal(principal)
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
            try:
                if _within(item.resolve(strict=False), _project_data()):
                    continue
            except OSError:
                continue
            entries.append({"name": item.name, "kind": "directory" if item.is_dir() else "file", "size": item.stat().st_size if item.is_file() else 0})
    return {"roots": len(roots), "entries": entries, "truncated": len(entries) >= limit}


def read_workspace(principal: TaskPrincipal, path: str) -> str:
    _principal(principal)
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


def write_workspace(principal: TaskPrincipal, path: str, content: str, *, overwrite: bool = False, operation: str = "create") -> dict[str, Any]:
    principal = _principal(principal)
    _permission(operation)
    if not isinstance(content, str):
        raise WorkspaceError("invalid_content")
    target, _ = _resolve(path, allow_missing=True)
    _check_name(target)
    data = content.encode("utf-8")
    _reserve()
    try:
        with _VERSION_LOCK:
            exists = target.exists()
            if operation == "create" and exists:
                raise WorkspaceError("already_exists")
            if operation == "update" and not exists:
                raise WorkspaceError("path_not_found")
            if exists and not overwrite and operation == "update":
                raise WorkspaceError("overwrite_confirmation_required")
            _check_size(target, data)
            previous = target.read_bytes() if exists and target.is_file() else None
            version = _record_version(principal, target, operation=operation, previous=previous, previous_exists=exists)
            if not safe_write_bytes(target, data):
                raise WorkspaceError("atomic_write_failed")
    finally:
        _release()
    return _result(target, operation=operation, version=version, size=len(data), digest=hashlib.sha256(data).hexdigest()[:16])


def create_workspace(principal: TaskPrincipal, path: str, content: str) -> dict[str, Any]:
    return write_workspace(principal, path, content, operation="create")


def update_workspace(principal: TaskPrincipal, path: str, content: str, *, confirmed: bool = False) -> dict[str, Any]:
    return write_workspace(principal, path, content, overwrite=confirmed, operation="update")


def delete_workspace(principal: TaskPrincipal, path: str, *, confirmed: bool = False) -> dict[str, Any]:
    principal = _principal(principal)
    _permission("delete")
    if not confirmed:
        raise WorkspaceError("delete_confirmation_required")
    target, _ = _resolve(path)
    if not target.is_file():
        raise WorkspaceError("delete_file_only")
    _check_name(target)
    _check_size(target)
    _reserve()
    try:
        with _VERSION_LOCK:
            previous = target.read_bytes()
            version = _record_version(principal, target, operation="delete", previous=previous, previous_exists=True)
            target.unlink()
    except OSError as exc:
        raise WorkspaceError("delete_failed") from exc
    finally:
        _release()
    return _result(target, operation="delete", version=version)


def undo_workspace(principal: TaskPrincipal, path: str, *, confirmed: bool = False) -> dict[str, Any]:
    """Restore the latest durable version of a workspace file."""
    principal = _principal(principal)
    _permission("update")
    if not confirmed:
        raise WorkspaceError("undo_confirmation_required")
    target, _ = _resolve(path, allow_missing=True)
    _check_name(target)
    _reserve()
    try:
        with _VERSION_LOCK:
            directory, state = _load_versions(principal, target)
            history = state["versions"]
            entry = history[-1] if history else None
            if not entry:
                raise WorkspaceError("version_not_available")
            previous_exists = bool(entry.get("previous_exists"))
            previous = None
            if previous_exists:
                snapshot = directory / str(entry.get("snapshot") or "")
                try:
                    previous = snapshot.read_bytes()
                except OSError as exc:
                    raise WorkspaceError("version_not_available") from exc
                _check_size(target, previous)
            if previous_exists and previous is None:
                raise WorkspaceError("version_not_available")
            current_exists = target.exists()
            current = target.read_bytes() if current_exists and target.is_file() else None
            version = _record_version(principal, target, operation="undo", previous=current, previous_exists=current_exists)
            if previous_exists:
                if not safe_write_bytes(target, previous):
                    raise WorkspaceError("atomic_write_failed")
            elif target.exists():
                target.unlink()
    finally:
        _release()
    restored = previous or b""
    return _result(target, operation="undo", version=version, size=len(restored), digest=hashlib.sha256(restored).hexdigest()[:16] if previous_exists else "")


def capability_snapshot() -> dict[str, Any]:
    cfg = _cfg()
    configured_roots = _roots()
    desired_enabled = bool(cfg.get("enabled", False))
    if _remote():
        status = "disabled_remote_server"
    elif not desired_enabled:
        status = "disabled"
    elif not configured_roots:
        status = "not_configured"
    else:
        status = "enabled"
    return {
        "enabled": _enabled(),
        "desired_enabled": desired_enabled,
        "status": status,
        "root_count": len(configured_roots),
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
