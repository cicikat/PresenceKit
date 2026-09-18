"""Read-only fs_list / fs_read with backend vs external classification.

Writes stay out of this module. Sensitive export goes through
``core.sensitive_redaction`` before any truncate or model-facing cache.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

from core.sensitive_redaction import (
    REDACTION_VERSION,
    HighRiskDecision,
    RedactionError,
    inspect_high_risk,
    observability_snapshot as redaction_observability,
    redact_for_export,
)

_DEFAULT_MAX_READ_CHARS = 12_000
_HARD_MAX_READ_CHARS = 32_000
_DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024
_HARD_MAX_FILE_BYTES = 8 * 1024 * 1024
_DEFAULT_MAX_LIST_ENTRIES = 100
_HARD_MAX_LIST_ENTRIES = 200
_DEFAULT_MAX_DEPTH = 2
_HARD_MAX_DEPTH = 3
_DEFAULT_READ_SECONDS = 5.0
_HARD_READ_SECONDS = 15.0
_MAX_PATH_CHARS = 1024

_TEXT_EXTENSIONS: frozenset[str] = frozenset({
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml",
    ".csv", ".log", ".html", ".htm", ".ini", ".css", ".xml", ".sh", ".bat",
    ".cfg", ".conf", ".c", ".h", ".cpp", ".hpp", ".rs", ".go", ".java",
    ".rb", ".php", ".sql", ".r", ".lua", ".ps1", ".cmd",
})
_OFFICE_EXTENSIONS: frozenset[str] = frozenset({".docx", ".doc"})
_DEVICE_NAMES: frozenset[str] = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
})


class FsAccessError(ValueError):
    """fs_list/fs_read guard rejection; ``code`` is the frozen deny code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fs_config() -> dict:
    from core.config_loader import get_config
    return get_config().get("fs_access", {}) or {}


def _clamp_int(value, default: int, lo: int, hi: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(lo, min(hi, number))


def _clamp_float(value, default: float, lo: float, hi: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(lo, min(hi, number))


def _legacy_enabled() -> bool:
    return bool(_fs_config().get("enabled", False))


def _backend_enabled() -> bool:
    cfg = _fs_config()
    if "backend_read" in cfg:
        return bool(cfg.get("backend_read"))
    return True


def _external_enabled() -> bool:
    cfg = _fs_config()
    if "external_read" in cfg:
        return bool(cfg.get("external_read"))
    return _legacy_enabled()


def _allow_roots() -> list[Path]:
    roots = _fs_config().get("allow_roots") or []
    resolved: list[Path] = []
    for raw in roots:
        try:
            path = Path(str(raw)).expanduser()
            if _lexical_denial(str(path)) is not None:
                continue
            resolved.append(path.resolve())
        except OSError:
            continue
    return resolved


def _max_read_chars() -> int:
    return _clamp_int(
        _fs_config().get("max_read_chars", _DEFAULT_MAX_READ_CHARS),
        _DEFAULT_MAX_READ_CHARS, 1, _HARD_MAX_READ_CHARS,
    )


def _max_file_bytes() -> int:
    return _clamp_int(
        _fs_config().get("max_file_bytes", _DEFAULT_MAX_FILE_BYTES),
        _DEFAULT_MAX_FILE_BYTES, 1, _HARD_MAX_FILE_BYTES,
    )


def _max_list_entries() -> int:
    return _clamp_int(
        _fs_config().get("max_list_entries", _DEFAULT_MAX_LIST_ENTRIES),
        _DEFAULT_MAX_LIST_ENTRIES, 1, _HARD_MAX_LIST_ENTRIES,
    )


def _max_depth(requested: int) -> int:
    configured = _clamp_int(
        _fs_config().get("max_list_depth", _DEFAULT_MAX_DEPTH),
        _DEFAULT_MAX_DEPTH, 1, _HARD_MAX_DEPTH,
    )
    try:
        depth = int(requested)
    except (TypeError, ValueError):
        depth = 1
    if depth not in (1, 2, 3):
        depth = 1
    return min(depth, configured)


def _read_seconds() -> float:
    return _clamp_float(
        _fs_config().get("max_read_seconds", _DEFAULT_READ_SECONDS),
        _DEFAULT_READ_SECONDS, 0.1, _HARD_READ_SECONDS,
    )


def _project_data_dir() -> Path:
    from core.sandbox import get_paths
    return get_paths().root_dir().resolve()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _lexical_denial(raw: str) -> str | None:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > _MAX_PATH_CHARS:
        return "path_not_found"
    if "\x00" in raw:
        return "path_not_found"
    unified = raw.replace("/", "\\")
    lowered = unified.lower()
    if lowered.startswith("\\\\.\\") or lowered.startswith("//./"):
        return "device_path_denied"
    if lowered.startswith("\\\\?\\unc\\") or lowered.startswith("//?/unc/"):
        return "unc_network_denied"
    if lowered.startswith("\\\\") or lowered.startswith("//"):
        rest = unified.lstrip("\\/")
        if rest.lower().startswith("?\\"):
            # \\?\C:\... long path, not UNC
            inner = rest[2:] if rest.lower().startswith("?\\") else rest
            if inner.lower().startswith("unc\\"):
                return "unc_network_denied"
        else:
            return "unc_network_denied"
    stem = Path(raw).name.split(".")[0].lower()
    if stem in _DEVICE_NAMES:
        return "device_path_denied"
    # ADS: extra colon after the drive letter (C:\file.txt:stream)
    if os.name == "nt":
        body = unified
        if len(body) >= 2 and body[1] == ":":
            body = body[2:]
        if ":" in body:
            return "ads_denied"
    return None


def _path_has_reparse(path: Path) -> bool:
    parts = path.parts
    if not parts:
        return False
    cursor = Path(parts[0])
    chain = [cursor]
    for part in parts[1:]:
        cursor = cursor / part
        chain.append(cursor)
    for item in chain:
        try:
            if item.exists() and item.is_symlink():
                return True
            st = item.lstat()
        except OSError:
            continue
        mode = getattr(st, "st_file_attributes", 0)
        if mode & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT
            return True
        if stat.S_ISLNK(st.st_mode):
            return True
    return False


def _final_path(path: Path) -> Path:
    try:
        return Path(os.path.realpath(path))
    except OSError:
        return path.resolve(strict=False)


def _principal(user_id: str | None, char_id: str | None) -> tuple[str, str]:
    uid = str(user_id or "").strip()
    cid = str(char_id or "").strip()
    if not uid or not cid:
        raise FsAccessError("grant_principal_mismatch")
    from core.data_paths import safe_user_id
    try:
        return safe_user_id(uid), safe_user_id(cid)
    except ValueError as exc:
        raise FsAccessError("grant_principal_mismatch") from exc


def _isolation_denial(resolved: Path, uid: str, char_id: str) -> str | None:
    from core.sandbox import get_paths
    paths = get_paths()
    data_root = _project_data_dir()
    if not _is_within(resolved, data_root):
        return None

    def _root(fn, *args, **kwargs) -> Path | None:
        try:
            return fn(*args, **kwargs).resolve()
        except Exception:
            return None

    auth = _root(paths.auth_dir)
    if auth is not None and _is_within(resolved, auth):
        return "credential_store_denied"

    dreams = _root(lambda: paths._p("runtime", "dreams"))
    if dreams is not None and _is_within(resolved, dreams):
        return "dream_isolation_denied"

    private = _root(paths.private_exchange_dir)
    if private is not None and _is_within(resolved, private):
        return "dream_isolation_denied"

    meta = _root(lambda: paths._p("runtime", "self_meta"))
    if meta is not None and _is_within(resolved, meta):
        return "audit_store_denied"

    own_memory = _root(paths.user_memory_root, uid, char_id=char_id)
    own_library = _root(paths.character_document_root, uid, char_id=char_id)
    own_artifacts = _root(paths.chat_artifacts_dir, uid, char_id=char_id)
    own_identity = _root(paths.user_identity_dir, char_id=char_id)
    own_runtime_char = _root(paths.runtime_character_dir, char_id=char_id)
    own_chars = _root(lambda: paths._p("chars", char_id))

    scoped_roots = (
        ("runtime", "memory"),
        ("runtime", "character_library"),
        ("runtime", "chat_artifacts"),
        ("runtime", "self"),
    )
    rel = resolved.relative_to(data_root)
    rel_parts = rel.parts
    for prefix in scoped_roots:
        if rel_parts[: len(prefix)] != prefix:
            continue
        rest = rel_parts[len(prefix):]
        if not rest:
            return None
        if rest[0] != char_id:
            return "cross_char_denied"
        if len(rest) >= 2 and rest[1] != uid:
            return "cross_owner_denied"
        return None

    if rel_parts[:1] == ("chars",) and len(rel_parts) >= 2 and rel_parts[1] != char_id:
        return "cross_char_denied"
    if rel_parts[:2] == ("runtime", "characters") and len(rel_parts) >= 3 and rel_parts[2] != char_id:
        return "cross_char_denied"

    for own in (own_memory, own_library, own_artifacts, own_identity, own_runtime_char, own_chars):
        if own is not None and _is_within(resolved, own):
            return None
    return None


def _is_backend(resolved: Path) -> bool:
    if _is_within(resolved, _project_data_dir()):
        return True
    if _is_within(resolved, _repo_root()):
        return True
    try:
        from core.data_paths import _CONFIG_PATH
        return resolved == Path(_CONFIG_PATH).resolve()
    except Exception:
        return False


def _classify(resolved: Path) -> str:
    return "backend" if _is_backend(resolved) else "external"


def _remote_blocks(kind: str) -> None:
    from core.deployment_capabilities import is_remote_server
    if is_remote_server() and kind == "external":
        raise FsAccessError("disabled_remote_server_local_capability")


def _require_kind(kind: str) -> None:
    _remote_blocks(kind)
    if kind == "backend" and not _backend_enabled():
        raise FsAccessError("backend_read_disabled")
    if kind == "external" and not _external_enabled():
        raise FsAccessError("external_read_disabled")


def _high_risk_or_raise(path: Path, data: bytes | None = None) -> None:
    decision: HighRiskDecision = inspect_high_risk(
        name=path.name, data=data, parts=path.parts,
    )
    if decision.denied:
        raise FsAccessError(decision.code or "high_risk_secret_denied")


def _resolve_candidate(raw_path: str) -> Path:
    denial = _lexical_denial(raw_path)
    if denial:
        raise FsAccessError(denial)
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        roots = _allow_roots()
        matches = []
        for root in roots:
            probe = root / candidate
            try:
                if probe.exists():
                    matches.append(probe)
            except OSError:
                continue
        if len(matches) > 1:
            raise FsAccessError("path_not_found")
        if matches:
            candidate = matches[0]
        elif len(roots) == 1:
            candidate = roots[0] / candidate
        else:
            raise FsAccessError("path_not_found")
    try:
        if _path_has_reparse(candidate):
            raise FsAccessError("reparse_denied")
        resolved = _final_path(candidate)
    except FsAccessError:
        raise
    except OSError as exc:
        err = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
        if err in {5, 13, 32}:
            raise FsAccessError("os_permission_denied") from exc
        raise FsAccessError("path_not_found") from exc
    if _path_has_reparse(resolved):
        raise FsAccessError("reparse_denied")
    lexical = _lexical_denial(str(resolved))
    if lexical:
        raise FsAccessError(lexical)
    return resolved


def _resolve_and_guard(
    raw_path: str,
    *,
    user_id: str | None,
    char_id: str | None,
) -> tuple[Path, str]:
    uid, cid = _principal(user_id, char_id)
    resolved = _resolve_candidate(raw_path)
    kind = _classify(resolved)
    _require_kind(kind)
    isolated = _isolation_denial(resolved, uid, cid)
    if isolated:
        raise FsAccessError(isolated)
    _high_risk_or_raise(resolved)
    return resolved, kind


def _fail(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    if isinstance(exc, PermissionError):
        return "os_permission_denied"
    if isinstance(exc, RedactionError):
        return "sensitive_redaction_failed"
    if isinstance(exc, TimeoutError):
        return "read_budget_exceeded"
    return "path_not_found"


def fs_list(
    path: str | None = None,
    depth: int = 1,
    *,
    user_id: str | None = None,
    char_id: str | None = None,
) -> str:
    started = time.monotonic()
    budget = _read_seconds()
    try:
        if not path:
            return _discovery_map()
        resolved, _kind = _resolve_and_guard(path, user_id=user_id, char_id=char_id)
        if time.monotonic() - started > budget:
            raise FsAccessError("read_budget_exceeded")
        if not resolved.exists():
            raise FsAccessError("path_not_found")
        if resolved.is_file():
            size_kb = resolved.stat().st_size / 1024
            return f"{resolved.name}（文件，{size_kb:.1f} KB）"
        if not resolved.is_dir():
            raise FsAccessError("not_a_directory")
        max_entries = _max_list_entries()
        lines, truncated = _list_dir_entries(
            resolved, _max_depth(depth), max_entries, started, budget,
            user_id=user_id, char_id=char_id,
        )
        if not lines:
            return "（空目录）"
        text = "\n".join(lines)
        if truncated:
            text += f"\n（已达 {max_entries} 条上限，未列出全部）\nlist_limit_exceeded"
        return text
    except FsAccessError as exc:
        return _fail(exc)
    except PermissionError:
        return "os_permission_denied"
    except OSError as exc:
        return _fail(exc)


def _discovery_map() -> str:
    lines = ["可浏览入口（发现提示，不是唯一准入）："]
    if _backend_enabled():
        lines.append("backend: 本进程仓库与内部文件（脱敏后只读）")
    else:
        lines.append("backend_read_disabled")
    if _external_enabled():
        from core.deployment_capabilities import is_remote_server
        if is_remote_server():
            lines.append("external: disabled_remote_server_local_capability")
        else:
            roots = _fs_config().get("allow_roots") or []
            if roots:
                lines.append("external 常用目录：")
                for raw in roots:
                    lines.append(f"{raw}/")
            else:
                lines.append("external: 提供绝对路径读取本机普通文件；不枚举全盘")
    else:
        lines.append("external_read_disabled")
    if len(lines) == 1:
        return "backend_read_disabled"
    return "\n".join(lines)


def _list_dir_entries(
    root: Path,
    depth: int,
    max_entries: int,
    started: float,
    budget: float,
    *,
    user_id: str | None,
    char_id: str | None,
) -> tuple[list[str], bool]:
    lines: list[str] = []
    truncated = False
    uid, cid = _principal(user_id, char_id)

    def _emit(directory: Path, prefix: str, remaining_depth: int) -> None:
        nonlocal truncated
        if time.monotonic() - started > budget:
            raise FsAccessError("read_budget_exceeded")
        try:
            children = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError as exc:
            raise FsAccessError("os_permission_denied") from exc
        except OSError:
            return
        for child in children:
            if len(lines) >= max_entries:
                truncated = True
                return
            if child.is_symlink() or _path_has_reparse(child):
                continue
            try:
                final = _final_path(child)
            except OSError:
                continue
            isolated = _isolation_denial(final, uid, cid)
            if isolated:
                continue
            risk = inspect_high_risk(name=child.name, parts=child.parts)
            if risk.denied:
                continue
            if child.is_dir():
                lines.append(f"{prefix}{child.name}/")
                if remaining_depth > 1:
                    _emit(child, prefix + "  ", remaining_depth - 1)
            else:
                try:
                    size_kb = child.stat().st_size / 1024
                except OSError:
                    size_kb = 0.0
                lines.append(f"{prefix}{child.name}（{size_kb:.1f} KB）")

    _emit(root, "", depth)
    return lines, truncated


def _decode_text(raw: bytes) -> str | None:
    if b"\x00" in raw[:4096]:
        return None
    for encoding in ("utf-8", "gbk"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" in text:
            return None
        return text
    return None


def _parse_non_text(raw: bytes, filename: str, suffix: str) -> str | None:
    if suffix in _OFFICE_EXTENSIONS:
        from core.media_processor import MediaIngestError, parse_file_bytes
        try:
            return parse_file_bytes(raw, filename)
        except MediaIngestError:
            return None
    return None


def fs_read(
    path: str,
    offset: int = 0,
    *,
    user_id: str | None = None,
    char_id: str | None = None,
) -> str:
    started = time.monotonic()
    budget = _read_seconds()
    try:
        resolved, _kind = _resolve_and_guard(path, user_id=user_id, char_id=char_id)
        if time.monotonic() - started > budget:
            raise FsAccessError("read_budget_exceeded")
        if not resolved.exists():
            raise FsAccessError("path_not_found")
        if resolved.is_dir():
            raise FsAccessError("not_a_file")
        if not resolved.is_file():
            raise FsAccessError("not_a_file")
        size = resolved.stat().st_size
        limit = _max_file_bytes()
        if size > limit:
            raise FsAccessError("file_size_limit_exceeded")
        raw = resolved.read_bytes()
        try:
            after = resolved.stat()
        except OSError as exc:
            raise FsAccessError("path_not_found") from exc
        if int(after.st_size) != int(size):
            raise FsAccessError("path_not_found")
        _high_risk_or_raise(resolved, raw)
        if time.monotonic() - started > budget:
            raise FsAccessError("read_budget_exceeded")
        suffix = resolved.suffix.lower()
        if suffix in _OFFICE_EXTENSIONS:
            parsed = _parse_non_text(raw, resolved.name, suffix)
            if parsed is None:
                raise FsAccessError("unsupported_file_type")
            text = parsed
        else:
            text = _decode_text(raw)
            if text is None:
                raise FsAccessError("unsupported_file_type")
        try:
            redacted = redact_for_export(text)
        except RedactionError as exc:
            raise FsAccessError("sensitive_redaction_failed") from exc
        except Exception as exc:
            raise FsAccessError("sensitive_redaction_failed") from exc
        try:
            start = max(0, int(offset))
        except (TypeError, ValueError):
            start = 0
        max_chars = _max_read_chars()
        page = redacted[start: start + max_chars]
        if len(redacted) > start + max_chars:
            return page + f"\n（文件共 {len(redacted)} 字，已截断，可指定更精确的问题）"
        return page
    except FsAccessError as exc:
        return _fail(exc)
    except PermissionError:
        return "os_permission_denied"
    except RedactionError:
        return "sensitive_redaction_failed"
    except OSError as exc:
        return _fail(exc)


def effective_state() -> dict:
    from core.deployment_capabilities import is_remote_server
    cfg = _fs_config()
    remote = is_remote_server()
    backend_configured = True
    backend_on = _backend_enabled()
    if remote:
        backend_reason = "" if backend_on else "backend_read_disabled"
        backend_effective = backend_on
    else:
        backend_reason = "" if backend_on else "backend_read_disabled"
        backend_effective = backend_on
    external_on = _external_enabled()
    if remote:
        external_reason = "disabled_remote_server_local_capability"
        external_effective = False
    elif not external_on:
        external_reason = "external_read_disabled"
        external_effective = False
    else:
        external_reason = ""
        external_effective = True
    roots = _allow_roots()
    return {
        "enabled": backend_effective or external_effective,
        "configured": bool(roots) or backend_configured,
        "effective": backend_effective or external_effective,
        "blocking_reason": (
            "" if (backend_effective or external_effective)
            else (external_reason or backend_reason or "disabled")
        ),
        "source": "fs_access",
        "allow_roots": [str(p) for p in roots],
        "legacy_enabled": _legacy_enabled(),
        "backend_read": {
            "configured": backend_configured,
            "effective": backend_effective,
            "blocking_reason": backend_reason,
        },
        "external_read": {
            "configured": bool(roots) or ("external_read" in cfg) or _legacy_enabled(),
            "effective": external_effective,
            "blocking_reason": external_reason,
            "allow_roots_count": len(roots),
        },
        "limits": {
            "max_read_chars": _max_read_chars(),
            "max_file_bytes": _max_file_bytes(),
            "max_list_entries": _max_list_entries(),
            "max_read_seconds": _read_seconds(),
        },
        "redaction": {
            "version": REDACTION_VERSION,
            "counts": redaction_observability()["counts"],
        },
        "note": (
            "backend 默认只读；allow_roots 是外部发现提示而不是唯一准入。"
            "秘密出口先脱敏再截断。工具还需在当前角色和模型中可见。"
        ),
    }


def observability_snapshot() -> dict:
    state = effective_state()
    return {
        "capability": "backend-read.v1",
        "backend_read": state["backend_read"],
        "external_read": {
            "configured": state["external_read"]["configured"],
            "effective": state["external_read"]["effective"],
            "blocking_reason": state["external_read"]["blocking_reason"],
            "allow_roots_count": state["external_read"]["allow_roots_count"],
        },
        "legacy_enabled": state["legacy_enabled"],
        "limits": state["limits"],
        "redaction": state["redaction"],
        "effective": state["effective"],
        "blocking_reason": state["blocking_reason"],
        "note": "metadata only; no file bodies, secrets, or full paths",
    }
