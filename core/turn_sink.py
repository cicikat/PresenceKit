"""
Unified assistant turn sink.

This module is the single handoff point after Ye Xuan has produced a message:
record critical memory writes, fan out to channels, and leave slow memory work
on the existing post_process queue.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Sequence, Union

logger = logging.getLogger(__name__)


class TurnSource(str, Enum):
    USER_CHAT = "user_chat"
    TRIGGER = "trigger"
    SENSOR = "sensor"
    WATCH = "watch"


FanoutPolicy = Union[str, Sequence[str]]


@dataclass
class TurnResult:
    turn_id: str
    written_to_memory: bool
    fanout_targets: list[str]
    fanout_failures: dict[str, str] = field(default_factory=dict)
    post_process_scheduled: bool = False
    emotion: str = "neutral"


def _require_pipeline(pipeline=None):
    if pipeline is not None:
        return pipeline
    from core.pipeline_registry import get

    resolved = get()
    if resolved is None:
        raise RuntimeError("Pipeline is not initialized")
    return resolved


def _validate_inputs(
    assistant_text: str,
    source: TurnSource,
    trigger_name: Optional[str],
    user_text: Optional[str],
) -> None:
    if not assistant_text:
        raise ValueError("assistant_text cannot be empty")
    if source == TurnSource.USER_CHAT:
        if not user_text:
            raise ValueError("USER_CHAT requires user_text")
        if trigger_name:
            raise ValueError("USER_CHAT must not set trigger_name")
        return
    if not trigger_name:
        raise ValueError(f"{source.value} requires trigger_name")
    if user_text:
        raise ValueError(f"{source.value} must not set user_text")


@asynccontextmanager
async def _maybe_conversation_gate(uid: str, bypass_gate: bool):
    if bypass_gate:
        yield
        return

    from core.conversation_gate import conversation_lock

    async with conversation_lock(uid):
        yield


async def _fanout(
    *,
    assistant_text: str,
    uid: str,
    fanout: FanoutPolicy,
    behavior: Optional[dict],
    exclude_origin_channel: Optional[str] = None,
    ws_msg_id: Optional[str] = None,
    char_id: Optional[str] = None,
    source: Optional[TurnSource] = None,
) -> tuple[list[str], dict[str, str]]:
    from channels import registry

    targets: list = []
    if fanout in ("all", "broadcast"):
        targets = registry.get_active()
        if exclude_origin_channel:
            targets = [ch for ch in targets if ch.name != exclude_origin_channel]

        # Durable mobile fallback: proactive turns (not USER_CHAT) must reach the
        # mobile durable queue even when the phone is offline (is_active=False).
        # mobile.send() only writes to the persistent queue + fires relay signal —
        # safe to call for an offline phone. is_active should gate live poll
        # responses, not whether we bother leaving a message in the queue.
        _is_proactive = source is not None and source != TurnSource.USER_CHAT
        if _is_proactive and exclude_origin_channel != "mobile":
            mobile_ch = registry.get("mobile")
            already = any(getattr(t, "name", "") == "mobile" for t in targets)
            if mobile_ch is not None and not already:
                targets.append(mobile_ch)
    elif isinstance(fanout, str):
        channel = registry.get(fanout)
        if channel is not None and channel.is_active:
            targets = [channel]
    else:
        for name in fanout:
            channel = registry.get(str(name))
            if channel is not None and channel.is_active:
                targets.append(channel)

    from core.response_processor import strip_render_tags as _strip_tags

    # Visible output: strip render/NMP tags only so action descriptions survive
    # for chat texture.  Action descriptions are cleaned on the memory path
    # (memory_text in record_assistant_turn) — not here.
    _visible_text = _strip_tags(assistant_text)

    sent_targets: list[str] = []
    failures: dict[str, str] = {}
    for channel in targets:
        name = getattr(channel, "name", channel.__class__.__name__)
        sent_targets.append(name)
        try:
            text_to_send = _visible_text
            send_kwargs = {"behavior": behavior}
            if char_id is not None:
                send_kwargs["char_id"] = char_id
            # Desktop, mobile, and device share the canonical turn id so clients
            # can correlate the same assistant turn across transports (device
            # also needs this to match up channel_message with message_segments).
            if ws_msg_id is not None and name in ("desktop", "mobile", "device"):
                send_kwargs["msg_id"] = ws_msg_id
            await channel.send(text_to_send, uid, **send_kwargs)
        except Exception as exc:
            failures[name] = str(exc)
            logger.warning("[turn_sink] fanout failed channel=%s: %s", name, exc)

    if not targets:
        logger.warning("[turn_sink] no active fanout targets for policy=%r", fanout)

    return sent_targets, failures


async def record_assistant_turn(
    *,
    assistant_text: str,
    uid: str,
    source: TurnSource,
    trigger_name: Optional[str] = None,
    user_text: Optional[str] = None,
    fanout: FanoutPolicy = "all",
    payload: Optional[dict] = None,
    await_critical_post_process: bool = True,
    bypass_gate: bool = False,
    exclude_origin_channel: Optional[str] = None,
    pipeline=None,
    envelope=None,
    audit_extras: Optional[dict] = None,
    # QQ-specific params: passed through to pipeline.post_process (R1-D)
    target_id: str = "",
    is_group: bool = False,
    pending_paths: Optional[list] = None,
    frozen_scope=None,
    char_id: Optional[str] = None,
    web_echo: bool = False,
    coplay_echo: bool = False,
    loop_executed: bool = False,
) -> TurnResult:
    """
    Record one completed assistant turn and deliver it to the requested channels.

    capture_turn still owns disk writes through Pipeline.post_process_critical.
    source is retained here for validation and future audit; current persistence
    encodes non-user sources through trigger_name only.
    envelope 未传时默认零值（fail-closed）。

    Brief 37：当 await_critical_post_process=True 时，只 await
    post_process_critical()（毫秒级本地落盘），fanout 完成后再用
    asyncio.create_task() 调度 post_process_slow()（detect_emotion / mood_state /
    avatar / profile / slow_queue）——这些都是 LLM/网络往返或非关键写入，绝不能
    堵住 send。返回的 TurnResult.emotion 因此只是 critical 段的占位值
    "neutral"，真实情绪要等 post_process_slow 异步跑完才落进 mood_state /
    mid_term，调用方不应假设它反映本轮真实检测结果。

    QQ 路径（R1-D）：传入 target_id / is_group / pending_paths / frozen_scope，
    经由 post_process_critical/_slow 透传到 TTS/sticker side effects 和 scope
    freeze 链路。
    fanout=[] 时不执行 channel fanout（QQ visible send 由调用方在 adapter 内独立完成）。
    bypass_gate=True 时跳过 conversation_lock（QQ adapter 已在 conversation_lock 内）。
    loop_executed（Brief 28）：本轮是否走了 tool loop，透传给 post_process_slow →
    Path B 跳过判断。
    """
    from core.write_envelope import WriteEnvelope
    if envelope is None:
        envelope = WriteEnvelope()

    source = TurnSource(source)
    _validate_inputs(assistant_text, source, trigger_name, user_text)
    pipeline = _require_pipeline(pipeline)

    memory_input = user_text if source == TurnSource.USER_CHAT else (trigger_name or "")
    capture_trigger = "" if source == TurnSource.USER_CHAT else (trigger_name or "")
    behavior = payload.get("behavior") if payload else None
    if char_id is None and frozen_scope is not None:
        char_id = getattr(frozen_scope, "character_id", None)
    if char_id is None:
        char_id = getattr(pipeline, "_active_character_id", None) or None

    # Reality inlet pre-scrub (defense-in-depth): strip render markup then
    # action/narration content before passing to post_process.  This covers all
    # paths going through record_assistant_turn (desktop, scheduler, sensor, watch,
    # and QQ via R1-D).
    # The authoritative final scrub is in capture_turn — do not remove this call,
    # but also do not rely on it as the sole scrub guard.
    # (scrub_reality_output_text is idempotent; double-scrub with capture_turn is safe.)
    from core.response_processor import strip_render_tags as _strip_tags
    from core.reality_output_scrubber import scrub_reality_output_text as _scrub
    memory_text = _scrub(_strip_tags(assistant_text)) or ""

    post_info: dict | None = None
    async with _maybe_conversation_gate(uid, bypass_gate):
        if await_critical_post_process:
            # Brief 37: send 前只 await 落盘（post_process_critical），不再等
            # detect_emotion 等 LLM 往返。post_process_slow 在下面 fanout 完成后
            # 才用 create_task 调度，emotion/mood/slow_queue 全挪到 send 之后。
            post_info = await pipeline.post_process_critical(
                uid,
                memory_input,
                memory_text,
                target_id=target_id,
                is_group=is_group,
                pending_paths=pending_paths,
                trigger_name=capture_trigger,
                envelope=envelope,
                audit_extras=audit_extras,
                frozen_scope=frozen_scope,
                web_echo=web_echo,
                coplay_echo=coplay_echo,
            )
        else:
            asyncio.create_task(
                pipeline.post_process(
                    uid,
                    memory_input,
                    memory_text,
                    target_id=target_id,
                    is_group=is_group,
                    pending_paths=pending_paths,
                    trigger_name=capture_trigger,
                    envelope=envelope,
                    audit_extras=audit_extras,
                    frozen_scope=frozen_scope,
                    web_echo=web_echo,
                    coplay_echo=coplay_echo,
                    loop_executed=loop_executed,
                )
            )

    # Use the post-process turn_id as the canonical cross-transport correlation id.
    # The fallback only applies to non-critical async post-process paths where no
    # turn_id exists yet.
    _ws_msg_id: Optional[str] = (post_info or {}).get("turn_id") or None
    if _ws_msg_id is None:
        try:
            from channels import desktop_ws as _dws_pre
            if _dws_pre.is_connected():
                _ws_msg_id = _dws_pre._new_msg_id()
        except Exception:
            pass

    targets, failures = await _fanout(
        assistant_text=assistant_text,
        uid=uid,
        fanout=fanout,
        behavior=behavior,
        exclude_origin_channel=exclude_origin_channel,
        ws_msg_id=_ws_msg_id,
        char_id=char_id,
        source=source,
    )

    # Brief 37: send（上面的 fanout）已经完成，慢段（detect_emotion / mood_state /
    # slow_queue 入队等）现在才调度，绝不 await——否则又把下一条消息的 send 堵住。
    if await_critical_post_process and post_info is not None:
        asyncio.create_task(
            pipeline.post_process_slow(
                uid,
                memory_input,
                memory_text,
                post_info,
                target_id=target_id,
                is_group=is_group,
                trigger_name=capture_trigger,
                envelope=envelope,
                audit_extras=audit_extras,
                web_echo=web_echo,
                coplay_echo=coplay_echo,
                loop_executed=loop_executed,
            )
        )

    # Narrative segments: push a parallel message_segments envelope to UI clients
    # (desktop + device) — but only to the ones actually included in this fanout.
    # Sending segments to a client that never got the corresponding channel_message
    # (e.g. fanout=[] on desktop_wake Path B, or an exclude_origin_channel gap)
    # would push an orphaned message that client can never correlate and consume,
    # violating the single-display-path invariant.
    if "desktop" in targets or "device" in targets:
        try:
            from channels import ui_push
            if ui_push.any_connected():
                # Join with "\n" (NOT " ") so paragraph boundaries survive.  The desktop
                # client splits channel_message content by \n+ into one bubble per
                # paragraph and maps message_segments content onto those bubbles by
                # index.  A space-join collapses a multi-paragraph reply into a single
                # part, so bubble #0's segmentedContent became the WHOLE message while
                # later bubbles kept their raw paragraph — rendering duplicated text
                # that looked like a double-send for multi-paragraph trigger messages.
                from core.narrative_parser import build_say_segments
                _say_content, _say_segs = build_say_segments(assistant_text)
                from core.perform_mapper import enrich_say_segments
                _say_segs = await enrich_say_segments(assistant_text, _say_segs, char_id=char_id)
                segment_kwargs = {"msg_id": _ws_msg_id}
                if char_id is not None:
                    segment_kwargs["char_id"] = char_id
                from channels import desktop_ws as _dws, device_ws as _dvws
                if "desktop" in targets and _dws.is_connected():
                    await _dws.push_segments(_say_content, _say_segs, **segment_kwargs)
                if "device" in targets and _dvws.is_connected():
                    await _dvws.push_segments(_say_content, _say_segs, **segment_kwargs)
        except Exception:
            logger.debug("[turn_sink] message_segments fanout failed", exc_info=True)

    return TurnResult(
        turn_id=(post_info or {}).get("turn_id", ""),
        written_to_memory=bool((post_info or {}).get("critical_written", False)),
        fanout_targets=targets,
        fanout_failures=failures,
        post_process_scheduled=not await_critical_post_process,
        emotion=(post_info or {}).get("emotion", "neutral"),
    )
