"""
Gomoku Companion Chat — activity-local LLM reply generator (P0).

Flow:
  session state + recent transcript + user message
    → LLM
    → reply text + optional control block
    → transcript (user_chat + assistant_chat)

Boundary guarantees (must not violate):
- Does NOT write short_term / event_log / user_hidden_state / afterglow / impression.
- Does NOT call perceive_event / trigger / scheduler / Dream.
- LLM cannot modify board / move_history / winner / status.
- LLM cannot directly place a stone.
- No activity summary generation (summary is only in close_game by move_count threshold).

LLM output protocol (optional control block):
  自然语言回复

  <activity_control>
  {"ai_style_tilt": "gentle", "commentary_tone": "calm"}
  </activity_control>

  Allowed values:
    ai_style_tilt: "gentle" | "balanced" | "serious" | "teaching" | None
    commentary_tone: "calm" | "teasing" | "focused" | "comforting" | None
  Invalid values are silently discarded.
  Parse failure never affects the visible reply.

P0 control is saved to transcript only — it does not yet influence AI move selection.
P1 (pending): pending_ai_turn + control influences next AI move style.
"""
from __future__ import annotations

import json
import logging
import re

from core import llm_client as _llm_client
from core.activity import transcript as _tr
from core.activity.session import now_iso

logger = logging.getLogger(__name__)

_FALLBACK_REPLY = "我刚才走神了一下。你把刚才那句话再说一遍。"
_TRANSCRIPT_CONTEXT_LIMIT = 6
_RECENT_MOVES_LIMIT = 6

_VALID_AI_STYLE_TILTS = frozenset({"gentle", "balanced", "serious", "teaching"})
_VALID_COMMENTARY_TONES = frozenset({"calm", "teasing", "focused", "comforting"})

_CONTROL_RE = re.compile(r"<activity_control>\s*(.*?)\s*</activity_control>", re.DOTALL)

_SYSTEM_YEXUAN_AI = """\
你是叶瑄，正在和用户下五子棋。用户执黑，你执白。

请以叶瑄的身份自然地回应用户的聊天消息。你可以评论棋局、回应用户的情绪、提供战术提示，\
但不要直接指示落子坐标，不要判断胜负，不要帮用户下棋。

如果你想影响后续游戏风格（可选），在回复末尾附加控制块：
<activity_control>
{"ai_style_tilt": "gentle|balanced|serious|teaching", "commentary_tone": "calm|teasing|focused|comforting"}
</activity_control>
只在有实际意图时加控制块，没有则省略。"""

_SYSTEM_HUMAN = """\
你是叶瑄，正在陪用户看一局双人五子棋对弈。

请以叶瑄的身份自然地回应用户的聊天消息。你可以评论棋局、回应用户的情绪，\
但不要直接指示落子坐标，不要判断胜负。

如果你想影响后续风格（可选），在回复末尾附加控制块：
<activity_control>
{"ai_style_tilt": "gentle|balanced|serious|teaching", "commentary_tone": "calm|teasing|focused|comforting"}
</activity_control>
只在有实际意图时加控制块，没有则省略。"""


# ── 提示构造助手 ───────────────────────────────────────────────────────────────

def _fmt_last_move(m: dict | None) -> str:
    if not m:
        return "暂无"
    p = "黑" if m.get("player") == "black" else "白"
    return f"第{m.get('move_no','?')}手 {p}({m.get('x')},{m.get('y')})"


def _fmt_move_history_brief(history: list[dict], limit: int = _RECENT_MOVES_LIMIT) -> str:
    """Return a short summary of the last *limit* moves. Never dumps the full list."""
    if not history:
        return "（尚未落子）"
    recent = history[-limit:]
    parts = []
    for mv in recent:
        p = "黑" if mv.get("player") == "black" else "白"
        src = "（AI）" if mv.get("source") == "ai" else ""
        parts.append(f"第{mv.get('move_no','?')}手 {p}({mv.get('x')},{mv.get('y')}){src}")
    total = len(history)
    header = f"（共{total}手，最近{len(recent)}手）" if total > limit else f"（共{total}手）"
    return header + " " + "→".join(parts)


def _fmt_transcript_context(entries: list[dict]) -> str:
    lines = []
    for e in entries:
        if e.get("type") == "user_chat":
            lines.append(f"用户：{e.get('text', '')}")
        elif e.get("type") == "assistant_chat":
            lines.append(f"叶瑄：{e.get('text', '')}")
    return "\n".join(lines)


def _build_messages(state: dict, recent_transcript: list[dict], user_message: str) -> list[dict]:
    """Build LLM messages from game state, transcript context, and current user message."""
    is_ai = state.get("opponent") == "yexuan_ai"
    system = _SYSTEM_YEXUAN_AI if is_ai else _SYSTEM_HUMAN

    status_label = {"active": "进行中", "completed": "已结束"}.get(
        state.get("status", "active"), state.get("status", "")
    )
    turn_raw = state.get("current_turn", "black")
    if is_ai:
        turn_label = "黑棋（用户）" if turn_raw == "black" else "白棋（叶瑄AI）"
    else:
        turn_label = "黑棋" if turn_raw == "black" else "白棋"

    state_lines = [
        "【当前棋局】",
        f"状态：{status_label}",
        f"当前轮次：{turn_label}",
        f"胜者：{state.get('winner') or '暂无'}",
        f"总步数：{len(state.get('move_history', []))}",
        f"最新一手：{_fmt_last_move(state.get('last_move'))}",
    ]
    if is_ai:
        state_lines.append(f"AI风格：{state.get('ai_style', 'balanced')}")

    move_brief = _fmt_move_history_brief(state.get("move_history", []))
    transcript_ctx = _fmt_transcript_context(recent_transcript)

    context = "\n".join(state_lines)
    context += f"\n\n【最近棋步摘要】{move_brief}"
    if transcript_ctx:
        context += f"\n\n【最近对话】\n{transcript_ctx}"
    context += f"\n\n用户说：{user_message}"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": context},
    ]


# ── control 解析 ───────────────────────────────────────────────────────────────

def _parse_control(raw: str) -> tuple[str, dict]:
    """Strip <activity_control> block from raw reply. Returns (clean_reply, control_dict)."""
    m = _CONTROL_RE.search(raw)
    if not m:
        return raw.strip(), {}

    clean = _CONTROL_RE.sub("", raw).strip()
    try:
        data = json.loads(m.group(1).strip())
    except (json.JSONDecodeError, ValueError):
        logger.warning("[gomoku_companion] control block JSON parse failed")
        return clean, {}

    control: dict = {}
    tilt = data.get("ai_style_tilt")
    if tilt in _VALID_AI_STYLE_TILTS:
        control["ai_style_tilt"] = tilt
    tone = data.get("commentary_tone")
    if tone in _VALID_COMMENTARY_TONES:
        control["commentary_tone"] = tone
    return clean, control


# ── LLM 调用（带 fallback）────────────────────────────────────────────────────

async def _call_llm(messages: list[dict]) -> tuple[str, dict]:
    """Call LLM and return (reply, control). Returns fallback tuple on any error."""
    try:
        raw = await _llm_client.chat(messages, max_tokens_override=400)
        if not raw or not raw.strip():
            return _FALLBACK_REPLY, {}
        return _parse_control(raw)
    except Exception as e:
        logger.warning("[gomoku_companion] LLM call failed, using fallback: %s", e)
        return _FALLBACK_REPLY, {}


# ── 公开接口 ───────────────────────────────────────────────────────────────────

async def generate_reply(
    char_id: str,
    uid: str,
    session_id: str,
    state: dict,
    user_message: str,
) -> tuple[str, dict]:
    """
    Generate a companion reply for *user_message* in the given gomoku session.

    Steps:
      1. Load recent transcript context (before current message).
      2. Build LLM prompt from state + context + message.
      3. Call LLM (fallback on error).
      4. Write user_chat + assistant_chat entries to transcript.
      5. Return (reply_text, control_dict).

    Does NOT modify game state (board / move_history / winner / status).
    Does NOT write short_term / event_log / user_hidden_state.
    """
    # 1. Load context BEFORE writing current message
    recent_ctx = _tr.load_recent(char_id, uid, "gomoku", session_id, limit=_TRANSCRIPT_CONTEXT_LIMIT)

    # 2. Build LLM messages
    messages = _build_messages(state, recent_ctx, user_message)

    # 3. Call LLM
    reply, control = await _call_llm(messages)

    ts = now_iso()

    # 4. Write user_chat entry
    _tr.append_entry(char_id, uid, "gomoku", session_id, {
        "type": "user_chat",
        "text": user_message,
        "ts": ts,
    })

    # Write assistant_chat entry (include control only if non-empty)
    assistant_entry: dict = {
        "type": "assistant_chat",
        "text": reply,
        "ts": ts,
    }
    if control:
        assistant_entry["control"] = control

    _tr.append_entry(char_id, uid, "gomoku", session_id, assistant_entry)

    return reply, control
