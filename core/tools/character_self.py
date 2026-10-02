"""Character-callable self file tools (work order 256 C).

Relative paths only. Frozen uid/char come from the dispatcher, never from
model JSON. Writes are grant-gated, not danger-gated. Self-internal delete
does not require per-op user confirmation.
"""

from __future__ import annotations

from core.character_self import (
    create_self,
    delete_self,
    dumps,
    list_self,
    move_self,
    read_self,
    restore_self,
    update_self,
)

_SELF_TOOL_NAMES = (
    "self_list",
    "self_read",
    "self_create",
    "self_update",
    "self_move",
    "self_delete",
    "self_restore",
)


AGENT_MD_HINT = (
    "提示：你还没有 AGENT.md。在空间根目录用 self_create 写一份 AGENT.md，"
    "写下想长期带着的工作习惯，写好后下一轮起每次都会自动带上（约 2000 字以内）。"
)


async def self_list_tool(path: str | None = None, depth: int = 1, *, user_id=None, char_id=None) -> str:
    result = list_self(path=path, depth=depth, user_id=user_id, char_id=char_id, origin="tool")
    # Direction only: tell the model the file exists as an option, never an error.
    if isinstance(result, dict) and result.get("ok") and not str(path or "").strip("/ "):
        entries = result.get("entries") or []
        if not any(isinstance(e, dict) and e.get("path") == "AGENT.md" for e in entries) and not result.get("truncated"):
            result["hint"] = AGENT_MD_HINT
    return dumps(result)


async def self_read_tool(path: str, offset: int = 0, *, user_id=None, char_id=None) -> str:
    return dumps(read_self(path=path, offset=offset, user_id=user_id, char_id=char_id, origin="tool"))


async def self_create_tool(path: str, content: str, *, user_id=None, char_id=None) -> str:
    return dumps(create_self(path=path, content=content, user_id=user_id, char_id=char_id, origin="tool"))


async def self_update_tool(
    path: str,
    content: str,
    expected_revision: int,
    *,
    user_id=None,
    char_id=None,
) -> str:
    return dumps(update_self(
        path=path,
        content=content,
        expected_revision=expected_revision,
        user_id=user_id,
        char_id=char_id,
        origin="tool",
    ))


async def self_move_tool(
    source: str,
    dest: str,
    expected_revision: int,
    overwrite: bool = False,
    dest_expected_revision: int | None = None,
    *,
    user_id=None,
    char_id=None,
) -> str:
    return dumps(move_self(
        source=source,
        dest=dest,
        expected_revision=expected_revision,
        overwrite=overwrite,
        dest_expected_revision=dest_expected_revision,
        user_id=user_id,
        char_id=char_id,
        origin="tool",
    ))


async def self_delete_tool(path: str, expected_revision: int, *, user_id=None, char_id=None) -> str:
    return dumps(delete_self(
        path=path,
        expected_revision=expected_revision,
        user_id=user_id,
        char_id=char_id,
        origin="tool",
    ))


async def self_restore_tool(path: str, revision: int, *, user_id=None, char_id=None) -> str:
    return dumps(restore_self(
        path=path,
        revision=revision,
        user_id=user_id,
        char_id=char_id,
        origin="tool",
    ))


def register_tools(registry: dict) -> None:
    registry["self_list"] = {
        "func": self_list_tool,
        "description": (
            "列出本角色自有文件空间中的相对路径；可自由组织目录。"
            "想长期记住、整理、积累自己的东西时先看这里。"
            "这是角色自己的笔记空间，不是工作区，也不是系统权限。"
        ),
        "dangerous": False,
        "category": "self",
        "effect": "read",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对目录；省略时列出空间根。"},
                "depth": {"type": "integer", "enum": [1, 2], "description": "列举深度，只能为 1 或 2。"},
            },
            "required": [],
        },
        "examples": ["看看自己的笔记目录", "列一下我整理过的文件"],
        "keywords": ["自己的笔记", "自有文件", "列目录", "整理笔记"],
        "trace_args": ["path"],
    }
    registry["self_read"] = {
        "func": self_read_tool,
        "description": "读取本角色自有文件空间中的文本；敏感值会先脱敏。",
        "dangerous": False,
        "category": "self",
        "effect": "read",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对文件路径，例如 notes/ledger.md。"},
                "offset": {"type": "integer", "description": "脱敏后的字符偏移，用于分页。"},
            },
            "required": ["path"],
        },
        "examples": ["读一下自己写的笔记", "打开自有空间里的文件"],
        "keywords": ["读笔记", "自己的文件", "自有空间"],
        "trace_args": ["path"],
    }
    registry["self_create"] = {
        "func": self_create_tool,
        "description": (
            "在本角色自有文件空间新建文本文件；文件已存在时拒绝。创建可执行文本不等于获准执行。"
            "想长期记住、积累的东西可以写成笔记；根目录的 AGENT.md 是写给自己的习惯，"
            "写了之后下一轮起会一直带着（约 2000 字以内）。"
        ),
        "dangerous": False,
        "category": "self",
        "effect": "write",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对新文件路径，可含自定目录。"},
                "content": {"type": "string", "description": "要写入的 UTF-8 文本。"},
            },
            "required": ["path", "content"],
        },
        "examples": ["新建一份自己的账本", "在笔记目录写一首歌单"],
        "keywords": ["新建笔记", "写账本", "自有文件"],
        "trace_args": ["path"],
    }
    registry["self_update"] = {
        "func": self_update_tool,
        "description": (
            "更新本角色自有文件；必须带当前 expected_revision，冲突时拒绝且不覆盖。"
            "改 AGENT.md 即改写自己长期带着的习惯（约 2000 字以内，下一轮起生效）。"
        ),
        "dangerous": False,
        "category": "self",
        "effect": "write",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对文件路径。"},
                "content": {"type": "string", "description": "替换后的 UTF-8 文本。"},
                "expected_revision": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "读到的当前 revision；不一致则 revision_conflict。",
                },
            },
            "required": ["path", "content", "expected_revision"],
        },
        "examples": ["改一下自己的笔记", "更新账本内容"],
        "keywords": ["改笔记", "更新自己的文件"],
        "trace_args": ["path"],
    }
    registry["self_move"] = {
        "func": self_move_tool,
        "description": "在自有空间内移动文件；目标已存在时必须 overwrite=true，并校验源和目标 revision。",
        "dangerous": False,
        "category": "self",
        "effect": "write",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "源相对路径。"},
                "dest": {"type": "string", "description": "目标相对路径。"},
                "expected_revision": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "源文件当前 revision。",
                },
                "overwrite": {"type": "boolean", "description": "目标已存在时是否覆盖。"},
                "dest_expected_revision": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "覆盖目标时必须提供的目标 revision。",
                },
            },
            "required": ["source", "dest", "expected_revision"],
        },
        "examples": ["把笔记挪到另一个目录"],
        "keywords": ["移动笔记", "整理文件"],
        "trace_args": ["source", "dest"],
    }
    registry["self_delete"] = {
        "func": self_delete_tool,
        "description": "把自有文件放进有界回收站，可按 revision 恢复；不需要用户再次确认。",
        "dangerous": False,
        "category": "self",
        "effect": "write",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要删除的相对文件路径。"},
                "expected_revision": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "当前 revision；不一致则拒绝。",
                },
            },
            "required": ["path", "expected_revision"],
        },
        "examples": ["删掉自己写错的笔记"],
        "keywords": ["删除笔记", "丢掉自己的文件"],
        "trace_args": ["path"],
    }
    registry["self_restore"] = {
        "func": self_restore_tool,
        "description": "按 revision 从回收站恢复自有文件；目标已存在时拒绝。",
        "dangerous": False,
        "category": "self",
        "effect": "write",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要恢复的相对路径。"},
                "revision": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "删除时记录的 revision。",
                },
            },
            "required": ["path", "revision"],
        },
        "examples": ["把刚才删掉的笔记恢复回来"],
        "keywords": ["恢复笔记", "还原文件"],
        "trace_args": ["path"],
    }
