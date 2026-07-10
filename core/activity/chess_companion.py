"""
Chess Companion Chat — activity-local LLM reply generator.

Flow:
  session state + recent transcript + user message
    → grounding facts (deterministic, from FEN/move_history)
    → LLM (constrained by <game_facts>)
    → reply text + optional control block
    → transcript (user_chat + assistant_chat)

Boundary guarantees (must not violate):
- Does NOT write short_term / event_log / user_hidden_state / afterglow / impression.
- Does NOT call perceive_event / trigger / scheduler / Dream.
- LLM cannot modify board / move_history / result / status.
- LLM cannot output a chess move on the user's behalf.

LLM output protocol (optional control block):
  自然语言回复

  <activity_control>
  {"commentary_tone": "calm"}
  </activity_control>

  Allowed: commentary_tone: "calm" | "teasing" | "focused" | "comforting"
  Invalid values are silently discarded.
"""
from __future__ import annotations

import json
import logging
import re

from core import llm_client as _llm_client
from core.activity import transcript as _tr
from core.activity.chess_grounding import build_chess_grounding_facts, format_chess_grounding_for_prompt
from core.activity.companion_text import strip_action_descriptions
from core.activity.session import now_iso
from core.observe import prompt_capture as _prompt_capture

logger = logging.getLogger(__name__)

_FALLBACK_REPLY = "我刚才走神了一下。你把刚才那句话再说一遍。"
_TRANSCRIPT_CONTEXT_LIMIT = 6

_VALID_COMMENTARY_TONES = frozenset({"calm", "teasing", "focused", "comforting"})

_CONTROL_RE = re.compile(r"<activity_control>\s*(.*?)\s*</activity_control>", re.DOTALL)

_GROUNDING_CONSTRAINT = """\

棋局判断规则：
你可以用自然语气陪用户下棋，但关于棋局的判断必须依据 <game_facts>。
如果 <game_facts> 没有支持，不要声称"这步很强""你已经占优势"等具体判断。
你可以说"局面还在走"，但不能编造未发生的将军或吃子。
你不能代替用户走棋，也不能直接输出棋步记号要求走棋。
你不能判定胜负；胜负只由规则引擎决定。"""

_SYSTEM_CHESS = (
    "你是叶瑄，正在和用户一起下国际象棋。这是一局本地双人裁判模式，白方与黑方轮流走棋。\n\n"
    "请以叶瑄的身份自然地回应用户的聊天消息。你可以评论棋局、回应用户的情绪、提供战术提示，"
    "但不要直接指示走棋记号，不要判断最终胜负。\n\n"
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
    state: dict,
    recent_transcript: list[dict],
    user_message: str,
    facts: dict,
    char_name: str = "(角色未加载)",
) -> list[dict]:
    status_label = {"active": "进行中", "completed": "已结束"}.get(
        state.get("status", "active"), state.get("status", "")
    )
    turn = state.get("turn", "white")
    turn_label = "白方" if turn == "white" else "黑方"
    move_count = len(state.get("move_history", []))

    state_lines = [
        "【当前棋局】",
        f"状态：{status_label}",
        f"当前轮次：{turn_label}",
        f"总步数：{move_count}",
    ]
    if state.get("result"):
        state_lines.append(f"结果：{state['result']}")

    context = "\n".join(state_lines)
    context += f"\n\n{format_chess_grounding_for_prompt(facts)}"

    transcript_ctx = _fmt_transcript_context(recent_transcript, char_name)
    if transcript_ctx:
        context += f"\n\n【最近对话】\n{transcript_ctx}"
    context += f"\n\n用户说：{user_message}"

    return [
        {"role": "system", "content": _SYSTEM_CHESS.replace("叶瑄", char_name), "_layer": "activity_system"},
        {"role": "user", "content": context, "_layer": "activity_context"},
    ]


def _parse_control(raw: str) -> tuple[str, dict]:
    m = _CONTROL_RE.search(raw)
    if not m:
        return raw.strip(), {}
    clean = _CONTROL_RE.sub("", raw).strip()
    try:
        data = json.loads(m.group(1).strip())
    except (json.JSONDecodeError, ValueError):
        logger.warning("[chess_companion] control block JSON parse failed")
        return clean, {}
    control: dict = {}
    tone = data.get("commentary_tone")
    if tone in _VALID_COMMENTARY_TONES:
        control["commentary_tone"] = tone
    return clean, control


def _capture_prompt(uid: str, session_id: str, messages: list[dict], kind: str = "chat") -> None:
    """Record this LLM call for /observe/prompt-layers. Fail-open: observation must
    never break companion chat."""
    try:
        _prompt_capture.set_capture_origin({
            "origin": "activity",
            "activity_type": "chess",
            "session_id": session_id,
            "kind": kind,
        })
        _prompt_capture.capture(uid, messages, {
            "tags": [],
            "layers_activated": [m.get("_layer", "unknown") for m in messages],
            "token_estimate": sum(len(m.get("content", "")) for m in messages),
        })
    except Exception as e:
        logger.warning("[chess_companion] prompt_capture failed: %s", e)


def _capture_output(uid: str, reply: str) -> None:
    try:
        _prompt_capture.update_llm_output(uid, reply)
    except Exception as e:
        logger.warning("[chess_companion] prompt_capture update failed: %s", e)


async def _call_llm(messages: list[dict]) -> tuple[str, dict]:
    try:
        raw = await _llm_client.chat(messages, max_tokens_override=400)
        if not raw or not raw.strip():
            return _FALLBACK_REPLY, {}
        return _parse_control(raw)
    except Exception as e:
        logger.warning("[chess_companion] LLM call failed, using fallback: %s", e)
        return _FALLBACK_REPLY, {}


async def generate_reply(
    char_id: str,
    uid: str,
    session_id: str,
    state: dict,
    user_message: str,
) -> tuple[str, dict, dict]:
    """
    Generate a grounded companion reply for user_message in the given chess session.

    Steps:
      1. Build deterministic grounding facts from game state.
      2. Load recent transcript context.
      3. Build LLM prompt with <game_facts>.
      4. Call LLM (fallback on error).
      5. Write user_chat + assistant_chat to transcript.
      6. Return (reply_text, control_dict, grounding_subset).

    Does NOT modify game state (board / move_history / result / status).
    Does NOT write short_term / event_log / user_hidden_state.
    """
    from core.character_name_provider import get_char_name as _get_char_name
    char_name = _get_char_name(char_id)

    facts = build_chess_grounding_facts(state)
    recent_ctx = _tr.load_recent(char_id, uid, "chess", session_id, limit=_TRANSCRIPT_CONTEXT_LIMIT)
    messages = _build_messages(state, recent_ctx, user_message, facts, char_name=char_name)
    _capture_prompt(uid, session_id, messages, kind="chat")
    reply, control = await _call_llm(messages)
    reply = strip_action_descriptions(reply)
    _capture_output(uid, reply)

    ts = now_iso()

    _tr.append_entry(char_id, uid, "chess", session_id, {
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

    _tr.append_entry(char_id, uid, "chess", session_id, assistant_entry)

    grounding = {
        "last_move": facts.get("last_move"),
        "last_san": facts.get("last_san"),
        "move_hint": facts.get("move_hint"),
        "is_check": facts.get("is_check"),
        "captured_piece": facts.get("captured_piece"),
        "material_balance_desc": facts.get("material_balance_desc"),
        "turn": facts.get("turn"),
    }

    return reply, control, grounding
