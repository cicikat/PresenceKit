"""Character-callable reminder tools (work order 256 E).

Frozen uid/char come from the dispatcher, never from model JSON. Writes are
not danger-gated. Cancel/restore do not require a second user utterance.
"""

from __future__ import annotations

from core.tools.reminder import (
    add_reminder,
    cancel_reminder,
    dumps,
    get_reminder,
    list_reminders,
    restore_reminder,
    update_reminder,
)

_REMINDER_TOOL_NAMES = (
    "list_reminders",
    "get_reminder",
    "add_reminder",
    "update_reminder",
    "cancel_reminder",
    "restore_reminder",
)


async def list_reminders_tool(include_cancelled: bool = False, *, user_id=None, char_id=None) -> str:
    return dumps(list_reminders(user_id, char_id=char_id, include_cancelled=include_cancelled))


async def get_reminder_tool(schedule_id: str, *, user_id=None, char_id=None) -> str:
    return dumps(get_reminder(user_id, schedule_id, char_id=char_id))


async def add_reminder_tool(
    content: str,
    remind_at: str,
    repeat: str | None = None,
    *,
    user_id=None,
    char_id=None,
) -> str:
    return add_reminder(user_id, content, remind_at, char_id=char_id, repeat=repeat)


async def update_reminder_tool(
    schedule_id: str,
    expected_revision: int,
    content: str | None = None,
    remind_at: str | None = None,
    repeat: str | None = None,
    *,
    user_id=None,
    char_id=None,
) -> str:
    kwargs = {}
    if repeat is not None:
        kwargs["repeat"] = repeat
    return dumps(update_reminder(
        user_id,
        schedule_id,
        expected_revision,
        char_id=char_id,
        content=content,
        remind_at=remind_at,
        **kwargs,
    ))


async def cancel_reminder_tool(
    schedule_id: str,
    expected_revision: int,
    *,
    user_id=None,
    char_id=None,
) -> str:
    return dumps(cancel_reminder(user_id, schedule_id, expected_revision, char_id=char_id))


async def restore_reminder_tool(
    schedule_id: str,
    revision: int,
    *,
    user_id=None,
    char_id=None,
) -> str:
    return dumps(restore_reminder(user_id, schedule_id, revision, char_id=char_id))


def register_tools(registry: dict) -> None:
    registry["list_reminders"] = {
        "func": list_reminders_tool,
        "description": (
            "列出本角色的定时提醒。无时间的自由笔记请用 self 文件，不要把笔记当提醒。"
            "返回 schedule_id、revision、正文、到期时间和状态；不要靠文字匹配删除。"
        ),
        "dangerous": False,
        "category": "info",
        "effect": "read",
        "parameters": {
            "type": "object",
            "properties": {
                "include_cancelled": {
                    "type": "boolean",
                    "description": "是否包含已取消、仍可恢复的提醒。",
                },
            },
            "required": [],
        },
        "examples": ["看看有哪些提醒", "我记过什么待办"],
        "keywords": ["提醒列表", "待办", "备忘录"],
    }
    registry["get_reminder"] = {
        "func": get_reminder_tool,
        "description": "按 schedule_id 读取一条本角色提醒的安全投影。",
        "dangerous": False,
        "category": "info",
        "effect": "read",
        "parameters": {
            "type": "object",
            "properties": {
                "schedule_id": {"type": "string", "description": "稳定 schedule_id，不是正文。"},
            },
            "required": ["schedule_id"],
        },
        "examples": ["看一下这条提醒的详情"],
        "keywords": ["提醒详情"],
        "trace_args": ["schedule_id"],
    }
    registry["add_reminder"] = {
        "func": add_reminder_tool,
        "description": (
            "创建一条定时提醒。仅在用户明确要求记录事项并在指定时间提醒时调用。"
            "写进 self 笔记里的“明天提醒”不会自动变成定时任务。"
        ),
        "dangerous": False,
        "category": "info",
        "effect": "write",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要提醒用户做什么；使用简短、完整的事项文本。",
                },
                "remind_at": {
                    "type": "string",
                    "description": "提醒的本地时间，格式为 HH:MM、MM-DD HH:MM 或 YYYY-MM-DD HH:MM。",
                },
                "repeat": {
                    "type": "string",
                    "description": "可选重复：none / daily / weekly，或间隔秒数（至少 60）。",
                },
            },
            "required": ["content", "remind_at"],
        },
        "examples": ["提醒我8点吃药", "明天下午三点记得开会", "帮我记一下"],
        "keywords": ["提醒", "记得", "帮我记"],
        "trace_args": ["remind_at"],
    }
    registry["update_reminder"] = {
        "func": update_reminder_tool,
        "description": "按 schedule_id 和 expected_revision 修改本角色提醒；冲突时拒绝。可改用户交办给本角色的提醒。",
        "dangerous": False,
        "category": "info",
        "effect": "write",
        "parameters": {
            "type": "object",
            "properties": {
                "schedule_id": {"type": "string", "description": "稳定 schedule_id。"},
                "expected_revision": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "读到的当前 revision；不一致则 revision_conflict。",
                },
                "content": {"type": "string", "description": "新的提醒正文。"},
                "remind_at": {"type": "string", "description": "新的本地时间。"},
                "repeat": {"type": "string", "description": "新的重复规则；none 表示取消重复。"},
            },
            "required": ["schedule_id", "expected_revision"],
        },
        "examples": ["把提醒改到明天", "改一下提醒内容"],
        "keywords": ["改提醒", "推迟", "改时间"],
        "trace_args": ["schedule_id"],
    }
    registry["cancel_reminder"] = {
        "func": cancel_reminder_tool,
        "description": "取消本角色的未来提醒，可按 revision 恢复；不需要用户再说一次“删除”。已进入发送的不能撤回。",
        "dangerous": False,
        "category": "info",
        "effect": "write",
        "parameters": {
            "type": "object",
            "properties": {
                "schedule_id": {"type": "string", "description": "稳定 schedule_id。"},
                "expected_revision": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "当前 revision。",
                },
            },
            "required": ["schedule_id", "expected_revision"],
        },
        "examples": ["取消这条提醒", "不用提醒了"],
        "keywords": ["取消提醒", "删掉提醒"],
        "trace_args": ["schedule_id"],
    }
    registry["restore_reminder"] = {
        "func": restore_reminder_tool,
        "description": "按 revision 恢复已取消的提醒，生成新的可运行生命周期；不能复活已完成的那一次。",
        "dangerous": False,
        "category": "info",
        "effect": "write",
        "parameters": {
            "type": "object",
            "properties": {
                "schedule_id": {"type": "string", "description": "稳定 schedule_id。"},
                "revision": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "取消时记录的 revision。",
                },
            },
            "required": ["schedule_id", "revision"],
        },
        "examples": ["把刚才取消的提醒恢复"],
        "keywords": ["恢复提醒"],
        "trace_args": ["schedule_id"],
    }
