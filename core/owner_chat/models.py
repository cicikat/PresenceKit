"""Per-turn values passed between phases; no new persisted or shared state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    from core.pipeline import Pipeline
    from core.memory.scope import MemoryScope
    from core.event_context import EventContext
    from core.pretool_router import PreToolRouteResult
    from core.session_state import SessionState
    from core.write_envelope import WriteEnvelope


@dataclass(frozen=True)
class OwnerChatTurn:
    message: str
    provenance_channel: str
    live_origin_channel: str
    durable_mobile_mirror: bool
    _probe_text: str
    allowed_tool_categories: frozenset[str] | None
    allowed_tool_names: frozenset[str] | None
    audit_extras: dict[str, Any] | None
    pipeline: Pipeline
    user_id: str
    frozen_scope: MemoryScope
    tool_execution_enabled: bool
    prompt_context_note: str
    prompt_capture_origin: str | None
    turn_source: str
    trigger_name: str
    envelope: WriteEnvelope | None
    fanout: str | Sequence[str]
    provenance_source: str
    schedule_slow: bool
    media_refs: list[dict] | None
    event_context: EventContext
    _loop_active: bool
    _loop_session_state: SessionState
    _t_start: float
    request_id: str


@dataclass(frozen=True)
class PreparedTurn:
    pretool_result: PreToolRouteResult | str | None
    context: dict[str, Any]
    _turn_char_name: str
    tool_result_text: str | None
    _pretool_direct_reply: str | None
    _fast_path_exclude_tools: set[str]
    _tool_result_status: str | None
    _tool_result_generated_at: float | None
    _tool_call_required: bool
    _required_tool_names: set[str]
    _t_probe: float
    _t_ctx: float


@dataclass(frozen=True)
class GeneratedTurn:
    reply: str
    _stream_msg_id: str | None
    _use_stream: bool
    _t_first_delta: float | None
    _t_stream: float | None
    _t_llm: float | None
    _t_prompt: float
