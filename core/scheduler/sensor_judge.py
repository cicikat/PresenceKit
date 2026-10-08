"""
sensor_judge.py — sensor 候选事件裁决器。

不扮演角色，只做客观打分。事件进，判决出，纯函数，不改任何状态。

调用方式：result = await judge(event)
"""
import json
import logging
from typing import Optional

from core.llm_failover import (
    PreparedAttempt,
    breaker_permits,
    clear_breakers,
    execute_create,
)
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
_BREAKERS = None  # kept for tests that clear the shared breaker via this name


def _failure_category(exc: Exception) -> str:
    from core.llm_failover import breaker_category_for, classify_exception

    category, reason = classify_exception(exc)
    if reason == "geo_blocked":
        return "geo_blocked"
    mapped = breaker_category_for(category)
    if mapped == "upstream_rate_limited":
        return "rate_limited"
    if mapped == "connection_error":
        return "transport"
    return mapped


def _breaker_permits(key: tuple[str, str], now: float) -> bool:
    return breaker_permits(key[0], key[1], now=now)


def _breaker_record(key: tuple[str, str], category: str, *, ok: bool, now: float) -> None:
    from core.llm_failover import breaker_record

    breaker_record(key[0], key[1], category, ok=ok, now=now)


class _BreakerProxy:
    """Compatibility shim so existing tests can still ``_BREAKERS.clear()``."""

    def clear(self) -> None:
        clear_breakers()


_BREAKERS = _BreakerProxy()


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

def _log_judge_failure(event_type: str, category: str) -> None:
    """Judge failures are fail-closed (the event is dropped) and arrive in long streaks when
    the upstream is down: count every one, log the first and every 20th per category.

    Deliberately not sampled: each call is one real sensor event, and skipping the call would
    silently change which proactive decisions get made, not merely reduce noise (工单 F).
    """
    from core.runtime_signal_observability import record_and_should_log

    if record_and_should_log(
        category="model_quality", code="sensor_judge_failed", status="attention",
        context={"event": str(event_type)[:40], "reason": str(category)[:60]},
    ):
        logger.warning("[sensor_judge] LLM 调用失败 event=%s category=%s", event_type, category)


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
        from core.decision_contract import DecisionRequest, Question, prepare as prepare_decision
        decision = DecisionRequest('sensor_judge', user_text, {
            'score': Question('score', _SYSTEM.split('只输出 JSON')[0], (
                '无价值或纯噪音；打扰成本极高', '勉强可提，更像无话找话',
                '中性时刻，可提可不提', '有意义的瞬间，适合开口', '几乎一定应该说点什么',
            ), scale=(0, 100)),
            'recent_chat': Question('noul', '用户是否刚刚与陪伴者交流过？', threshold=.65),
            'focused': Question('noul', '当前证据是否表明用户正在专注做事？', threshold=.65),
            'late_night': Question('noul', '本地时间是否是应避免打扰的深夜？', threshold=.65),
            'repeated': Question('noul', '证据是否表明同类事件刚出现过？未知时回答否。', threshold=.65),
        }, constants={'reason': '结构化评分'})

        def _prepare(_target) -> PreparedAttempt:
            from core.prompt_layer import sanitize_messages
            from core.prompt_style import apply_prompt_style

            return prepare_decision(_target, decision,
                messages=messages,
                gen_kwargs={
                    "max_tokens": 80,
                    "temperature": 0.1,
                    "timeout": _target.request_timeout_s,
                },
            )

        def _validate_json(value) -> None:
            text = (getattr(value, "assistant_text", None) or "").strip()
            if text.startswith("```"):
                text = text.strip("`").lstrip("json").strip()
            data = json.loads(text)
            score = data.get("score")
            if not isinstance(score, int) or not (0 <= score <= 100):
                raise ValueError("sensor_judge score invalid")

        outcome = await execute_create(
            call_category="sensor_judge",
            prepare=_prepare,
            caller="sensor_judge",
            primary_mc=mc,
            validate=_validate_json,
            reset_breaker_on_http_success=False,
        )
        if not outcome.ok:
            if outcome.skip_reason == "breaker_open":
                logger.info("[sensor_judge] circuit open preset=%s", (outcome.mc or mc).name)
            elif outcome.error_category == "response_format":
                raw_fail = ""
                if outcome.value is not None:
                    raw_fail = (getattr(outcome.value, "assistant_text", None) or "")
                logger.warning(
                    "[sensor_judge] 非 JSON 响应 event=%s chars=%s",
                    event_type,
                    len(raw_fail),
                )
                return {
                    **dict(_FAILURE),
                    "judge_input_prompt": audit_prompt,
                    "judge_output_raw": raw_fail or None,
                }
            else:
                _log_judge_failure(event_type, outcome.error_category or outcome.skip_reason or "unknown")
            return {**dict(_FAILURE), "judge_input_prompt": audit_prompt, "judge_output_raw": None}
        raw = outcome.value.assistant_text.strip()
        used = outcome.mc or mc
    except Exception as e:
        category = _failure_category(e)
        _log_judge_failure(event_type, category)
        return {**dict(_FAILURE), "judge_input_prompt": audit_prompt, "judge_output_raw": None}

    parsed = raw
    if parsed.startswith("```"):
        parsed = parsed.strip("`").lstrip("json").strip()
    data = json.loads(parsed)
    from core.llm_failover import confirm_business_success
    confirm_business_success(used.name, "sensor_judge")
    return {
        "score":                int(data["score"]),
        "reason":               str(data.get("reason", "")),
        "intent_tier":          _score_to_tier(int(data["score"])),
        "judge_input_prompt":   audit_prompt,
        "judge_output_raw":     raw,
    }
