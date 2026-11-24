"""Own one loaded character/lore session and its transient next-turn author note."""
from dataclasses import dataclass
import logging
from core.memory.scope import MemoryScope

logger = logging.getLogger("core.pipeline")


@dataclass(frozen=True)
class CharacterContextSnapshot:
    character: object
    lore_engine: object
    active_character_id: str


class CharacterSession:
    def __init__(self, character, lore_engine, active_character_id: str):
        self.character = character
        self.lore_engine = lore_engine
        self.active_character_id = active_character_id
        self.author_note_extra = ""

    def context_snapshot(self) -> CharacterContextSnapshot:
        return CharacterContextSnapshot(self.character, self.lore_engine, self.active_character_id)

    def is_active_character(self, char_id: str) -> bool:
        return self.active_character_id == char_id

    def consistency_character(self):
        return self.active_character_id, self.character

    def write_author_note(self, char_id: str, issue: str) -> bool:
        if char_id != self.active_character_id:
            return False
        self.author_note_extra = issue
        return True

    def _apply_character(self, character, char_id: str, *, clear_note: bool, scoped_lore: bool):
        self.character = character
        self.active_character_id = char_id
        if clear_note:
            self.author_note_extra = ""
        if self.lore_engine is not None:
            if scoped_lore:
                self.lore_engine.load(char_id=char_id)
            else:
                self.lore_engine.load()
            if character.world_book:
                self.lore_engine.load_entries(character.world_book)

    def reload_character(self, char_id: str, *, clear_note: bool = False):
        from core.character_loader import load
        character = load(char_id)
        self._apply_character(character, char_id, clear_note=clear_note, scoped_lore=False)
        return character

    def refresh(self) -> None:
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
                f"当前角色: {self.active_character_id!r} — 本轮已中止"
            )
            raise ValueError(
                "[pipeline] active_prompt_assets.json missing or empty active_character"
            )

        if new_id == self.active_character_id:
            return  # no change, fast path

        from core import character_loader as _cl
        try:
            new_char = _cl.load(new_id)
        except (ValueError, FileNotFoundError) as exc:
            logger.error(
                f"[pipeline] active_character {new_id!r} 无法加载: {exc}  "
                f"— 保持原角色 {self.active_character_id!r}，本轮已中止"
            )
            raise ValueError(
                f"[pipeline] active_character {new_id!r} 无法加载"
            ) from exc

        self._apply_character(new_char, new_id, clear_note=True, scoped_lore=True)
        logger.info(f"[pipeline] character hot-swapped to {new_id!r} ({new_char.name})")

    def reality_scope(self, user_id: str) -> MemoryScope:
        """Construct a reality-domain MemoryScope for the active character.

        Calls refresh() — raises ValueError/RuntimeError on
        invalid active character.  Callers must not call refresh()
        again separately.
        """
        self.refresh()
        return MemoryScope.reality_scope(str(user_id), self.active_character_id)
