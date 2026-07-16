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
        self._character = character
        self._lore = lore
        self.pipeline = Pipeline(character, lore, active_character_id=char_id)

    def topic_keywords(self, _owner_uid: str) -> tuple[str, ...]:
        """Bounded, cached topic hints from already-loaded character/lore assets."""
        from core.text_match import ngram_tokens
        words: list[str] = []
        for entry in self._character.world_book:
            words.extend(str(word) for word in (entry.get("keywords") or entry.get("keyword") or []) if str(word).strip())
        for entry in self._lore.entries:
            words.extend(str(word) for word in entry.get("keywords", []) if str(word).strip())
        words.extend(sorted(ngram_tokens(self._character.description + self._character.personality, lengths=(2, 3))))
        return tuple(dict.fromkeys(word.strip() for word in words if word.strip()))[:30]

    @staticmethod
    def _lightweight_context() -> dict:
        """Phase B continuation context: character card core layer only.

        Skips episodic/mid_term/diary/lore retrieval and the private
        history/relation/profile/user_identity layers — a continuation replies
        to the group's shared transcript, not the owner's long-term memory
        (Brief 85 §1). Phase A (triggered_by="user"/"owner") keeps the full
        `fetch_context()` path since that response is worth full memory.
        """
        return {
            "history": [],
            "relation": {},
            "profile": {},
            "group_context": "",
            "user_identity_text": "",
            "user_facts_text": "",
            "event_search_result": "",
            "lore_entries": [],
            "episodic_result": "",
            "episodic_fallback_result": "",
            "mid_term": "",
            "diary_context": "",
            "reminders": [],
        }

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
        lightweight = triggered_by not in ("user", "owner")
        if lightweight:
            context = self._lightweight_context()
        else:
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
            limit=12 if lightweight else 40,
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
        extra_block = self._extra_instruction(stage, triggered_by, transcript)
        if extra_block:
            stage_instruction = extra_block + "\n\n" + stage_instruction
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
                "[stage.prompt] group=%s char_id=%s turn_id=%s triggered_by=%s lightweight=%s token_estimate=%s",
                stage.group_id,
                self.char_id,
                turn_id,
                triggered_by,
                lightweight,
                debug.get("token_estimate"),
            )
        return await self.pipeline.run_llm(messages, char_id=self.char_id)

    def _extra_instruction(
        self, stage: Stage, triggered_by: str, transcript: list[TranscriptEntry]
    ) -> str:
        """Content-side hook for directed replies (§2) and topic seeds (§4)."""
        return ""


class StageViewRegistry:
    def __init__(self) -> None:
        self._views: dict[str, StageCharacterView] = {}

    def get(self, char_id: str) -> StageCharacterView:
        view = self._views.get(char_id)
        if view is None:
            view = StageCharacterView(char_id)
            self._views[char_id] = view
        return view
