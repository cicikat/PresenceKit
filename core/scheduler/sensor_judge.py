"""
sensor_judge.py — sensor 候选事件裁决器。

不扮演角色，只做客观打分。事件进，判决出，纯函数，不改任何状态。

调用方式：result = await judge(event)
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Optional

from core.model_registry import get_model_client

logger = logging.getLogger(__name__)

# ── score → intent_tier 映射 ─────────────────────────────────────────────────
def _score_to_tier(score: int) -> str:
    if score < 41:
        return "drop"
    if score <= 55:
        return "weak"
    if score <= 70:
        return "medium"
    if score <= 85:
        return "strong"
    return "must"


_FAILURE: dict = {"score": 0, "reason": "裁决失败", "intent_tier": "drop"}
_BREAKER_THRESHOLD = 3
_BREAKER_COOLDOWN_S = 60.0


@dataclass
class _Breaker:
    failures: int = 0
    open_until: float = 0.0
    half_open_in_flight: bool = False


_BREAKERS: dict[tuple[str, str], _Breaker] = {}


def _failure_category(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timeout" in text:
        return "timeout"
    if status in (401, 403) or "forbidden" in text or "unauthorized" in text:
        return "auth_or_forbidden"
    if status == 429 or "rate limit" in text:
        return "rate_limited"
    if isinstance(status, int) and status >= 500:
        return "upstream_5xx"
    return "transport"


def _breaker_permits(key: tuple[str, str], now: float) -> bool:
    breaker = _BREAKERS.setdefault(key, _Breaker())
    if breaker.open_until <= now:
        if breaker.open_until and breaker.half_open_in_flight:
            return False
        if breaker.open_until:
            breaker.half_open_in_flight = True
        return True
    return False


def _breaker_record(key: tuple[str, str], category: str, *, ok: bool, now: float) -> None:
    breaker = _BREAKERS.setdefault(key, _Breaker())
    breaker.half_open_in_flight = False
    if ok:
        breaker.failures = 0
        breaker.open_until = 0.0
    elif category in {"auth_or_forbidden", "upstream_5xx"}:
        breaker.failures += 1
        if breaker.failures >= _BREAKER_THRESHOLD:
            breaker.open_until = now + _BREAKER_COOLDOWN_S


# ── 字段叙事化辅助 ────────────────────────────────────────────────────────────

def _at_desk_human(secs: int) -> str:
    if secs < 1800:
        return "刚坐下没多久"
    if secs < 3600:
        return "快一个小时"
    if secs < 7200:
        return "一个多小时"
    if secs < 10800:
        return "两个多小时"
    if secs < 14400:
        return "三个多小时"
    return "超过四小时"


def _fmt_minutes(v) -> str:
    return "从未" if v is None else str(v)


# ── Prompt 模板 ───────────────────────────────────────────────────────────────

_SYSTEM = (
    "你是一个事件评估器。你的任务是为生活中的一个瞬间打分，"
    "判断它是否值得一个陪伴者主动开口提及。\n\n"
    "打分参考：\n"
    "- 0-20：无价值，纯噪音，提及只会显得在刷存在感\n"
    "- 21-40：勉强可提，但更像无话找话\n"
    "- 41-60：中性时刻，可提可不提\n"
    "- 61-80：有意义的瞬间，适合开口\n"
    "- 81-100：几乎一定该说点什么\n\n"
    "重要倾向（以下情况应扣分）：\n"
    "- 用户状态为「刚刚还在交流」时（刚刚说过话，不必主动）\n"
    "- 用户状态表明正在专注做事（打扰成本高）\n"
    "- 同类事件刚刚出现过\n"
    "- 深夜时段，除非事件本身就跟「该休息」相关\n"
    "- 事件描述非常普通，缺乏开口契机\n\n"
    "只输出 JSON，不要 markdown，不要任何其他文字：\n"
    '{"score": <0-100 整数>, "reason": "<不超过 20 字的中文理由>"}'
)

_USER_TEMPLATE = """\
【事件】
{event_narrative}

【上下文】
- 现在是本地时间 {local_hour} 点
- 用户状态：{presence_summary}
- 用户当前应用：{focus_app}
- 用户当前在做什么：{focus_title_hint}
- 手机屏幕文本摘要：{screen_text_hint}
- 手机可点击项摘要：{screen_click_hint}
- 用户键击密度：{keystroke_density}\
"""


# ── 对外接口 ─────────────────────────────────────────────────────────────────

async def judge(event: dict) -> dict:
    """
    输入：sensor_events.tick() 的单个事件 dict
    输出：{"score": int, "reason": str, "intent_tier": str,
           "judge_input_prompt": str|None, "judge_output_raw": str|None}

    judge_input_prompt / judge_output_raw 是排障观测字段，下游业务逻辑不读。
    异常：任何情况都返回合法 dict，不抛异常，失败时 intent_tier="drop"。
    """
    event_type = event.get("type", "UNKNOWN")
    narrative  = event.get("narrative", "")
    ctx        = event.get("context", {})

    # Use the semantic presence_summary from the derived PresenceState.
    # Falls back gracefully if ctx comes from an older code path.
    presence_summary = ctx.get("presence_summary") or ""
    if not presence_summary:
        ps_obj = ctx.get("presence_state")
        if ps_obj is not None:
            presence_summary = getattr(ps_obj, "state_summary", "") or ""
    if not presence_summary:
        from core.scheduler.rhythm import is_quiet_sleep_time
        if is_quiet_sleep_time():
            presence_summary = "睡眠保护中，不应主动打扰"
        else:
            presence_summary = ctx.get("presence", "unknown")

    user_text = _USER_TEMPLATE.format(
        event_narrative=narrative,
        local_hour=ctx.get("local_hour", "?"),
        presence_summary=presence_summary,
        focus_app=ctx.get("focus_app", ""),
        focus_title_hint=ctx.get("focus_title_hint", ""),
        screen_text_hint=ctx.get("screen_text_hint", ""),
        screen_click_hint=ctx.get("screen_click_hint", ""),
        keystroke_density=ctx.get("keystroke_density", "未知"),
    )

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",   "content": user_text},
    ]

    audit_prompt = f"[SYSTEM]\n{_SYSTEM}\n\n[USER]\n{user_text}"

    try:
        mc = get_model_client("sensor_judge")
        breaker_key = (mc.name, "sensor_judge")
        started = time.monotonic()
        if not _breaker_permits(breaker_key, started):
            logger.info("[sensor_judge] circuit open preset=%s", mc.name)
            return {**dict(_FAILURE), "judge_input_prompt": audit_prompt, "judge_output_raw": None}
        from core.llm_protocol import create as create_protocol_response
        response = await asyncio.wait_for(create_protocol_response(
            mc, messages, tools=None, tool_choice=None,
            gen_kwargs={"max_tokens": 80, "temperature": 0.1, "timeout": mc.request_timeout_s},
        ), timeout=mc.request_timeout_s)
        raw = response.assistant_text.strip()
        _breaker_record(breaker_key, "", ok=True, now=time.monotonic())
    except Exception as e:
        category = _failure_category(e)
        if "breaker_key" in locals():
            _breaker_record(breaker_key, category, ok=False, now=time.monotonic())
        try:
            from core.api_call_log import append
            append(caller="sensor_judge", purpose="sensor_judge", provider=getattr(locals().get("mc", None), "provider_kind", "unknown"), model=getattr(locals().get("mc", None), "model", "unknown"), duration_ms=int((time.monotonic() - locals().get("started", time.monotonic())) * 1000), ok=False, protocol=getattr(locals().get("mc", None), "api_protocol", ""), error_category=category)
        except Exception:
            pass
        logger.warning("[sensor_judge] LLM 调用失败 event=%s category=%s", event_type, category)
        return {**dict(_FAILURE), "judge_input_prompt": audit_prompt, "judge_output_raw": None}

    # 解析 JSON（容错 markdown 代码块包裹）
    try:
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"[sensor_judge] 非 JSON 响应 event={event_type}: {raw!r}")
        try:
            from core.api_call_log import append
            append(caller="sensor_judge", purpose="sensor_judge", provider=mc.provider_kind, model=mc.model, duration_ms=int((time.monotonic() - started) * 1000), ok=False, protocol=mc.api_protocol, error_category="response_format")
        except Exception:
            pass
        return {**dict(_FAILURE), "judge_input_prompt": audit_prompt, "judge_output_raw": raw}

    score = data.get("score")
    if not isinstance(score, int) or not (0 <= score <= 100):
        logger.warning(
            f"[sensor_judge] score 非法 event={event_type}: score={score!r}"
        )
        return {**dict(_FAILURE), "judge_input_prompt": audit_prompt, "judge_output_raw": raw}
    return {
        "score":                score,
        "reason":               str(data.get("reason", "")),
        "intent_tier":          _score_to_tier(score),
        "judge_input_prompt":   audit_prompt,
        "judge_output_raw":     raw,
    }
