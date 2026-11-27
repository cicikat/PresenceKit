"""Output cleaning, sole turn_sink commit and canonical stream delivery."""

import logging
import time

from core.owner_chat.models import OwnerChatTurn, PreparedTurn, GeneratedTurn

logger = logging.getLogger("admin.routers.chat")


async def commit_and_deliver(turn: OwnerChatTurn, prepared: PreparedTurn,
                             generated: GeneratedTurn) -> dict:
    reply = generated.reply
    message = turn.message
    provenance_channel = turn.provenance_channel
    live_origin_channel = turn.live_origin_channel
    durable_mobile_mirror = turn.durable_mobile_mirror
    _probe_text = turn._probe_text
    audit_extras = turn.audit_extras
    pipeline = turn.pipeline
    user_id = turn.user_id
    frozen_scope = turn.frozen_scope
    turn_source = turn.turn_source
    trigger_name = turn.trigger_name
    envelope = turn.envelope
    fanout = turn.fanout
    provenance_source = turn.provenance_source
    schedule_slow = turn.schedule_slow
    media_refs = turn.media_refs
    event_context = turn.event_context
    _loop_active = turn._loop_active
    _t_start = turn._t_start
    request_id = turn.request_id
    context = prepared.context
    _turn_char_name = prepared._turn_char_name
    _t_probe = prepared._t_probe
    _t_ctx = prepared._t_ctx
    _stream_msg_id = generated._stream_msg_id
    _use_stream = generated._use_stream
    _t_first_delta = generated._t_first_delta
    _t_stream = generated._t_stream
    _t_llm = generated._t_llm
    _t_prompt = generated._t_prompt
    _frozen_scope = turn.frozen_scope
    try:
        from core.observe.prompt_capture import update_llm_output as _upd_prompt_out
        _upd_prompt_out(user_id, reply)
    except Exception:
        pass

    # Shared reality guard: remove tool_call residue, character-name prefix,
    # AI self-disclosure before memory write. Brief 72's paragraph enforcer
    # is deliberately skipped here and applied only to the outgoing copy below.
    if reply:
        from core.reality_output_guard import (
            clean_reality_reply_text as _clean_reply,
            clean_reality_reply_text_for_memory as _clean_memory_reply,
        )
        reply = _clean_memory_reply(reply, _turn_char_name) or reply

    from channels import desktop_ws as _dws, ui_push as _ui_push
    from channels.registry import get as _get_channel
    channel = _get_channel(live_origin_channel)
    if channel and hasattr(channel, "set_active"):
        channel.set_active(True)

    from core.turn_sink import TurnSource, record_assistant_turn
    from core.write_envelope import stamp_user_chat
    from core.coplay.session import is_active as _coplay_is_active
    _web_echo = bool(context.get("web_recall_result"))
    _coplay_echo = _coplay_is_active(user_id, char_id=_frozen_scope.character_id)
    from core.control_markers import strip_control_markers
    visible_source = _clean_reply(reply, _turn_char_name) or reply
    visible_source = strip_control_markers(visible_source)
    reply = strip_control_markers(reply)
    _t0 = time.monotonic()
    logger.debug("[owner_chat] stage=commit request_id=%s", request_id)
    from core.conversation_execution import begin_commit
    begin_commit()
    turn_result = await record_assistant_turn(
        assistant_text=reply,
        uid=user_id,
        source=TurnSource(turn_source),
        trigger_name=trigger_name or None,
        user_text=message if turn_source == TurnSource.USER_CHAT.value else None,
        fanout=fanout,
        bypass_gate=True,
        exclude_origin_channel=live_origin_channel,
        durable_mobile_mirror=durable_mobile_mirror,
        effects=pipeline.effects,
        envelope=envelope if envelope is not None else stamp_user_chat(),
        audit_extras=audit_extras,
        frozen_scope=_frozen_scope,
        web_echo=_web_echo,
        coplay_echo=_coplay_echo,
        loop_executed=_loop_active,
        provenance_source=provenance_source,
        schedule_slow=schedule_slow,
        event_channel=provenance_channel,
        raw_user_text=_probe_text,
        media_refs=media_refs,
        event_context=event_context,
        visible_assistant_text=visible_source,
        request_id=request_id,
    )
    _t_post = time.monotonic() - _t0

    # Visible reply: strip render/NMP tags only so action descriptions survive
    # for chat texture.  Memory is already scrubbed inside record_assistant_turn.
    from core.response_processor import inline_display_text, strip_render_tags as _strip_tags
    visible_reply = _strip_tags(visible_source) or visible_source

    # 流式路径：record 走完后用同一 msg_id 推 canonical 干净版替换临时气泡。
    # record_assistant_turn 已通过 exclude_origin_channel="desktop" 跳过 desktop fanout，
    # 此处是唯一一次向 desktop WS 推送 canonical channel_message，不会重复。
    logger.debug("[owner_chat] stage=deliver request_id=%s", request_id)
    if _stream_msg_id and reply:
        await _dws.push_message(
            visible_reply,
            msg_id=_stream_msg_id,
            char_id=_frozen_scope.character_id,
            artifacts=turn_result.artifacts or None,
            request_id=request_id or None,
        )
        # Optional say-only segments, same msg_id as the canonical channel_message
        # above so the client can correlate them; failure never affects the main
        # flow (same fault-tolerance stance as turn_sink's fanout push).
        try:
            if _ui_push.any_connected():
                from core.narrative_parser import build_say_segments
                _say_content, _say_segs = build_say_segments(visible_source)
                from core.perform_mapper import enrich_say_segments
                _say_segs = await enrich_say_segments(
                    visible_source,
                    _say_segs,
                    char_id=_frozen_scope.character_id,
                )
                await _dws.push_segments(
                    _say_content,
                    _say_segs,
                    msg_id=_stream_msg_id,
                    char_id=_frozen_scope.character_id,
                    request_id=request_id or None,
                )
        except Exception:
            logger.debug("[owner_chat] message_segments push failed", exc_info=True)

    # 纯 logging，不改任何行为。字段含义见 cc-tasks/18。
    _chars = len(reply) if reply else 0
    _t_total = time.monotonic() - _t_start
    if _use_stream:
        _cps = (_chars / _t_stream) if _t_stream else 0.0
        logger.info(
            f"[owner_chat/timing] channel={provenance_channel} probe={_t_probe:.2f}s ctx={_t_ctx:.2f}s "
            f"prompt={_t_prompt:.2f}s first_delta={(_t_first_delta or 0.0):.2f}s "
            f"stream={(_t_stream or 0.0):.2f}s chars={_chars} ({_cps:.1f} c/s) "
            f"post={_t_post:.2f}s total={_t_total:.2f}s"
        )
    else:
        _cps = (_chars / _t_llm) if _t_llm else 0.0
        logger.info(
            f"[owner_chat/timing] channel={provenance_channel} probe={_t_probe:.2f}s ctx={_t_ctx:.2f}s "
            f"prompt={_t_prompt:.2f}s llm={(_t_llm or 0.0):.2f}s "
            f"chars={_chars} ({_cps:.1f} c/s) post={_t_post:.2f}s total={_t_total:.2f}s"
        )

    return {
        "reply": visible_reply,
        **({"display_text": inline_display_text(visible_source)}
           if provenance_channel == "mobile" else {}),
        "emotion": turn_result.emotion,
        "turn_id": turn_result.turn_id,
        # 流式路径：HTTP msg_id 与 WS 流式帧共享同一 id。
        # 非流式（含 mobile）回传 transport correlator；无 turn_id 时仍不为空。
        "msg_id": _stream_msg_id or getattr(turn_result, "msg_id", "") or turn_result.turn_id,
        "critical_written": turn_result.written_to_memory,
        "artifacts": list(turn_result.artifacts or []),
        **({"request_id": request_id} if request_id else {}),
        "char_id": _frozen_scope.character_id,
        "domain": "reality",
    }
