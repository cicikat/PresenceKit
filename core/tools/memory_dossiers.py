"""Bounded owner-chat tools for Brief 258 topic memory."""
from __future__ import annotations

import json
from typing import Any

from core.memory.scope import MemoryScope
from core.tools.tool_result import ToolResult


def _scope(user_id: str, char_id: str) -> MemoryScope:
    return MemoryScope.reality_scope(str(user_id), str(char_id))


def _result(value: object, *, truncated: bool = False) -> ToolResult:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return ToolResult(safe_summary=text, raw_data=text, meta={"truncated": truncated})


async def search_memory_dossiers(user_id: str, query: str = "", limit: int = 3, *, char_id: str) -> ToolResult:
    from core.memory.dossiers import search
    return _result({"items": search(_scope(user_id, char_id), query, limit=min(3, limit))})


async def read_memory_dossier(user_id: str, dossier_id: str, *, char_id: str) -> ToolResult:
    from core.memory.dossiers import read
    value = read(_scope(user_id, char_id), dossier_id)
    return _result(value or {"error": "dossier_not_found"})


async def search_dossier_events(user_id: str, dossier_id: str, offset: int = 0,
                                limit: int = 20, *, char_id: str) -> ToolResult:
    from core.memory.dossiers import dossier_events
    value = dossier_events(_scope(user_id, char_id), dossier_id, offset=offset, limit=min(50, limit))
    return _result(value, truncated=bool(value["truncated"]))


async def update_memory_dossier(user_id: str, operation_id: str, operations: list[dict[str, Any]],
                                *, char_id: str) -> ToolResult:
    from core.memory.dossiers import apply_operations
    value = apply_operations(_scope(user_id, char_id), operations, operation_id=operation_id,
                             actor="character", chain="owner_chat")
    return _result(value)


async def get_memory_consolidation_status(user_id: str, *, char_id: str) -> ToolResult:
    from core.memory.dossiers import status_snapshot
    return _result(status_snapshot(_scope(user_id, char_id)))


async def request_memory_consolidation(user_id: str, request_id: str,
                                       scope_mode: str = "incremental", *, char_id: str) -> ToolResult:
    """Queue durable work; never scan history synchronously in the chat turn."""
    if scope_mode not in {"incremental", "full_history"}:
        return _result({"error": "invalid_scope_mode"})
    from core.agent_runtime.models import CausationRef, RetryPolicy, TaskPrincipal
    from core.agent_runtime.task_manager import create_task
    receipt, created = create_task(
        TaskPrincipal.reality(user_id, char_id), capability="memory.consolidation",
        source="owner_chat_tool", idempotency_key=request_id, ttl_seconds=86400,
        causation_ref=CausationRef(kind="tool_request", reference=request_id),
        retry_policy=RetryPolicy.SAFE.value, max_attempts=3,
        request_context={"scope_mode": scope_mode},
        request_summary={"scope_mode": scope_mode},
    )
    return _result({"task_id": receipt["task_id"], "status": receipt["status"],
                    "created": created, "scope_mode": scope_mode})
