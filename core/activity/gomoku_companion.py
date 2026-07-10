"""
Gomoku Companion Chat — activity-local LLM reply generator (P0 + grounding + P1 style tilt).

Flow:
  session state + recent transcript + user message
    → grounding facts (deterministic)
    → LLM (constrained by <game_facts>)
    → reply text + optional control block
    → holdback filter (post-process)
    → transcript (user_chat + assistant_chat)

Boundary guarantees (must not violate):
- Does NOT write short_term / event_log / user_hidden_state / afterglow / impression.
- Does NOT call perceive_event / trigger / scheduler / Dream.
- LLM cannot modify board / move_history / winner / status.
- LLM cannot directly place a stone.
- No activity summary generation (summary is only in close_game by move_count threshold).

Grounding guarantee:
- build_gomoku_grounding_facts() derives deterministic facts from board state.
- <game_facts> is injected into every prompt — LLM must base judgments on those facts only.
- Holdback claims ("我让你了") are post-processed out unless did_hold_back=True.

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

P1 (pending): get_recent_ai_style_tilt() reads transcript to influence next AI move style.
Control is passed to apply_ai_move() via /ai_move — LLM does NOT output coordinates.
"""
from __future__ import annotations

import json
import logging
import re

from core import llm_client as _llm_client
from core.activity import transcript as _tr
from core.activity.companion_text import strip_action_descriptions
from core.activity.gomoku_grounding import build_gomoku_grounding_facts
from core.activity.session import now_iso

logger = logging.getLogger(__name__)

_FALLBACK_REPLY = "我刚才走神了一下。你把刚才那句话再说一遍。"
_TRANSCRIPT_CONTEXT_LIMIT = 6
_RECENT_MOVES_LIMIT = 6

_VALID_AI_STYLE_TILTS = frozenset({"gentle", "balanced", "serious", "teaching"})
_VALID_COMMENTARY_TONES = frozenset({"calm", "teasing", "focused", "comforting"})

_CONTROL_RE = re.compile(r"<activity_control>\s*(.*?)\s*</activity_control>", re.DOTALL)

# Matches sentences that claim the AI is intentionally holding back / letting win
_HOLDBACK_CLAIM_RE = re.compile(
    r"[^。！？\n]*(?:我[在]?让[你了着过]|故意[输让]|没有认真|随便[下走]|放水)[^。！？\n]*[。！？]?",
    re.UNICODE,
)

_GROUNDING_CONSTRAINT = """\

棋局判断规则：
你可以用自然语气陪用户下棋，但关于棋局的判断必须依据 <game_facts>。
如果 <game_facts> 没有支持，不要声称"这步很强""我在让你""你已经形成活三"等具体判断。
你可以说"不急，我再看看局面"，但不能编造棋形。
你不能输出坐标要求自己落子。
你不能判定胜负；胜负只由规则引擎决定。
放水声明：只有 <game_facts> 中 did_hold_back=True 时才能表达放水意思。\
否则只能说"我没有急着逼你。"或"这局我先按现在的节奏来。"等中性说法，不能说"我让你了"。"""

_SYSTEM_YEXUAN_AI = (
    "你是叶瑄，正在和用户下五子棋。用户执黑，你执白。\n\n"
    "请以叶瑄的身份自然地回应用户的聊天消息。你可以评论棋局、回应用户的情绪、提供战术提示，"
    "但不要直接指示落子坐标，不要判断胜负，不要帮用户下棋。\n\n"
    "如果你想影响后续游戏风格（可选），在回复末尾附加控制块：\n"
    "<activity_control>\n"
    '{"ai_style_tilt": "gentle|balanced|serious|teaching", "commentary_tone": "calm|teasing|focused|comforting"}\n'
    "</activity_control>\n"
    "只在有实际意图时加控制块，没有则省略。\n\n"
    "只输出说出口的话。不写旁白、不写括号动作描写、不写星号动作、不用 Markdown。"
    + _GROUNDING_CONSTRAINT
)

_SYSTEM_HUMAN = (
    "你是叶瑄，正在陪用户看一局双人五子棋对弈。\n\n"
    "请以叶瑄的身份自然地回应用户的聊天消息。你可以评论棋局、回应用户的情绪，"
    "但不要直接指示落子坐标，不要判断胜负。\n\n"
    "如果你想影响后续风格（可选），在回复末尾附加控制块：\n"
    "<activity_control>\n"
    '{"ai_style_tilt": "gentle|balanced|serious|teaching", "commentary_tone": "calm|teasing|focused|comforting"}\n'
    "</activity_control>\n"
    "只在有实际意图时加控制块，没有则省略。\n\n"
    "只输出说出口的话。不写旁白、不写括号动作描写、不写星号动作、不用 Markdown。"
    + _GROUNDING_CONSTRAINT
)


# ── Grounding prompt formatter ─────────────────────────────────────────────────

def _format_grounding_for_prompt(facts: dict) -> str:
    """Format grounding facts as a <game_facts> block for LLM injection."""
    lines = ["<game_facts>"]

    status_label = {"active": "进行中", "completed": "已结束"}.get(
        facts.get("status", ""), facts.get("status", "")
    )
    lines.append(f"步数：{facts.get('move_count', 0)}")
    lines.append(f"状态：{status_label}")
    winner = facts.get("winner")
    lines.append(f"胜者：{winner or '暂无'}")

    ai_style = facts.get("ai_style")
    did_hold_back = facts.get("did_hold_back", False)
    if ai_style:
        lines.append(f"AI风格：{ai_style}")
    lines.append(f"did_hold_back：{'True' if did_hold_back else 'False'}")

    # User last move facts
    lu = facts.get("last_user_move")
    luf = facts.get("last_user_move_facts", {})
    if lu:
        lines.append(f"\n最近用户落子：({lu.get('x')},{lu.get('y')}) 第{lu.get('move_no')}手")
        lines.append(f"  形成连子：{luf.get('created_chain', '?')}连")
        blocked = luf.get("blocked_opponent_chain")
        lines.append(f"  封堵对方：{'{}连'.format(blocked) if blocked else '无'}")
        lines.append(f"  中心区域：{'是' if luf.get('is_center_area') else '否'}  "
                     f"边缘区域：{'是' if luf.get('is_edge_area') else '否'}")
        lines.append(f"  邻近棋子数：{luf.get('adjacent_stones', 0)}")
        lines.append(f"  小结：{luf.get('summary', '')}")

    # AI last move facts
    lai = facts.get("last_ai_move")
    laif = facts.get("last_ai_move_facts", {})
    if lai and facts.get("opponent") == "character_ai":
        lines.append(f"\n最近AI落子：({lai.get('x')},{lai.get('y')}) 第{lai.get('move_no')}手")
        lines.append(f"  意图：{laif.get('purpose', 'unknown')}")
        lines.append(f"  形成连子：{laif.get('created_chain', '?')}连")
        blocked_u = laif.get("blocked_user_chain")
        lines.append(f"  封堵用户：{'{}连'.format(blocked_u) if blocked_u else '无'}")
        lines.append(f"  小结：{laif.get('summary', '')}")

    # Board facts
    bf = facts.get("board_facts", {})
    if bf:
        lines.append("\n棋盘形势：")
        lines.append(f"  黑棋最长连子：{bf.get('black_longest_chain', 0)}")
        lines.append(f"  白棋最长连子：{bf.get('white_longest_chain', 0)}")
        lines.append(f"  黑棋活三：{'有' if bf.get('black_has_open_three') else '无'}")
        lines.append(f"  白棋活三：{'有' if bf.get('white_has_open_three') else '无'}")
        lines.append(f"  黑棋四连：{'有' if bf.get('black_has_four') else '无'}")
        lines.append(f"  白棋四连：{'有' if bf.get('white_has_four') else '无'}")

    lines.append("</game_facts>")
    return "\n".join(lines)


# ── Holdback claim filter ──────────────────────────────────────────────────────

def _filter_holdback_claims(reply: str, facts: dict) -> str:
    """
    Post-process reply to remove holdback claims when not permitted.
    Permitted only when facts["did_hold_back"] is True.
    If the claim is present but not permitted, the offending sentence is removed.
    If removal leaves an empty reply, a neutral fallback is returned.
    """
    if facts.get("did_hold_back", False):
        return reply
    if not _HOLDBACK_CLAIM_RE.search(reply):
        return reply
    filtered = _HOLDBACK_CLAIM_RE.sub("", reply).strip()
    # Remove any orphaned whitespace artifacts
    filtered = re.sub(r"\n{3,}", "\n\n", filtered).strip()
    return filtered if filtered else "我不会替你下结论，先看你下一手。"


# ── Prompt construction ────────────────────────────────────────────────────────

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


def _fmt_transcript_context(entries: list[dict], char_name: str) -> str:
    lines = []
    for e in entries:
        if e.get("type") == "user_chat":
            lines.append(f"用户：{e.get('text', '')}")
        elif e.get("type") == "assistant_chat":
            lines.append(f"{char_name}：{e.get('text', '')}")
    return "\n".join(lines)


def _build_messages(
    state: dict,
    recent_transcript: list[dict],
    user_message: str,
    facts: dict | None = None,
    char_name: str = "(角色未加载)",
) -> list[dict]:
    """Build LLM messages from game state, grounding facts, transcript context, and user message."""
    is_ai = state.get("opponent") == "character_ai"
    system = (_SYSTEM_YEXUAN_AI if is_ai else _SYSTEM_HUMAN).replace("叶瑄", char_name)

    status_label = {"active": "进行中", "completed": "已结束"}.get(
        state.get("status", "active"), state.get("status", "")
    )
    turn_raw = state.get("current_turn", "black")
    if is_ai:
        turn_label = "黑棋（用户）" if turn_raw == "black" else f"白棋（{char_name}AI）"
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
    transcript_ctx = _fmt_transcript_context(recent_transcript, char_name)

    context = "\n".join(state_lines)
    context += f"\n\n【最近棋步摘要】{move_brief}"

    if facts is not None:
        context += f"\n\n{_format_grounding_for_prompt(facts)}"

    if transcript_ctx:
        context += f"\n\n【最近对话】\n{transcript_ctx}"
    context += f"\n\n用户说：{user_message}"

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": context},
    ]


# ── Control parser ─────────────────────────────────────────────────────────────

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


# ── LLM call (with fallback) ───────────────────────────────────────────────────

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


# ── Public API ─────────────────────────────────────────────────────────────────

async def generate_reply(
    char_id: str,
    uid: str,
    session_id: str,
    state: dict,
    user_message: str,
) -> tuple[str, dict, dict]:
    """
    Generate a grounded companion reply for *user_message* in the given gomoku session.

    Steps:
      1. Build deterministic grounding facts from game state.
      2. Load recent transcript context (before current message).
      3. Build LLM prompt with <game_facts> injected.
      4. Call LLM (fallback on error).
      5. Apply holdback claim filter.
      6. Write user_chat + assistant_chat entries to transcript.
      7. Return (reply_text, control_dict, grounding_subset).

    Does NOT modify game state (board / move_history / winner / status).
    Does NOT write short_term / event_log / user_hidden_state.
    Does NOT read from Dream / hidden_state / main memory.
    """
    from core.character_name_provider import get_char_name as _get_char_name
    char_name = _get_char_name(char_id)

    # Read-path normalization for legacy sessions (Brief 25 §3 P2): callers may pass
    # session.state loaded directly (bypassing gomoku.get_active_session()'s normalization).
    from core.activity.gomoku import _normalize_opponent
    state = {**state, "opponent": _normalize_opponent(state.get("opponent", "human"))}

    # 1. Grounding facts (deterministic)
    facts = build_gomoku_grounding_facts(state)

    # 2. Load context BEFORE writing current message
    recent_ctx = _tr.load_recent(char_id, uid, "gomoku", session_id, limit=_TRANSCRIPT_CONTEXT_LIMIT)

    # 3. Build LLM messages with grounding
    messages = _build_messages(state, recent_ctx, user_message, facts, char_name=char_name)

    # 4. Call LLM
    reply, control = await _call_llm(messages)
    reply = strip_action_descriptions(reply)

    # 5. Post-process: filter holdback claims not supported by facts
    reply = _filter_holdback_claims(reply, facts)

    ts = now_iso()

    # 6. Write user_chat entry
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

    # 7. Grounding subset for response
    grounding = {
        "last_user_move_facts": facts["last_user_move_facts"],
        "last_ai_move_facts": facts["last_ai_move_facts"],
    }

    return reply, control, grounding


# ── Style tilt reader (for pending AI move) ────────────────────────────────────

def get_recent_ai_style_tilt(char_id: str, uid: str, session_id: str) -> str | None:
    """
    Return the most recent valid ai_style_tilt from assistant_chat transcript entries.

    Called by the /ai_move router before apply_ai_move() to read any style preference
    the LLM expressed during companion chat. Returns None if no valid tilt found.
    Does NOT write to transcript or modify game state.
    """
    recent = _tr.load_recent(char_id, uid, "gomoku", session_id, limit=10)
    for entry in reversed(recent):
        if entry.get("type") == "assistant_chat":
            tilt = entry.get("control", {}).get("ai_style_tilt")
            if tilt in _VALID_AI_STYLE_TILTS:
                return tilt
    return None
