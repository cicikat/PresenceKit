"""Whitelisted text-only toy files for character-side reading and writing."""

from pathlib import Path

from core.safe_write import safe_write_text
from core.sandbox import get_paths

_TOYBOX_FILES: dict[str, str] = {
    "diary": "思考笔记.txt",
    "wishlist": "愿望清单.md",
    "doodle": "涂鸦板.txt",
}
_TOY_FILE_CHAR_CAP = 4000


def _assert_within(root: Path, candidate: Path) -> None:
    root_absolute = root.absolute()
    root_resolved = root.resolve()
    if root_resolved != root_absolute:
        raise ValueError("玩具箱目录不是安全的真实目录")
    try:
        candidate.resolve().relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("玩具文件路径越过了沙盒边界") from exc


def _resolve_toy_path(file_key: str) -> Path:
    if not isinstance(file_key, str) or file_key not in _TOYBOX_FILES:
        raise ValueError("未知的玩具文件")

    root = get_paths().very_formal_project_dir()
    target = root / _TOYBOX_FILES[file_key]
    _assert_within(root, target)
    _assert_within(root, target.with_suffix(target.suffix + ".tmp"))
    return target


def read_toy_file(file_key: str) -> str:
    target = _resolve_toy_path(file_key)
    if not target.exists():
        return "这个玩具文件还是空的。"
    if not target.is_file():
        raise ValueError("玩具文件不是普通文本文件")

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("玩具文件不是 UTF-8 文本") from exc
    if len(content) > _TOY_FILE_CHAR_CAP:
        return content[:_TOY_FILE_CHAR_CAP] + "\n（内容过长，读取结果已截断）"
    return content


def write_toy_file(file_key: str, content: str, mode: str = "overwrite") -> str:
    if not isinstance(content, str):
        raise ValueError("玩具文件只接受文本内容")
    if mode not in {"overwrite", "append"}:
        raise ValueError("写入模式只能是 overwrite 或 append")
    if len(content) > _TOY_FILE_CHAR_CAP:
        raise ValueError(f"单次写入不能超过 {_TOY_FILE_CHAR_CAP} 字")

    target = _resolve_toy_path(file_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = _resolve_toy_path(file_key)

    combined = content
    if mode == "append" and target.exists():
        if not target.is_file():
            raise ValueError("玩具文件不是普通文本文件")
        try:
            combined = target.read_text(encoding="utf-8") + content
        except UnicodeDecodeError as exc:
            raise ValueError("玩具文件不是 UTF-8 文本") from exc
    if len(combined) > _TOY_FILE_CHAR_CAP:
        raise ValueError(f"玩具文件总长度不能超过 {_TOY_FILE_CHAR_CAP} 字")

    target = _resolve_toy_path(file_key)
    if not safe_write_text(target, combined):
        raise OSError("玩具文件写入失败")
    return "玩具文件写好了。"
