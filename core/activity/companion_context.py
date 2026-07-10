"""
Activity companion context loaders (Brief 43 §C).

Read-only boundary change: activity companion chat (chess/gomoku) may now
*read* a short persona summary and the last few main-chat rounds to ground
its replies. The write boundary is unchanged — activity chat still never
writes short_term / event_log / user_hidden_state / afterglow.

All loaders fail-open: any read error returns "" so a memory hiccup never
breaks companion chat.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

PERSONA_BRIEF_MAX_CHARS = 300
MAIN_CHAT_RECALL_ROUNDS = 3

MAIN_CHAT_RECALL_HEADER = (
    "【主线聊天最近对话（只读参考，不要复述，不要把那边的话题强行接过来）】"
)


def load_persona_brief(char_id: str) -> str:
    """Short persona summary for read-only grounding.

    Takes character_loader's personality field truncated to ~300 chars,
    falling back to description when personality is empty. Returns "" on any
    load failure (fail-open) — see core/character_loader.py::load.
    """
    try:
        from core.character_loader import load as _load_character
        char = _load_character(char_id)
        text = (char.personality or char.description or "").strip()
        return text[:PERSONA_BRIEF_MAX_CHARS]
    except Exception as e:
        logger.warning("[companion_context] load_persona_brief failed char_id=%s: %s", char_id, e)
        return ""


def load_main_chat_recall(uid: str, char_id: str, rounds: int = MAIN_CHAT_RECALL_ROUNDS) -> str:
    """Main-chat recent *rounds* rounds, formatted as 用户：… /{char_name}：… lines.

    Read-only — does not touch activity transcript or any main-memory write
    path. Assistant lines are already sanitized by short_term's
    _sanitize_assistant_message on write, so they're used as-is here.
    Returns "" on any load failure (fail-open).
    """
    try:
        from core.character_name_provider import get_char_name
        from core.memory.short_term import get_history
        history = get_history(uid, max_turns=rounds, char_id=char_id)
        if not history:
            return ""
        char_name = get_char_name(char_id)
        lines: list[str] = []
        for msg in history:
            content = str(msg.get("content") or "").strip()
            if not content:
                continue
            if msg.get("role") == "user":
                lines.append(f"用户：{content}")
            elif msg.get("role") == "assistant":
                lines.append(f"{char_name}：{content}")
        return "\n".join(lines)
    except Exception as e:
        logger.warning(
            "[companion_context] load_main_chat_recall failed uid=%s char_id=%s: %s", uid, char_id, e
        )
        return ""
