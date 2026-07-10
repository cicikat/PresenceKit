"""
Emerald-Presence 主程序
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


# N7-B: 只有零参数、零副作用、低误触风险的工具才允许走快速路径。
# 排除在此 allowlist 之外 ≠ 禁用工具；排除的工具继续由 LLM probe 识别。
FAST_PATH_TOOL_ALLOWLIST: frozenset[str] = frozenset({
    "get_time",  # 零参数，零副作用，关键词（"几点"/"时间"/"几号"/"星期"）低误触
})


def _check_admin_auth_startup(secret: str, has_tokens: bool) -> None:
    """Brief 33 §1.2：占位/空 secret 且 token registry 无任何记录 → 阻断启动。

    对齐同文件 gating_shadow 的阻断先例：安全网关不能只是 log 一行就放行。
    有 registry token 时 legacy secret 已失效（admin.auth.get_admin_secret 过滤占位），
    无风险，允许启动，仅提示。
    """
    if secret:
        return
    if has_tokens:
        logger.warning(
            "[startup] admin.secret_key 为空/占位，legacy secret 已失效；仅 registry token 可用"
        )
        return
    logger.error("=" * 60)
    logger.error("  [启动阻断] 未检测到有效鉴权配置（secret_key 为空/占位，且无任何 token）")
    logger.error("  请先运行: python scripts/setup_auth.py")
    logger.error("=" * 60)
    sys.exit(1)


def _fast_path_match(user_msg: str) -> tuple[str, str] | None:
    """快速路径关键词匹配（N7 可观测版）。

    返回 (tool_name, matched_keyword)，未命中返回 None。
    N7-B: 只扫描 FAST_PATH_TOOL_ALLOWLIST 内的工具，避免副作用工具或高误触风险
    工具绕过 LLM probe 直接进入工具流程。排除 ≠ 禁用。
    """
    from core import tool_dispatcher as _td
    for name, spec in _td._TOOL_REGISTRY.items():
        if name not in FAST_PATH_TOOL_ALLOWLIST:
            continue
        for kw in spec.get("keywords", []):
            if kw in user_msg:
                return name, kw
    return None


def _init_modules():
    """同步初始化：加载配置、角色卡、世界书、Pipeline"""
    global _pipeline

    logger.info("正在加载配置文件...")
    from core.config_loader import get_config
    cfg = get_config()
    logger.info("配置文件加载完成")
    logger.info(f"[startup] config.yaml 路径: {os.path.abspath('config.yaml')}")

    from core.sandbox import get_paths as _get_paths_for_log
    logger.info(f"[startup] 数据根目录: {_get_paths_for_log()._base.resolve()}")

    # 安全 P0（Brief 33 §1.2）：占位/空 secret 且 registry 无 token → 阻断启动。
    from admin.auth import get_admin_secret as _get_admin_secret
    from admin.token_registry import list_records as _list_auth_tokens
    _check_admin_auth_startup(_get_admin_secret(), bool(_list_auth_tokens()))

    if cfg.get("mode") == "production" and "test_sandbox" in str(cfg.get("data_prefix", "")):
        logger.error("=" * 60)
        logger.error("  [启动警告] mode=production 但 data_prefix 指向 test_sandbox")
        logger.error("=" * 60)

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
            # R2-D: DND 主入口接线 — 检测 owner 消息中的"别打扰/在忙"等关键词，
            # 匹配则设置 3 小时 DND；结束词（"下课/搞定了"等）则清除 DND。
            # 仅对 owner 消息生效，不影响快速路径（detect_and_set 是纯内存操作）。
            try:
                from core.scheduler.triggers.dnd import detect_and_set as _dnd_detect
                _dnd_detect(user_id, message.get("content", ""))
            except Exception:
                logger.exception("[handle_message] DND detect_and_set 失败")
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
        # 群聊走隔离路径，不进 reality 主链
        await _handle_group_message(group_id, sender_name, content, target_id)
        return

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
                char_id=_char_id,
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
            char_id=_char_id,
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
    # N2-B: qq envelope 提前构造，供 thinking helper 传入 envelope 参数
    from core.write_envelope import stamp_qq as _stamp_qq_early
    _qq_envelope = _stamp_qq_early()
    async with conversation_lock(user_id):
        # ── 步骤3：工具调用探测 ──────────────────────────────────────────────
        from core import llm_client
        from core.config_loader import get_config
        cfg = get_config()

        # Brief 28 · Path C 总闸：开关开 + owner 私聊 + chat preset 为 function_calling。
        # 为真时跳过 pre-pipeline 探针（工具决策权整体移交主模型），主生成走 run_agentic_loop。
        _loop_active = tool_dispatcher.tool_loop_active(user_id)

        tool_result_text: str | None = None

        from core.memory import user_profile as _up
        _profile = _up.load(user_id, char_id=_char_id)
        _location = _profile.get("location", "杭州")
        # 快速路径：关键词命中直接走，不调 LLM；只匹配 trusted_user_text，不含 media span
        _fast_match = _fast_path_match(_trusted_user_text)
        if _fast_match:
            _fast_tool, _fast_kw = _fast_match
            # N7: 结构化命中日志（观测用，不影响行为）
            _fast_spec = tool_dispatcher._TOOL_REGISTRY.get(_fast_tool, {})
            _fast_requires_args = bool(
                _fast_spec.get("parameters", {}).get("required", [])
            )
            logger.info(
                "[qq_fast_path_match] event=qq_fast_path_match "
                "uid=%s is_group=%s matched_tool=%s matched_keyword=%r "
                "tool_category=%s has_side_effect=%s fast_path_risk=%s "
                "original_text_preview=%r will_skip_probe=True "
                "tool_requires_args=%s has_empty_args=%s",
                user_id, is_group,
                _fast_tool, _fast_kw,
                _fast_spec.get("category", ""),
                tool_dispatcher.is_side_effect_tool(_fast_tool),
                tool_dispatcher.tool_fast_path_risk(_fast_tool),
                _trusted_user_text[:80],
                _fast_requires_args,
                _fast_requires_args,   # fast path 恒传 {}，若工具要参数则 has_empty_args=True
            )
            tool_calls = [{"name": _fast_tool, "arguments": {}}]
            logger.info(f"[handle_message] 快速路径命中工具: {_fast_tool}")
            _probe_snap: dict = {
                "is_fast_path": True,
                "matched_tool": _fast_tool,
                "matched_keyword": _fast_kw,
                "fast_path_risk": tool_dispatcher.tool_fast_path_risk(_fast_tool),
                "user_message": _trusted_user_text,
                "tool_calls": list(tool_calls),
                "channel": "qq",
            }
        elif _loop_active:
            # Brief 28 §3.4：loop 激活时跳过 pre-pipeline 探针，工具决策权整体移交主模型。
            tool_calls = None
            _probe_snap = {
                "is_fast_path": False,
                "skipped_reason": "tool_loop_active",
                "user_message": _trusted_user_text,
                "tool_calls": [],
                "channel": "qq",
            }
        else:
            # 上下文降格为只读参考块：不喂 role:assistant 回合，避免分类器被表演台词带跑
            import re as _re_probe
            from core.memory import short_term as _st_probe
            from core.character_name_provider import get_active_char_name as _get_probe_char_name
            _probe_char_name = _get_probe_char_name()
            _probe_ctx_raw = _st_probe.load(user_id, char_id=_char_id)
            _ref_lines: list[str] = []
            for _m in _probe_ctx_raw[-4:]:
                if _m.get("_source") == "trigger_stub":
                    continue
                _txt = (_m.get("content") or "").strip()
                if _m.get("role") == "assistant":
                    # strip action/stage-direction text to avoid biasing the classifier
                    _txt = _re_probe.sub(r"（[^）]*）|\([^)]*\)", "", _txt).strip()
                    if not _txt:
                        continue
                    _ref_lines.append(f"{_probe_char_name}：{_txt}")
                else:
                    _ref_lines.append(f"用户：{_txt}")
            _ref_block = "\n".join(_ref_lines)

            _probe_system = tool_dispatcher.get_probe_prompt(_location)
            if _ref_block:
                _probe_system += (
                    "\n\n【最近对话（仅供解析指代词等，不要续写、不要表演、不要进入角色）】\n"
                    + _ref_block
                )
            tool_detection_messages = [
                {"role": "system", "content": _probe_system},
                {"role": "user", "content": _trusted_user_text},
            ]
            tools_schema = tool_dispatcher.get_tools_schema(categories=["info", "desktop"])
            try:
                probe_response = await llm_client.chat(tool_detection_messages, tools=tools_schema, call_category="probe")
            except Exception:
                probe_response = ""

            tool_calls = llm_client.parse_tool_call_response(probe_response)
            logger.info(f"[handle_message] probe_response type={type(probe_response)} tool_calls={tool_calls}")
            _probe_snap = {
                "is_fast_path": False,
                "probe_system": _probe_system,
                "probe_context": _ref_block,
                "user_message": _trusted_user_text,
                "tools_available": [
                    (t.get("function") or t).get("name", "")
                    for t in tools_schema
                ],
                "probe_response_raw": probe_response if isinstance(probe_response, str) else "",
                "tool_calls": tool_calls or [],
                "channel": "qq",
            }
        _probe_tool_results: list[dict] = []
        if tool_calls:
            try:
                # N2-A/N2-B: thinking mood 写入通过显式 helper，传入 qq envelope
                from core.mood_helpers import mark_tool_thinking_mood as _mark_thinking
                await _mark_thinking(uid=user_id, char_id=_char_id, envelope=_qq_envelope)
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
                    char_id=_char_id,
                )
                if ask_text:
                    logger.info(f"[handle_message] 高危工具 {t_name}，等待用户确认")
                    # capture what we have before early return
                    _probe_snap["tool_results"] = _probe_tool_results
                    try:
                        from core.observe.probe_capture import capture_probe as _cap_probe
                        _cap_probe(user_id, _probe_snap)
                    except Exception:
                        pass
                    await text_output.send(target_id, [ask_text], is_group)
                    return
                _probe_tool_results.append({
                    "name": t_name,
                    "arguments": t_args,
                    "result": t_result or "",
                    "has_side_effect": tool_dispatcher.is_side_effect_tool(t_name),
                })
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
        _probe_snap["tool_results"] = _probe_tool_results
        try:
            from core.observe.probe_capture import capture_probe as _cap_probe
            _cap_probe(user_id, _probe_snap)
        except Exception:
            pass

        # ── 步骤4：拉取上下文（并发）────────────────────────────────────────
        logger.debug("[handle_message] 并发拉取上下文...")
        context = await _pipeline.fetch_context(user_id, content, group_id, frozen_scope=_frozen_scope)

        # ── 步骤5：组装 prompt ───────────────────────────────────────────────
        logger.debug("[handle_message] 组装 prompt...")
        messages, _meta = _pipeline.build_prompt(user_id, content, context, tool_result=tool_result_text, channel="qq", char_id=_char_id)

        # ── 步骤6：调用主 LLM ────────────────────────────────────────────────
        logger.info("[handle_message] 调用主 LLM...")
        if _loop_active:
            raw_reply = await _pipeline.run_agentic_loop(
                messages, uid=user_id, char_id=_char_id, session_state=state, is_group=is_group,
            )
        else:
            raw_reply = await _pipeline.run_llm(messages)
        try:
            from core.observe.prompt_capture import update_llm_output as _upd_prompt_out
            _upd_prompt_out(user_id, raw_reply)
        except Exception:
            pass
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

        # ── 步骤8+9：QQ reality reply adapter ──────────────────────────────
        # visible strip → send → memory pre-scrub → post_process → capture_turn (authority)
        from core.coplay.session import is_active as _coplay_is_active
        await _qq_reality_reply_adapter(
            segments, user_id, content, target_id, is_group,
            frozen_scope=_frozen_scope,
            pending_paths=_meta.get("pending_paths", []),
            web_echo=bool(context.get("web_recall_result")),
            coplay_echo=_coplay_is_active(user_id, char_id=_char_id),
            loop_executed=_loop_active,
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
    from core.error_handler import log_error
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
        if not segments:
            logger.warning("[_reply_with_tool_result] 处理后回复为空，不发送")
            return

        # QQ tool-reply: visible send + memory pre-scrub → post_process → capture_turn (authority)
        from core.coplay.session import is_active as _coplay_is_active
        await _qq_reality_reply_adapter(
            segments, user_id, _turn_content, target_id, is_group,
            frozen_scope=frozen_scope,
            pending_paths=_meta.get("pending_paths", []),
            coplay_echo=_coplay_is_active(user_id, char_id=_char_id),
        )



# ═══════════════════════════════════════════════════════════════════════════════
# 群聊隔离路径（不写主记忆）
# ═══════════════════════════════════════════════════════════════════════════════

async def _handle_group_message(
    group_id: str,
    sender_name: str,
    content: str,
    target_id: str,
) -> None:
    """
    群聊隔离路径：只读 group_context + 角色卡生成回复，直发后追加到 group_context。
    不触发 record_assistant_turn / capture_turn / fixation 等主记忆写入。
    """
    from core.memory import group_context as _gc
    from core.output import text_output
    from core import response_processor
    from core.response_processor import strip_render_tags as _strip_rt
    from core.conversation_gate import conversation_lock
    from core.error_handler import log_error as _log_error

    if _pipeline is None:
        logger.error("[group_message] pipeline 未初始化，跳过群消息 group=%s", group_id)
        return

    char = _pipeline.character

    # 组装最简 system prompt：角色卡 + 群聊定位
    parts: list[str] = []
    if char.system_prompt:
        parts.append(char.system_prompt)
    if char.description:
        parts.append(f"外貌与背景：{char.description}")
    if char.personality:
        parts.append(f"性格：{char.personality}")
    parts.append(
        "【群聊模式】你现在在QQ群聊里，以轻松自然的方式参与群聊。"
        "回复简短有趣为主，不要写独白或旁白动作描述。"
    )

    # 拼入最近群聊历史（不含刚追加的当前消息，那条作为 user turn）
    recent = _gc.get_recent(group_id)
    history = recent[:-1] if recent else []
    if history:
        ctx_lines = "\n".join(
            f"[{m.get('timestamp', '')}] {m.get('sender_name', '')}: {m.get('content', '')}"
            for m in history
        )
        parts.append(f"以下是最近的群聊记录（供参考）：\n{ctx_lines}")

    system_prompt = "\n\n".join(parts)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"[{sender_name}] {content}"},
    ]

    async with conversation_lock(f"group_{group_id}"):
        try:
            raw_reply = await _pipeline.run_llm(messages)
        except Exception as _e:
            _log_error("group_message.run_llm", _e)
            return

        if not raw_reply:
            logger.warning("[group_message] LLM 回复为空 group=%s", group_id)
            return

        segments = response_processor.process(raw_reply, char.name)
        if not segments:
            logger.warning("[group_message] 回复处理后为空 group=%s", group_id)
            return

        clean = [s for s in (_strip_rt(seg) for seg in segments) if s]
        if not clean:
            logger.warning("[group_message] strip 后为空 group=%s", group_id)
            return

        logger.info("[group_message] 发送群 %s 共 %d 段", group_id, len(clean))
        try:
            await text_output.send(target_id, clean, is_group=True)
        except Exception as _e:
            _log_error("group_message.send", _e)
            return

        # 把机器人回复追加进群上下文，供下轮参考（可丢弃，不写主记忆）
        _gc.append(group_id, char.name, "\n".join(clean))


# ═══════════════════════════════════════════════════════════════════════════════
# QQ LLM_ASSISTANT_REPLY 统一出口
# ═══════════════════════════════════════════════════════════════════════════════

async def _qq_reality_reply_adapter(
    segments: list[str],
    user_id: str,
    user_content: str,
    target_id: str,
    is_group: bool,
    frozen_scope,
    pending_paths: list | None = None,
    web_echo: bool = False,
    coplay_echo: bool = False,
    loop_executed: bool = False,
) -> None:
    """
    QQ LLM_ASSISTANT_REPLY 统一出口（R1-D: turn_sink 统一链路）。

    handle_message（普通 LLM 回复）和 _reply_with_tool_result（工具确认 LLM 回复）
    共用此 adapter：

      record_assistant_turn（turn_sink 统一链路）
        → scrub + post_process → capture_turn（REALITY_MEMORY 权威 scrub 点）
      → REALITY_VISIBLE strip → QQ send（text_output.send）

    顺序拍板（审计§四/裁定 2026-07-08）：轮次完整性 > 投递确认，先写记忆后发送。
    宁可"她没看到但角色记得"（发送失败，记忆已写入，下一轮语境自洽），
    不可"她看到了但我忘了"（原顺序下 send 失败会直接 return，记忆完全不写）。
    record 失败仍 fail-open（不做补偿删除、不做重发队列），之后照常尝试 send。

    只处理 LLM_ASSISTANT_REPLY。Dream guard / SYSTEM_SHORT_TEXT /
    TOOL_CONFIRMATION_PROMPT 等系统短文本继续直发，不经此 adapter，不写 memory。

    fanout=[]: visible send 由本函数自己调用 text_output.send，turn_sink 不重复发送。
    bypass_gate=True: adapter 调用方已在 conversation_lock 内，无需重入。
    loop_executed（Brief 28）：本轮是否走了 tool loop，透传给 record_assistant_turn。
    """
    from core.response_processor import strip_render_tags as _strip_rt
    from core.output import text_output
    from core.turn_sink import record_assistant_turn as _record_turn, TurnSource
    from core.write_envelope import stamp_qq
    from core.error_handler import log_error as _log_error

    # REALITY_VISIBLE: strip render/NMP tags for QQ visible output; preserve
    # action descriptions for chat texture (heavy scrub only on memory path below).
    clean = [s for s in (_strip_rt(seg) for seg in segments) if s]
    if not clean:
        logger.warning("[qq_reality_reply_adapter] 回复 strip 后为空，不发送 uid=%s", user_id)
        return

    # Route memory write through turn_sink unified chain — BEFORE visible send.
    # scrub_reality_output_text + capture_turn (authority scrub) live inside
    # record_assistant_turn → pipeline.post_process → capture_turn.
    # fanout=[] avoids double-send (visible send happens below via text_output.send).
    # bypass_gate=True: already inside conversation_lock from the call site.
    try:
        await _record_turn(
            assistant_text="\n".join(segments),
            uid=user_id,
            source=TurnSource.USER_CHAT,
            user_text=user_content,
            fanout=[],
            bypass_gate=True,
            envelope=stamp_qq(),
            target_id=target_id,
            is_group=is_group,
            pending_paths=pending_paths,
            frozen_scope=frozen_scope,
            pipeline=_pipeline,
            web_echo=web_echo,
            coplay_echo=coplay_echo,
            loop_executed=loop_executed,
        )
    except Exception as _ts_err:
        _log_error("qq_reality_reply_adapter.turn_sink", _ts_err)
        from core import silent_failure
        silent_failure.note("turn_sink.record_assistant_turn", _ts_err)
        logger.error(
            "[qq_reality_reply_adapter] turn_sink 异常（记忆写入可能丢失）uid=%s: %s",
            user_id, _ts_err,
        )

    logger.info(
        "[qq_reality_reply_adapter] 发送到 %s%s 共 %d 段",
        "群" if is_group else "私聊", target_id, len(clean),
    )
    try:
        await text_output.send(target_id, clean, is_group)
    except Exception as e:
        _log_error("qq_reality_reply_adapter.send", e)
        logger.error(
            "[qq_reality_reply_adapter] 发送异常 uid=%s: %s: %s",
            user_id, type(e).__name__, e,
        )
        return


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════════

async def main():
    logger.info("=" * 60)
    logger.info("  Emerald-Presence 启动中...")
    logger.info("=" * 60)

    _init_modules()

    from core.config_loader import get_config
    cfg = get_config()

    if cfg.get("logging", {}).get("console_quiet", True):
        from admin.log_filter import install_console_quiet_mode
        install_console_quiet_mode()

    from core import session_state
    session_state.start_cleanup_task()
    logger.info("会话超时清理任务已启动")

    # MCP 客户端（Brief 29 · 4）：mcp_servers.enabled=false（默认）时零开销直接返回
    from core import mcp_client
    await mcp_client.init_mcp_servers()

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
    from channels.device import DeviceChannel
    _desktop_channel = DesktopChannel()
    _reg_channel(_desktop_channel)
    _mobile_channel = MobileChannel()
    _reg_channel(_mobile_channel)
    _device_channel = DeviceChannel()
    _reg_channel(_device_channel)

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
        await mcp_client.shutdown_mcp_servers()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot 已停止")
