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
    "【同一现实聊天的最近对话（只读参考，按当前话题自然接续）】"
)

# Brief 43 §D: proactive move comment — replaces the "用户说：…" tail of the prompt
# when the user hasn't spoken and the backend decided (via key-moment/probability/
# cooldown policy) that a comment is warranted.
PROACTIVE_COMMENT_INSTRUCTION = (
    "（系统指令：用户没有说话。请你主动对刚才这一手棋/当前局面说一句话，"
    "不超过 40 字，只输出说出口的话，依据 <game_facts>，不判断胜负。）"
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


def cooldown_satisfied(
    char_id: str,
    uid: str,
    activity_type: str,
    session_id: str,
    current_move_count: int,
    min_gap: int = 2,
) -> bool:
    """True when it's been at least *min_gap* moves since the last proactive
    comment (or none has ever been made). Reads the activity transcript looking
    for the most recent assistant_chat entry with proactive=True and compares
    its at_move against current_move_count.

    Fails open to True on any read error — an unreadable transcript should not
    permanently suppress proactive commentary.
    """
    try:
        from core.activity import transcript as _tr
        recent = _tr.load_recent(char_id, uid, activity_type, session_id, limit=20)
        for entry in reversed(recent):
            if entry.get("type") == "assistant_chat" and entry.get("proactive"):
                last_at_move = entry.get("at_move", 0)
                return (current_move_count - last_at_move) >= min_gap
        return True
    except Exception as e:
        logger.warning("[companion_context] cooldown_satisfied check failed: %s", e)
        return True
