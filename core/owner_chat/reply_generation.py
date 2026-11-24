"""Model reply generation and output governance; no character/runtime ownership."""
import logging

logger = logging.getLogger("core.pipeline")


class ReplyGeneration:
    def __init__(self, *, resolve_active_character_id):
        if not callable(resolve_active_character_id):
            raise TypeError("ReplyGeneration requires resolve_active_character_id capability")
        self._resolve_active_character_id = resolve_active_character_id

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

            logger.info(
                "[anti_collapse] stream_soft_degrade uid=%s char_id=%s rule=homogeneity_prefix prefix=%r",
                user_id, char_id, prefix,
            )
            from core.memory.short_term import note_stream_collapse_signal
            note_stream_collapse_signal(user_id, prefix, char_id=char_id)
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

        char_id: 显式指定"替谁说话"时传（Brief 30）；None 且有 user_id 时，
          请求前通过注入能力冻结已加载角色；无 user_id 时维持模型侧解析。
        is_proactive: 本次是否 scheduler 主动消息（Brief 32）。
        user_id: 反坍缩软降级信号（ACT-2）归属的用户；不传则跳过检测（无法定位下一轮读取位置）。
        """
        from core import llm_client
        from core.control_markers import ControlMarkerStreamFilter
        if char_id is None and user_id:
            char_id = self._resolve_active_character_id()
            if not isinstance(char_id, str) or not char_id.strip():
                raise ValueError("stream collapse signal requires a loaded character ID")
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
