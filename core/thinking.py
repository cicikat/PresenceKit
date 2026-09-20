"""
core/thinking — Brief 32：内部思考链（原生 reasoning + 前置独白，可开关）。

全局开关，默认关。两条路按 preset 能力自动选：
  - native：把 preset.reasoning_extra_body 原样并入请求 extra_body；可选追加通用
    角色心声文风提示（不是独立思考区 prompt，不保证供应商摘要遵从）。
  - monologue：主生成前一次轻量调用产出内心活动，注入当轮 messages 尾部（用户消息之前），
    用完即弃。

思考内容不进 short_term history、不广播、不落 event_log。API 返回的思考由协议边界
默认写入独立 reasoning archive；前置独白成功后另以 source=monologue 归档，供气泡
按展示策略读取。本模块不把独白写入对话记忆。
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from core.config_loader import get_config

if TYPE_CHECKING:
    from core.model_registry import ModelClient

logger = logging.getLogger(__name__)

_MONOLOGUE_LAYER = "11.7_inner_monologue"
# 独白调用的 10s 超时在 core/llm_client.py 的 _CALL_TIMEOUTS["monologue"] 里统一管理。
_MONOLOGUE_MAX_TOKENS_DEFAULT = 200

def strip_think_tags(text: str | None) -> str | None:
    """剥除文本中的 <think>…</think> / <thinking>…</thinking>（含跨行、大小写不敏感）。"""
    if not text:
        return text
    filter_ = ThinkTextFilter()
    return filter_.feed(text) + filter_.finish()


class ThinkTextFilter:
    """Hide complete and interrupted thinking blocks across arbitrary chunks."""

    def __init__(self):
        self.pending = ""
        self.closing = ""

    def feed(self, text: str) -> str:
        self.pending += text
        visible = []
        while self.pending:
            if self.closing:
                end = self.pending.lower().find(self.closing)
                if end < 0:
                    self.pending = self.pending[-(len(self.closing) - 1):]
                    break
                self.pending = self.pending[end + len(self.closing):]
                self.closing = ""
                continue
            match = re.search(r"<(think|thinking)>", self.pending, re.I)
            if match:
                visible.append(self.pending[:match.start()])
                self.closing = "</" + match.group(1).lower() + ">"
                self.pending = self.pending[match.end():]
                continue
            keep = 0
            lower = self.pending.lower()
            for tag in ("<think>", "<thinking>"):
                for size in range(1, len(tag)):
                    if lower.endswith(tag[:size]):
                        keep = max(keep, size)
            if keep:
                visible.append(self.pending[:-keep])
                self.pending = self.pending[-keep:]
            else:
                visible.append(self.pending)
                self.pending = ""
            break
        return "".join(visible)

    def finish(self) -> str:
        tail = "" if self.closing else self.pending
        self.pending = ""
        return tail


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------

def _cfg() -> dict:
    return get_config().get("thinking", {}) or {}


def is_enabled() -> bool:
    return bool(_cfg().get("enabled", False))


def get_mode() -> str:
    return _cfg().get("mode", "auto")


def get_monologue_max_tokens() -> int:
    return int(_cfg().get("monologue_max_tokens", _MONOLOGUE_MAX_TOKENS_DEFAULT))


def get_apply_to_proactive() -> bool:
    return bool(_cfg().get("apply_to_proactive", False))


def character_voice_enabled() -> bool:
    return bool(_cfg().get("character_voice", True))


def display_prefer_monologue() -> bool:
    """Bubble order only: prefixed monologue before native reasoning when both exist."""
    return bool(_cfg().get("display_prefer_monologue", True))


# Last monologue attempt metadata only (no body). Process-local, fail-open.
_LAST_MONOLOGUE: dict[str, Any] | None = None

_MONOLOGUE_TIMEOUT = frozenset({"timeout", "budget_exhausted"})
_MONOLOGUE_EMPTY = frozenset({"empty_or_format", "response_format", "empty_body"})


def _stamp_last_monologue(status: str, **meta: Any) -> None:
    """Record redacted last-status; never store monologue text."""
    global _LAST_MONOLOGUE
    now = float(meta.pop("ts", 0) or 0) or time.time()
    row = {
        "status": status,
        "ts": now,
        "iso": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
    }
    for key in (
        "reason", "skip_reason", "error_category", "switch_reason",
        "route_role", "logical_call_id", "model",
    ):
        value = meta.get(key)
        if value:
            row[key] = str(value)[:64]
    _LAST_MONOLOGUE = row


def reset_last_monologue_status() -> None:
    """Test helper."""
    global _LAST_MONOLOGUE
    _LAST_MONOLOGUE = None


def _chat_preset_reasoning_native(char_id: str | None = None) -> bool:
    try:
        from core.model_registry import _get_preset_config, _resolve_preset_name
        mp = _get_preset_config()
        preset_name = _resolve_preset_name("chat", char_id=char_id)
    except ValueError:
        return False
    preset = mp.get("presets", {}).get(preset_name, {})
    return bool(preset.get("reasoning_native", False))


def resolve_configured_mode(*, chat_reasoning_native: bool | None = None) -> str | None:
    """Current thinking path from config + chat preset, ignoring per-turn gates."""
    if not is_enabled():
        return None
    mode = get_mode()
    if mode == "native":
        return "native"
    if mode == "monologue":
        return "monologue"
    if chat_reasoning_native is None:
        chat_reasoning_native = _chat_preset_reasoning_native()
    return "native" if chat_reasoning_native else "monologue"


def describe_monologue_route(*, char_id: str | None = None) -> dict[str, Any]:
    """Live monologue/chat route for admin. Never uses the profile being edited."""
    empty = {
        "effective_profile": "",
        "primary_preset": "",
        "source": "",
        "fallback_preset": "",
        "fallback_source": "off",
        "fallback_refused_reason": "",
        "binding_source": "global",
        "global_profile": "",
        "character_binding": "",
        "chat_preset": "",
        "chat_preset_reasoning_native": False,
        "resolved_mode": None,
        "monologue_active": False,
        "native_uses": "chat",
    }
    try:
        from core.model_registry import (
            _get_preset_config,
            _resolve_preset_name,
            resolve_category_info,
            resolve_routing_info,
        )

        info = resolve_category_info("monologue", char_id=char_id)
        chat_preset = _resolve_preset_name("chat", char_id=char_id)
        mp = _get_preset_config()
        chat_native = bool(mp.get("presets", {}).get(chat_preset, {}).get("reasoning_native", False))
        global_profile = str(mp.get("active_routing") or "")
        binding_source = "global"
        character_binding = ""
        if char_id:
            routing = resolve_routing_info(char_id)
            binding_source = str(routing.get("binding_source") or "global")
            character_binding = str(routing.get("model_routing") or "")
            global_profile = str(routing.get("global_profile") or global_profile)
    except Exception:
        return empty
    resolved = resolve_configured_mode(chat_reasoning_native=chat_native)
    return {
        "effective_profile": str(info.get("effective_profile") or ""),
        "primary_preset": str(info.get("effective_preset") or ""),
        "source": str(info.get("source") or ""),
        "fallback_preset": str(info.get("fallback_preset") or ""),
        "fallback_source": str(info.get("fallback_source") or "off"),
        "fallback_refused_reason": str(info.get("fallback_refused_reason") or ""),
        "binding_source": binding_source,
        "global_profile": global_profile,
        "character_binding": character_binding,
        "chat_preset": chat_preset,
        "chat_preset_reasoning_native": chat_native,
        "resolved_mode": resolved,
        "monologue_active": bool(is_enabled() and resolved == "monologue"),
        "native_uses": "chat",
    }


def _classify_monologue_ledger(row: dict[str, Any]) -> str:
    skip = str(row.get("skip_reason") or "")
    error = str(row.get("error_category") or "")
    hint = str(row.get("output_hint") or "")
    tokens = {skip, error, hint}
    if tokens & _MONOLOGUE_EMPTY:
        return "empty_or_format"
    if row.get("fallback_ok"):
        return "fallback_success"
    if row.get("ok"):
        return "success"
    if tokens & _MONOLOGUE_TIMEOUT:
        return "timeout"
    return "skipped"


def last_monologue_status(*, chat_reasoning_native: bool | None = None) -> dict[str, Any]:
    """Redacted last prefixed-monologue status. Never returns the monologue body."""
    resolved = resolve_configured_mode(chat_reasoning_native=chat_reasoning_native)
    base = {
        "status": "disabled",
        "resolved_mode": resolved,
        "reason": "",
        "ts": 0.0,
        "iso": "",
        "skip_reason": "",
        "error_category": "",
        "switch_reason": "",
        "route_role": "",
        "logical_call_id": "",
        "model": "",
        "body": None,
    }
    if resolved is None:
        base["reason"] = "thinking_disabled"
        return base
    if resolved == "native":
        base["status"] = "not_applicable"
        base["reason"] = "native_uses_chat_preset"
        return base
    ledger = None
    try:
        from core.api_call_log import last_purpose_call
        ledger = last_purpose_call("monologue")
    except Exception:
        ledger = None
    local = _LAST_MONOLOGUE
    chosen: dict[str, Any] | None = None
    if local:
        # Process-local stamp is the source of truth for this process: empty
        # HTTP-success bodies are classified here, not from older ledger rows.
        chosen = local
    else:
        chosen = ledger
    if not chosen:
        base["status"] = "not_applicable"
        base["reason"] = "no_recent_call"
        return base
    status = chosen.get("status") if chosen is local and chosen.get("status") else _classify_monologue_ledger(chosen)
    base.update({
        "status": status,
        "reason": str(chosen.get("reason") or chosen.get("skip_reason") or chosen.get("error_category") or ""),
        "ts": float(chosen.get("ts") or 0),
        "iso": str(chosen.get("iso") or ""),
        "skip_reason": str(chosen.get("skip_reason") or ""),
        "error_category": str(chosen.get("error_category") or ""),
        "switch_reason": str(chosen.get("switch_reason") or ""),
        "route_role": str(chosen.get("route_role") or ""),
        "logical_call_id": str(chosen.get("logical_call_id") or ""),
        "model": str(chosen.get("model") or ""),
        "body": None,
    })
    return base


def _record_empty_monologue_ledger() -> None:
    try:
        from core.api_call_log import append
        append(
            caller="thinking",
            purpose="monologue",
            provider="",
            model="",
            duration_ms=0,
            ok=False,
            skip_reason="empty_or_format",
            error_category="empty_or_format",
            output_hint="empty_body",
            logical_final=True,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 模式解析
# ---------------------------------------------------------------------------

def resolve_effective_mode(mc: "ModelClient", *, is_proactive: bool = False) -> str | None:
    """返回本次调用应走的路线："native" | "monologue" | None（不思考）。

    只对主生成（call_category=="chat"）语义有效，call_category 的过滤由调用方
    （llm_client）负责，本函数只管开关 + mode 语义。
    """
    if not is_enabled():
        return None
    if is_proactive and not get_apply_to_proactive():
        return None
    mode = get_mode()
    if mode == "native":
        return "native"
    if mode == "monologue":
        return "monologue"
    # auto
    return "native" if getattr(mc, "reasoning_native", False) else "monologue"


def build_reasoning_kwargs(
    mc: "ModelClient", *, call_category: str, is_proactive: bool = False
) -> dict[str, Any]:
    """native 路线的 extra_body 逃生舱：绕过 provider 参数白名单，原样透传。

    只在主生成（call_category=="chat"）且解析到 native 路线时生效；其余 call_category
    （intent/probe/summary/...）不受思考开关影响，成本不翻倍。
    """
    if call_category != "chat":
        return {}
    if resolve_effective_mode(mc, is_proactive=is_proactive) != "native":
        return {}
    extra_body = getattr(mc, "reasoning_extra_body", None)
    if not extra_body:
        return {}
    return {"extra_body": extra_body}


# ---------------------------------------------------------------------------
# monologue 路线：前置独白
# ---------------------------------------------------------------------------

_MONOLOGUE_SYSTEM_TEMPLATE = (
    "你是{char_name}。看到对方刚说的话，先在心里想一下："
    "对方想要什么/你此刻的情绪/你打算怎么回。\n"
    "口语化、跳跃、不成段都行，{max_chars}字以内。只输出内心活动本身，不要输出任何前缀、标签或引号。"
)


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    return s if len(s) <= n else s[:n] + "…"


def _last_user_content(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return m.get("content", "") if isinstance(m.get("content"), str) else ""
    return ""


def _recent_history_summary(messages: list[dict]) -> str:
    """从已构建好的 messages 里取 9_history 层最近两轮，拼一句摘要。不发起新的记忆查询。"""
    hist = [m for m in messages if m.get("_layer") == "9_history"][-4:]
    lines = []
    for m in hist:
        content = m.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        speaker = "你" if m.get("role") == "user" else "我"
        lines.append(f"{speaker}：{_truncate(content, 40)}")
    return "\n".join(lines)


def _mood_hint(char_id: str | None) -> str:
    try:
        import json
        from core.data_paths import DEFAULT_CHAR_ID
        from core.mood_text import get_mood_text
        from core.sandbox import get_paths

        mood_raw = json.loads(
            get_paths().mood_state(char_id=char_id or DEFAULT_CHAR_ID).read_text(encoding="utf-8")
        )
        return get_mood_text(mood_raw, subject="你")
    except Exception:
        return ""


async def _run_monologue_call(messages: list[dict], *, char_id: str | None) -> str | None:
    """一次轻量调用产出内心活动。失败/超时/空结果 → None（fail-open，调用方跳过注入）。"""
    try:
        user_msg = _truncate(_last_user_content(messages), 800)
        hist_summary = _recent_history_summary(messages)
        if character_voice_enabled():
            from core.thinking_voice import preview
            voice = preview(char_id)
            # Reuse the turn's frozen persona, not whichever card is active later.
            persona = "\n".join(
                m["content"] for m in messages
                if m.get("_layer") == "2_char_desc" and isinstance(m.get("content"), str)
            )[:6000]
            mono_messages = [
                {"role": "system", "content": (
                    f"以下是此刻的人设，心声保持其中的性格与关系：\n{persona}\n\n"
                    + voice["prompt"]
                    + f"\n只写心声本身，约{get_monologue_max_tokens()}字以内，不写标题、标签或回复正文。"
                )},
                {"role": "user", "content": f"你刚说：{user_msg}\n最近的对话：\n{hist_summary}"},
            ]
        else:
            from core.character_name_provider import get_char_name
            system = _MONOLOGUE_SYSTEM_TEMPLATE.format(
                char_name=get_char_name(char_id), max_chars=get_monologue_max_tokens()
            )
            mono_messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": f"对方刚说：{user_msg}\n你的心情：{_mood_hint(char_id)}\n最近两轮对话：\n{hist_summary}"},
            ]

        from core import llm_client

        reply = await llm_client.chat(
            mono_messages,
            call_category="monologue",
            max_tokens_override=get_monologue_max_tokens(),
            char_id=char_id,
        )
        reply = strip_think_tags(reply) or ""
        reply = reply.strip()
        if not reply:
            _stamp_last_monologue("empty_or_format", reason="empty_body")
            _record_empty_monologue_ledger()
            return None
        try:
            from core.api_call_log import last_purpose_call
            ledger = last_purpose_call("monologue")
        except Exception:
            ledger = None
        status = "fallback_success" if ledger and ledger.get("fallback_ok") else "success"
        _stamp_last_monologue(status, **(ledger or {}))
        try:
            from core.llm_reasoning_store import archive_text
            await archive_text("monologue", reply, purpose="monologue")
        except Exception as archive_exc:
            from core.error_handler import log_error
            log_error("thinking.monologue_archive", archive_exc)
        return reply
    except Exception as e:
        from core.error_handler import log_error
        log_error("thinking.monologue", e)
        try:
            from core.llm_failover import classify_exception
            category, _reason = classify_exception(e)
        except Exception:
            category = type(e).__name__
        if category in _MONOLOGUE_TIMEOUT:
            status = "timeout"
        elif category in _MONOLOGUE_EMPTY:
            status = "empty_or_format"
        else:
            status = "skipped"
        _stamp_last_monologue(status, error_category=category, reason=category)
        return None


def _inject_monologue_message(messages: list[dict], monologue: str) -> list[dict]:
    out = list(messages)
    block = {
        "role": "system",
        "content": f"（你此刻的内心活动，不要直接复述：{monologue}）",
        "_layer": _MONOLOGUE_LAYER,
    }
    if out and out[-1].get("role") == "user":
        out.insert(len(out) - 1, block)
    else:
        out.append(block)
    return out


async def maybe_apply(
    messages: list[dict],
    *,
    call_category: str,
    char_id: str | None = None,
    is_proactive: bool = False,
    mc: "ModelClient | None" = None,
) -> list[dict]:
    """独白与 native 文风提示入口：条件不满足时原样返回 messages（no-op）。

    - 非 call_category=="chat" → no-op（探针/摘要等杂活不思考）。
    - 总开关关闭 / apply_to_proactive 不满足 → no-op，且不触碰 model_registry
      （thinking 关闭是默认状态，不该为了这次判断额外构建一个 ModelClient）。
    - 已包含 11.7_inner_monologue 层 → no-op（tool loop 多步复用同一份 messages 时防重复注入）。
    - native 路线只拼接角色心声文风提示，不增加模型调用。
    - 独白调用失败/超时/空结果 → no-op，fail-open，主生成照常。
    """
    if call_category != "chat":
        return messages
    if not is_enabled():
        return messages
    if is_proactive and not get_apply_to_proactive():
        return messages
    if any(m.get("_layer") == _MONOLOGUE_LAYER for m in messages):
        return messages

    mode = get_mode()
    if mode == "auto":
        if mc is None:
            from core.model_registry import get_model_client
            mc = get_model_client(call_category, char_id=char_id)
        mode = "native" if mc.reasoning_native else "monologue"
    if mode == "native":
        if not character_voice_enabled():
            return messages
        from core.thinking_voice import LAYER, native_message
        if any(m.get("_layer") == LAYER for m in messages):
            return messages
        try:
            block = native_message(char_id)
        except Exception:
            logger.warning("[thinking] character voice unavailable; continuing without hint")
            return messages
        out = list(messages)
        position = len(out) - 1 if out and out[-1].get("role") == "user" else len(out)
        out.insert(position, block)
        from core.observe.prompt_capture import capture_injected_messages
        capture_injected_messages(messages, out)
        return out
    # mode == "monologue"，或 auto 落到 monologue 分支

    monologue = await _run_monologue_call(messages, char_id=char_id)
    if not monologue:
        return messages
    out = _inject_monologue_message(messages, monologue)
    from core.observe.prompt_capture import capture_injected_messages
    capture_injected_messages(messages, out)
    return out
