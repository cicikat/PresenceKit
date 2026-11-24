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
    Reality 会话的组合与 prompt 实现：

    角色/世界书由 CharacterSession 持有，模型算法由 ReplyGeneration 执行。
    context / tools / effects 是实际领域实现，不经本类转发。
    """

    def __init__(self, character, lore_engine, active_character_id: str = ""):
        from core.character_session import CharacterSession
        from core.memory.reality_context import RealityContextReader
        from core.owner_chat.reply_generation import ReplyGeneration
        from core.owner_chat.tool_loop import ToolTurnRunner
        from core.post_process.turn_effects import TurnEffects

        self.character_state = CharacterSession(character, lore_engine, active_character_id)
        self.generation = ReplyGeneration(
            resolve_active_character_id=self.character_state.current_character_id,
        )
        self._last_channel: str | None = None
        self.context = RealityContextReader(
            resolve_scope=self.character_state.reality_scope,
            read_snapshot=self.character_state.context_snapshot,
        )
        self.tools = ToolTurnRunner(
            run_llm=self.generation.run_llm,
            run_llm_stream=self.generation.run_llm_stream,
            guard_tool_meta_leak=self.generation._guard_tool_meta_leak,
            guard_unverified_claim=self.generation._guard_unverified_claim,
            anti_collapse_prefix_retry=self.generation._anti_collapse_prefix_retry,
        )
        self.effects = TurnEffects(
            resolve_scope=self.character_state.reality_scope,
            is_active_character=self.character_state.is_active_character,
        )


    @property
    def character(self):
        return self.character_state.character

    @character.setter
    def character(self, value):
        self.character_state.character = value

    @property
    def lore_engine(self):
        return self.character_state.lore_engine

    @lore_engine.setter
    def lore_engine(self, value):
        self.character_state.lore_engine = value

    @property
    def _active_character_id(self):
        return self.character_state.active_character_id

    @_active_character_id.setter
    def _active_character_id(self, value):
        self.character_state.active_character_id = value

    @property
    def author_note_extra(self):
        return self.character_state.author_note_extra

    @author_note_extra.setter
    def author_note_extra(self, value):
        self.character_state.author_note_extra = value



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

        char_id: 如果传入，使用该值作为 _char_id，跳过 character_state.refresh()。
          入口处已做一次性 scope freeze 时必须传入（N1）。
        """
        if char_id is not None:
            _char_id = char_id
        else:
            self.character_state.refresh()
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
