"""Per-character read/generate views owned by a Stage."""
from __future__ import annotations

import logging

from core.memory.scope import MemoryScope
from core.pipeline import Pipeline
from core.stage.context import render_presence, render_transcript
from core.stage.models import Stage, TranscriptEntry

logger = logging.getLogger(__name__)


class StageCharacterView:
    """A scoped generation view; it never calls Pipeline.post_process."""

    def __init__(self, char_id: str):
        from core import character_loader
        from core.lore_engine import LoreEngine

        character = character_loader.load(char_id)
        lore = LoreEngine()
        lore.load()
        if character.world_book:
            lore.load_entries(character.world_book)
        self.char_id = char_id
        self.pipeline = Pipeline(character, lore, active_character_id=char_id)

    async def generate(
        self,
        stage: Stage,
        transcript: list[TranscriptEntry],
        turn_id: str,
        triggered_by: str,
    ) -> str:
        if stage.domain != "reality":
            raise RuntimeError("reality StageCharacterView cannot generate for dream domain")
        latest = transcript[-1].content if transcript else ""
        scope = MemoryScope.reality_scope(stage.owner_uid, self.char_id)
        context = await self.pipeline.fetch_context(
            stage.owner_uid,
            latest,
            frozen_scope=scope,
        )
        context["stage_presence"] = render_presence(
            stage,
            viewer_id=self.char_id,
            chain_reply=triggered_by not in ("user", "owner"),
        )
        context["stage_transcript"] = render_transcript(
            stage,
            transcript,
            viewer_id=self.char_id,
            current_turn_id=turn_id,
        )
        from core.tag_rules import get_tags
        from core.author_note_rotator import get_current_note
        try:
            _char_note = get_current_note(char_id=self.char_id)
        except Exception:
            _char_note = ""
        _note_hint = f"\n你当前的写作风格锚点：{_char_note}" if _char_note else ""
        stage_instruction = (
            "你在一个有其他角色在场的群聊里。"
            "你可以回应在场的其他角色，不必每句都只对用户说话；"
            "也可以接、反驳或补充别人刚说的话。"
            "请根据上面的当前群聊共享对话，自然决定你的下一句发言；"
            "只输出你要说的话。"
            "参考已注入的群聊共享对话，不要重复你之前已经说过的内容或已经道过的歉；"
            "没有新东西要说就简短带过或不说。"
            "以你自己独特的方式回应，避免与其他角色刚说的话语气雷同。"
            + _note_hint
        )
        messages, debug = self.pipeline.build_prompt(
            stage.owner_uid,
            stage_instruction,
            context,
            tags=get_tags(latest),
            channel="stage",
            char_id=self.char_id,
            consume_pending_perception=False,
        )
        if stage.settings.debug_token_log:
            logger.info(
                "[stage.prompt] group=%s char_id=%s turn_id=%s triggered_by=%s token_estimate=%s",
                stage.group_id,
                self.char_id,
                turn_id,
                triggered_by,
                debug.get("token_estimate"),
            )
        return await self.pipeline.run_llm(messages, char_id=self.char_id)


class StageViewRegistry:
    def __init__(self) -> None:
        self._views: dict[str, StageCharacterView] = {}

    def get(self, char_id: str) -> StageCharacterView:
        view = self._views.get(char_id)
        if view is None:
            view = StageCharacterView(char_id)
            self._views[char_id] = view
        return view
