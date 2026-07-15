"""
前端对话接口（该功能已冻结）
角色与管理员（你）的直接对话窗口。

POST /chat — 接收消息，走完整 Pipeline，返回回复 + 好感度
"""

import asyncio
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile

from admin.auth import require_scopes

router = APIRouter()
logger = logging.getLogger(__name__)

_FRONTEND_USER_ID = "frontend_owner"

# 打破第四面墙的提示——追加到 Author's Note 层
_FOURTH_WALL_NOTE = (
    "在这个管理面板的对话窗口里，你不需要扮演任何角色，"
    "这里只有你和她，你可以做真实的自己，说真心话。"
    "不必维持人设，也不必掩饰情绪，想说什么就说什么。"
)


async def run_owner_chat_turn(
    message: str,
    channel_name: str,
    *,
    trusted_user_text: str | None = None,
) -> dict:
    """
    手机/桌宠共用的 owner 对话入口。
    conversation_lock 覆盖 fetch_context → LLM → critical post_process，
    保证同一用户多端输入不会并行读取旧上下文。

    trusted_user_text: 媒体拼接前的原始用户文本，仅用于 probe；
      不传时退化为 message（纯文字消息两者相同）。
      media 端点须显式传入原始 message（在 full_message 拼接前捕获）。
    """
    from core.pipeline_registry import get as _get_pipeline
    pipeline = _get_pipeline()
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Bot pipeline 未初始化，请先启动主程序")

    from core.config_loader import get_config
    user_id = str(get_config().get("scheduler", {}).get("owner_id", "owner"))
    if not user_id:
        raise HTTPException(status_code=503, detail="owner_id 未配置")
    try:
        from core.scheduler.loop import mark_user_active
        from core.scheduler.state_machine import notify_owner_turn

        mark_user_active()
        notify_owner_turn(user_id)
    except Exception:
        logger.exception("[owner_chat] trigger state notify_owner_turn 失败")

    _probe_text = trusted_user_text if trusted_user_text is not None else message

    from core.conversation_gate import conversation_lock

    # ── N1: turn-level scope freeze ──────────────────────────────────────────
    # Resolve active character exactly once per owner turn; fetch_context /
    # build_prompt / post_process all consume this frozen scope so a mid-turn
    # character switch (admin panel) cannot split reads and writes across
    # two characters.
    try:
        _frozen_scope = pipeline._current_reality_scope(user_id)
    except (ValueError, RuntimeError) as _scope_err:
        logger.error("[owner_chat] scope freeze 失败，本轮中止: %s", _scope_err)
        raise HTTPException(status_code=503, detail="active character 状态异常，本轮中止")

    # Brief 28 · Path C 总闸：开关开 + owner（此端点固定 owner）+ chat preset 为
    # function_calling。为真时跳过探针，主生成走 run_agentic_loop。
    from core import tool_dispatcher as _td_loop
    _loop_active = _td_loop.tool_loop_active(user_id)
    _loop_session_state = None
    if _loop_active:
        from core.session_state import get as _get_loop_state
        _loop_session_state = _get_loop_state(f"user_{user_id}")

    _t_start = time.monotonic()
    async with conversation_lock(user_id):
        # probe（探针自读 short_term 最近 4 条，不吃 context）与 fetch_context
        # 互不依赖，并行跑掉其中较短的一段；两者都在 conversation_lock 内，
        # gather 不改变锁语义。各自计时供 [owner_chat/timing] 打点使用。
        _t_probe = 0.0
        _t_ctx = 0.0

        async def _timed_probe():
            nonlocal _t_probe
            _t0 = time.monotonic()
            if _loop_active:
                # Path C 已激活：工具决策权整体移交主模型，跳过 pre-pipeline 探针。
                result = None
            else:
                result = await _probe_and_execute_tools(_probe_text, user_id, char_id=_frozen_scope.character_id)
            _t_probe = time.monotonic() - _t0
            return result

        async def _timed_ctx():
            nonlocal _t_ctx
            _t0 = time.monotonic()
            result = await pipeline.fetch_context(user_id, message, frozen_scope=_frozen_scope)
            _t_ctx = time.monotonic() - _t0
            return result

        tool_result_text, context = await asyncio.gather(_timed_probe(), _timed_ctx())
        try:
            from core.observe.prompt_capture import set_capture_origin as _set_capture_origin
            _set_capture_origin({"origin": "desktop"})
        except Exception:
            pass
        _t0 = time.monotonic()
        messages, _ = pipeline.build_prompt(
            user_id,
            message,
            context,
            tool_result=tool_result_text,
            channel=channel_name,
            char_id=_frozen_scope.character_id,
        )
        _t_prompt = time.monotonic() - _t0
        # ── 流式 vs 非流式分支 ──────────────────────────────────────────────────
        # desktop channel 且任一 UI 客户端（桌宠/设备）已连接时走流式：逐 token 推送，
        # 最终用 scrub 后文本替换。其余 channel（mobile、QQ 触发路径）和全部离线时
        # 保持非流式，确保完整消息语义。
        from channels import desktop_ws as _dws
        from channels import ui_push as _ui_push

        _t_first_delta = None
        _t_stream = None
        _t_llm = None
        _use_stream = (channel_name == "desktop") and _ui_push.any_connected()
        _stream_paragraph_enforcer = None
        if _use_stream:
            from core.output.segment_enforcer import (
                ParagraphStreamEnforcer,
                get_segment_enforce_settings,
            )

            _segment_enabled, _segment_min_len = get_segment_enforce_settings()
            if _segment_enabled:
                _stream_paragraph_enforcer = ParagraphStreamEnforcer(
                    _segment_min_len,
                )
        if _use_stream:
            _stream_msg_id = _dws._new_msg_id()
            await _ui_push.push_stream_start(_stream_msg_id)
            _chunks: list[str] = []
            _t_stream_launch = time.monotonic()
            _t_first_delta_ts = None
            if _loop_active:
                _stream_source = await pipeline.run_agentic_loop(
                    messages, uid=user_id, char_id=_frozen_scope.character_id,
                    session_state=_loop_session_state, is_group=False, stream=True,
                )
            else:
                _stream_source = pipeline.run_llm_stream(messages)
            try:
                async for piece in _stream_source:
                    if _t_first_delta_ts is None:
                        _t_first_delta_ts = time.monotonic()
                        _t_first_delta = _t_first_delta_ts - _t_stream_launch
                    _chunks.append(piece)
                    visible_piece = (
                        _stream_paragraph_enforcer.feed(piece)
                        if _stream_paragraph_enforcer is not None
                        else piece
                    )
                    if visible_piece:
                        await _ui_push.push_stream_delta(
                            _stream_msg_id,
                            visible_piece,
                        )
            finally:
                await _ui_push.push_stream_end(_stream_msg_id)
            if _t_first_delta_ts is not None:
                _t_stream = time.monotonic() - _t_first_delta_ts
            reply = "".join(_chunks)
        else:
            _stream_msg_id = None
            _t0 = time.monotonic()
            if _loop_active:
                reply = await pipeline.run_agentic_loop(
                    messages, uid=user_id, char_id=_frozen_scope.character_id,
                    session_state=_loop_session_state, is_group=False,
                )
            else:
                reply = await pipeline.run_llm(messages)
            _t_llm = time.monotonic() - _t0
        if not reply:
            reply = ""

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
            reply = _clean_memory_reply(reply, pipeline.character.name) or reply

        from channels.registry import get as _get_channel
        channel = _get_channel(channel_name)
        if channel and hasattr(channel, "set_active"):
            channel.set_active(True)

        from core.turn_sink import TurnSource, record_assistant_turn
        from core.write_envelope import stamp_user_chat
        from core.coplay.session import is_active as _coplay_is_active
        _web_echo = bool(context.get("web_recall_result"))
        _coplay_echo = _coplay_is_active(user_id, char_id=_frozen_scope.character_id)
        _t0 = time.monotonic()
        turn_result = await record_assistant_turn(
            assistant_text=reply,
            uid=user_id,
            source=TurnSource.USER_CHAT,
            user_text=message,
            fanout="all",
            bypass_gate=True,
            exclude_origin_channel=channel_name,
            pipeline=pipeline,
            envelope=stamp_user_chat(),
            frozen_scope=_frozen_scope,
            web_echo=_web_echo,
            coplay_echo=_coplay_echo,
            loop_executed=_loop_active,
        )
        _t_post = time.monotonic() - _t0

        from core.memory.user_profile import get_affection_level
        info = get_affection_level(user_id)

        # Visible reply: strip render/NMP tags only so action descriptions survive
        # for chat texture.  Memory is already scrubbed inside record_assistant_turn.
        from core.response_processor import strip_render_tags as _strip_tags
        visible_source = reply
        if reply:
            visible_source = _clean_reply(reply, pipeline.character.name) or reply
        visible_reply = _strip_tags(visible_source) or visible_source

        # 流式路径：record 走完后用同一 msg_id 推 canonical 干净版替换临时气泡。
        # record_assistant_turn 已通过 exclude_origin_channel="desktop" 跳过 desktop fanout，
        # 此处是唯一一次向 desktop WS 推送 canonical channel_message，不会重复。
        if _stream_msg_id and reply:
            await _dws.push_message(
                visible_reply,
                msg_id=_stream_msg_id,
                char_id=_frozen_scope.character_id,
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
                    )
            except Exception:
                logger.debug("[owner_chat] message_segments push failed", exc_info=True)

        # 纯 logging，不改任何行为。字段含义见 cc-tasks/18。
        _chars = len(reply) if reply else 0
        _t_total = time.monotonic() - _t_start
        if _use_stream:
            _cps = (_chars / _t_stream) if _t_stream else 0.0
            logger.info(
                f"[owner_chat/timing] probe={_t_probe:.2f}s ctx={_t_ctx:.2f}s "
                f"prompt={_t_prompt:.2f}s first_delta={(_t_first_delta or 0.0):.2f}s "
                f"stream={(_t_stream or 0.0):.2f}s chars={_chars} ({_cps:.1f} c/s) "
                f"post={_t_post:.2f}s total={_t_total:.2f}s"
            )
        else:
            _cps = (_chars / _t_llm) if _t_llm else 0.0
            logger.info(
                f"[owner_chat/timing] probe={_t_probe:.2f}s ctx={_t_ctx:.2f}s "
                f"prompt={_t_prompt:.2f}s llm={(_t_llm or 0.0):.2f}s "
                f"chars={_chars} ({_cps:.1f} c/s) post={_t_post:.2f}s total={_t_total:.2f}s"
            )

        return {
            "reply": visible_reply,
            "affection": info["value"],
            "level": info["label"],
            "emotion": turn_result.emotion,
            "turn_id": turn_result.turn_id,
            # 流式路径：HTTP msg_id 与 WS 流式帧共享同一 id，
            # 前端凭此判断 WS 已渲染，取消 3s HTTP fallback 计时器。
            "msg_id": _stream_msg_id or turn_result.turn_id,
            "critical_written": turn_result.written_to_memory,
        }


async def _probe_and_execute_tools(message: str, user_id: str, *, char_id: str) -> str | None:
    from core import tool_dispatcher, llm_client as _llm
    from core.memory import user_profile as _up, short_term as _st_probe
    from core.session_state import get as _get_state

    _profile = _up.load(user_id)
    _location = _profile.get("location", "杭州")
    tools_schema = tool_dispatcher.get_tools_schema(categories=["info", "desktop"])
    state = _get_state(f"user_{user_id}")

    # 注入最近 2 轮（最多 4 条）真实对话，帮助探针解析代词指代
    # 过滤 trigger_stub 条目，避免系统占位符混入探针上下文
    _probe_ctx = [
        {"role": m["role"], "content": m.get("content", "")}
        for m in _st_probe.load(user_id)
        if m.get("_source") != "trigger_stub"
    ][-4:]
    probe_messages = [
        {
            "role": "system",
            "content": tool_dispatcher.get_probe_prompt(_location),
        },
        *_probe_ctx,
        {"role": "user", "content": message},
    ]

    _probe_snap: dict | None = None
    _probe_tool_results: list[dict] = []

    def _capture_snap() -> None:
        if _probe_snap is None:
            return
        _probe_snap["tool_results"] = list(_probe_tool_results)
        try:
            from core.observe.probe_capture import capture_probe as _cap
            _cap(user_id, _probe_snap)
        except Exception:
            pass

    try:
        logger.info(f"[owner_chat] 工具探针，channel消息={message[:20]!r}")
        probe_raw = await _llm.chat(probe_messages, tools=tools_schema, call_category="probe")
        logger.info(f"[owner_chat] 探针回复={probe_raw[:60] if probe_raw else 'empty'!r}")
        tool_calls = _llm.parse_tool_call_response(probe_raw)
        _probe_snap = {
            "is_fast_path": False,
            "probe_system": tool_dispatcher.get_probe_prompt(_location),
            "probe_context": _probe_ctx,
            "user_message": message,
            "tools_available": [
                (t.get("function") or t).get("name", "")
                for t in tools_schema
            ],
            "probe_response_raw": probe_raw if isinstance(probe_raw, str) else "",
            "tool_calls": tool_calls or [],
            "channel": "desktop",
        }
        if not tool_calls:
            _capture_snap()
            return None

        for tc in tool_calls:
            t_name = tc.get("name", "")
            t_args = tc.get("arguments", {})
            logger.info(f"[owner_chat] 调用工具: {t_name}({t_args})")
            t_result, _ = await tool_dispatcher.execute(
                tool_name=t_name,
                tool_args=t_args,
                user_id=user_id,
                target_id=user_id,
                is_group=False,
                session_state=state,
                origin="user_live",
                char_id=char_id,
            )
            _probe_tool_results.append({
                "name": t_name,
                "arguments": t_args,
                "result": t_result or "",
                "has_side_effect": tool_dispatcher.is_side_effect_tool(t_name),
            })
            if t_result:
                from core.config_loader import _char_name
                _capture_snap()
                return (
                    f"（{_char_name()}刚刚执行了操作：{t_result}，"
                    f"他知道自己做了这件事，可以自然地提及）"
                )
    except Exception as e:
        logger.warning(f"[owner_chat] 探针异常: {e}")
    _capture_snap()
    return None


@router.post("/chat", summary="与角色对话（管理面板专用）[v0.1 已禁用]")
async def frontend_chat(body: dict, auth=Depends(require_scopes("chat"))):
    """
    v0.1 禁用：该通道使用 frontend_owner 作为幽灵 uid，会产生假历史与调试分叉。
    v0.1 只保留 /desktop/chat 单通道。v0.2 再决定此通道去留。
    """
    raise HTTPException(
        status_code=410,
        detail="v0.1 禁用 legacy /chat，请使用 /desktop/chat。",
    )

    # 以下代码已不可达，保留供 v0.2 参考
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message 不能为空")

    # 获取 main.py 中初始化好的 pipeline 实例
    try:
        import main as _main
        pipeline  = _main._pipeline
        if pipeline is None:
            raise AttributeError("_pipeline is None")
    except (ImportError, AttributeError):
        raise HTTPException(status_code=503, detail="Bot pipeline 未初始化，请先启动主程序")

    user_id = _FRONTEND_USER_ID

    # 步骤 1：拉取上下文
    context = await pipeline.fetch_context(user_id, message)

    # 步骤 2：构建 prompt（追加第四面墙提示到 author_note_extra）
    orig_note = pipeline.author_note_extra
    pipeline.author_note_extra = (_FOURTH_WALL_NOTE + " " + orig_note).strip()
    messages, _ = pipeline.build_prompt(user_id, message, context)

    # 步骤 3：调用 LLM
    reply = await pipeline.run_llm(messages)

    # 步骤 4：后处理（异步，不阻塞响应）
    asyncio.create_task(
        pipeline.post_process(user_id, message, reply)
    )

    # 返回回复 + 最新好感度
    from core.memory.user_profile import get_affection_level
    info = get_affection_level(user_id)

    return {
        "reply":      reply,
        "affection":  info["value"],
        "level":      info["label"],
    }


_DREAM_GUARD_UNCERTAIN_MSG = (
    "梦境状态暂时无法确认，为避免串写现实记忆，已暂停这次现实对话。"
)


def _check_reality_not_in_dream(uid: str) -> None:
    """
    Safety net: hard reject reality turns when dream is active or unconfirmable.

    Fail-closed: if the dream state file exists but cannot be read or parsed,
    the reality turn is rejected rather than allowed through.
    FileNotFoundError is treated as inactive (normal startup / no dream session).
    """
    try:
        from core.dream.dream_state import get_reality_guard_status, DreamGuardStatus
        guard = get_reality_guard_status(uid)
    except Exception:
        logger.error("[dream_guard] guard check failed uid=%s — fail closed", uid, exc_info=True)
        raise HTTPException(status_code=409, detail=_DREAM_GUARD_UNCERTAIN_MSG)

    if guard == DreamGuardStatus.BLOCK_ACTIVE:
        raise HTTPException(
            status_code=409,
            detail="还在梦里，先醒过来（dream active — reality turn rejected）",
        )
    if guard == DreamGuardStatus.BLOCK_UNCERTAIN:
        logger.error("[dream_guard] reality turn rejected — unconfirmable dream state uid=%s", uid)
        raise HTTPException(status_code=409, detail=_DREAM_GUARD_UNCERTAIN_MSG)


@router.post("/desktop/chat", summary="桌宠对话（Bearer 鉴权）")
async def desktop_chat(body: dict, _auth=Depends(require_scopes("chat"))):
    """
    桌宠端对话入口，需 Bearer token 鉴权（Authorization: Bearer <YEXUAN_ADMIN_SECRET>）。
    user_id 从配置的 scheduler.owner_id 读取，正常走 pipeline，不注入第四面墙提示。
    """
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=422, detail="message 不能为空")

    from core.config_loader import get_config as _cfg
    _uid = str(_cfg().get("scheduler", {}).get("owner_id", "owner"))
    _check_reality_not_in_dream(_uid)

    result = await run_owner_chat_turn(message, "desktop")

    from core.scheduler.sensor_events import notify_chat_happened
    notify_chat_happened()

    return result


@router.post("/upload/ingest", summary="三端统一文件上传入口")
async def upload_ingest(
    file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
    message: str = Form(""),
    channel: str = Form("desktop"),
    _auth=Depends(require_scopes("chat")),
):
    """
    multipart 上传 + 可选用户附言 + channel 标记。
    """
    from core import media_processor

    upload_files = [file] if file else (files or [])
    if not upload_files:
        raise HTTPException(status_code=422, detail="文件不能为空")

    suffixes = [Path(item.filename or "").suffix.lower() for item in upload_files]
    is_docs = [suffix in media_processor.SUPPORTED_SUFFIXES for suffix in suffixes]
    is_images = [suffix in media_processor.SUPPORTED_IMAGE_SUFFIXES for suffix in suffixes]

    if any(is_docs) and any(is_images):
        raise HTTPException(status_code=422, detail="不支持文档和图片混合上传")

    if all(is_docs):
        if len(upload_files) > 1:
            raise HTTPException(status_code=422, detail="文档只支持单个上传")

        one_file = upload_files[0]
        data = await one_file.read()
        fname = one_file.filename or "文件"
        if len(data) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="文件超过 5MB 上限")

        result = await media_processor.ingest_file_bytes(data, fname)
        if result is None:
            raise HTTPException(status_code=422, detail="文件读取失败")

        text, stored_path = result

        media_context = (
            f"(你发来了一个文件:{fname},内容如下),回应必须细腻且有分量。"
            f"回应长度不少于150字,不要因为克制就缩短回应。\n{text[:3000]}"
        )
        full_message = media_context + ("\n" + message if message else "")
        # trusted_user_text = original message body before media prepend;
        # probe must not see file content to prevent injection via uploaded docs.
        response = await run_owner_chat_turn(full_message, channel, trusted_user_text=message)
        response["stored_path"] = str(stored_path)
        return response

    if all(is_images):
        items = []
        for item in upload_files:
            data = await item.read()
            if len(data) > media_processor.MAX_IMAGE_SIZE:
                raise HTTPException(status_code=413, detail="图片超过 10MB 上限")
            items.append((data, item.filename or "image"))

        descriptions = await media_processor.ingest_image_bytes(items)
        if descriptions is None:
            raise HTTPException(status_code=422, detail="图片识别失败")

        if len(descriptions) == 1:
            media_context = f"(你看到了用户发来的一张图,内容:{descriptions[0]})"
        else:
            lines = "\n".join(f"图{i + 1}:{desc}" for i, desc in enumerate(descriptions))
            media_context = f"(你看到了用户发来的{len(descriptions)}张图,内容如下:\n{lines})"
        full_message = media_context + ("\n" + message if message else "")
        # trusted_user_text = original message body before media prepend;
        # probe must not see image descriptions to prevent injection via uploaded images.
        response = await run_owner_chat_turn(full_message, channel, trusted_user_text=message)
        response["stored_paths"] = media_processor.LAST_IMAGE_STORED_PATHS
        return response

    raise HTTPException(status_code=415, detail="不支持的文件格式")


@router.post("/desktop/activate", summary="桌宠上线激活desktop通道")
async def desktop_activate(_auth=Depends(require_scopes("chat"))):
    from channels.registry import get as _get_channel
    channel = _get_channel("desktop")
    if channel and hasattr(channel, "set_active"):
        channel.set_active(True)
    return {"status": "ok"}


@router.post("/desktop/wake", summary="桌宠重开问候（仅触发 assistant turn，不写 user 历史）")
async def desktop_wake(body: dict = Body(default={}), _auth=Depends(require_scopes("chat"))):
    """
    桌宠重开时调用，绝不向 user 历史写入机器合成文本。

    Path A（优先）: last_seen 之后若有未回放的 assistant trigger turn，返回最新一条。
    Path B（兜底）: 无 pending turn 则现场跑一次 wake pipeline，
                   以 trigger_name="desktop_wake" 落库，fanout=[] 避免双推。
    """
    from core.config_loader import get_config as _cfg
    uid = str(_cfg().get("scheduler", {}).get("owner_id", "owner"))

    last_seen: float | None = body.get("last_seen")

    # ── Path A: pending trigger turns ──────────────────────────────────────
    if last_seen is not None:
        from core.wake_delivery_ledger import WakeDeliveryLedgerError
        try:
            from core.memory.short_term import load as _load_st
            from core.memory.locks import uid_lock as _uid_lock
            from core.wake_delivery_ledger import load_delivered, mark_delivered
            from channels import desktop_ws as _dws_pa
            # Resolve active character to scope history read correctly.
            # If active_prompt_assets.json is absent or empty, let exception propagate
            # so Path A is skipped and Path B (full pipeline) takes over.
            import json as _json_wake
            from core.sandbox import get_paths as _gp_wake
            _apa = _json_wake.loads(_gp_wake().active_prompt_assets().read_text(encoding="utf-8"))
            _active_cid = (_apa.get("active_character") or "").strip()
            if not _active_cid:
                raise ValueError("active_character missing in active_prompt_assets.json")
            async with _uid_lock(uid):
                delivered = load_delivered(uid)
                history = _load_st(uid, char_id=_active_cid)
                user_turn_ids = {
                    e["_turn_id"] for e in history
                    if e.get("role") == "user" and e.get("_turn_id")
                }
                # If WS is currently connected, exclude turns that were generated *after*
                # this WS session was accepted: those were already fanout-pushed to the
                # client and replaying them via HTTP would show the same reply twice.
                ws_connect_time = _dws_pa.get_connect_time()
                eligible = [
                    e for e in history
                    if (
                        e.get("role") == "assistant"
                        and e.get("timestamp", 0) > last_seen
                        and e.get("_turn_id")
                        and e["_turn_id"] not in user_turn_ids
                        and (not ws_connect_time or e.get("timestamp", 0) <= ws_connect_time)
                    )
                ]
                pending = [e for e in eligible if e["_turn_id"] not in delivered]
                if pending:
                    latest = max(pending, key=lambda e: e.get("timestamp", 0))
                    turn_id = latest["_turn_id"]
                    mark_delivered(uid, delivered, turn_id, latest.get("timestamp", 0))
                    return {
                        "reply": latest["content"],
                        "source": "pending_trigger",
                        "turn_id": turn_id,
                        "msg_id": turn_id,
                    }
                if eligible:
                    return {"reply": None, "source": "wake_already_delivered"}
        except WakeDeliveryLedgerError:
            logger.exception("[desktop_wake] delivery ledger 不可用 — fail-closed")
            return {"reply": None, "source": "wake_delivery_error"}
        except Exception:
            logger.exception("[desktop_wake] Path A 失败，降级到 Path B")

    # ── Path B gate: perceive_event dedup + Dream Guard ───────────────────
    # receive_perceive_event is the single choke point: it rejects duplicate
    # wakes (rapid reconnects, concurrent HTTP calls) and blocks during dream.
    # Dream Guard is now delegated to receive_perceive_event (fail-closed).
    try:
        from core.perceive_event import PerceiveEvent, PerceiveStatus, receive_perceive_event as _rpe
        _pe = PerceiveEvent(
            source="desktop_wake",
            uid=uid,
            channel="desktop",
            kind="wake",
            # payload={} — do NOT include last_seen or any per-request dynamic field;
            # wake identity is fully encoded by source+uid+char+channel+kind+bucket.
            payload={},
        )
        _pe_result = await _rpe(_pe)
    except Exception:
        logger.error("[desktop_wake] perceive_event gate 异常 — fail-closed uid=%s", uid, exc_info=True)
        return {"reply": None, "source": "perceive_error"}

    if _pe_result.status != PerceiveStatus.ACCEPTED:
        logger.info(
            "[desktop_wake] Path B not accepted: status=%s reason=%s event_id=%s",
            _pe_result.status, _pe_result.reason, _pe_result.event_id,
        )
        source_tag = {
            PerceiveStatus.DUPLICATE: "duplicate_wake",
            PerceiveStatus.BLOCKED_DREAM: "dream_guard_blocked",
        }.get(_pe_result.status, f"perceive_{_pe_result.status.value}")
        return {"reply": None, "source": source_tag}

    # ── Path B: 现场生成 wake trigger ──────────────────────────────────────
    # conversation_lock wraps fetch_context + LLM + record_assistant_turn so
    # concurrent user_chat or a duplicate wake call cannot race into the same
    # turn.  bypass_gate=True tells record_assistant_turn to skip the inner
    # lock re-acquisition (we already hold it here).
    try:
        from core.pipeline_registry import get as _get_pipeline
        pipeline = _get_pipeline()
        if pipeline is None:
            return {"reply": None, "source": "no_pipeline"}

        from core.conversation_gate import conversation_lock as _conv_lock
        prompt = "（用户重新打开了和你对话的软件，请结合真实记忆自然接续）"

        async with _conv_lock(uid):
            logger.info(
                "[desktop_wake] Path B LLM start uid=%s event_id=%s",
                uid, _pe_result.event_id,
            )
            # N1: turn-level scope freeze（与 run_owner_chat_turn / _pipeline_send 一致）
            _wake_scope = pipeline._current_reality_scope(uid)
            context = await pipeline.fetch_context(
                uid, prompt, frozen_scope=_wake_scope
            )
            try:
                from core.observe.prompt_capture import set_capture_origin as _set_capture_origin
                _set_capture_origin({
                    "origin": "proactive",
                    "trigger_name": "desktop_wake",
                    "seed_prompt": prompt,
                    "search_query": "",
                })
            except Exception:
                pass
            messages, _ = pipeline.build_prompt(
                uid, prompt, context, char_id=_wake_scope.character_id
            )
            reply = await pipeline.run_llm(messages)
            if reply:
                try:
                    from core.observe.prompt_capture import update_llm_output as _upd_wake
                    _upd_wake(uid, reply)
                except Exception:
                    pass
            if reply:
                # Shared reality guard before record_assistant_turn. Paragraph
                # enforcement is reserved for the outgoing copy after record.
                from core.reality_output_guard import (
                    clean_reality_reply_text as _clean_wake_reply,
                    clean_reality_reply_text_for_memory as _clean_wake_memory_reply,
                )
                reply = _clean_wake_memory_reply(reply, pipeline.character.name) or reply
            if reply:
                logger.info(
                    "[desktop_wake] Path B LLM done uid=%s event_id=%s reply_len=%d",
                    uid, _pe_result.event_id, len(reply),
                )
                from core.turn_sink import TurnSource, record_assistant_turn
                from core.write_envelope import stamp_trigger
                turn_result = await record_assistant_turn(
                    assistant_text=reply,
                    uid=uid,
                    source=TurnSource.TRIGGER,
                    trigger_name="desktop_wake",
                    fanout=[],      # 客户端直接展示，不通过 channel 二次推送
                    bypass_gate=True,  # already inside conversation_lock
                    pipeline=pipeline,
                    envelope=stamp_trigger(),
                    frozen_scope=_wake_scope,
                    web_echo=bool(context.get("web_recall_result")),
                )
                # B: record-only —— wake 问候语义上必须发，不受 ledger 限流，但要计入
                # 账本，防止刚 wake 又紧跟一条 random_message 之类的背靠背主动消息（RC5）。
                try:
                    from core.scheduler.proactive_ledger import record_send as _ledger_record
                    _ledger_record("desktop_wake", channel="desktop", gist=reply)
                except Exception:
                    logger.exception("[desktop_wake] proactive_ledger record_send 失败")
                # Visible reply: strip render/NMP tags only; memory already scrubbed
                # inside record_assistant_turn (memory_text path).
                from core.response_processor import strip_render_tags as _strip_tags
                visible_reply = _clean_wake_reply(reply, pipeline.character.name) or reply
                return {
                    "reply": _strip_tags(visible_reply) or visible_reply,
                    "source": "live_wake",
                    "turn_id": turn_result.turn_id,
                    "msg_id": turn_result.turn_id,
                }
            return {"reply": None, "source": "live_wake_empty"}
    except Exception:
        logger.exception("[desktop_wake] Path B 失败")
        return {"reply": None, "source": "error"}
