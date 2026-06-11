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
) -> tuple[list[str], dict[str, str]]:
    from channels import registry

    targets: list = []
    if fanout in ("all", "broadcast"):
        targets = registry.get_active()
        if exclude_origin_channel:
            targets = [ch for ch in targets if ch.name != exclude_origin_channel]
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
            # Pass ws_msg_id only to the desktop channel so channel_message and
            # message_segments can share the same correlation id.  Other channels
            # (mobile, QQ) don't have this concept and are not changed.
            if ws_msg_id is not None and name == "desktop":
                await channel.send(text_to_send, uid, behavior=behavior, msg_id=ws_msg_id)
            else:
                await channel.send(text_to_send, uid, behavior=behavior)
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
) -> TurnResult:
    """
    Record one completed assistant turn and deliver it to the requested channels.

    capture_turn still owns disk writes through Pipeline.post_process. source is
    retained here for validation and future audit; current persistence encodes
    non-user sources through trigger_name only.
    envelope 未传时默认零值（fail-closed）。

    QQ 路径（R1-D）：传入 target_id / is_group / pending_paths / frozen_scope，
    经由 post_process 透传到 TTS/sticker side effects 和 scope freeze 链路。
    fanout=[] 时不执行 channel fanout（QQ visible send 由调用方在 adapter 内独立完成）。
    bypass_gate=True 时跳过 conversation_lock（QQ adapter 已在 conversation_lock 内）。
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
            post_info = await pipeline.post_process(
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
                )
            )

    # Generate the shared msg_id AFTER post_process, immediately before fanout.
    # This ensures channel_message and message_segments always share the same id
    # even when the WS reconnects during the (potentially slow) LLM call above.
    _ws_msg_id: Optional[str] = None
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
    )

    # Narrative segments: push a parallel message_segments envelope to the
    # desktop WS client only — but only when desktop was actually included in
    # the fanout.  Sending segments when fanout=[] (e.g. desktop_wake Path B)
    # would push an orphaned message that the client can never correlate and
    # consume, violating the single-display-path invariant.
    if "desktop" in targets:
        try:
            from channels import desktop_ws as _dws
            if _dws.is_connected():
                from core.narrative_parser import parse_narrative_segments
                _parsed = parse_narrative_segments(assistant_text)
                _say_segs = [s for s in _parsed["segments"] if s.get("type") == "say"]
                _say_content = " ".join(s.get("text", "") for s in _say_segs).strip() or _parsed["content"]
                await _dws.push_segments(_say_content, _say_segs, msg_id=_ws_msg_id)
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
