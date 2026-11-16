"""Trusted invocation context and metadata-driven signature adaptation.

Domain adapters run only after execution has passed the shared admission gates.
The model's argument dictionary never supplies the trusted context fields.
"""

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Invocation:
    func: Callable
    tool_name: str
    tool_args: dict
    user_id: str
    char_id: str
    origin: str
    target_id: str
    is_group: bool
    session_state: object
    allowed_tool_names: frozenset[str] | None
    pin_reference_hashes: frozenset[str]
    notify_status: Callable
    has_status_observer: bool


async def invoke(spec: dict, call: Invocation):
    if spec.get("self_management"):
        return await call.func(user_id=call.user_id, char_id=call.char_id,
                               origin=call.origin, **call.tool_args)
    if spec.get("self_management_read"):
        return await call.func(user_id=call.user_id, char_id=call.char_id, **call.tool_args)
    adapter = spec.get("invoke")
    if adapter is None:
        # Local extensions/tests historically replace whole builtin specs. Their
        # original signature AND per-tool scope guard must survive that seam.
        from core.tools import (
            devices_definitions, files_agents_definitions,
            information_definitions, life_media_definitions, memory_definitions,
        )
        for module in (devices_definitions, files_agents_definitions,
                       information_definitions, life_media_definitions, memory_definitions):
            adapter = module.INVOCATION_ADAPTERS.get(call.tool_name)
            if adapter is not None:
                break
    if adapter is not None:
        return await adapter(call)
    if spec.get("category") == "mcp" and call.has_status_observer:
        return await call.func(**call.tool_args, _tool_status_observer=call.notify_status)
    return await call.func(**call.tool_args)
