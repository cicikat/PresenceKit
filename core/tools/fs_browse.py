"""只读文件浏览工具：fs_list / fs_read。

范围严格限于 config.fs_access.allow_roots，不新增任何写入入口
（唯一写出口仍是 core/tools/toybox.py 的 write_toy_file）。
路径越界防御复用 toybox 的 resolve() + relative_to() 包含校验模式。
"""

from pathlib import Path

_DEFAULT_MAX_READ_CHARS = 10000
_DEFAULT_MAX_LIST_ENTRIES = 100
_MAX_READ_FILE_BYTES = 5 * 1024 * 1024

# 不可移除的 deny_names 底线集，config.fs_access.deny_names 只能追加，不能替换或清空。
_DENY_NAMES_BASELINE: frozenset[str] = frozenset({
    "secrets", ".env", ".git", "node_modules", "__pycache__", "config.yaml", "token",
})

_TEXT_EXTENSIONS: frozenset[str] = frozenset({
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".toml",
    ".csv", ".log", ".html", ".htm", ".ini", ".css", ".xml", ".sh", ".bat",
    ".cfg", ".conf",
})


class FsAccessError(ValueError):
    """fs_list/fs_read 守卫拒绝时抛出，消息本身即用户可见文案。"""


def _fs_config() -> dict:
    from core.config_loader import get_config
    return get_config().get("fs_access", {}) or {}


def _is_enabled() -> bool:
    from core.deployment_capabilities import is_remote_server
    if is_remote_server():
        return False
    return bool(_fs_config().get("enabled", False))


def _deny_names() -> frozenset[str]:
    extra = _fs_config().get("deny_names") or []
    return _DENY_NAMES_BASELINE | {str(n).lower() for n in extra}


def _allow_roots() -> list[Path]:
    roots = _fs_config().get("allow_roots") or []
    resolved = []
    for r in roots:
        try:
            resolved.append(Path(r).resolve())
        except OSError:
            continue
    return resolved


def _project_data_dir() -> Path:
    from core.sandbox import get_paths
    return get_paths().root_dir().resolve()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _check_deny_names(path: Path) -> None:
    deny = _deny_names()
    for part in path.parts:
        lowered = part.lower()
        if any(bad in lowered for bad in deny):
            raise FsAccessError(f"路径包含被禁止访问的名称：{part}")


def _resolve_and_guard(raw_path: str) -> Path:
    """共同守卫 2-4：allow_roots 包含校验、deny_names、软链拒绝、data/ 隐式拒绝。"""
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        roots = _allow_roots()
        matches = [root / candidate for root in roots if (root / candidate).exists()]
        if len(matches) > 1:
            raise FsAccessError("多个授权目录存在同名文件，请提供完整路径")
        if matches:
            candidate = matches[0]
        elif len(roots) == 1:
            candidate = roots[0] / candidate
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise FsAccessError("路径无法解析") from exc

    if _is_within(resolved, _project_data_dir()):
        raise FsAccessError("这是项目内部沙盒目录，不能浏览")

    roots = _allow_roots()
    if not roots or not any(_is_within(resolved, root) for root in roots):
        raise FsAccessError("这个路径不在允许浏览的范围内")

    _check_deny_names(candidate)
    _check_deny_names(resolved)

    if candidate.is_symlink():
        raise FsAccessError("不支持读取软链接")

    return resolved


def fs_list(path: str | None = None, depth: int = 1) -> str:
    from core.deployment_capabilities import is_remote_server
    if is_remote_server():
        return "disabled_remote_server_local_capability"
    if not _is_enabled():
        return "文件浏览未开启"

    if not path:
        roots = _fs_config().get("allow_roots") or []
        if not roots:
            return "还没有配置可浏览的目录"
        return "可浏览的目录：\n" + "\n".join(f"{r}/" for r in roots)

    try:
        resolved = _resolve_and_guard(path)
    except FsAccessError as exc:
        return str(exc)

    if not resolved.exists():
        return "路径不存在"

    if resolved.is_file():
        size_kb = resolved.stat().st_size / 1024
        return f"{resolved.name}（文件，{size_kb:.1f} KB）"

    depth = 2 if depth == 2 else 1
    cfg = _fs_config()
    max_entries = int(cfg.get("max_list_entries", _DEFAULT_MAX_LIST_ENTRIES))
    lines, truncated = _list_dir_entries(resolved, depth, max_entries)
    if not lines:
        return "（空目录）"
    text = "\n".join(lines)
    if truncated:
        text += f"\n（已达 {max_entries} 条上限，未列出全部）"
    return text


def _list_dir_entries(root: Path, depth: int, max_entries: int) -> tuple[list[str], bool]:
    lines: list[str] = []
    truncated = False

    def _emit(d: Path, prefix: str, remaining_depth: int) -> None:
        nonlocal truncated
        try:
            children = sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            return
        for child in children:
            if len(lines) >= max_entries:
                truncated = True
                return
            if child.is_symlink():
                continue
            try:
                _check_deny_names(child)
            except FsAccessError:
                continue
            if _is_within(child, _project_data_dir()):
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


def fs_read(path: str) -> str:
    from core.deployment_capabilities import is_remote_server
    if is_remote_server():
        return "disabled_remote_server_local_capability"
    if not _is_enabled():
        return "文件浏览未开启"

    try:
        resolved = _resolve_and_guard(path)
    except FsAccessError as exc:
        return str(exc)

    if not resolved.exists():
        return "文件不存在"
    if not resolved.is_file():
        return "这是一个目录，请用 fs_list 浏览"

    size = resolved.stat().st_size
    if size > _MAX_READ_FILE_BYTES:
        return f"文件过大（{size / 1024 / 1024:.1f} MB），超过 5MB 上限，不读取"

    if resolved.suffix.lower() not in _TEXT_EXTENSIONS:
        return f"这是二进制/不支持的文件类型，大小 {size / 1024:.1f} KB"

    raw = resolved.read_bytes()
    text = None
    for encoding in ("utf-8", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None or "\x00" in text:
        return f"这是二进制/不支持的文件类型，大小 {size / 1024:.1f} KB"

    cfg = _fs_config()
    max_chars = int(cfg.get("max_read_chars", _DEFAULT_MAX_READ_CHARS))
    if len(text) > max_chars:
        return text[:max_chars] + f"\n（文件共 {len(text)} 字，已截断，可指定更精确的问题）"
    return text


def effective_state() -> dict:
    from core.deployment_capabilities import is_remote_server
    cfg = _fs_config()
    enabled = bool(cfg.get("enabled", False))
    roots = _allow_roots()
    reason = "remote_server" if is_remote_server() else "disabled" if not enabled else "no_allowed_roots" if not roots else ""
    return {"enabled": enabled, "configured": bool(roots), "effective": not bool(reason),
            "blocking_reason": reason, "source": "fs_access", "allow_roots": [str(p) for p in roots],
            "note": "只读授权目录；内部数据与敏感名称仍拒绝。工具还需在当前角色和模型中可见。"}
