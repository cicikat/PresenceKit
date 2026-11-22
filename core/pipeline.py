"""
消息处理流水线
把 handle_message 的核心步骤封装成独立方法，main.py 只保留骨架调用。

Pipeline 实例持有角色卡和世界书引擎的引用，在 main.py 中初始化后全程复用。
"""

import asyncio
import logging
from core.tool_activity import display_chain
from core.llm_turn_route import scoped_tool_turn

from core.llm_output_validator import record_failure, reset
from core.memory import pending_perception as _pending_perception
from core.memory.scope import MemoryScope
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

_TOOL_EPHEMERAL_WAITING_AFTER_S = 3.0

# Phase A v8: avatar directive throttle — same emotion within cooldown window is skipped
_AVATAR_DIRECTIVE_LAST: dict[str, tuple[str, float]] = {}  # char_id → (emotion, sent_at)
_AVATAR_DIRECTIVE_COOLDOWN_SEC = 5.0




def _detect_dream_echo(user_id: str, char_id: str, content: str, reply: str) -> bool:
    """Read-only check：本轮是否携带梦境印象回流（Brief 79）。
    只读本地印象文件 + tag 匹配，毫秒级、无 LLM，不消费 forced_impression_rounds_left
    ——计数器消费仍然只在 post_process_slow 的 consume_forced_impression_round 里发生一次，
    这里可以安全地在 post_process_critical（capture_turn 之前）提前调用一次判定来源标记。
    fail-open：读取异常按无回流处理。
    """
    try:
        from core.dream.dream_state import read_state as _read_dream_state
        from core.dream.impression_loader import load_impression_text as _load_imp_for_echo
        from core.config_loader import get_config as _get_config_for_echo
        from core.tag_rules import get_tags as _get_tags

        _echo_state = _read_dream_state(user_id)
        try:
            _echo_forced_left = max(0, int(_echo_state.get("forced_impression_rounds_left", 0)))
        except (TypeError, ValueError):
            _echo_forced_left = 0
        _echo_dream_cfg = _get_config_for_echo().get("dream") or {}
        _echo_imp_cfg = _echo_dream_cfg.get("impression") or {}
        return bool(_load_imp_for_echo(
            user_id,
            char_id=char_id,
            forced_rounds_left=_echo_forced_left,
            latest_dream_id=str(_echo_state.get("last_dream_id") or ""),
            user_text=f"{content}\n{reply}",
            tags=set(_get_tags(content)),
            recall_enabled=bool(_echo_imp_cfg.get("recall_enabled", True)),
        ))
    except Exception as _echo_error:
        logger.warning(
            "[pipeline._detect_dream_echo] dream impression echo check failed uid=%s: %s",
            user_id, _echo_error,
        )
        return False


async def _maybe_push_avatar_directive(mood_state: dict, char_id: str) -> None:
    """Phase A v8: push avatar_directive to desktop client when mood changes."""
    import time as _t
    try:
        from core.tools.devices_definitions import _push_desktop_action
        from core.pipeline_registry import get as _get_pipeline
        active = _get_pipeline()
        if active is not None and getattr(active, "_active_character_id", "") != char_id:
            return
        emotion = mood_state.get("current", "")
        if not emotion or not mood_state.get("has_signal"):
            return
        intensity = float(mood_state.get("intensity", 0.0))
        now = _t.time()
        last_emotion, last_sent = _AVATAR_DIRECTIVE_LAST.get(char_id, (None, 0.0))
        if last_emotion == emotion and (now - last_sent) < _AVATAR_DIRECTIVE_COOLDOWN_SEC:
            return
        _AVATAR_DIRECTIVE_LAST[char_id] = (emotion, now)
        await _push_desktop_action({
            "type": "avatar_directive",
            "expression": emotion,
            "intensity": round(min(1.0, max(0.0, intensity)), 3),
            "ttl_ms": 6000,
            "char_id": char_id,
            "version": mood_state.get("version", 0),
            "source_turn_id": mood_state.get("source_turn_id", ""),
            "source_at": mood_state.get("source_at", 0.0),
            "expires_at": min(float(mood_state.get("expires_at") or 0), now + 6),
        })
    except Exception as e:
        logger.debug("[pipeline] avatar_directive push skipped: %s", e)


def _validate_episode(data: dict) -> bool:
    for key in ("raw_facts", "topic_keywords", "emotion_peak", "strength"):
        if key not in data:
            return False
    if not isinstance(data["raw_facts"], list) or len(data["raw_facts"]) == 0:
        return False
    if not isinstance(data["topic_keywords"], list) or len(data["topic_keywords"]) == 0:
        return False
    if data["emotion_peak"] not in {"neutral", "happy", "sad", "gentle", "surprised", "angry", "thinking", "sleepy"}:
        return False
    try:
        s = float(data["strength"])
        if not (0.0 <= s <= 1.0):
            return False
    except (TypeError, ValueError):
        return False
    return True


class Pipeline:
    """
    消息处理流水线，四个核心步骤：

    1. fetch_context  — 并发拉取记忆数据 + 世界书匹配
    2. build_prompt   — 组装完整 prompt 消息列表
    3. run_llm        — 调用 LLM 生成回复（含重试）
    4. post_process   — 写记忆、更新画像、触发角色认知更新
    """

    def __init__(self, character, lore_engine, active_character_id: str = ""):
        self.character = character
        self.lore_engine = lore_engine
        # Track id of currently loaded character for hot-swap detection
        self._active_character_id: str = active_character_id
        # Author's Note 动态追加内容（consistency_check 结果），用完即清
        self.author_note_extra: str = ""
        self._last_channel: str | None = None

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
    # 步骤 1：并发拉取记忆数据 + 世界书匹配
    # ──────────────────────────────────────────────────────────────────────────

    async def fetch_context(
        self,
        user_id: str,
        content: str,
        group_id: str | None = None,
        frozen_scope: "MemoryScope | None" = None,
        recall_policy: str = "seed",
        query_free_fallback: bool = False,
    ) -> dict:
        """
        并发拉取所有记忆数据并进行世界书关键词匹配。

        返回 context 字典，供 build_prompt 使用：
        {
            "history":            list[dict],  # 短期对话历史
            "profile":            dict,        # 用户画像（scoped by char_id）
            "relation":           dict,        # 用户关系配置
            "group_context":      str,         # 群消息流（私聊为 ""）
            "user_identity_text": str,         # 用户稳定行为模式描述（scoped by char_id）
            "user_facts_text":    str,         # 当前角色的用户信息
            "event_search_result": str,        # 事件日志语义搜索结果
            "lore_entries":       list[str],   # 命中的世界书条目
        }

        frozen_scope: 如果传入，直接使用该 scope，跳过 _current_reality_scope()。
          入口处已做一次性 scope freeze 时必须传入，保证全轮角色一致性（N1）。

        recall_policy（CC 任务 19 · C，召回锚点治理）：
          - "seed"（默认，兼容现状）：episodic/event_search/web_recall 正常检索，锚点为
            content（调用方传入的 search_query 或 prompt 全文）。
          - "anchored"：语义同 "seed"（检索层不跳过），仅表示调用方已用触发器自带的具体
            锚点（话题 key、被选中记忆原文）作为 content，而非宽泛种子词或 prompt 全文。
          - "none"：跳过 episodic/event_search/web_recall 三个检索层，只保留
            identity/mood/short_term/花园等状态层。主动触发的"由头"已经在种子 prompt 里
            写死（天气、想她了、看到她久坐），角色不需要再被 "今天" 这类宽泛词捞出的旧
            情景记忆带偏（RC6：乱召回导致"胡乱召回然后说一大堆废话"）。

        query_free_fallback（工单 S1）：是否允许 retrieve_fallback()（不看 query 的
        long/repair 高强度兜底回忆）。默认 False（普通聊天不跑）；仅调度器主动开口传 True。
        """
        # Guard: if frozen_scope provided, use it directly (turn-level scope freeze);
        # otherwise validate active_character and construct scope.
        if frozen_scope is not None:
            scope = frozen_scope
        else:
            scope = self._current_reality_scope(user_id)
        from core.memory.reality_context import fetch_reality_context

        return await fetch_reality_context(
            scope=scope,
            content=content,
            group_id=group_id,
            recall_policy=recall_policy,
            query_free_fallback=query_free_fallback,
            character=self.character,
            lore_engine=self.lore_engine,
            active_character_id=self._active_character_id,
        )

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

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤3B：多步工具执行器（Brief 28 · Path C，function_calling 模型专用）
    # ──────────────────────────────────────────────────────────────────────────

    @display_chain
    @scoped_tool_turn
    async def run_agentic_loop(
        self,
        messages: list[dict],
        *,
        uid: str,
        char_id: str,
        session_state,
        is_group: bool = False,
        stream: bool = False,
        is_proactive: bool = False,
        exclude_tools: set[str] | None = None,
        tool_event_observer=None,
        tool_call_required: bool = False,
        required_tool_names: list[str] | set[str] | tuple[str, ...] = (),
        allowed_tool_categories: frozenset[str] | None = None,
        allowed_tool_names: frozenset[str] | None = None,
        media_refs: list[dict] | None = None,
    ):
        """Compatibility entry for the scoped and displayed tool turn."""
        from core.owner_chat.tool_loop import run_tool_turn

        return await run_tool_turn(
            messages,
            run_llm=self.run_llm,
            run_llm_stream=self.run_llm_stream,
            guard_tool_meta_leak=self._guard_tool_meta_leak,
            guard_unverified_claim=self._guard_unverified_claim,
            anti_collapse_prefix_retry=self._anti_collapse_prefix_retry,
            waiting_after_s=_TOOL_EPHEMERAL_WAITING_AFTER_S,
            uid=uid,
            char_id=char_id,
            session_state=session_state,
            is_group=is_group,
            stream=stream,
            is_proactive=is_proactive,
            exclude_tools=exclude_tools,
            tool_event_observer=tool_event_observer,
            tool_call_required=tool_call_required,
            required_tool_names=required_tool_names,
            allowed_tool_categories=allowed_tool_categories,
            allowed_tool_names=allowed_tool_names,
            media_refs=media_refs,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # 步骤 4：异步后处理
    # ──────────────────────────────────────────────────────────────────────────

    async def post_process_critical(
        self,
        user_id: str,
        content: str,
        reply: str,
        target_id: str = "",
        is_group: bool = False,
        pending_paths: list[str] | None = None,
        trigger_name: str = "",
        envelope=None,
        audit_extras: dict | None = None,
        frozen_scope: "MemoryScope | None" = None,
        web_echo: bool = False,
        coplay_echo: bool = False,
        provenance_source: str = "",
        event_channel: str = "",
        event_source: str = "",
        visible_reply: str | None = None,
        raw_user_text: str | None = None,
        media_refs: list[dict] | None = None,
        event_context=None,
    ) -> dict:
        """
        Brief 37：send 前必须走完的关键路径——只做毫秒级本地落盘（capture_turn），
        不 await 任何 LLM/网络往返。expression / appraisal / avatar / profile /
        slow_queue 全部移到 post_process_slow，由调用方在 send 完成后异步调度
        （见 core/turn_sink.py record_assistant_turn）。

        event_log 的 emotion 保留 "neutral" 标注；慢段明确表达用于原有整合策略，
        内在事件评价只进入独立状态投影，不回填此标注，也不作为事实记忆证据。

        返回值原样传给 post_process_slow(critical_result=...)。

        frozen_scope: 如果传入，直接使用该 scope，跳过 _current_reality_scope()（N1）。

        web_echo / coplay_echo（Brief 79）：本轮是否携带 web 搜索召回 / coplay 演出内容，
        由调用方（turn_sink.record_assistant_turn）透传。连同本地重新判定的梦境印象回流
        一起换算成 event_log 的 source 标记，堵住 event_log_salvage 抢救链绕过固化隔离的
        通路。dream_echo 在这里独立重新判定（只读，不消费 forced_impression_rounds_left），
        与 post_process_slow 里那份用于 mid_term 跳过判断的 dream_echo 各自独立求值，
        两处判定逻辑一致（同一个 _detect_dream_echo），只是求值时机不同。
        """
        from core.write_envelope import WriteEnvelope
        if envelope is None:
            envelope = WriteEnvelope()

        # Guard: if frozen_scope provided, use it directly (turn-level scope freeze);
        # otherwise validate active_character and construct scope.
        if frozen_scope is not None:
            scope = frozen_scope
        else:
            scope = self._current_reality_scope(user_id)
        char_id = scope.character_id
        assert char_id is not None
        scope_payload = scope.to_payload()

        from core.memory import locks as _locks
        from core.error_handler import log_error
        from core.post_process import slow_queue

        _should_update_profile = False
        from core.tool_result_pins import projected as pin_projected
        _pin_echo = pin_projected(user_id,char_id)
        _profile_recent: list = []
        import time as _time
        _critical_started_at = _time.monotonic()
        _turn_id = (event_context.turn_id if event_context is not None and event_context.turn_id
                    else f"{user_id}_{int(_time.time() * 1000)}")
        _turn_context = event_context.with_turn(_turn_id) if event_context is not None else None
        _critical_written = False

        async with _locks.uid_lock(user_id):
            # ── 检查用户画像更新条件（在写入前，读取当前历史长度 +2 估算）────
            try:
                from core.memory import short_term as _st
                from core.config_loader import get_config
                cfg = get_config()
                every_n = cfg.get("memory", {}).get("summary_every_n_rounds", 20)
                _hist_len_after = len(_st.load(user_id, char_id=char_id)) + 2
                if _hist_len_after > 0 and _hist_len_after % every_n == 0:
                    _should_update_profile = True
                    _profile_recent = _st.load(user_id, char_id=char_id)[-(every_n * 2):]
            except Exception as e:
                log_error("post_process.check_conditions", e)

            # ── Brief 88：现实轮 gap 快照（读 short_term，早于 capture_turn 追加本轮）──
            # 供 post_process_slow 的 user_hidden_state SEEK_COMPANIONSHIP(a) 判定：
            # 本轮追加历史前，距上一条 owner 消息的秒数。读取失败 → None（判定端
            # 按"未知"处理，不触发）。
            _prior_gap_seconds: float | None = None
            try:
                from core.memory import short_term as _st_gap
                from core.presence import get_gap_from_history as _get_gap_from_history
                _prior_gap_seconds = _get_gap_from_history(_st_gap.load(user_id, char_id=char_id))
            except Exception:
                _prior_gap_seconds = None

            # ── capture_turn：写 short_term + event_log（含 turn_id 血缘）───
            try:
                from core.memory.fixation_pipeline import capture_turn as _capture_turn
                _dream_echo_for_source = _detect_dream_echo(user_id, char_id, content, reply)
                _source = 'tool_pin' if _pin_echo else provenance_source or (
                    "dream_echo" if _dream_echo_for_source
                    else "web" if web_echo
                    else "coplay" if coplay_echo
                    else ""
                )
                _capture_turn_kwargs = {
                    "turn_id": _turn_id,
                    "trigger_name": trigger_name,
                    "envelope": envelope,
                    "char_id": char_id,
                    "audit_extras": audit_extras,
                    "source": _source,
                    "event_channel": event_channel,
                    "event_source": event_source,
                    "visible_reply": visible_reply,
                    "raw_user_text": raw_user_text,
                    "media_refs": media_refs,
                }
                # Legacy callers have no ingress context. Do not add an
                # optional keyword argument when there is no context to carry.
                if _turn_context is not None:
                    _capture_turn_kwargs["event_context"] = _turn_context
                    _capture_turn_kwargs["observer_started_at"] = _critical_started_at
                _turn_id = _capture_turn(
                    user_id,
                    content,
                    reply,
                    "neutral",
                    **_capture_turn_kwargs,
                )
                _critical_written = True
                try:
                    from core.event_context_observer import record as _record_event_context
                    _record_event_context(
                        stage="critical", disposition="written", context=_turn_context,
                        orphan=_turn_context is None, started_at=_critical_started_at,
                    )
                except Exception:
                    pass
                logger.debug(f"[pipeline.post_process_critical] capture_turn: {_turn_id}")
                # 语义索引：event_log 条目异步写入向量库（fail-open）
                if envelope.can_write_memory:
                    try:
                        import time as _time
                        from core.memory import vector_store as _vs
                        _el_text = f"{content}\n{reply}".strip()
                        asyncio.create_task(
                            _vs.upsert(user_id, char_id, "event_log", _turn_id, _time.time(), _el_text)
                        )
                    except Exception as _vs_e:
                        logger.debug("[pipeline.post_process_critical] vector_store upsert schedule error: %s", _vs_e)
            except Exception as e:
                log_error("post_process.capture_turn", e)
                try:
                    from core.event_context_observer import record as _record_event_context
                    _record_event_context(
                        stage="critical", disposition="failed", context=_turn_context,
                        error_code="identity_mismatch" if isinstance(e, ValueError) else "capture_failed",
                        orphan=_turn_context is None,
                        scope_match=bool(_turn_context is None or _turn_context.scope == scope),
                        started_at=_critical_started_at,
                    )
                except Exception:
                    pass
                if envelope.can_write_memory:
                    slow_queue.enqueue("capture_turn_retry", {
                        "turn_id": _turn_id,
                        "uid": user_id,
                        "user_content": content,
                        "reply": reply,
                        "emotion": "neutral",
                        "trigger_name": trigger_name,
                        "char_id": char_id,
                        "scope": scope_payload,
                        "event_context": _turn_context.to_payload() if _turn_context is not None else None,
                        "audit_extras": audit_extras,
                        "source": _source,
                        "event_channel": event_channel,
                        "event_source": event_source,
                        "media_refs": media_refs,
                        "visible_reply": visible_reply,
                        "raw_user_text": raw_user_text,
                    })

        if pending_paths:
            _pending_perception.confirm_delivered(pending_paths)

        if _critical_written and envelope.can_write_memory and not trigger_name and not is_group:
            try:
                from core.tool_result_pins import consume
                consume(user_id,char_id,_turn_id)
            except Exception:
                logger.warning('[tool_result_pins] round commit unavailable')

        if _critical_written and envelope.can_write_memory and not trigger_name and not is_group and not web_echo and not coplay_echo and not _dream_echo_for_source and not _pin_echo:
            if not (audit_extras or {}).get("asr_low_confidence"):
                try:
                    from core.food_memory import enqueue as enqueue_food
                    enqueue_food(user_id, char_id, _turn_id, raw_user_text if raw_user_text is not None else content)
                except Exception as exc:
                    logger.warning("[food_memory] enqueue failed: %s", type(exc).__name__)

        return {
            "turn_id": _turn_id,
            "critical_written": _critical_written,
            "emotion": "neutral",
            "char_id": char_id,
            "scope_payload": scope_payload,
            "should_update_profile": _should_update_profile and not _pin_echo,
            "tool_pin_echo": _pin_echo,
            "profile_recent": _profile_recent,
            "prior_gap_seconds": _prior_gap_seconds,
            "expression_signal": (audit_extras or {}).get("expression_signal", __import__("core.expression_signal", fromlist=["explicit_expression"]).explicit_expression(reply)),
            "source_at": event_context.occurred_at if event_context is not None else _time.time(),
            "appraisal_input": raw_user_text if raw_user_text is not None else content,
            "source_refs": [str(ref["source_ref"]) for ref in (media_refs or [])
                            if isinstance(ref, dict) and ref.get("kind") == "activity_record" and ref.get("source_ref")],
        }

    async def post_process_slow(
        self,
        user_id: str,
        content: str,
        reply: str,
        critical_result: dict,
        target_id: str = "",
        is_group: bool = False,
        trigger_name: str = "",
        envelope=None,
        audit_extras: dict | None = None,
        web_echo: bool = False,
        coplay_echo: bool = False,
        loop_executed: bool = False,
        provenance_source: str = "",
    ) -> dict:
        """
        Brief 37：send 之后异步执行的慢段——明确表达 → mood_state → avatar/
        heart → profile → slow_queue 入队 → TTS/表情包 → 意图解析。调用方（
        turn_sink.record_assistant_turn）应在 channel fanout 完成后用
        asyncio.create_task() 调度本方法，不得 await（否则又把 send 堵回去）。

        critical_result: post_process_critical() 的返回值，携带 turn_id /
        char_id / scope_payload / should_update_profile / profile_recent。
        """
        from core.write_envelope import WriteEnvelope
        if envelope is None:
            envelope = WriteEnvelope()

        from core.memory import locks as _locks
        from core import llm_client
        from core.error_handler import log_error
        from core.post_process import slow_queue

        char_id = critical_result["char_id"]
        scope_payload = critical_result["scope_payload"]
        _turn_id = critical_result["turn_id"]
        _should_update_profile = critical_result["should_update_profile"]
        _profile_recent = critical_result["profile_recent"]
        web_echo = web_echo or bool(critical_result.get('tool_pin_echo'))

        if envelope.can_write_memory and not trigger_name and not is_group:
            slow_queue.enqueue("food_memory_update", {"uid": user_id, "char_id": char_id, "scope": scope_payload})

        if not trigger_name and envelope.can_write_memory:
            from core.memory.short_term import schedule_long_user_summaries
            schedule_long_user_summaries(user_id, char_id=char_id)

        if not trigger_name and not is_group:
            from core.document_reading import schedule_summaries
            schedule_summaries(user_id, char_id)

        _emotion = str(critical_result.get("expression_signal") or "")

        # ── Brief 88：user_hidden_state 现实侧信号映射（对话侧，emotion 已可用）──
        # 判定全用现成数据（tags / emotion / 现实轮 gap 快照 / 常量词表），零 LLM；
        # fail-open，任何异常不影响主链。trigger 轮零参与（函数内部判断）。
        from core.tag_rules import get_tags as _get_tags
        _reality_tags = set(_get_tags(content))
        try:
            from core.memory.user_hidden_state_reality_signals import process_reality_turn
            if not provenance_source.startswith("activity:"):
                process_reality_turn(
                    uid=user_id,
                    content=content,
                    tags=_reality_tags,
                    assistant_emotion="",
                    trigger_name=trigger_name,
                    envelope=envelope,
                    prior_gap_seconds=critical_result.get("prior_gap_seconds"),
                    char_id=char_id,
                    turn_id=str(_turn_id or ""),
                )
        except Exception as _hs_err:
            logger.warning(f"[pipeline.post_process_slow] hidden_state reality signal 映射失败: {_hs_err}")

        async with _locks.uid_lock(user_id):
            # Outward expression commits immediately, before semantic appraisal.
            _mood_state_after: dict | None = None
            if envelope.can_affect_mood:
                async with _locks.global_lock("mood_state"):
                    try:
                        from core.memory.mood_state import update as _update_mood
                        _mood_state_after = _update_mood(_emotion, source="explicit", char_id=char_id,
                            source_turn_id=str(_turn_id), source_at=critical_result.get("source_at"))
                    except Exception as e:
                        log_error("post_process.mood_state", e)

            # Phase A v8: push avatar directive (fail-open, outside locks)
            if (_mood_state_after is not None and _mood_state_after.get("has_signal")
                    and _mood_state_after.get("source_turn_id") == str(_turn_id)):
                asyncio.create_task(_maybe_push_avatar_directive(_mood_state_after, char_id))
            # 爱意探针 → 板子爱心（非阻塞、锁外、fail-open）；梦境/沙盒等场景不触发实体爱心。
            if envelope.can_affect_mood:
                from core.embodiment.heart import maybe_draw_heart as _maybe_heart
                asyncio.create_task(_maybe_heart(reply, char_id))

        # Real dialogue only. Trigger seeds, imports and isolated material are not
        # personal events. Network work is outside both UID and state locks.
        from core.write_envelope import SourceType
        if (envelope.can_affect_mood and not trigger_name and not is_group
                and envelope.source in (SourceType.USER_CHAT, SourceType.QQ, SourceType.WECHAT)
                and critical_result.get("critical_written")
                and not web_echo and not coplay_echo and not provenance_source
                and not _detect_dream_echo(user_id, char_id, content, reply)):
            try:
                from core import affective_appraisal
                from core.memory import mood_state as _mood
                _input = str(critical_result.get("appraisal_input") or "")
                _state = _mood.load(char_id=char_id)
                if (_mood.enabled() and _state.get("last_turn_id") == str(_turn_id)
                        and _state.get("last_appraisal_turn_id") != str(_turn_id)):
                    if affective_appraisal.source_available(user_id, char_id, str(_turn_id)):
                        _proposal, _status = await affective_appraisal.evaluate(_input, char_id=char_id)
                    else:
                        _proposal, _status = None, "source_unavailable"
                    if not affective_appraisal.source_available(user_id, char_id, str(_turn_id)):
                        _proposal, _status = None, "source_unavailable"
                    _mood.record_appraisal(_proposal, char_id=char_id, turn_id=str(_turn_id),
                                           occurred_at=critical_result["source_at"], evidence=_input, status=_status)
            except Exception as e:
                log_error("post_process.affective_appraisal", e)

        # ── 入慢队列 ───────────────────────────────────────────
        _mt_tags = list(_reality_tags)

        # Consolidation is silent exactly when this turn is a forced injection
        # or a topic-recall hit. Read before consuming so the third forced turn
        # still carries dream_echo=True.
        _dream_echo = _detect_dream_echo(user_id, char_id, content, reply)
        try:
            from core.dream.dream_state import consume_forced_impression_round
            consume_forced_impression_round(
                user_id, reality_owner_turn=not bool(trigger_name)
            )
        except Exception as _consume_error:
            logger.warning(
                "[pipeline.post_process_slow] forced impression counter failed uid=%s: %s",
                user_id, _consume_error,
            )

        # 若 emotion 显著，handler 内部会自动入队 reflect_to_episodic（eager）
        if envelope.can_write_memory:
            _mt_payload: dict = {
                "turn_id": _turn_id,
                "uid": user_id,
                "user_content": content,
                "reply": reply,
                "tags": _mt_tags,
                "emotion": _emotion,
                "char_id": char_id,
                "scope": scope_payload,
            }
            if trigger_name:
                _mt_payload["trigger_name"] = trigger_name
            if _dream_echo:
                _mt_payload["dream_echo"] = True
            if web_echo:
                _mt_payload["web_echo"] = True
            if coplay_echo:
                _mt_payload["coplay_echo"] = True
            if provenance_source:
                _mt_payload["source"] = provenance_source
            if critical_result.get("source_refs"):
                _mt_payload["source_refs"] = critical_result["source_refs"]
            if audit_extras and audit_extras.get("input_modality") == "voice":
                _mt_payload["input_modality"] = "voice"
                _mt_payload["asr_low_confidence"] = bool(audit_extras.get("asr_low_confidence"))
            slow_queue.enqueue("summarize_to_midterm", _mt_payload)
        slow_queue.enqueue("consistency_check", {
            "reply": reply,
        })
        if envelope.can_write_memory and _should_update_profile:
            slow_queue.enqueue("user_profile_update", {
                "uid": user_id,
                "recent": _profile_recent,
                "char_id": char_id,
                "scope": scope_payload,
            })
            logger.info(f"[pipeline.post_process_slow] 用户画像更新已入队: {user_id}")
        if envelope.can_write_memory:
            slow_queue.enqueue("trait_tracker_update", {
                "uid": user_id,
                "char_id": char_id,
                "scope": scope_payload,
            })
        if envelope.can_write_memory:
            slow_queue.enqueue("toy_autogrow", {
                "uid": user_id,
                "char_id": char_id,
                "scope": scope_payload,
                "user_content": content,
                "reply": reply,
            })

        # ── side effects：保持 asyncio.create_task ────────────────────────────
        # QQ TTS needs a OneBot destination. Sticker delivery is different:
        # desktop/mobile have no QQ id but can receive its self-contained
        # payload through the channel registry, so do not share this gate.
        if target_id and _emotion != "neutral":
            try:
                from core.config_loader import get_config as _cfg
                import random
                _tts_enabled = _cfg().get("tts", {}).get("enabled", False)
                _tts_prob = _cfg().get("tts", {}).get("probability", 0.3)
                if _tts_enabled and random.random() < _tts_prob:
                    from core.qq_service import create_reply_media_task
                    create_reply_media_task(self._send_tts(reply, target_id, is_group, emotion=_emotion, char_id=char_id))
            except Exception as e:
                log_error("pipeline.post_process.tts", e)

        # Stickers are only for a real owner-message reply.  `target_id` is
        # optional QQ routing data, not a delivery prerequisite for desktop or
        # mobile.  The sticker helper keeps QQ image delivery optional and
        # broadcasts the data-URL payload to active non-QQ channels.
        if envelope.can_affect_mood and not trigger_name and _emotion:
            try:
                from core.output.sticker import maybe_send_sticker
                from core.qq_service import create_reply_media_task
                create_reply_media_task(
                    maybe_send_sticker(
                        reply,
                        target_id or None,
                        is_group,
                        emotion=_emotion,
                        char_id=char_id,
                        recipient_id=user_id,
                    )
                )
            except Exception as e:
                log_error("pipeline.post_process.sticker", e)

        if target_id:
            try:
                from core.output.image_delivery import send_turn_images
                from core.qq_service import create_reply_media_task
                from core.tools.chat_artifacts import artifacts_for_turns
                _imgs = artifacts_for_turns([str(_turn_id)], uid=user_id, char_id=char_id).get(str(_turn_id), [])
                if _imgs:
                    create_reply_media_task(
                        send_turn_images(_imgs, target_id, is_group)
                    )
            except Exception as e:
                log_error("pipeline.post_process.image_delivery", e)

        return {
            "emotion": _emotion,
            "turn_id": _turn_id,
        }

    async def post_process(
        self,
        user_id: str,
        content: str,
        reply: str,
        target_id: str = "",
        is_group: bool = False,
        pending_paths: list[str] | None = None,
        trigger_name: str = "",
        envelope=None,
        audit_extras: dict | None = None,
        frozen_scope: "MemoryScope | None" = None,
        web_echo: bool = False,
        coplay_echo: bool = False,
        loop_executed: bool = False,
        provenance_source: str = "",
        event_channel: str = "",
        event_source: str = "",
        visible_reply: str | None = None,
        raw_user_text: str | None = None,
        media_refs: list[dict] | None = None,
    ):
        """
        Brief 37 拆分前的组合入口：依次 await post_process_critical()（落盘）与
        post_process_slow()（expression / appraisal / slow_queue 等）。

        只给不需要"send 前只等落盘"的调用方用（如 admin/routers/chat.py 的
        fire-and-forget 桌宠聊天路径，整个 post_process 已经是 asyncio.create_task
        丢出去的，早于它的 HTTP 响应已经返回，等多久都不影响 send）。

        send 关键路径（core/turn_sink.py record_assistant_turn）请分别 await
        post_process_critical() 再在 send 完成后 asyncio.create_task()
        post_process_slow()，不要调用这个组合入口。
        """
        critical_result = await self.post_process_critical(
            user_id, content, reply,
            target_id=target_id, is_group=is_group, pending_paths=pending_paths,
            trigger_name=trigger_name, envelope=envelope, audit_extras=audit_extras,
            frozen_scope=frozen_scope, web_echo=web_echo, coplay_echo=coplay_echo,
            provenance_source=provenance_source,
            event_channel=event_channel, event_source=event_source,
            visible_reply=visible_reply, raw_user_text=raw_user_text,
            media_refs=media_refs,
        )
        slow_result = await self.post_process_slow(
            user_id, content, reply, critical_result,
            target_id=target_id, is_group=is_group, trigger_name=trigger_name,
            envelope=envelope, audit_extras=audit_extras, web_echo=web_echo,
            coplay_echo=coplay_echo, loop_executed=loop_executed,
            provenance_source=provenance_source,
        )
        return {
            "emotion": slow_result["emotion"],
            "turn_id": critical_result["turn_id"],
            "critical_written": critical_result["critical_written"],
        }

    async def _send_tts(self, text: str, target_id: str, is_group: bool, emotion: str = "neutral", char_id: str | None = None):
        """异步 TTS 合成并通过 NapCat 发送语音消息，失败只记日志。

        char_id 透传给 voice_adapter.synthesize()，按角色卡 presence_ext.tts_preset
        解析命名 TTS 预设（角色资产路由）；未传时回落全局 tts 配置（现状行为不变）。
        """
        from core.output.voice_adapter import clean_tts_text, synthesize, send_voice
        from core.error_handler import log_error
        import re
        # 清洗文本：去掉括号内的动作/环境描写，只保留说出口的话
        clean = clean_tts_text(text)
        if not clean:
            logger.debug("[pipeline.tts] 清洗后文本为空，跳过语音")
            return
        # 按标点切分，随机抽一句，优先抽10-30字的句子
        import random
        _sentences = re.split(r'[。！？…\n]', clean)
        _sentences = [s.strip() for s in _sentences if 5 <= len(s.strip()) <= 40]
        if _sentences:
            clean = random.choice(_sentences)
        else:
            clean = clean[:40]
        try:
            audio_bytes = await synthesize(clean, emotion, char_id=char_id)
            if audio_bytes:
                await send_voice(target_id, audio_bytes, is_group)
                logger.info(f"[pipeline.tts] 语音已发送 -> {target_id} (emotion={emotion})")
            else:
                logger.debug("[pipeline.tts] synthesize 返回 None，跳过语音发送")
        except Exception as e:
            log_error("pipeline._send_tts", e)


# ═══════════════════════════════════════════════════════════════════════════════
# 慢队列独立函数 + handlers（由 main.py 注册到 slow_queue）
# ═══════════════════════════════════════════════════════════════════════════════

def _get_scope_from_payload(payload: dict, handler_name: str) -> MemoryScope:
    """从 payload 解析 MemoryScope。

    优先读 payload["scope"]（新格式）；无 scope 时 fallback 到 legacy uid+char_id；
    两者均缺时 WARN + fallback yexuan（DLQ 兼容层）。
    payload["scope"] 存在但解析失败或 domain 非 reality → fail-loud，不 fallback。
    """
    raw = payload.get("scope")
    if raw is not None:
        scope = MemoryScope.from_payload(raw)  # 坏数据 fail-loud
        if scope.domain != "reality":
            raise ValueError(
                f"[pipeline.{handler_name}] scope domain must be 'reality', got {scope.domain!r}"
            )
        return scope

    # legacy fallback：旧 payload 无 scope 字段
    uid = payload.get("uid", "unknown")
    char_id = payload.get("char_id")
    if char_id:
        return MemoryScope.reality_scope(str(uid), char_id)

    logger.warning(
        "[pipeline.%s] payload 缺少 scope/char_id，使用 legacy DLQ fallback char_id=%s "
        "(uid=%s)",
        handler_name, DEFAULT_CHAR_ID, uid,
    )
    return MemoryScope.reality_scope(str(uid), DEFAULT_CHAR_ID)


async def _do_compress_episode(
    user_id: str, user_content: str, reply: str, *, char_id: str = DEFAULT_CHAR_ID
) -> None:
    """
    用 LLM 把一轮对话压缩成情景记忆并写入。
    handler 通过 slow_queue 调用，异常向上抛供 worker 重试。
    """
    import re
    import json
    import time
    from core import llm_client
    from core.memory import locks as _locks
    from core.memory.episodic_memory import write_episode
    from core.character_name_provider import get_active_char_name

    char_name = get_active_char_name()

    base_prompt = f"""你是一个对话记录分析器。请分析下面这段对话，输出结构化记忆，只输出JSON，不要有任何多余文字：
{{
  "raw_facts": ["用户说了什么（一句话事实）", "用了什么词或表达", "表达了什么状态"],
  "topic_keywords": ["3到5个话题关键词，用于未来召回"],
  "emotion_peak": "neutral/happy/sad/gentle/surprised/angry 中选一个",
  "emotion_texture": "用一句话描述对话中最有重量的情绪质感，20字以内，可留空",
  "emotion_arc": "情绪流动方向，10字以内，可留空",
  "user_state": "用一个短语描述用户当时的状态，如 stressed_about_work / excited / tired",
  "narrative_summary": "用一句自然语言描述这段对话发生了什么，15字以内，供{char_name}回忆时用",
  "strength": 0到1之间的浮点数（情绪越强、事件越重要越高）
}}

重要：你不是{char_name}，你是分析器。用第三人称客观陈述，不要使用文学化语言，不要写动作描写。

用户说：{user_content}
{char_name}回：{reply}"""

    _fail_key = f"compress_episode_{user_id}"
    data = None
    _last_result = ""

    for attempt in range(3):
        _prompt = base_prompt
        if attempt > 0:
            _prompt += "\n\n上次输出不符合格式要求，请严格只输出JSON，不要有任何多余文字。"
        _last_result = await llm_client.chat(
            messages=[{"role": "user", "content": _prompt}],
            max_tokens_override=400,
        )
        try:
            cleaned = re.sub(r"```json|```", "", _last_result).strip()
            candidate = json.loads(cleaned)
            if _validate_episode(candidate):
                data = candidate
                break
        except (json.JSONDecodeError, Exception):
            pass

    if data is None:
        record_failure(_fail_key, _last_result, user_id)
        return

    if data.get("emotion_peak") == "neutral" and data.get("strength", 0) < 0.4:
        return

    episode = {
        "id": f"ep_{int(time.time())}",
        "timestamp": time.time(),
        "raw_facts": data.get("raw_facts", []),
        "topic_keywords": data.get("topic_keywords", []),
        "emotion_peak": data.get("emotion_peak", "neutral"),
        "emotion_texture": data.get("emotion_texture", ""),
        "emotion_arc": data.get("emotion_arc", ""),
        "user_state": data.get("user_state", ""),
        "narrative_summary": data.get("narrative_summary", ""),
        "strength": data.get("strength", 0.5),
        "retrieval_count": 0,
        "last_retrieved": None,
    }
    async with _locks.uid_lock(user_id):
        write_episode(user_id, episode, char_id=char_id)
    reset(_fail_key)


async def _handler_consistency_check(payload: dict) -> None:
    from core import character_loader
    from core.pipeline_registry import get as _get_pipeline
    pipeline = _get_pipeline()
    if pipeline is None:
        return
    check_result = await character_loader.consistency_check(pipeline.character, payload["reply"])
    if not check_result.get("ok"):
        issue = check_result.get("issue", "")
        if issue:
            pipeline.author_note_extra = issue
            logger.info(f"[pipeline.consistency] 一致性问题，下轮追加纠偏: {issue}")


async def _handler_user_profile_update(payload: dict) -> None:
    from core.memory import locks as _locks, user_profile
    scope = _get_scope_from_payload(payload, "_handler_user_profile_update")
    uid = scope.uid
    char_id = scope.character_id
    async with _locks.uid_lock(uid):
        await user_profile.extract_and_update(uid, payload["recent"], char_id=char_id)
    logger.info(f"[pipeline.user_profile] 画像更新完成: {uid}")


async def _handler_trait_tracker_update(payload: dict) -> None:
    # R8-B: independent trait refresh — detached from character_growth.update()
    import yaml
    from core.memory import locks as _locks, short_term as _st
    from core.memory.trait_tracker import count_traits_in_history, update_trait_state
    from core.sandbox import get_paths

    scope = _get_scope_from_payload(payload, "_handler_trait_tracker_update")
    uid = scope.uid
    char_id = scope.character_id

    from core.character_data import load_traits
    try:
        traits = load_traits(char_id)
    except (ValueError, OSError) as exc:
        logger.warning("[pipeline.trait_tracker] traits definition unavailable: char_id=%s error=%s", char_id, exc)
        return
    if not traits:
        logger.debug("[pipeline.trait_tracker] traits 为空，跳过: char_id=%s", char_id)
        return

    async with _locks.uid_lock(uid):
        recent = _st.load(uid, char_id=char_id)[-40:]
        # 只统计 assistant 行：user 行包含关键词不代表角色表达过该特质
        history_lines = [msg["content"] for msg in recent if msg.get("role") == "assistant"]
        counts = count_traits_in_history(history_lines, traits)
        trait_path = get_paths().trait_state(char_id=char_id)
        update_trait_state(counts, trait_path, write_path=trait_path)
    logger.info("[pipeline.trait_tracker] 更新完成: uid=%s char_id=%s", uid, char_id)


def register_slow_handlers() -> None:
    """main.py 启动时调用一次，注册所有慢任务 handler。"""
    from core.post_process import slow_queue
    from core.memory.fixation_pipeline import (
        handler_capture_turn_retry,
        handler_summarize_to_midterm,
        handler_reflect_to_episodic,
        handler_consolidate_to_identity,
        handler_storyline_evicted_input,
    )
    # 新 pipeline handler
    slow_queue.register_handler("capture_turn_retry",       handler_capture_turn_retry)
    slow_queue.register_handler("summarize_to_midterm",     handler_summarize_to_midterm)
    slow_queue.register_handler("reflect_to_episodic",      handler_reflect_to_episodic)
    slow_queue.register_handler("consolidate_to_identity",  handler_consolidate_to_identity)
    slow_queue.register_handler("storyline_evicted_input",  handler_storyline_evicted_input)
    slow_queue.register_handler("consistency_check",       _handler_consistency_check)
    slow_queue.register_handler("user_profile_update",     _handler_user_profile_update)
    from core.food_memory import process_pending as process_food_pending

    async def handle_food(payload):
        scope = _get_scope_from_payload(payload, "food_memory_update")
        await process_food_pending(scope.uid, scope.character_id)

    slow_queue.register_handler("food_memory_update", handle_food)
    slow_queue.register_handler("trait_tracker_update",    _handler_trait_tracker_update)
    from core.post_process.toy_autogrow import handler_toy_autogrow
    slow_queue.register_handler("toy_autogrow",            handler_toy_autogrow)
    from core.stage.char_relations import handler_update_char_relations
    slow_queue.register_handler("update_char_relations",   handler_update_char_relations)
