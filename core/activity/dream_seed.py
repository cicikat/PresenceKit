"""
梦境预构 (Dream Seed) activity backend.

Activity conversation stays in the activity-local transcript. Closing a
session distills one short-lived, one-shot seed for the next Dream entry.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from core.activity import store, transcript
from core.activity.session import ActivitySession, now_iso
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope, require_character_id
from core.safe_write import safe_write_json
from core.sandbox import safe_user_id

logger = logging.getLogger(__name__)

ACTIVITY_TYPE = "dream_seed"
DREAM_SEED_TTL_HOURS = 12.0
TRANSCRIPT_CONTEXT_LIMIT = 6
DISTILL_TRANSCRIPT_LIMIT = 10


def _scope(uid: str, char_id: str) -> MemoryScope:
    return MemoryScope.reality_scope(safe_user_id(uid), require_character_id(char_id))


def _seed_path(uid: str, char_id: str):
    return resolve_path(_scope(uid, char_id), "dream_seed")


def start_session(uid: str, *, char_id: str) -> ActivitySession:
    """Create a new active Dream Seed session."""
    return store.create_session(
        uid=safe_user_id(uid),
        char_id=require_character_id(char_id),
        activity_type=ACTIVITY_TYPE,
        initial_state={"started_at": time.time()},
    )


def get_session(uid: str, session_id: str, *, char_id: str) -> Optional[ActivitySession]:
    return store.load_session(
        require_character_id(char_id),
        safe_user_id(uid),
        ACTIVITY_TYPE,
        session_id,
    )


def append_turn(uid: str, session_id: str, role: str, content: str, *, char_id: str) -> bool:
    """Append one activity-local chat turn."""
    session = get_session(uid, session_id, char_id=char_id)
    if session is None or session.status != "active":
        return False
    if role not in {"user", "assistant"}:
        raise ValueError(f"invalid dream_seed transcript role: {role!r}")
    text = (content or "").strip()
    if not text:
        return False
    transcript.append_entry(
        char_id,
        safe_user_id(uid),
        ACTIVITY_TYPE,
        session_id,
        {
            "type": f"{role}_chat",
            "text": text,
            "ts": now_iso(),
        },
    )
    return True


async def generate_reply(uid: str, session_id: str, user_msg: str, *, char_id: str) -> str:
    """Generate a short companion reply without writing main memory."""
    from core import llm_client

    history = transcript.load_recent(
        require_character_id(char_id),
        safe_user_id(uid),
        ACTIVITY_TYPE,
        session_id,
        limit=TRANSCRIPT_CONTEXT_LIMIT,
    )
    messages = [{
        "role": "system",
        "content": (
            "你和用户正在一起构建今晚的梦境场景。\n"
            "共同决定地点、氛围、天气、时间，以及你们会在梦里做什么。\n"
            "自然地回应并适时问一个具体问题；不要宣布设定完成。\n"
            "回复不超过50字，不写旁白或括号动作描写。"
        ),
    }]
    for turn in history:
        turn_type = turn.get("type")
        if turn_type in {"user_chat", "assistant_chat"}:
            messages.append({
                "role": "user" if turn_type == "user_chat" else "assistant",
                "content": str(turn.get("text") or ""),
            })
    last_is_current_user = bool(
        history
        and history[-1].get("type") == "user_chat"
        and str(history[-1].get("text") or "").strip() == user_msg.strip()
    )
    if not last_is_current_user:
        messages.append({"role": "user", "content": user_msg})
    return (await llm_client.chat(
        messages,
        call_category="activity_dream_seed",
        max_tokens_override=120,
    ) or "").strip()


async def close_session(uid: str, session_id: str, *, char_id: str) -> Optional[str]:
    """Distill and save a seed, then close the activity session."""
    from core import llm_client

    session = get_session(uid, session_id, char_id=char_id)
    if session is None:
        return None
    if session.status == "closed":
        return str(session.state.get("seed_text") or "") or None

    turns = transcript.load_recent(
        require_character_id(char_id),
        safe_user_id(uid),
        ACTIVITY_TYPE,
        session_id,
        limit=DISTILL_TRANSCRIPT_LIMIT,
    )
    chat_turns = [t for t in turns if t.get("type") in {"user_chat", "assistant_chat"}]
    if len(chat_turns) < 2:
        return None

    dialogue = "\n".join(
        f"{'用户' if t['type'] == 'user_chat' else '角色'}：{t.get('text', '')}"
        for t in chat_turns
    )
    prompt = (
        f"以下是用户和角色为今晚梦境做的预构对话：\n\n{dialogue}\n\n"
        "把商量好的梦境设定总结成一段自然的梦境入口描述，60字以内。"
        "尽量包含地点、氛围和两人会做什么。只输出描述本身。"
    )
    seed_text = (await llm_client.chat(
        [{"role": "user", "content": prompt}],
        call_category="dream_seed_distill",
        max_tokens_override=120,
    ) or "").strip()
    if not seed_text:
        return None

    if not save_seed(uid, seed_text, session_id=session_id, char_id=char_id):
        return None

    new_state = dict(session.state)
    new_state.update({"seed_text": seed_text, "closed_at": time.time()})
    store.update_state(char_id, safe_user_id(uid), ACTIVITY_TYPE, session_id, new_state)
    store.close_session(char_id, safe_user_id(uid), ACTIVITY_TYPE, session_id)
    return seed_text


def save_seed(uid: str, seed_text: str, *, session_id: str = "", char_id: str) -> bool:
    """Atomically write the next-Dream seed."""
    text = (seed_text or "").strip()
    if not text:
        return False
    path = _seed_path(uid, char_id)
    ok = safe_write_json(path, {
        "seed_text": text,
        "created_at": time.time(),
        "session_id": session_id,
        "uid": safe_user_id(uid),
        "char_id": require_character_id(char_id),
    })
    if ok:
        logger.info("[dream_seed] saved uid=%s char=%s session=%s", uid, char_id, session_id)
    return ok


def load_seed(uid: str, *, char_id: str, now: float | None = None) -> Optional[str]:
    """Read a valid seed without consuming it."""
    path = _seed_path(uid, char_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        created_at = float(data.get("created_at", 0))
        age_hours = ((time.time() if now is None else now) - created_at) / 3600.0
        if age_hours < 0 or age_hours > DREAM_SEED_TTL_HOURS:
            return None
        return str(data.get("seed_text") or "").strip() or None
    except Exception as exc:
        logger.warning("[dream_seed] load failed uid=%s char=%s: %s", uid, char_id, exc)
        return None


def consume_seed(uid: str, *, char_id: str) -> Optional[str]:
    """Consume a valid seed exactly once on Dream entry."""
    seed = load_seed(uid, char_id=char_id)
    if not seed:
        return None
    path = _seed_path(uid, char_id)
    try:
        path.unlink()
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("[dream_seed] consume failed uid=%s char=%s: %s", uid, char_id, exc)
        return None
    return seed
