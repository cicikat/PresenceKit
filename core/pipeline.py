"""
消息处理流水线
把 handle_message 的核心步骤封装成独立方法，main.py 只保留骨架调用。

Pipeline 实例持有角色卡和世界书引擎的引用，在 main.py 中初始化后全程复用。
"""

import logging

from core.memory import pending_perception as _pending_perception
from core.memory.scope import MemoryScope

logger = logging.getLogger(__name__)


class Pipeline:
    """
    Reality 会话的 prompt / generation 与领域组件实例所有者：

    持有角色/世界书生命周期，执行 prompt 组装与模型生成。
    context / tools / effects 是实际领域实现，不经本类转发。
    """

    def __init__(self, character, lore_engine, active_character_id: str = ""):
        self.character = character
        self.lore_engine = lore_engine
        # Track id of currently loaded character for hot-swap detection
        self._active_character_id: str = active_character_id
        # Author's Note 动态追加内容（consistency_check 结果），用完即清
        self.author_note_extra: str = ""
        self._last_channel: str | None = None
        from core.memory.reality_context import RealityContextReader
        from core.owner_chat.tool_loop import ToolTurnRunner
        from core.post_process.turn_effects import TurnEffects
        self.context = RealityContextReader(self)
        self.tools = ToolTurnRunner(self)
        self.effects = TurnEffects(self)

    def _refresh_character_if_needed(self) -> None:
        """Re-read active_prompt_assets.json; hot-swap self.character if active_character changed.

        Fail-loud contract (raises, does NOT silently continue):
        - File I/O error: raises RuntimeError.
        - active_character field missing or empty: raises ValueError + ERROR log.
        - active_character id unknown to registry: raises ValueError + ERROR log.
        pipeline.character is left unchanged in all error cases.
        """
        import json as _json
        from core.sandbox import get_paths as _get_paths

        # active_prompt_assets() raises RuntimeError if file is missing and config.default unset
        try:
            active_data = _json.loads(
                _get_paths().active_prompt_assets().read_text(encoding="utf-8")
            )
        except (OSError, IOError) as exc:
            raise RuntimeError(
                f"[pipeline] active_prompt_assets.json 读取失败: {exc}"
            ) from exc
        except _json.JSONDecodeError as exc:
            raise RuntimeError(
                f"[pipeline] active_prompt_assets.json JSON 解析失败: {exc}"
            ) from exc

        new_id = active_data.get("active_character", "")
        if not new_id:
            logger.error(
                "[pipeline] active_prompt_assets.json 中 active_character 字段缺失或为空，"
                f"当前角色: {self._active_character_id!r} — 本轮已中止"
            )
            raise ValueError(
                "[pipeline] active_prompt_assets.json missing or empty active_character"
            )

        if new_id == self._active_character_id:
            return  # no change, fast path

        from core import character_loader as _cl
        try:
            new_char = _cl.load(new_id)
        except (ValueError, FileNotFoundError) as exc:
            logger.error(
                f"[pipeline] active_character {new_id!r} 无法加载: {exc}  "
                f"— 保持原角色 {self._active_character_id!r}，本轮已中止"
            )
            raise ValueError(
                f"[pipeline] active_character {new_id!r} 无法加载"
            ) from exc

        self.character = new_char
        self._active_character_id = new_id
        self.author_note_extra = ""
        if self.lore_engine is not None:
            self.lore_engine.load(char_id=new_id)
            if new_char.world_book:
                self.lore_engine.load_entries(new_char.world_book)
        logger.info(f"[pipeline] character hot-swapped to {new_id!r} ({new_char.name})")

    def _current_reality_scope(self, user_id: str) -> MemoryScope:
        """Construct a reality-domain MemoryScope for the active character.

        Calls _refresh_character_if_needed() — raises ValueError/RuntimeError on
        invalid active character.  Callers must not call _refresh_character_if_needed()
        again separately.
        """
        self._refresh_character_if_needed()
        return MemoryScope.reality_scope(str(user_id), self._active_character_id)

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤 2：组装 prompt
    # ──────────────────────────────────────────────────────────────────────────

    def build_prompt(
        self,
        user_id: str,
        content: str,
        context: dict,
        tool_result: str | None = None,
        tags: set[str] | None = None,
        channel: str | None = None,
        char_id: "str | None" = None,
        consume_pending_perception: bool = True,
        tool_result_status: str | None = None,
        tool_result_generated_at: float | None = None,
        tool_call_required: bool = False,
        required_tool_names: list[str] | set[str] | tuple[str, ...] = (),
        hardware_jobs_text: str | None = None,
    ) -> tuple[list[dict], dict]:
        """
        调用 prompt_builder 组装完整消息列表。
        根据 chat.mode 在 system prompt 末尾追加风格提示。
        author_note_extra 用完后立即清空（只影响本轮）。

        char_id: 如果传入，使用该值作为 _char_id，跳过 _refresh_character_if_needed()。
          入口处已做一次性 scope freeze 时必须传入（N1）。
        """
        if char_id is not None:
            _char_id = char_id
        else:
            self._refresh_character_if_needed()
            _char_id = self._active_character_id
        from core import prompt_builder
        from core.config_loader import get_config
        from datetime import datetime
        _now = datetime.now()
        _current_time = (
            _now.strftime("%Y年%m月%d日 %H:%M 星期")
            + ["一", "二", "三", "四", "五", "六", "日"][_now.weekday()]
        )

        from core.tag_rules import get_tags
        _tags = tags if tags is not None else get_tags(content)

        _perception = ""
        _pending_paths: list[str] = []
        if consume_pending_perception:
            _pending, _pending_paths = _pending_perception.read_and_mark()
            if _pending:
                _perception = _pending.strip()

        # 跨通道接续感知
        if channel and self._last_channel and channel != self._last_channel:
            _switch_hint = "（你感觉她像是换了个地方继续跟你说话，但这还是同一段对话的延续。）"
            _perception = (_perception + "；" + _switch_hint) if _perception else _switch_hint
        if channel:
            self._last_channel = channel

        scoped_character = context.get("_scoped_character", self.character)
        scoped_author_note = self.author_note_extra if _char_id == self._active_character_id else ""
        _prompt_kwargs = dict(
            character=scoped_character,
            user_id=user_id,
            user_message=content,
            history=context["history"],
            relation=context["relation"],
            profile=context["profile"],
            group_context=context["group_context"],
            user_identity_text=context["user_identity_text"],
            user_facts_text=context.get("user_facts_text", ""),
            event_search_result=context["event_search_result"],
            lore_entries=context["lore_entries"],
            tool_result=tool_result,
            perception_block=_perception,
            author_note_extra=scoped_author_note,
            current_time=_current_time,
            reminders=context.get("reminders", []),
            diary_context=context.get("diary_context", ""),
            episodic_result=context.get("episodic_result", ""),
            episodic_fallback_result=context.get("episodic_fallback_result", ""),
            episodic_top_bucket=context.get("episodic_top_bucket", ""),
            relationship_span_text=context.get("relationship_span_text", ""),
            memory_dossier_context=context.get("memory_dossier_context", ""),
            mid_term_context=context.get("mid_term", ""),
            tags=_tags,
            dream_impression_text=context.get("dream_impression_text", ""),
            coplay_context_text=context.get("coplay_context_text", ""),
            coplay_residue_text=context.get("coplay_residue_text", ""),
            coplay_recall_text=context.get("coplay_recall_text", ""),
            char_id=_char_id,
            stage_presence=context.get("stage_presence", ""),
            stage_transcript=context.get("stage_transcript", ""),
            stage_transcript_private=context.get("stage_transcript_private", False),
            suppress_emotional_recall=context.get("suppress_emotional_recall", False),
            web_recall_result=context.get("web_recall_result", ""),
            web_recall_hits=context.get("web_recall_hits", []),
            action_trace_entries=context.get("action_trace_entries", []),
            tool_result_status=tool_result_status,
            tool_result_generated_at=tool_result_generated_at,
            tool_call_required=tool_call_required,
            required_tool_names=required_tool_names,
        )
        _hardware_jobs_for_prompt = context.get("hardware_jobs_text", "") if hardware_jobs_text is None else hardware_jobs_text
        from core.tool_result_pins import reset_projection
        reset_projection(user_id, _char_id)
        if (channel in {'qq', 'wechat', 'desktop', 'mobile'} and context.get('_continuity_private')
                and not context.get('stage_presence') and not context.get('stage_transcript')):
            from core.context_continuity import messages as continuity_messages
            _prompt_kwargs['continuity_messages'] = continuity_messages(user_id, _char_id)
        if _hardware_jobs_for_prompt:
            _prompt_kwargs["hardware_jobs_text"] = _hardware_jobs_for_prompt
        try:
            from core.character_self import load_agent_md_snapshot
            _prompt_kwargs["self_agent_md_snapshot"] = load_agent_md_snapshot(user_id, _char_id)
        except Exception:
            _prompt_kwargs["self_agent_md_snapshot"] = None
        messages, debug_info = prompt_builder.build(**_prompt_kwargs)
        if _char_id == self._active_character_id:
            self.author_note_extra = ""
        debug_info["pending_paths"] = _pending_paths

        try:
            from core.observe.prompt_capture import capture as _capture_prompt
            _capture_prompt(user_id, messages, debug_info)
        except Exception:
            pass

        try:
            from core.state_composer import record_shadow_packet as _record_state_packet
            _record_state_packet(user_id, _char_id, context, messages, debug_info, query=content)
        except Exception:
            pass

        return messages, debug_info

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤 3：调用 LLM（含重试）
    # ──────────────────────────────────────────────────────────────────────────

    async def run_llm(
        self, messages: list[dict], *, char_id: str | None = None, is_proactive: bool = False,
        require_complete: bool = False,
    ) -> str:
        """调用 LLM 生成回复，失败自动重试。

        char_id: 显式指定"替谁说话"时传（Brief 30，如 Stage 群聊非活跃角色）；
          None（默认）按活跃角色解析，与现状一致。
        is_proactive: 本次是否 scheduler 主动消息（Brief 32 · thinking.apply_to_proactive 用）。
        """
        from core import llm_client
        from core.error_handler import with_retry

        async def _call():
            return await llm_client.chat(messages, char_id=char_id, is_proactive=is_proactive)

        call = _call if require_complete else with_retry(module_name="pipeline.llm_call")(_call)
        reply = await call()
        from core.context_continuity import acknowledge
        acknowledge(messages)
        reply = await self._anti_collapse_prefix_retry(
            messages, reply, char_id=char_id, is_proactive=is_proactive,
        )
        from core.control_markers import strip_control_markers
        return await self._guard_unverified_claim(
            messages, strip_control_markers(reply),
            char_id=char_id, is_proactive=is_proactive,
        )

    async def _guard_unverified_claim(
        self,
        messages: list[dict],
        reply: str,
        *,
        successful_tool_call: bool | None = None,
        char_id: str | None = None,
        is_proactive: bool = False,
    ) -> str:
        """未经证实的完成声明：不改写文本，同轮 nudge 让模型用角色口吻重写 1 次。

        重写后仍命中则原样放行；每次触发写 runtime signal trace。fail-open。
        """
        try:
            from core.tool_grounding import (
                build_claim_nudge, check_unverified_claim, required_from_messages,
            )
            hit = check_unverified_claim(reply, messages, successful_tool_call=successful_tool_call)
            if not hit:
                return reply
            from core.runtime_signal_observability import record as _record_claim
            ctx = {"snippet": hit["snippet"], "basis": hit["basis"]}
            from core.character_name_provider import get_char_name
            grounding = required_from_messages(messages) or {}
            retry_messages = list(messages) + [
                {"role": "assistant", "content": reply},
                {"role": "system", "content": build_claim_nudge(
                    get_char_name(), list(grounding.get("tool_names") or []),
                )},
            ]
            from core import llm_client
            from core.control_markers import strip_control_markers
            retry_reply = strip_control_markers(await llm_client.chat(
                retry_messages, char_id=char_id, is_proactive=is_proactive,
            ))
            if retry_reply and not check_unverified_claim(
                retry_reply, messages, successful_tool_call=successful_tool_call,
            ):
                _record_claim(category="tool_grounding", code="unverified_claim", status="attention",
                              context={**ctx, "action": "rewritten"})
                return retry_reply
            _record_claim(category="tool_grounding", code="unverified_claim", status="attention",
                          context={**ctx, "action": "passed_through_after_retry"})
            return retry_reply or reply
        except Exception as e:
            from core.error_handler import log_error
            log_error("pipeline._guard_unverified_claim", e)
            return reply

    @staticmethod
    def _homogeneity_hist_for_check(messages: list[dict]) -> list[dict]:
        """从层9历史条目里复原 detect_reply_homogeneity_prefix() 要用的 assistant 历史。

        build() 侧可能已做过去同质投影（剥掉了重复前缀），命中的用 _raw_content 复原
        原文，才能拿到与 build() 内部同一份检测结果 P。供输出端重试与流式软降级共用。
        """
        return [
            {"role": "assistant", "content": m.get("_raw_content", m.get("content", ""))}
            for m in messages
            if m.get("_layer") == "9_history" and m.get("role") == "assistant"
        ]

    async def _anti_collapse_prefix_retry(
        self,
        messages: list[dict],
        reply: str,
        *,
        char_id: str | None = None,
        is_proactive: bool = False,
    ) -> str:
        """
        问题7 (c) 输出端校验重试（硬止血）：
        软提示（层9历史投影去同质 + 层11 S2 提示）已经是"劝"，这里是"拦"——
        如果输出仍然复现了检测到的重复句首 P，追加一条强 system 指令重试 1 次；
        重试仍命中且 P 是填充词（嗯/啊/呃/哦/唔/哈等）→ 剥掉开头 P 后接受，
        非填充词前缀不做硬剥离，只接受重试结果。fail-open：任何异常都返回原始 reply。
        """
        try:
            from core.control_markers import strip_control_markers
            from core.config_loader import get_config
            if not get_config().get("anti_collapse", {}).get("prefix_retry", True):
                return strip_control_markers(reply)

            from core.memory.short_term import (
                detect_reply_homogeneity_prefix,
                is_filler_prefix,
            )
            hist_for_check = self._homogeneity_hist_for_check(messages)
            prefix = detect_reply_homogeneity_prefix(hist_for_check)
            if not prefix or not reply.strip().startswith(prefix):
                return strip_control_markers(reply)

            logger.info("[anti_collapse] prefix retry")
            from core import llm_client
            retry_messages = messages + [{
                "role": "system",
                "content": f"你上一条回复又以「{prefix}」开头了，这次绝对不能再用这个开头，换一种方式开口。",
            }]
            retry_reply = await llm_client.chat(
                retry_messages, char_id=char_id, is_proactive=is_proactive,
            )
            if retry_reply.strip().startswith(prefix) and is_filler_prefix(prefix):
                stripped = retry_reply.strip()[len(prefix):].lstrip()
                return strip_control_markers(stripped or retry_reply)
            return strip_control_markers(retry_reply)
        except Exception as e:
            from core.error_handler import log_error
            log_error("pipeline._anti_collapse_prefix_retry", e)
            from core.control_markers import strip_control_markers
            return strip_control_markers(reply)

    async def _guard_tool_meta_leak(
        self,
        messages: list[dict],
        reply: str,
        *,
        char_id: str | None = None,
        is_proactive: bool = False,
    ) -> str:
        """
        工单 C1：发送前过滤闸门——模型把它在 system 消息里看到的工具目录/发现层
        描述当成了要讲给用户听的说明文（结论 3：不是系统误发工具结果，是模型
        复述元数据）。命中后静默重试一次，显式要求不得提及工具内部结构；仍命中
        则走兜底话术，不把泄漏文本发给用户。重试失败时已确认泄漏的文本也不发送。
        """
        try:
            from core.tool_grounding import detect_tool_meta_leak
            if not reply or not detect_tool_meta_leak(reply):
                return reply
            from core.runtime_signal_observability import record as _record_discovery
            _record_discovery(category="tool_loop_discovery", code="tool_meta_leak", status="attention")
            logger.warning("[pipeline._guard_tool_meta_leak] tool meta leak detected, retrying once")
            from core import llm_client
            retry_messages = messages + [{
                "role": "system",
                "content": (
                    "你刚才的回复提到了工具列表、工具定义或"
                    "「只发现工具，不执行任何业务操作」这类内部协议说明。"
                    "这些是系统内部的工具元数据，绝对不能出现在给用户看的回复里。"
                    "重新回复一次，正常对话，不要提及任何工具名称、工具分类或工具调用机制。"
                ),
            }]
            retry_reply = await llm_client.chat(
                retry_messages, char_id=char_id, is_proactive=is_proactive,
            )
            if detect_tool_meta_leak(retry_reply):
                _record_discovery(category="tool_loop_discovery", code="tool_meta_leak_retry_failed", status="attention")
                logger.warning("[pipeline._guard_tool_meta_leak] retry still leaked, falling back")
                return "抱歉，刚才那句话说得不太对，当作没说——有什么想聊的吗？"
            return retry_reply
        except Exception as e:
            from core.error_handler import log_error
            log_error("pipeline._guard_tool_meta_leak", e)
            from core.tool_grounding import detect_tool_meta_leak
            return "刚才没有完成查询，请再试一次。" if detect_tool_meta_leak(reply) else reply

    def _check_stream_collapse(
        self, messages: list[dict], reply: str, *, char_id: str | None, user_id: str,
    ) -> None:
        """
        ACT-2 · 方案B（流式路径反坍缩软降级）：
        流式 token 已对用户可见，不能像非流式那样丢弃重试——命中与 S2 同源的
        detect_reply_homogeneity_prefix() 检测时，本轮原样放行，只打观测日志 +
        写入下一轮可读的一次性信号（core.memory.short_term.note_stream_collapse_signal），
        由下一轮 build_prompt 注入反坍缩提示层后消费清除。fail-open：任何异常都不影响本轮输出。
        """
        if not user_id:
            return
        try:
            from core.memory.short_term import detect_reply_homogeneity_prefix
            prefix = detect_reply_homogeneity_prefix(self._homogeneity_hist_for_check(messages))
            if not prefix or not reply.strip().startswith(prefix):
                return

            _char_id = char_id or getattr(self, "_active_character_id", DEFAULT_CHAR_ID)
            logger.info(
                "[anti_collapse] stream_soft_degrade uid=%s char_id=%s rule=homogeneity_prefix prefix=%r",
                user_id, _char_id, prefix,
            )
            from core.memory.short_term import note_stream_collapse_signal
            note_stream_collapse_signal(user_id, prefix, char_id=_char_id)
        except Exception as e:
            from core.error_handler import log_error
            log_error("pipeline._check_stream_collapse", e)

    async def run_llm_stream(
        self,
        messages: list[dict],
        *,
        char_id: str | None = None,
        is_proactive: bool = False,
        user_id: str = "",
        require_complete: bool = False,
    ):
        """流式生成，逐 token yield。失败（含零产出）时降级为非流式整段 yield 一次。

        降级语义：
        - 0 token + 异常 → 非流式 run_llm 兜底（完整输出）。
        - 部分 token + 异常 → 中止，不追加非流式文本（避免重复拼接）。

        char_id: 显式指定"替谁说话"时传（Brief 30）；None（默认）按活跃角色解析。
        is_proactive: 本次是否 scheduler 主动消息（Brief 32）。
        user_id: 反坍缩软降级信号（ACT-2）归属的用户；不传则跳过检测（无法定位下一轮读取位置）。
        """
        from core import llm_client
        from core.control_markers import ControlMarkerStreamFilter
        got_any = False
        pieces: list[str] = []
        marker_filter = ControlMarkerStreamFilter()
        try:
            stream_options = {"require_complete": True} if require_complete else {}
            async for piece in llm_client.chat_stream(
                messages, char_id=char_id, is_proactive=is_proactive, **stream_options,
            ):
                got_any = True
                visible = marker_filter.feed(piece)
                if visible:
                    pieces.append(visible)
                    yield visible
            tail = marker_filter.finish()
            if tail:
                pieces.append(tail)
                yield tail
            # 流正常结束
            if got_any:
                from core.context_continuity import acknowledge
                acknowledge(messages)
                self._check_stream_collapse(messages, "".join(pieces), char_id=char_id, user_id=user_id)
                return
            # 0 token（模型返回空流） → 降级
        except Exception as e:
            from core.error_handler import log_error
            log_error("pipeline.run_llm_stream", e)
            if got_any and require_complete:
                raise
            if got_any:
                # 已推出部分 token，中止而非追加以免重复。
                # 未闭合的协议标记前缀留在 filter 里，不 flush，避免闪现。
                return
        options = {"require_complete": True} if require_complete else {}
        full = await self.run_llm(messages, char_id=char_id, is_proactive=is_proactive, **options)
        if full:
            yield full
