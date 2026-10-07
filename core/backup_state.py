"""Offline, integrity-checked private-state snapshots.

This module deliberately handles *only* creation and verification.  It never
restores a snapshot, modifies source state, or attempts to stop a running
PresenceKit process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

from core.sandbox import paths_for_installation


UTC = timezone.utc


MANIFEST_NAME = "manifest.json"
MANIFEST_CHECKSUM_NAME = "manifest.sha256"
MANIFEST_VERSION = 1
PROTECTION_MODE_PROTECTED_VOLUME = "protected_volume"
PRIVATE_ROOTS_VERSION = 1
MAX_RESTORE_FILES = 100_000
MAX_RESTORE_BYTES = 8 * 1024 * 1024 * 1024


class BackupError(RuntimeError):
    """A safe, non-sensitive backup failure with a stable machine error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ServiceState(str, Enum):
    OFFLINE = "offline"
    RUNNING = "running"
    UNKNOWN = "unknown"


def _pid_is_alive(pid: int) -> bool | None:
    """True if the pid is running, False if absent, None if inspection failed.

    Windows ``os.kill(pid, 0)`` is not a liveness probe: signal 0 is invalid
    there and raises ``OSError``, which previously made a live marker look
    UNKNOWN and blocked verified snapshots.
    """
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
        except ImportError:
            return None
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if handle:
            code = wintypes.DWORD()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            kernel32.CloseHandle(handle)
            if not ok:
                return None
            return int(code.value) == still_active
        error = int(kernel32.GetLastError() or 0)
        if error == 5:  # ERROR_ACCESS_DENIED: process exists
            return True
        if error == 87:  # ERROR_INVALID_PARAMETER: typically no such pid
            return False
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None


@dataclass(frozen=True)
class ProtectionRoot:
    """A classified private write root, expressed relative to an installation."""

    root_id: str
    relative_path: PurePosixPath
    required: bool
    kind: str  # directory | file | legacy_selector


# This is the versioned, centralized private-write inventory for backup.  The
# legacy selector is intentionally explicit: those paths remain compatibility
# readers/writers for installations not yet fully materialized into userdata.
PROTECTION_ROOTS: tuple[ProtectionRoot, ...] = (
    ProtectionRoot("data", PurePosixPath("data"), True, "directory"),
    ProtectionRoot("userdata", PurePosixPath("userdata"), False, "directory"),
    ProtectionRoot("config", PurePosixPath("config.yaml"), True, "file"),
    ProtectionRoot("secrets_local", PurePosixPath("secrets.local.yaml"), False, "file"),
    ProtectionRoot("legacy_private_assets", PurePosixPath("."), False, "legacy_selector"),
)

# A test or a future audited migration may declare an unresolved write root
# here.  Creation must never quietly omit one.
UNCLASSIFIED_PRIVATE_ROOTS: tuple[str, ...] = ()

# These are the only exclusions below data/.  They correspond to data-registry
# entries whose durability is derived or forensic, never canonical state.
_EXCLUDED_DATA_PREFIXES = (
    PurePosixPath("data/test_sandbox"),
    PurePosixPath("data/logs"),
    PurePosixPath("data/cache"),
    PurePosixPath("data/inbox"),
    PurePosixPath("data/debug"),
    PurePosixPath("data/runtime/pending_perception"),
    PurePosixPath("data/runtime/observability/llm_debug_requests.jsonl"),
    PurePosixPath("data/runtime/service_state.json"),
)


def _is_reparse_point(path: Path) -> bool:
    try:
        attrs = path.lstat().st_file_attributes  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return path.is_symlink()
    return path.is_symlink() or bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _safe_relative(path: PurePosixPath) -> PurePosixPath:
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise BackupError("unsafe_path", "备份路径不安全。")
    return path


def _relative_to_installation(installation: Path, path: Path) -> PurePosixPath:
    try:
        relative = path.resolve().relative_to(installation.resolve())
    except ValueError as exc:
        raise BackupError("unsafe_path", "发现位于安装目录外的文件。") from exc
    return _safe_relative(PurePosixPath(relative.as_posix()))


def _is_excluded_data_path(relative: PurePosixPath) -> bool:
    if any(relative == prefix or prefix in relative.parents for prefix in _EXCLUDED_DATA_PREFIXES):
        return True
    # sqlite-vec is a derived index; SQLite sidecars belong to the same cache.
    if relative.match("data/runtime/memory/*/*/vector_store.db*"):
        return True
    # The older memory-index tree is likewise a rebuildable index.
    return relative.match("data/chars/*/memory_index/*")


def _legacy_private_files(installation: Path) -> Iterable[Path]:
    """Yield only ignored legacy private assets, never tracked defaults/seeds."""
    characters = installation / "characters"
    if characters.is_dir():
        for item in characters.iterdir():
            if item.name in {"default.json", "default_author_notes.json", "dream_postcards"}:
                continue
            if item.name in {"reality", "dream_presets", "dream_worlds"}:
                yield from _walk_regular_files(item)
            elif item.is_file() and item.suffix.lower() in {".json", ".txt", ".md"}:
                yield item
    stickers = installation / "assets" / "stickers"
    if stickers.is_dir():
        yield from (path for path in _walk_regular_files(stickers) if path.name != ".gitkeep")
    content = installation / "content" / "characters"
    if content.is_dir():
        for character_dir in content.iterdir():
            if character_dir.name == "default" or not character_dir.is_dir():
                continue
            for item in _walk_regular_files(character_dir):
                if ".example." not in item.name:
                    yield item


def _walk_regular_files(root: Path, *, installation: Path | None = None) -> Iterable[Path]:
    if _is_reparse_point(root):
        raise BackupError("unsafe_link", "备份范围内存在符号链接、junction 或 reparse point。")
    for entry in root.iterdir():
        if installation is not None and _is_excluded_data_path(
            PurePosixPath(entry.relative_to(installation).as_posix())
        ):
            continue
        if _is_reparse_point(entry):
            raise BackupError("unsafe_link", "备份范围内存在符号链接、junction 或 reparse point。")
        if entry.is_file():
            yield entry
        elif entry.is_dir():
            yield from _walk_regular_files(entry, installation=installation)


def _selected_files(installation: Path) -> tuple[list[tuple[Path, str]], list[str]]:
    if UNCLASSIFIED_PRIVATE_ROOTS:
        raise BackupError("unclassified_private_root", "存在未分类的已知私人写入根，已拒绝创建备份。")
    selected: list[tuple[Path, str]] = []
    optional_missing: list[str] = []
    paths = paths_for_installation(installation)
    for root in PROTECTION_ROOTS:
        if root.root_id == "data":
            source = paths.root_dir()
        elif root.root_id == "userdata":
            source = paths.userdata_root()
        else:
            source = installation.joinpath(*root.relative_path.parts)
        if root.kind == "legacy_selector":
            selected.extend((path, root.root_id) for path in _legacy_private_files(installation))
            continue
        if not source.exists():
            if root.required:
                raise BackupError("missing_required_root", f"缺少必需私人状态根：{root.root_id}。")
            optional_missing.append(root.relative_path.as_posix())
            continue
        if _is_reparse_point(source):
            raise BackupError("unsafe_link", "保护根不能是符号链接、junction 或 reparse point。")
        if root.kind == "file":
            if not source.is_file():
                raise BackupError("missing_required_root", f"保护文件类型无效：{root.root_id}。")
            selected.append((source, root.root_id))
        else:
            selected.extend(
                (path, root.root_id)
                for path in _walk_regular_files(source, installation=installation if root.root_id == "data" else None)
                if not _is_excluded_data_path(_relative_to_installation(installation, path))
            )
    deduplicated: dict[PurePosixPath, tuple[Path, str]] = {}
    for path, root_id in selected:
        relative = _relative_to_installation(installation, path)
        deduplicated.setdefault(relative, (path, root_id))
    return [deduplicated[key] for key in sorted(deduplicated)], optional_missing


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_layout_marker(installation: Path) -> dict[str, Any]:
    marker = paths_for_installation(installation).layout_version()
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("manifest_invalid", "无法解析 data layout marker，已拒绝创建备份。") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data_layout_schema_version"), int):
        raise BackupError("manifest_invalid", "data layout marker 缺少受支持的 schema 信息。")
    return {
        "product_baseline": payload.get("product_baseline"),
        "data_layout_schema_version": payload["data_layout_schema_version"],
        "first_initialized_version": payload.get("first_initialized_version"),
    }


def _current_version(installation: Path) -> str:
    try:
        value = (installation / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    return value or "unknown"


def service_state(installation: Path) -> ServiceState:
    """Fail closed when process inspection cannot establish a safe offline state."""
    marker = paths_for_installation(installation).service_state()
    if marker.exists():
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
            pid = payload["pid"]
            expected_root = Path(payload["installation_root"]).resolve()
            if not isinstance(pid, int) or expected_root != installation.resolve():
                return ServiceState.UNKNOWN
            alive = _pid_is_alive(pid)
            if alive is True:
                return ServiceState.RUNNING
            if alive is None:
                return ServiceState.UNKNOWN
            # Stale marker after a crash; scan below protects old installs too.
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return ServiceState.UNKNOWN
    if os.name == "nt":
        try:
            query = "Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'python.exe','pythonw.exe' } | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
            result = subprocess.run(["powershell", "-NoProfile", "-Command", query], capture_output=True, text=True, timeout=10, check=False)
            if result.returncode:
                return ServiceState.UNKNOWN
            commands = json.loads(result.stdout or "[]")
            if isinstance(commands, dict):
                commands = [commands]
            if not isinstance(commands, list):
                return ServiceState.UNKNOWN
            for command in commands:
                if not isinstance(command, dict):
                    return ServiceState.UNKNOWN
                if command.get("ProcessId") == os.getpid():
                    continue
                parts = str(command.get("CommandLine") or "").replace('"', "").split()
                for part in parts:
                    candidate = Path(part)
                    if candidate.name.lower() == "main.py":
                        if candidate.is_absolute() and candidate.resolve() == installation / "main.py":
                            return ServiceState.RUNNING
                        if not candidate.is_absolute():
                            return ServiceState.UNKNOWN
            return ServiceState.OFFLINE
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
            return ServiceState.UNKNOWN
    try:
        import psutil
    except ImportError:
        return ServiceState.UNKNOWN
    try:
        for process in psutil.process_iter(["name", "cmdline"]):
            try:
                if process.pid == os.getpid():
                    continue
                info = process.info
                name = str(info.get("name") or "").lower()
                if name not in {"python.exe", "pythonw.exe", "python", "pythonw"}:
                    continue
                command = info.get("cmdline")
                if not command:
                    return ServiceState.UNKNOWN
                joined = " ".join(str(item) for item in command)
                if "main.py" not in joined.lower():
                    continue
                for item in command:
                    candidate = Path(str(item))
                    if candidate.name.lower() != "main.py":
                        continue
                    if candidate.is_absolute():
                        if candidate.resolve() == installation / "main.py":
                            return ServiceState.RUNNING
                        continue
                    # A relative main.py cannot be tied to an installation safely.
                    return ServiceState.UNKNOWN
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                return ServiceState.UNKNOWN
    except psutil.Error:
        return ServiceState.UNKNOWN
    return ServiceState.OFFLINE


def _validate_output(installation: Path, output: Path) -> Path:
    resolved = output.expanduser().resolve()
    if output.exists():
        raise BackupError("invalid_output_path", "备份目标已存在；不会覆盖已有备份。")
    if not resolved.parent.is_dir():
        raise BackupError("invalid_output_path", "备份目标的父目录不存在或不可用。")
    installation = installation.resolve()
    paths = paths_for_installation(installation)
    protected = [paths.root_dir(), paths.userdata_root()]
    if any(resolved == installation or parent == resolved or resolved in parent.parents for parent in protected):
        raise BackupError("output_inside_installation", "备份目标必须位于安装目录及保护根之外。")
    try:
        resolved.relative_to(installation)
    except ValueError:
        return resolved
    raise BackupError("output_inside_installation", "备份目标必须位于安装目录外。")


def _manifest_root_entries() -> list[dict[str, Any]]:
    return [
        {"id": root.root_id, "path": root.relative_path.as_posix(), "required": root.required, "kind": root.kind}
        for root in PROTECTION_ROOTS
    ]


def _record_belongs_to_root(path: PurePosixPath, root_id: str) -> bool:
    if root_id == "data":
        return path.parts[0] == "data"
    if root_id == "userdata":
        return path.parts[0] == "userdata"
    if root_id == "config":
        return path == PurePosixPath("config.yaml")
    if root_id == "secrets_local":
        return path == PurePosixPath("secrets.local.yaml")
    if root_id == "legacy_private_assets":
        return path.parts[:2] in {("characters", "reality"), ("characters", "dream_presets"), ("characters", "dream_worlds"), ("assets", "stickers"), ("content", "characters")} or path.parts[0] == "characters"
    return False


def create_snapshot(
    installation: Path,
    output: Path,
    *,
    protection_mode: str,
    get_service_state: Callable[[Path], ServiceState] = service_state,
) -> dict[str, Any]:
    if protection_mode != PROTECTION_MODE_PROTECTED_VOLUME:
        raise BackupError("encryption_required", "首版只支持显式声明的受保护卷快照；便携加密归档尚未启用。")
    installation = installation.resolve()
    final = _validate_output(installation, output)
    state = get_service_state(installation)
    if state == ServiceState.RUNNING:
        raise BackupError("service_running", "检测到 PresenceKit 服务仍在运行；请手动停止后重试。")
    if state != ServiceState.OFFLINE:
        raise BackupError("service_state_unknown", "无法可靠确认服务已停止；为保护一致性，未创建备份。")
    files, optional_missing = _selected_files(installation)
    layout = _read_layout_marker(installation)
    temporary = final.parent / f".{final.name}.tmp-{uuid.uuid4().hex}"
    try:
        temporary.mkdir()
        records: list[dict[str, Any]] = []
        for source, root_id in files:
            relative = _relative_to_installation(installation, source)
            destination = temporary.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            before_hash = _sha256(source)
            size = source.stat().st_size
            shutil.copy2(source, destination)
            if destination.stat().st_size != size or _sha256(destination) != before_hash:
                raise BackupError("archive_unreadable", "复制后的快照文件校验失败。")
            records.append({"path": relative.as_posix(), "size": size, "sha256": before_hash, "root": root_id})
        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "product_version": _current_version(installation),
            "data_layout": layout,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "backup_id": str(uuid.uuid4()),
            "protection_mode": protection_mode,
            "private_roots_version": PRIVATE_ROOTS_VERSION,
            "included_roots": _manifest_root_entries(),
            "optional_missing_files": optional_missing,
            "files": records,
        }
        (temporary / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (temporary / MANIFEST_CHECKSUM_NAME).write_text(_sha256(temporary / MANIFEST_NAME) + "\n", encoding="ascii")
        verified = verify_snapshot(temporary)
        if not verified["ok"]:
            raise BackupError(verified["errors"][0]["code"], "临时快照内部校验失败。")
        os.replace(temporary, final)
        return {"ok": True, "backup_id": manifest["backup_id"], "backup_path": str(final), "file_count": len(records), "protection_mode": protection_mode}
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _read_manifest(snapshot: Path) -> dict[str, Any]:
    try:
        checksum = (snapshot / MANIFEST_CHECKSUM_NAME).read_text(encoding="ascii").strip()
        manifest_path = snapshot / MANIFEST_NAME
        if len(checksum) != 64 or _sha256(manifest_path) != checksum:
            raise BackupError("hash_mismatch", "manifest 完整性校验失败。")
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("manifest_invalid", "manifest 无法读取或不是有效 JSON。") from exc
    if not isinstance(payload, dict):
        raise BackupError("manifest_invalid", "manifest 顶层必须是对象。")
    if payload.get("manifest_version") != MANIFEST_VERSION:
        raise BackupError("unsupported_manifest_version", "不支持的 manifest 版本。")
    if not isinstance(payload.get("product_version"), str) or not isinstance(payload.get("data_layout"), dict):
        raise BackupError("manifest_invalid", "manifest 缺少产品或数据布局信息。")
    if not isinstance(payload["data_layout"].get("data_layout_schema_version"), int):
        raise BackupError("manifest_invalid", "manifest data layout schema 无效。")
    if payload.get("protection_mode") != PROTECTION_MODE_PROTECTED_VOLUME:
        raise BackupError("manifest_invalid", "manifest protection mode 无效。")
    if payload.get("private_roots_version") != PRIVATE_ROOTS_VERSION or payload.get("included_roots") != _manifest_root_entries():
        raise BackupError("manifest_invalid", "manifest 的保护根清单不一致。")
    if not isinstance(payload.get("optional_missing_files"), list) or not isinstance(payload.get("files"), list):
        raise BackupError("manifest_invalid", "manifest 文件清单无效。")
    optional = payload["optional_missing_files"]
    allowed_optional = {"userdata", "secrets.local.yaml"}
    if any(not isinstance(item, str) for item in optional) or len(optional) != len(set(optional)) or not set(optional).issubset(allowed_optional):
        raise BackupError("manifest_invalid", "manifest 的可选缺失状态无效。")
    return payload


def verify_snapshot(snapshot: Path) -> dict[str, Any]:
    snapshot = snapshot.expanduser().resolve()
    errors: list[dict[str, str]] = []
    try:
        if not snapshot.is_dir() or _is_reparse_point(snapshot):
            raise BackupError("archive_unreadable", "备份目录不可读取或不是普通目录。")
        manifest = _read_manifest(snapshot)
        expected: set[PurePosixPath] = {PurePosixPath(MANIFEST_NAME)}
        seen: set[PurePosixPath] = set()
        for record in manifest["files"]:
            if not isinstance(record, dict):
                raise BackupError("manifest_invalid", "manifest 文件条目无效。")
            path = record.get("path")
            size = record.get("size")
            checksum = record.get("sha256")
            root = record.get("root")
            allowed_roots = {item.root_id for item in PROTECTION_ROOTS}
            if not isinstance(path, str) or not isinstance(size, int) or size < 0 or not isinstance(checksum, str) or len(checksum) != 64 or root not in allowed_roots:
                raise BackupError("manifest_invalid", "manifest 文件条目字段无效。")
            relative = _safe_relative(PurePosixPath(path))
            if not _record_belongs_to_root(relative, root):
                raise BackupError("manifest_invalid", "manifest 文件条目与保护根不一致。")
            if relative in seen:
                raise BackupError("manifest_invalid", "manifest 包含重复文件条目。")
            seen.add(relative)
            expected.add(relative)
            candidate = snapshot.joinpath(*relative.parts)
            if not candidate.exists():
                errors.append({"code": "missing_file", "path": relative.as_posix()})
                continue
            if _is_reparse_point(candidate):
                errors.append({"code": "unsafe_link", "path": relative.as_posix()})
                continue
            if not candidate.is_file():
                errors.append({"code": "archive_unreadable", "path": relative.as_posix()})
                continue
            if candidate.stat().st_size != size:
                errors.append({"code": "size_mismatch", "path": relative.as_posix()})
                continue
            if _sha256(candidate) != checksum:
                errors.append({"code": "hash_mismatch", "path": relative.as_posix()})
        if PurePosixPath("config.yaml") not in seen:
            errors.append({"code": "missing_file", "path": "config.yaml"})
        if PurePosixPath("data/layout_version.json") not in seen:
            errors.append({"code": "missing_file", "path": "data/layout_version.json"})
        optional_missing = set(manifest["optional_missing_files"])
        for optional_path in ("secrets.local.yaml",):
            if (PurePosixPath(optional_path) in seen) == (optional_path in optional_missing):
                errors.append({"code": "manifest_invalid", "path": optional_path})
        actual: set[PurePosixPath] = set()
        for item in _walk_regular_files(snapshot):
            actual.add(PurePosixPath(item.relative_to(snapshot).as_posix()))
        expected.add(PurePosixPath(MANIFEST_CHECKSUM_NAME))
        extras = actual - expected
        for extra in sorted(extras):
            errors.append({"code": "manifest_invalid", "path": extra.as_posix()})
    except BackupError as exc:
        errors.append({"code": exc.code})
    return {"ok": not errors, "errors": errors}


_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _restore_safe_path(relative: PurePosixPath, staging: Path) -> PurePosixPath:
    _safe_relative(relative)
    key_parts = []
    for part in relative.parts:
        if ":" in part or part.endswith((".", " ")) or part.rstrip(". ").upper() in _WINDOWS_RESERVED:
            raise BackupError("unsafe_archive_path", "归档包含不安全的 Windows 路径。")
        key_parts.append(part.casefold())
    if len(str(staging.joinpath(*relative.parts))) >= 240:
        raise BackupError("unsafe_archive_path", "归档路径超过恢复目录的安全长度限制。")
    return PurePosixPath(*key_parts)


def _validate_restore_target(installation: Path, snapshot: Path, target: Path) -> tuple[Path, bool]:
    resolved = target.expanduser().resolve()
    if not resolved.parent.is_dir():
        raise BackupError("invalid_output_path", "恢复目标的父目录不存在。")
    if _is_reparse_point(resolved) if resolved.exists() else _is_reparse_point(resolved.parent):
        raise BackupError("unsafe_link", "恢复目标或其父目录不能是 reparse point。")
    installation = installation.resolve()
    paths = paths_for_installation(installation)
    if resolved == installation or any(
        resolved == root or root in resolved.parents
        for root in (paths.root_dir(), paths.userdata_root())
    ):
        raise BackupError("target_is_live_path", "恢复目标不能是当前安装或其私人状态根。")
    if resolved == snapshot or snapshot in resolved.parents:
        raise BackupError("target_is_live_path", "恢复目标不能位于备份快照内部。")
    existed = resolved.exists()
    if existed and (not resolved.is_dir() or any(resolved.iterdir())):
        raise BackupError("target_not_empty", "恢复目标必须不存在或是完全空的目录。")
    return resolved, existed


def _recovery_samples(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selectors = (("memory", "data/runtime/memory/"), ("authored_asset", "userdata/"), ("dream_or_state", "data/dream/"))
    samples = []
    for sample_type, prefix in selectors:
        record = next((item for item in records if str(item["path"]).startswith(prefix)), None)
        if record is not None:
            samples.append({"type": sample_type, "path": record["path"], "size": record["size"], "sha256": record["sha256"], "status": "matched"})
    return samples


def restore_snapshot(installation: Path, snapshot: Path, target: Path, *, startup_check: bool = True) -> dict[str, Any]:
    installation, snapshot = installation.resolve(), snapshot.expanduser().resolve()
    verified = verify_snapshot(snapshot)
    if not verified["ok"]:
        raise BackupError("backup_verify_failed", "备份校验失败，拒绝恢复。")
    manifest = _read_manifest(snapshot)
    final, existed_empty = _validate_restore_target(installation, snapshot, target)
    records = manifest["files"]
    if len(records) > MAX_RESTORE_FILES or sum(item["size"] for item in records) > MAX_RESTORE_BYTES:
        raise BackupError("archive_limit_exceeded", "备份文件数量或总大小超过恢复安全上限。")
    staging = final.parent / f".{final.name}.restore-tmp-{uuid.uuid4().hex}"
    seen: set[PurePosixPath] = set()
    started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    try:
        staging.mkdir()
        for record in records:
            relative = _safe_relative(PurePosixPath(record["path"]))
            key = _restore_safe_path(relative, staging)
            if key in seen:
                raise BackupError("path_collision", "归档包含 Windows 大小写冲突路径。")
            seen.add(key)
            source, destination = snapshot.joinpath(*relative.parts), staging.joinpath(*relative.parts)
            if _is_reparse_point(source):
                raise BackupError("unsafe_link", "归档包含 reparse point。")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if destination.stat().st_size != record["size"] or _sha256(destination) != record["sha256"]:
                raise BackupError("hash_mismatch", "恢复后的文件 hash 不匹配。")
        validation = {"startup_check": "skipped"}
        if startup_check:
            from core.recovery_validation import RecoveryValidationError, validate_restored_initialization
            try:
                validation = validate_restored_initialization(staging, live_root=installation)
            except RecoveryValidationError as exc:
                raise BackupError(exc.code, str(exc)) from exc
        report = {
            "backup_id": manifest["backup_id"], "manifest_version": manifest["manifest_version"],
            "source_product_version": manifest["product_version"], "source_layout": manifest["data_layout"],
            "target_path": str(final), "restore_started_at": started,
            "restore_completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "files_expected": len(records), "files_restored": len(records),
            "bytes_restored": sum(item["size"] for item in records), "hash_verification": "ok",
            "layout_validation": "ok", **validation, "manual_samples": _recovery_samples(records), "overall_status": "ok",
        }
        report_path = staging / ".presencekit-recovery" / "recovery-report.json"
        report_path.parent.mkdir()
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if existed_empty:
            final.rmdir()
            # A pre-existing empty directory cannot be atomically replaced on
            # Windows.  Publish only after every check, and move its already
            # verified top-level children; no user content ever existed here.
            final.mkdir()
            for child in staging.iterdir():
                child.rename(final / child.name)
            staging.rmdir()
        else:
            staging.rename(final)
        return {"ok": True, "backup_id": manifest["backup_id"], "target_path": str(final), "report_path": str(final / ".presencekit-recovery" / "recovery-report.json")}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or verify offline PresenceKit private-state snapshots.")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--quiet", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--output", required=True)
    create.add_argument("--protection-mode", choices=[PROTECTION_MODE_PROTECTED_VOLUME], required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("backup_path")
    restore = sub.add_parser("restore")
    restore.add_argument("backup_path")
    restore.add_argument("--target", required=True)
    restore.add_argument("--no-startup-check", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create_snapshot(Path.cwd(), Path(args.output), protection_mode=args.protection_mode)
        elif args.command == "verify":
            result = verify_snapshot(Path(args.backup_path))
            if not result["ok"]:
                raise BackupError(result["errors"][0]["code"], "备份校验失败。")
        else:
            result = restore_snapshot(Path.cwd(), Path(args.backup_path), Path(args.target), startup_check=not args.no_startup_check)
        if args.json_output:
            print(json.dumps(result, ensure_ascii=False))
        elif not args.quiet:
            print("备份已创建并完成内部校验。" if args.command == "create" else ("备份校验通过。" if args.command == "verify" else "恢复完成并通过离线初始化验证。"))
        return 0
    except BackupError as exc:
        result = {"ok": False, "error": {"code": exc.code}}
        if args.json_output:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"[backup-state:{exc.code}] {exc}", file=sys.stderr)
        return 1
