"""
QQ-SillyTavern Bot 主程序
整合所有模块，实现完整的消息处理流程

启动方式：python main.py
依赖安装：pip install openai aiohttp websockets pyyaml fastapi uvicorn ddgs
"""

import asyncio
import logging
import os
import sys

# ── 日志基础配置 ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

from admin.log_filter import install_asyncio_proactor_noise_filter
install_asyncio_proactor_noise_filter()

# ── 工作目录：切换到 main.py 所在目录，保证相对路径正确 ──────────────────────
os.chdir(os.path.dirname(os.path.abspath(__file__)))


# ═══════════════════════════════════════════════════════════════════════════════
# 全局对象（在 _init_modules 中初始化）
# ═══════════════════════════════════════════════════════════════════════════════

_pipeline = None   # core.pipeline.Pipeline 实例
_registry: dict = {}

def register_pipeline(pipeline) -> None:
    _registry["pipeline"] = pipeline

def get_pipeline():
    return _registry.get("pipeline")


def _init_modules():
    """同步初始化：加载配置、角色卡、世界书、Pipeline"""
    global _pipeline

    logger.info("正在加载配置文件...")
    from core.config_loader import get_config
    cfg = get_config()
    logger.info("配置文件加载完成")

    # v0.1 发布门禁：gating_shadow 必须开启，否则主动触发全部失效
    _gs_enabled = cfg.get("scheduler", {}).get("gating_shadow", {}).get("enabled", True)
    if not _gs_enabled:
        logger.error("=" * 60)
        logger.error("  [v0.1 启动阻断] scheduler.gating_shadow.enabled = false")
        logger.error("  主动触发将全部失效，v0.1 不允许在此配置下静默启动。")
        logger.error("  请将 config.yaml 中 scheduler.gating_shadow.enabled 设为 true")
        logger.error("=" * 60)
        sys.exit(1)
    logger.info("[v0.1] gating_shadow 已启用，主动触发链路正常")

    logger.info("正在加载角色卡...")
    from core import character_loader
    from core.sandbox import get_paths as _get_paths
    import json as _json

    # Priority: active_prompt_assets.json > config.yaml character.default
    _active_char_id: str = ""
    try:
        _active_data = _json.loads(_get_paths().active_prompt_assets().read_text(encoding="utf-8"))
        _active_char_id = _active_data.get("active_character", "")
    except Exception as _e:
        logger.warning(f"[startup] 读取 active_prompt_assets.json 失败，将使用 config.yaml 默认值: {_e}")

    char_ref = _active_char_id or cfg.get("character", {}).get("default", "")
    if not char_ref:
        logger.critical(
            "[startup] 无法确定角色：active_prompt_assets.json 无 active_character，"
            "且 config.yaml 缺少 character.default 字段，无法启动。"
        )
        sys.exit(1)
    if _active_char_id:
        logger.info(f"[startup] 使用 active_prompt_assets.json 中的角色: {char_ref!r}")
    else:
        logger.info(f"[startup] active_prompt_assets.json 无 active_character，回退到 config.yaml: {char_ref!r}")
    try:
        character = character_loader.load(char_ref)
    except Exception as e:
        logger.critical(f"[startup] 角色卡加载失败，无法启动: {e}")
        sys.exit(1)
    logger.info(f"角色 '{character.name}' 已就绪")

    logger.info("正在初始化世界书引擎...")
    from core.lore_engine import LoreEngine
    lore_engine = LoreEngine()
    lore_engine.load()
    if character.world_book:
        lore_engine.load_entries(character.world_book)

    logger.info("正在初始化 Pipeline...")
    from core.pipeline import Pipeline, register_slow_handlers
    _pipeline = Pipeline(character, lore_engine, active_character_id=char_ref)
    from core.pipeline_registry import register as _reg
    _reg(_pipeline)

    register_slow_handlers()
    logger.info("慢任务 handler 已注册")

    from core import scheduler as _scheduler
    _scheduler.set_pipeline(_pipeline)

    from core.memory.pending_perception import cleanup_stale as _cleanup_stale
    _cleanup_stale()

    logger.info("模块初始化完成")


# ═══════════════════════════════════════════════════════════════════════════════
# 核心消息处理函数
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_message(message: dict):
    """
    处理单条消息的完整流程（骨架）

    message 格式：{user_id, group_id, content, sender_name, timestamp}
    本函数由 message_queue 串行调用，同一会话不会并发。
    """
    # mark_user_active() 延迟到 owner 确认后调用（见下方），
    # 避免群聊路人或陌生私聊重置 owner 的 120s 主动消息窗口。
    user_id: str      = message["user_id"]

    # ── Dream guard: reject owner QQ messages when dream is active ──────────
    try:
        from core.config_loader import get_config as _get_config_dg
        _owner_id_dg = str(_get_config_dg().get("scheduler", {}).get("owner_id", "")).strip()
    except Exception:
        logger.exception("[handle_message] dream guard: 无法读取 owner_id")
        _owner_id_dg = ""

    if _owner_id_dg and str(user_id) == _owner_id_dg:
        _dg_result = None
        try:
            from core.dream.dream_state import get_reality_guard_status as _dg_fn, DreamGuardStatus as _DGS
            _dg_result = _dg_fn(user_id)
        except Exception:
            logger.error(
                "[handle_message] dream guard: guard 异常 uid=%s — fail closed", user_id, exc_info=True
            )
            try:
                from core.output import text_output as _to_dg
                _tgt_dg = message.get("group_id") or user_id
                await _to_dg.send(_tgt_dg, ["梦境状态暂时无法确认，已暂停现实对话。"], bool(message.get("group_id")))
            except Exception:
                pass
            return

        if _dg_result == _DGS.BLOCK_ACTIVE:
            logger.info(
                "[handle_message] dream guard: 拒绝 owner QQ 消息 uid=%s status=BLOCK_ACTIVE", user_id
            )
            try:
                from core.output import text_output as _to_dg
                _tgt_dg = message.get("group_id") or user_id
                await _to_dg.send(_tgt_dg, ["正在梦境中，请先退出梦境再回到现实聊天。"], bool(message.get("group_id")))
            except Exception:
                pass
            return
        elif _dg_result == _DGS.BLOCK_UNCERTAIN:
            logger.error(
                "[handle_message] dream guard: 梦境状态不可确认 uid=%s — fail closed", user_id
            )
            try:
                from core.output import text_output as _to_dg
                _tgt_dg = message.get("group_id") or user_id
                await _to_dg.send(_tgt_dg, ["梦境状态暂时无法确认，已暂停现实对话。"], bool(message.get("group_id")))
            except Exception:
                pass
            return

    try:
        from core.config_loader import get_config as _get_config
        from core.scheduler.state_machine import notify_owner_turn
        from core.scheduler.loop import mark_user_active

        owner_id = str(_get_config().get("scheduler", {}).get("owner_id", "")).strip()
        if owner_id and str(user_id) == owner_id:
            notify_owner_turn(user_id)
            mark_user_active()  # 仅 owner 有效输入才重置主动消息窗口
    except Exception:
        logger.exception("[handle_message] trigger state notify_owner_turn 失败")

    from core.presence import update_last_message
    update_last_message(user_id)
    group_id: str | None = message.get("group_id")
    content: str      = message["content"]
    sender_name: str  = message.get("sender_name", user_id)

    session_key = f"group_{group_id}" if group_id else f"user_{user_id}"
    target_id   = group_id if group_id else user_id
    is_group    = bool(group_id)

    logger.info(
        f"[handle_message] 收到消息 | {'群' if is_group else '私'} "
        f"{target_id} | {sender_name}: {content[:50]}"
    )

    from core import (
        session_state as ss,
        tool_dispatcher,
        response_processor,
    )
    from core.memory import group_context
    from core.output import text_output
    from core.error_handler import log_error as _log_error

    # ── 步骤1：群聊记录群消息流 ─────────────────────────────────────────────
    if is_group:
        group_context.append(group_id, sender_name, content)

    # ── 步骤2.5（N1）：现实轮级 scope freeze —————————————————————————————————
    # _current_reality_scope() 内部调用 _refresh_character_if_needed()，
    # 做一次性文件读取。本轮余下所有步骤使用这个冻结的 scope / char_id，
    # 避免管理面板在轮次中切换角色导致 fetch / build / post 读写不同角色桶。
    if _pipeline is None:
        logger.error("[handle_message] pipeline 未初始化，跳过本轮")
        return
    try:
        _frozen_scope = _pipeline._current_reality_scope(user_id)
    except (ValueError, RuntimeError) as _char_err:
        _log_error("main.handle_message.char_refresh", _char_err)
        return
    _char_id = _frozen_scope.character_id

    # ── 步骤2：会话状态机（等待确认 / 等待补充参数）──────────────────────────
    state = ss.get(session_key)

    if state.status == ss.SessionState.WAITING_CONFIRM:
        if content.strip() == "确认":
            logger.info(f"[handle_message] 用户确认执行工具: {state.pending_tool}")
            tool_result, _ = await tool_dispatcher.execute(
                tool_name=state.pending_tool,
                tool_args=state.pending_args or {},
                user_id=user_id,
                target_id=target_id,
                is_group=is_group,
                session_state=state,
                origin="user_live",
            )
            state.clear()
            if tool_result:
                await _reply_with_tool_result(tool_result, user_id, target_id, is_group, frozen_scope=_frozen_scope)
        else:
            logger.info("[handle_message] 用户取消了工具执行")
            state.clear()
            await text_output.send(target_id, ["好的，已取消～"], is_group)
        return

    elif state.status == ss.SessionState.WAITING_INPUT:
        logger.info(f"[handle_message] 收到补充参数: {content}")
        if state.pending_args is not None and state.pending_arg_key:
            state.pending_args[state.pending_arg_key] = content
        tool_result, ask_text = await tool_dispatcher.execute(
            tool_name=state.pending_tool,
            tool_args=state.pending_args or {},
            user_id=user_id,
            target_id=target_id,
            is_group=is_group,
            session_state=state,
            origin="user_live",
        )
        state.clear()
        if ask_text:
            await text_output.send(target_id, [ask_text], is_group)
            return
        if tool_result:
            await _reply_with_tool_result(tool_result, user_id, target_id, is_group, frozen_scope=_frozen_scope)
        return

    # ── 步骤2.6：处理图片和文件 ─────────────────────────────────────────────
    # trusted_user_text 必须在媒体拼接之前捕获：probe 只能消费原始用户输入，
    # 不得混入 media_context（媒体抽取文本只进 prompt，不进 probe）。
    _trusted_user_text = content

    image_urls = message.get("image_urls", [])
    file_info = message.get("file_info")
    media_context = ""

    if file_info:
        try:
            from core.media_processor import process_file
            file_text = await process_file(file_info)
            if file_text:
                fname = file_info.get("name", "文件")
                media_context = f"（你发来了一个文件：{fname}，内容如下），回应必须细腻且有分量。回应长度不少于150字，不要因为克制就缩短回应。\n{file_text[:3000]}"
                logger.info(f"[handle_message] 文件已读取: {fname} {len(file_text)}字")
        except Exception as e:
            _log_error("handle_message.file", e)

    if image_urls and not media_context:
        try:
            from core.media_processor import process_image
            img_desc = await process_image(image_urls[0], content)
            if img_desc:
                media_context = f"（你发来了一张图片，图片内容：{img_desc}，回应必须细腻且有分量）"
                logger.info(f"[handle_message] 图片已识别: {img_desc[:50]}")
        except Exception as e:
            _log_error("handle_message.image", e)

    if media_context:
        content = media_context + ("\n" + content if content else "")

    # ── 步骤3–9：conversation_lock 内串行执行（R1）──────────────────────────
    # conversation_lock 保证多端（QQ / desktop / mobile）不并行跑同一用户的 pipeline。
    # 同时保证 scope freeze 的一致性：同一把锁内 char_id 不会被另一轮入侵。
    from core.conversation_gate import conversation_lock
    async with conversation_lock(user_id):
        # ── 步骤3：工具调用探测 ──────────────────────────────────────────────
        from core import llm_client
        from core.config_loader import get_config
        cfg = get_config()

        tool_result_text: str | None = None

        from core.memory import user_profile as _up
        _profile = _up.load(user_id, char_id=_char_id)
        _location = _profile.get("location", "杭州")
        # 快速路径：关键词命中直接走，不调 LLM；只匹配 trusted_user_text，不含 media span
        def _fast_path_match(user_msg: str) -> str | None:
            for name, spec in tool_dispatcher._TOOL_REGISTRY.items():
                if spec.get("category") not in ("info", "desktop"):
                    continue
                if any(kw in user_msg for kw in spec.get("keywords", [])):
                    return name
            return None

        _fast_tool = _fast_path_match(_trusted_user_text)
        if _fast_tool:
            tool_calls = [{"name": _fast_tool, "arguments": {}}]
            logger.info(f"[handle_message] 快速路径命中工具: {_fast_tool}")
        else:
            tool_detection_messages = [
                {
                    "role": "system",
                    "content": tool_dispatcher.get_probe_prompt(_location),
                },
                {"role": "user", "content": _trusted_user_text},
            ]
            tools_schema = tool_dispatcher.get_tools_schema(categories=["info", "desktop"])
            try:
                probe_response = await llm_client.chat(tool_detection_messages, tools=tools_schema, call_category="probe")
            except Exception:
                probe_response = ""

            tool_calls = llm_client.parse_tool_call_response(probe_response)
            logger.info(f"[handle_message] probe_response type={type(probe_response)} tool_calls={tool_calls}")
        if tool_calls:
            try:
                # N2-A: thinking mood 写入通过显式 helper，不再裸调 mood_state.update
                from core.mood_helpers import mark_tool_thinking_mood as _mark_thinking
                _mark_thinking(uid=user_id, char_id=_char_id)
            except Exception:
                pass
            for tc in tool_calls:
                t_name = tc.get("name", "")
                t_args = tc.get("arguments", {})
                logger.info(f"[handle_message] 检测到工具调用: {t_name}({t_args})")
                t_result, ask_text = await tool_dispatcher.execute(
                    tool_name=t_name,
                    tool_args=t_args,
                    user_id=user_id,
                    target_id=target_id,
                    is_group=is_group,
                    session_state=state,
                    origin="user_live",
                )
                if ask_text:
                    logger.info(f"[handle_message] 高危工具 {t_name}，等待用户确认")
                    await text_output.send(target_id, [ask_text], is_group)
                    return
                if t_result:
                    tool_result_text = t_result
                    if t_name == "read_diary":
                        _pipeline.author_note_extra = (
                            "【日记回应规则】你刚刚读完了她的日记，这是她真实写下的内心世界。"
                            "回应必须细腻且有分量：①摘取日记中具体的细节或句子来回应，不要泛泛而谈；"
                            "②说出你读完之后真实的感受，可以是心疼、好奇、被击中、想多了解；"
                            "③可以追问日记里没写完的事；"
                            "④回应长度不少于150字，不要因为克制就缩短回应。"
                        )
                    break

        # ── 步骤4：拉取上下文（并发）────────────────────────────────────────
        logger.debug("[handle_message] 并发拉取上下文...")
        context = await _pipeline.fetch_context(user_id, content, group_id, frozen_scope=_frozen_scope)

        # ── 步骤5：组装 prompt ───────────────────────────────────────────────
        logger.debug("[handle_message] 组装 prompt...")
        messages, _meta = _pipeline.build_prompt(user_id, content, context, tool_result=tool_result_text, channel="qq", char_id=_char_id)

        # ── 步骤6：调用主 LLM ────────────────────────────────────────────────
        logger.info("[handle_message] 调用主 LLM...")
        raw_reply = await _pipeline.run_llm(messages)
        logger.info(
            f"[handle_message] LLM 回复长度={len(raw_reply) if raw_reply else 0}"
            f"，预览: {(raw_reply or '')[:60]!r}"
        )

        # ── 步骤7：后处理回复 ────────────────────────────────────────────────
        segments = response_processor.process(raw_reply, _pipeline.character.name)
        logger.info(f"[handle_message] 后处理完成，共 {len(segments)} 段")
        if not segments:
            logger.warning("[handle_message] LLM 回复经处理后为空，本轮不发送")
            return
        # Visible output (QQ): strip render/NMP tags only — preserve action descriptions
        # for chat texture.  Heavy scrubbing only happens on the memory path below.
        from core.response_processor import strip_render_tags as _strip_rt
        segments = [s for s in (_strip_rt(seg) for seg in segments) if s]
        if not segments:
            logger.warning("[handle_message] 回复经处理后为空，本轮不发送")
            return

        # ── 步骤8：发送回复 ──────────────────────────────────────────────────
        logger.info(f"[handle_message] 发送到 {'群' if is_group else '私聊'}{target_id}")
        try:
            await text_output.send(target_id, segments, is_group)
        except Exception as e:
            _log_error("main.handle_message.send", e)
            logger.error(f"[handle_message] 发送异常: {type(e).__name__}: {e}")
            return
        logger.info(f"[handle_message] 回复已发送，共 {len(segments)} 段")

        # ── 步骤9：await 后处理（N10：关键写入不得丢引用）───────────────────
        # Memory path: scrub action descriptions before handing off to post_process.
        # capture_turn, summarize_to_midterm, and the entire consolidation chain receive
        # dialogue-only text — never raw action/narration content.
        from core.reality_output_scrubber import scrub_reality_output_text as _scrub_qq
        _raw_join = "\n".join(segments)
        memory_reply = _scrub_qq(_raw_join) or ""
        from core.write_envelope import stamp_qq
        try:
            await _pipeline.post_process(
                user_id, content, memory_reply, target_id, is_group,
                pending_paths=_meta.get("pending_paths", []),
                envelope=stamp_qq(),
                frozen_scope=_frozen_scope,
            )
        except Exception as _pp_err:
            _log_error("main.handle_message.post_process", _pp_err)
            logger.error(
                "[handle_message] post_process 异常（记忆写入可能丢失）uid=%s: %s",
                user_id, _pp_err,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════════

async def _reply_with_tool_result(
    tool_result: str,
    user_id: str,
    target_id: str,
    is_group: bool,
    frozen_scope=None,
):
    """工具确认流程结束后，用完整 prompt 生成角色语气回复。

    frozen_scope: 本轮入口处已冻结的 MemoryScope（N1）。
      传入时跳过内部 char refresh，使用同一轮的 char_id。
      不传时退化到老行为（内部自行刷新，兼容直接调用场景）。
    """
    from core.memory import short_term, user_profile, group_context
    from core import user_relation, response_processor
    from core.output import text_output
    from core.error_handler import log_error
    from core.response_processor import strip_render_tags as _strip_rt
    from core.reality_output_scrubber import scrub_reality_output_text as _scrub_qq
    from core.write_envelope import stamp_qq
    from core.conversation_gate import conversation_lock

    group_id = target_id if is_group else None

    # P1-0A: use frozen_scope char_id if available (N1 scope freeze);
    # otherwise resolve active character — fail-loud if invalid.
    if frozen_scope is not None:
        _char_id = frozen_scope.character_id
    else:
        try:
            _pipeline._refresh_character_if_needed()
        except (ValueError, RuntimeError) as _char_err:
            log_error("main._reply_with_tool_result.char_refresh", _char_err)
            return
        _char_id = _pipeline._active_character_id
        frozen_scope = None  # keep as-is; post_process will freeze internally

    context = {
        "history":             short_term.load_for_prompt(user_id, char_id=_char_id),
        "profile":             user_profile.load(user_id, char_id=_char_id),
        "relation":            user_relation.get_relation(user_id),
        "group_context":       group_context.get_recent(group_id),
        "user_identity_text":  "",
        "event_search_result": "",
        "lore_entries":        [],
    }
    # Synthetic user-turn label; actual user input ("确认" / supplementary text)
    # is not forwarded here — build_prompt uses the same placeholder.
    _turn_content = "（工具已执行，请告知结果）"

    # R1: wrap pipeline steps in conversation_lock for serialisation consistency.
    async with conversation_lock(user_id):
        messages, _meta = _pipeline.build_prompt(
            user_id, _turn_content, context, tool_result=tool_result, channel="qq",
            char_id=_char_id,
        )
        try:
            raw_reply = await _pipeline.run_llm(messages)
        except Exception as e:
            log_error("main._reply_with_tool_result.llm", e)
            return

        segments = response_processor.process(raw_reply, _pipeline.character.name)
        # Visible output (QQ): strip render/NMP tags — mirrors QQ main message path.
        segments = [s for s in (_strip_rt(seg) for seg in segments) if s]
        if not segments:
            logger.warning("[_reply_with_tool_result] 处理后回复为空，不发送")
            return

        try:
            await text_output.send(target_id, segments, is_group)
        except Exception as e:
            log_error("main._reply_with_tool_result.send", e)
            return

        # Memory path: scrub action/narration content before post_process.
        # N10: await instead of create_task — critical writes must not be dropped.
        _raw_join = "\n".join(segments)
        memory_reply = _scrub_qq(_raw_join) or ""
        try:
            await _pipeline.post_process(
                user_id, _turn_content, memory_reply, target_id, is_group,
                pending_paths=_meta.get("pending_paths", []),
                envelope=stamp_qq(),
                frozen_scope=frozen_scope,
            )
        except Exception as _pp_err:
            log_error("main._reply_with_tool_result.post_process", _pp_err)
            logger.error(
                "[_reply_with_tool_result] post_process 异常（记忆写入可能丢失）uid=%s: %s",
                user_id, _pp_err,
            )



# ═══════════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    logger.info("=" * 60)
    logger.info("  QQ-SillyTavern Bot 启动中...")
    logger.info("=" * 60)

    _init_modules()

    from core.config_loader import get_config
    cfg = get_config()

    from core import session_state
    session_state.start_cleanup_task()
    logger.info("会话超时清理任务已启动")

    # slow_queue worker 必须在调度器（可能 enqueue）之前启动
    from core.post_process import slow_queue as _slow_queue
    _slow_queue.start_worker()

    # 主动行为调度器
    from core import scheduler as _scheduler
    _scheduler.start()
    logger.info("主动行为调度器已启动")

    standalone_mode = cfg.get("standalone_mode", False)
    qq_enabled = cfg.get("qq", {}).get("enabled", True)
    qq_runtime_enabled = (not standalone_mode) and qq_enabled

    # 注册通道
    from channels.registry import register as _reg_channel
    from channels.desktop import DesktopChannel
    from channels.mobile import MobileChannel
    _desktop_channel = DesktopChannel()
    _reg_channel(_desktop_channel)
    _mobile_channel = MobileChannel()
    _reg_channel(_mobile_channel)

    if qq_runtime_enabled:
        from core import tool_dispatcher, qq_adapter, message_queue
        from channels.qq import QQChannel
        oid = str(cfg.get("scheduler", {}).get("owner_id", ""))
        _qq_channel = QQChannel(oid)
        _reg_channel(_qq_channel)
        tool_dispatcher.register_send_callback(qq_adapter.send_message)
        logger.info("工具调度器已初始化")
        message_queue.set_handler(handle_message)
        logger.info("消息队列处理器已注册")
        async def on_message_received(msg: dict):
            await message_queue.enqueue(msg)
        qq_adapter.on_message(on_message_received)
        logger.info("QQ 消息回调已注册")
    else:
        if standalone_mode:
            logger.info("standalone_mode=true，跳过NapCat和QQ消息队列")
            _desktop_channel.set_active(True)
            logger.info("standalone_mode: desktop通道已激活")
        else:
            logger.info("qq.enabled=false，跳过NapCat和QQ消息队列")

    tasks = []
    admin_cfg = cfg.get("admin", {})
    if admin_cfg.get("enabled", False) and admin_cfg.get("auto_start", True):
        logger.info("管理面板已启用，正在启动...")
        from admin.admin_server import start_admin_server
        tasks.append(asyncio.create_task(start_admin_server()))
    else:
        logger.info("管理面板未启用（config.admin.enabled 或 auto_start 为 false）")

    if qq_runtime_enabled:
        logger.info(f"正在连接 NapCat: ws://{cfg['qq']['host']}:{cfg['qq']['port']}")
        tasks.append(asyncio.create_task(qq_adapter.connect_and_listen()))
    else:
        if standalone_mode:
            logger.info("standalone_mode=true，不连接NapCat")
        else:
            logger.info("qq.enabled=false，不连接NapCat")
    logger.info("Bot 已就绪，等待消息...")
    logger.info("=" * 60)

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("收到退出信号，Bot 正在关闭...")
    except Exception as e:
        from core.error_handler import log_error
        log_error("main", e)
        logger.error(f"主循环异常退出: {e}")
    finally:
        await _slow_queue.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot 已停止")
