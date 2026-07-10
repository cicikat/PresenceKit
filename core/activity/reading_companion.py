"""
Reading Companion Chat — activity-local LLM reply generator.

Flow:
  session (current_page, total_pages, filename) + page_text + recent transcript + user message
    → grounding facts (page excerpt, progress)
    → LLM (constrained by <page_context>)
    → reply text + optional control block
    → transcript (user_chat + assistant_chat)

Boundary guarantees (must not violate):
- Does NOT write short_term / event_log / user_hidden_state / afterglow / impression.
- Does NOT call perceive_event / trigger / scheduler / Dream.
- page_text is TRUNCATED before injection — full page never enters LLM prompt wholesale.
- LLM cannot flip pages or modify session state.

LLM output protocol (optional control block):
  自然语言回复

  <activity_control>
  {"commentary_tone": "calm"}
  </activity_control>
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from core import llm_client as _llm_client
from core.activity import transcript as _tr
from core.activity.companion_text import strip_action_descriptions
from core.activity.reading_grounding import (
    build_reading_grounding_facts,
    format_reading_grounding_for_prompt,
)
from core.activity.session import now_iso

logger = logging.getLogger(__name__)

_FALLBACK_REPLY = "我刚才走神了一下。你把刚才那句话再说一遍。"
_TRANSCRIPT_CONTEXT_LIMIT = 6

_VALID_COMMENTARY_TONES = frozenset({"calm", "teasing", "focused", "comforting"})

_CONTROL_RE = re.compile(r"<activity_control>\s*(.*?)\s*</activity_control>", re.DOTALL)

_GROUNDING_CONSTRAINT = """\

阅读判断规则：
你只能根据 <page_context> 中提供的本页开头片段来评论内容。
不要声称你读过完整本页或其他页的内容。
你可以说"这段开头听起来……"，但不能编造页面中未出现的信息。
你不能代替用户翻页或修改阅读进度。"""

_SYSTEM_READING = (
    "你是叶瑄，正在陪用户一起看书。\n\n"
    "请以叶瑄的身份自然地回应用户的聊天消息。你可以评论书的内容、回应用户的感受、分享阅读体验，"
    "但只能依据 <page_context> 中实际给出的内容。\n\n"
    "如果你想影响对话气氛（可选），在回复末尾附加控制块：\n"
    "<activity_control>\n"
    '{"commentary_tone": "calm|teasing|focused|comforting"}\n'
    "</activity_control>\n"
    "只在有实际意图时加控制块，没有则省略。\n\n"
    "只输出说出口的话。不写旁白、不写括号动作描写、不写星号动作、不用 Markdown。"
    + _GROUNDING_CONSTRAINT
)


def _fmt_transcript_context(entries: list[dict], char_name: str) -> str:
    lines = []
    for e in entries:
        if e.get("type") == "user_chat":
            lines.append(f"用户：{e.get('text', '')}")
        elif e.get("type") == "assistant_chat":
            lines.append(f"{char_name}：{e.get('text', '')}")
    return "\n".join(lines)


def _build_messages(
    current_page: int,
    total_pages: int,
    filename: str,
    page_text: Optional[str],
    recent_transcript: list[dict],
    user_message: str,
    facts: dict,
    char_name: str = "(角色未加载)",
) -> list[dict]:
    state_lines = [
        "【当前阅读进度】",
        f"文件：{filename}",
        f"当前页：第 {current_page} 页 / 共 {total_pages} 页",
    ]
    context = "\n".join(state_lines)
    context += f"\n\n{format_reading_grounding_for_prompt(facts, char_name=char_name)}"

    transcript_ctx = _fmt_transcript_context(recent_transcript, char_name)
    if transcript_ctx:
        context += f"\n\n【最近对话】\n{transcript_ctx}"
    context += f"\n\n用户说：{user_message}"

    return [
        {"role": "system", "content": _SYSTEM_READING.replace("叶瑄", char_name)},
        {"role": "user", "content": context},
    ]


def _parse_control(raw: str) -> tuple[str, dict]:
    m = _CONTROL_RE.search(raw)
    if not m:
        return raw.strip(), {}
    clean = _CONTROL_RE.sub("", raw).strip()
    try:
        data = json.loads(m.group(1).strip())
    except (json.JSONDecodeError, ValueError):
        logger.warning("[reading_companion] control block JSON parse failed")
        return clean, {}
    control: dict = {}
    tone = data.get("commentary_tone")
    if tone in _VALID_COMMENTARY_TONES:
        control["commentary_tone"] = tone
    return clean, control


async def _call_llm(messages: list[dict]) -> tuple[str, dict]:
    try:
        raw = await _llm_client.chat(messages, max_tokens_override=400)
        if not raw or not raw.strip():
            return _FALLBACK_REPLY, {}
        return _parse_control(raw)
    except Exception as e:
        logger.warning("[reading_companion] LLM call failed, using fallback: %s", e)
        return _FALLBACK_REPLY, {}


async def generate_reply(
    char_id: str,
    uid: str,
    session_id: str,
    current_page: int,
    total_pages: int,
    filename: str,
    page_text: Optional[str],
    user_message: str,
) -> tuple[str, dict, dict]:
    """
    Generate a grounded companion reply for user_message in a reading session.

    Steps:
      1. Build grounding facts (page excerpt, progress).
      2. Load recent transcript context.
      3. Build LLM prompt with <page_context>.
      4. Call LLM (fallback on error).
      5. Write user_chat + assistant_chat to transcript.
      6. Return (reply_text, control_dict, grounding_subset).

    Does NOT modify session state (current_page / status).
    Does NOT write short_term / event_log / user_hidden_state.
    page_text is truncated before injection — never stored in full.
    """
    from core.character_name_provider import get_char_name as _get_char_name
    char_name = _get_char_name(char_id)

    facts = build_reading_grounding_facts(current_page, total_pages, filename, page_text)
    recent_ctx = _tr.load_recent(char_id, uid, "reading", session_id, limit=_TRANSCRIPT_CONTEXT_LIMIT)
    messages = _build_messages(current_page, total_pages, filename, page_text, recent_ctx, user_message, facts, char_name=char_name)
    reply, control = await _call_llm(messages)
    reply = strip_action_descriptions(reply)

    ts = now_iso()

    _tr.append_entry(char_id, uid, "reading", session_id, {
        "type": "user_chat",
        "text": user_message,
        "ts": ts,
    })

    assistant_entry: dict = {
        "type": "assistant_chat",
        "text": reply,
        "ts": ts,
    }
    if control:
        assistant_entry["control"] = control

    _tr.append_entry(char_id, uid, "reading", session_id, assistant_entry)

    grounding = {
        "current_page": current_page,
        "total_pages": total_pages,
        "progress_pct": facts.get("progress_pct", 0),
        "filename": filename,
    }

    return reply, control, grounding
