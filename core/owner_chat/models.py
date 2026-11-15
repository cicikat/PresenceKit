"""Per-turn values passed between phases; no new persisted or shared state."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OwnerChatTurn:
    message: object
    provenance_channel: object
    live_origin_channel: object
    durable_mobile_mirror: object
    _probe_text: object
    allowed_tool_categories: object
    allowed_tool_names: object
    audit_extras: object
    pipeline: object
    user_id: object
    frozen_scope: object
    tool_execution_enabled: object
    prompt_context_note: object
    prompt_capture_origin: object
    turn_source: object
    trigger_name: object
    envelope: object
    fanout: object
    provenance_source: object
    schedule_slow: object
    media_refs: object
    event_context: object
    _loop_active: object
    _loop_session_state: object
    _t_start: object
    request_id: object


@dataclass(frozen=True)
class PreparedTurn:
    pretool_result: object
    context: object
    _turn_char_name: object
    tool_result_text: object
    _pretool_direct_reply: object
    _fast_path_exclude_tools: object
    _tool_result_status: object
    _tool_result_generated_at: object
    _tool_call_required: object
    _required_tool_names: object
    _t_probe: object
    _t_ctx: object


@dataclass(frozen=True)
class GeneratedTurn:
    reply: object
    _stream_msg_id: object
    _use_stream: object
    _t_first_delta: object
    _t_stream: object
    _t_llm: object
    _t_prompt: object
