"""
Activity memory reflow — 活动结束摘要写入主记忆。

在 close + summary_threshold 满足时被各 close 端点调用：
  1. 读取该 session 的 activity transcript
  2. LLM 生成 ≤1-2 句关系/情绪向摘要
  3. 写入 episodic 记忆（单条），来源标记 activity:{type}

隔离保证：
  - 只在活动**结束后**调用，过程中不写 short_term / hidden_state
  - 不绕过 core/sandbox.get_paths()（由 episodic_memory 内部保证）
"""
from __future__ import annotations

import logging
import time

from core import llm_client as _llm
from core.activity import transcript as _tr
from core.memory import episodic_memory

logger = logging.getLogger(__name__)

_ACTIVITY_LABELS: dict[str, str] = {
    "gomoku": "五子棋",
    "chess": "国际象棋",
    "reading": "一起看书",
}


async def generate_and_reflow(
    uid: str,
    char_id: str,
    activity_type: str,
    session_id: str,
) -> str | None:
    """
    Load the activity transcript, generate a 1-2 sentence emotional/relational
    summary, and write one episodic episode to main memory.

    Returns the summary text on success, None if skipped or failed.
    Caller is responsible for checking summary_threshold before calling.
    """
    entries = _tr.load_recent(char_id, uid, activity_type, session_id, limit=40)
    if not entries:
        logger.info(
            "[activity_summary] no transcript — skip reflow activity=%s session=%s",
            activity_type, session_id,
        )
        return None

    from core.config_loader import _char_name
    char_name = _char_name()
    label = _ACTIVITY_LABELS.get(activity_type, activity_type)

    lines: list[str] = []
    for e in entries:
        t = e.get("type", "")
        if t == "user_chat":
            lines.append(f"用户：{e.get('message', '')}")
        elif t == "assistant_chat":
            lines.append(f"{char_name}：{e.get('reply', '')}")
    context = "\n".join(lines) if lines else "（无对话内容）"

    system_prompt = (
        f"你是{char_name}，正在把刚结束的活动整理成一段简短的记忆。\n"
        "写1-2句话，从你的视角描述这次活动留下的情绪印象或与用户的关系感受。\n"
        "不要写棋谱、步骤或页码，只写感受和互动的温度。用第一人称。\n"
        "输出纯文本，不要引号、标签或解释。"
    )
    user_prompt = (
        f"刚才我们一起{label}，活动已结束。对话片段：\n\n{context}\n\n"
        "请用1-2句话描述这次活动给你留下的感受或印象。"
    )

    try:
        raw = await _llm.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens_override=150,
        )
    except Exception as exc:
        logger.error(
            "[activity_summary] LLM failed activity=%s session=%s: %s",
            activity_type, session_id, exc,
        )
        return None

    if not raw or not raw.strip():
        logger.info(
            "[activity_summary] empty LLM response — skip activity=%s session=%s",
            activity_type, session_id,
        )
        return None

    summary_text = raw.strip().strip('"').strip()

    now = time.time()
    ep_id = f"ep_act_{activity_type}_{int(now * 1000)}"

    episode: dict = {
        "id": ep_id,
        "timestamp": now,
        "raw_facts": [
            f"和用户一起{label}（来源 activity:{activity_type}）",
            summary_text,
        ],
        "topic_keywords": [label, "活动", activity_type],
        "emotion_peak": "gentle",
        "emotion_texture": "",
        "emotion_arc": "",
        "user_state": "",
        "narrative_summary": summary_text,
        "strength": 0.5,
        "status": "open",
        "resolved_at": None,
        "resolved_by": None,
        "temporal_ref": "none",
        "event_time": None,
        "expires_at": None,
        "retrieval_count": 0,
        "last_retrieved": None,
        "summary": summary_text,
        "yexuan_feeling": "",
        "tags": [label, "活动"],
        "source": f"activity:{activity_type}",
        "consolidated_at": None,
    }

    try:
        episodic_memory.write_episode(uid, episode, char_id=char_id)
    except Exception as exc:
        logger.error(
            "[activity_summary] write_episode failed session=%s: %s",
            session_id, exc,
        )
        return None

    logger.info(
        "[activity_summary] reflow done activity=%s session=%s ep_id=%s",
        activity_type, session_id, ep_id,
    )
    return summary_text
