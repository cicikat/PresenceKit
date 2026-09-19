"""Thin compatibility mapping from legacy toy file_key values onto self files.

The old shared ``data/very_formal_project/`` writer is frozen. Reads and
writes go through the unified character-self store. Dual-write is forbidden.
"""

from __future__ import annotations

from typing import Any

from core.character_self import (
    AGENT_MD_REL,
    append_self_text,
    create_self,
    read_self,
    update_self,
)

# Historical enum keys → self relative paths (notes/ is an example directory).
TOY_KEY_TO_SELF_PATH: dict[str, str] = {
    "diary": "notes/思考笔记.txt",
    "wishlist": "notes/愿望清单.md",
    "doodle": "notes/涂鸦板.txt",
}
_TOYBOX_FILES = TOY_KEY_TO_SELF_PATH  # backward alias for tests that patched the map
_TOY_FILE_CHAR_CAP = 4000
_EMPTY = "这个玩具文件还是空的。"
_FROZEN_WRITER = "旧玩具箱写入器已冻结；请使用 self 文件工具或兼容 file_key。"


def mapped_self_path(file_key: str) -> str:
    if not isinstance(file_key, str) or file_key not in TOY_KEY_TO_SELF_PATH:
        raise ValueError("未知的玩具文件")
    if file_key == AGENT_MD_REL:
        raise ValueError("未知的玩具文件")
    return TOY_KEY_TO_SELF_PATH[file_key]


def _require_scope(user_id: str | None, char_id: str | None) -> tuple[str, str]:
    uid = str(user_id or "").strip()
    cid = str(char_id or "").strip()
    if not uid or not cid:
        raise ValueError("玩具文件需要冻结的用户与角色范围")
    return uid, cid


def _denied_message(payload: dict[str, Any]) -> str:
    code = str(payload.get("code") or "self_path_denied")
    return f"玩具文件不可用：{code}"


def read_toy_file(file_key: str, *, user_id: str | None = None, char_id: str | None = None) -> str:
    path = mapped_self_path(file_key)
    uid, cid = _require_scope(user_id, char_id)
    result = read_self(path, user_id=uid, char_id=cid, origin="tool")
    if not result.get("ok"):
        code = result.get("code")
        if code in {"path_not_found", "not_a_file"}:
            return _EMPTY
        return _denied_message(result)
    content = str(result.get("content") or "")
    if not content.strip():
        return _EMPTY
    if len(content) > _TOY_FILE_CHAR_CAP:
        return content[:_TOY_FILE_CHAR_CAP] + "\n（内容过长，读取结果已截断）"
    return content


def write_toy_file(
    file_key: str,
    content: str,
    mode: str = "overwrite",
    *,
    user_id: str | None = None,
    char_id: str | None = None,
) -> str:
    if not isinstance(content, str):
        raise ValueError("玩具文件只接受文本内容")
    if mode not in {"overwrite", "append"}:
        raise ValueError("写入模式只能是 overwrite 或 append")
    if len(content) > _TOY_FILE_CHAR_CAP:
        raise ValueError(f"单次写入不能超过 {_TOY_FILE_CHAR_CAP} 字")
    path = mapped_self_path(file_key)
    uid, cid = _require_scope(user_id, char_id)
    if mode == "append":
        current = read_self(path, user_id=uid, char_id=cid, origin="tool")
        if current.get("ok") and len(str(current.get("content") or "") + content) > _TOY_FILE_CHAR_CAP:
            raise ValueError(f"玩具文件总长度不能超过 {_TOY_FILE_CHAR_CAP} 字")
        result = append_self_text(path, content, user_id=uid, char_id=cid, origin="tool")
    else:
        existing = read_self(path, user_id=uid, char_id=cid, origin="tool")
        if existing.get("ok"):
            if len(content) > _TOY_FILE_CHAR_CAP:
                raise ValueError(f"玩具文件总长度不能超过 {_TOY_FILE_CHAR_CAP} 字")
            result = update_self(
                path,
                content,
                expected_revision=int(existing.get("revision") or 0),
                user_id=uid,
                char_id=cid,
                origin="tool",
            )
        elif existing.get("code") in {"path_not_found", "not_a_file"}:
            result = create_self(path, content, user_id=uid, char_id=cid, origin="tool")
        else:
            return _denied_message(existing)
    if not result.get("ok"):
        if result.get("code") == "quota_exhausted":
            raise ValueError("玩具文件写入超出自有空间配额")
        return _denied_message(result)
    return "玩具文件写好了。"


def frozen_legacy_writer_message() -> str:
    return _FROZEN_WRITER
