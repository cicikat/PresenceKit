
"""Private intention tools, reachable only inside the Reality Autonomy loop."""

from __future__ import annotations

import json

from core.agent_runtime import intentions
from core.autonomy import intent_context
from core.tools.tool_result import ToolResult, sanitize_for_prompt

NAMES = frozenset({"list_character_intents", "propose_character_intent", "revise_character_intent"})


async def list_character_intents(uid, char_id, status="", limit=4, offset=0):
    intent_context.current(uid, char_id)
    return intent_context.list_private(uid, char_id, status=status, limit=limit, offset=offset)


async def propose_character_intent(uid, char_id, goal, motivation="", source_refs=None,
                                   next_check_at=None, expires_at=None):
    context = intent_context.current(uid, char_id)
    result = intentions.propose(
        uid, char_id, goal=goal, motivation=motivation,
        anchors=intent_context.verified_anchors(context, source_refs),
        next_check_at=next_check_at, expires_at=expires_at,
    )
    row = result["intent"]
    return {**_receipt(row), "duplicate": result["duplicate"]}


async def revise_character_intent(uid, char_id, intent_id, revision, action, tool="", target="",
                                  next_check_at=None, goal=None, motivation=None):
    context = intent_context.current(uid, char_id)
    rows, _ = intentions.snapshot(uid, char_id)
    row = next((row for row in rows if row["id"] == intent_id), None)
    if row is None:
        raise intentions.IntentError("intent_not_found")
    reason = ""
    if action == "ready":
        reason = intent_context.action_block_reason(context, row, tool, target)
        if reason:
            action = "block"
    result = intentions.revise(
        uid, char_id, identity=intent_id, revision=revision, action=action,
        run_id=context.run_id, tool=tool, target=target, next_check_at=next_check_at,
        goal=goal, motivation=motivation, block_reason=reason,
    )
    if action == "abandon":
        intent_context.cancel_linked_task(uid, char_id, result)
    return _receipt(result)


def _receipt(row):
    return {key: row.get(key) for key in ("id", "revision", "status", "next_check_at", "block_reason")}


async def execute(func, uid: str, char_id: str, args: dict) -> ToolResult:
    try:
        result = await func(uid, char_id, **args)
    except intentions.IntentError as exc:
        result = {"error": str(exc)}
    except (TypeError, ValueError):
        result = {"error": "invalid_intent_arguments"}
    summary = json.dumps(result, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    return ToolResult(raw_data="", safe_summary=sanitize_for_prompt(summary), meta={"validity": "current_turn"})


def register(registry: dict) -> None:
    string = {"type": "string"}
    integer = {"type": "integer"}
    number = {"type": "number"}
    definitions = (
        (list_character_intents, "分页查看当前角色的私有意图和版本。", "查看先前搁置的意图", {
            "status": {**string, "description": "可选状态，省略则查看当前活动意图。"},
            "limit": {**integer, "description": "每页 1 至 4 项，默认 4。"},
            "offset": {**integer, "description": "上一页返回的 next_offset。"},
        }, []),
        (propose_character_intent, "保存角色私有意图。它是计划，不能作为用户事实。可保存后安静结束。", "记录一个稍后再考虑的意图", {
            "goal": {**string, "description": "200 字以内的私有目标。"},
            "motivation": {**string, "description": "300 字以内的动机，不复制外部资料。"},
            "source_refs": {"type": "array", "items": string, "maxItems": 3,
                            "description": "仅使用本轮 context 的 fact_ 编号。无依据的念头可省略。"},
            "next_check_at": {**number, "description": "下次考虑的 Unix 秒，可省略。"},
            "expires_at": {**number, "description": "最晚有效的 Unix 秒，最多 30 天。"},
        }, ["goal"]),
        (revise_character_intent, "修改、推迟、准备或放弃私有意图。准备后仍调用已授权工具。完成状态由回执写入。", "推迟这条意图", {
            "intent_id": {**string, "description": "角色私有意图 ID。"},
            "revision": {**integer, "description": "最近读取的版本。"},
            "action": {**string, "enum": ["defer", "ready", "abandon"], "description": "推迟、准备或放弃。"},
            "tool": {**string, "description": "准备时选用当前已授权的工具。"},
            "target": {**string, "description": "准备时冻结的 track_id、pending_id 或 Agent task request_id。"},
            "next_check_at": {**number, "description": "推迟到此 Unix 秒，省略用默认冷却。"},
            "goal": {**string, "description": "可选的新目标，200 字以内。"},
            "motivation": {**string, "description": "可选的新动机，300 字以内。"},
        }, ["intent_id", "revision", "action"]),
    )
    for func, description, example, properties, required in definitions:
        registry[func.__name__] = {
            "func": func, "description": description, "category": "character_intents",
            "autonomy_only": True, "dangerous": False, "probe": False, "echo_event_log": False,
            "effect": "read" if func is list_character_intents else "write",
            "examples": [example], "keywords": ["私有意图"],
            "parameters": {"type": "object", "properties": properties,
                           "required": required, "additionalProperties": False},
        }
