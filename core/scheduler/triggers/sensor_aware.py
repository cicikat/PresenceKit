"""
sensor_aware.py — sensor 触发主动开口的实际出口。三层架构：

  sensor_events.tick()
    → sensor_judge.judge()  (客观评分)
    → BehaviorPlanner.plan()  (硬代码行为决策)
  → _pipeline_send(output_mode="return", record_turn=False)  (LLM 生成发言文本)
  → record_assistant_turn(fanout=["desktop", "mobile"])  (统一写入 + 推送)
"""
import logging
import time
from datetime import datetime
from typing import Optional

from core.scheduler import sensor_events, sensor_judge
from core.scheduler.loop import _char_name, _owner_id, _pipeline_send
from core.scheduler.triggers import sensor_aware_audit as _audit
from core.turn_sink import TurnSource, record_assistant_turn

logger = logging.getLogger(__name__)

_PROACTIVE_COOLDOWN_SECS = 8 * 60  # 全局兜底：距上次主动发言 < 8min 不再发
_LAST_DECISION: dict = {
    "ts": None,
    "stage": "never",
    "sent": False,
    "reason": "sensor_aware 尚未运行",
}


def _record_decision(**kwargs) -> None:
    _LAST_DECISION.clear()
    _LAST_DECISION.update({
        "ts": time.time(),
        **kwargs,
    })


def get_last_decision() -> dict:
    return dict(_LAST_DECISION)


def _event_summary(event: dict | None) -> dict | None:
    if not event:
        return None
    ctx = event.get("context", {}) or {}
    return {
        "type": event.get("type", "UNKNOWN"),
        "narrative": event.get("narrative", ""),
        "focus_app": ctx.get("focus_app", ""),
        "focus_title_hint": ctx.get("focus_title_hint", ""),
        "presence": ctx.get("presence", ""),
        "local_hour": ctx.get("local_hour"),
        "screen_app_label": ctx.get("screen_app_label", ""),
        "screen_text_hint": ctx.get("screen_text_hint", ""),
    }

# ── 行为级别阈值 ──────────────────────────────────────────────────────────────

LEVEL_THRESHOLDS = {
    "passive_speak":  35,   # score >= 35
    "soft_hint":      50,
    "attention_grab": 65,
    "direct_act":     80,
}

# ── 行为级别 → WS action_type（None = 只推 channel_message）─────────────────

LEVEL_TO_ACTION_TYPE: dict[str, Optional[str]] = {
    "passive_speak":  None,
    "soft_hint":      "pet_emote",    # 桌宠表情切换（客户端待实现）
    "attention_grab": "notify",       # 系统通知 + 置顶
    "direct_act":     "execute",      # 执行 behavior_id 对应的具体动作
}


# ── 叙事化辅助函数 ────────────────────────────────────────────────────────────

def _time_phrase(local_hour: int) -> str:
    if 5 <= local_hour <= 8:
        return "清晨"
    if 9 <= local_hour <= 11:
        return "上午"
    if 12 <= local_hour <= 13:
        return "中午"
    if 14 <= local_hour <= 17:
        return "下午"
    if 18 <= local_hour <= 20:
        return "傍晚"
    if 21 <= local_hour <= 23:
        return "晚上"
    return "深夜"  # 0-4


def _chat_phrase(minutes: int | None) -> str:
    if minutes is None:
        return "今天还没说过话"
    if minutes < 10:
        return "刚刚"
    if minutes < 30:
        return "几分钟前"
    if minutes < 120:
        return "一两小时前"
    return "已经很久没说话了"


def _at_desk_phrase(secs: int) -> str:
    if secs < 1800:
        return "刚坐下"
    if secs < 3600:
        return "快一小时"
    if secs < 7200:
        return "一个多小时"
    if secs < 10800:
        return "两个多小时"
    if secs < 14400:
        return "三个多小时"
    return "超过四小时"


def _presence_phrase(presence: str) -> str:
    if presence == "active":
        return "在"
    if presence == "idle":
        return "暂时离开"
    return "已经离开很久"


def _presence_narrative(ctx: dict) -> str:
    """
    Return an attribution-aware presence phrase for LLM situation narratives.

    Uses PresenceState.attribution to select the correct semantic framing:
    - FOCUSED_SILENT → "专注做事" (never "没理我/冷落")
    - SLEEPING        → "" (caller should suppress presence line entirely)
    - GENUINELY_ABSENT is the only attribution where absence semantics apply
    Falls back to _presence_phrase() when new context fields are absent.
    """
    from core.scheduler.presence_model import Attribution

    attribution = ctx.get("presence_attribution", "")
    summary     = ctx.get("presence_summary", "")

    if not attribution:
        return _presence_phrase(ctx.get("presence", "unknown"))

    if attribution == Attribution.SLEEPING.value:
        return ""

    return summary or _presence_phrase(ctx.get("presence", "unknown"))


# ── BehaviorPlanner（纯硬代码，模块级函数）──────────────────────────────────

def _resolve_level_and_id(event_type: str, score: int) -> tuple[str, str]:
    """
    映射 event_type × score → (level, behavior_id)。
    部分 event_type 有"不升级"上限，见各分支注释。
    """
    if event_type == "LONG_FOCUS":
        if score < 50: return "passive_speak", "casual_check_in"
        if score < 65: return "soft_hint",     "focus_acknowledged"
        if score < 80: return "attention_grab", "long_focus_remind"
        return             "direct_act",     "force_break_suggest"

    if event_type == "PRESENCE_RETURNED":
        # 封顶 soft_hint：人刚回来不该被通知/置顶骚扰
        if score < 50: return "passive_speak", "welcome_back_soft"
        if score < 65: return "soft_hint",     "welcome_back"
        return             "soft_hint",     "welcome_back_strong"

    if event_type == "PRESENCE_LEFT":
        # 封顶 passive_speak：人不在，做高强度行为无意义
        if score < 50: return "passive_speak", "noticed_leaving"
        return             "passive_speak", "noticed_leaving_warm"

    if event_type == "LONG_AT_DESK":
        if score < 50: return "passive_speak", "sit_long_soft"
        if score < 65: return "soft_hint",     "sit_long_concern"
        if score < 80: return "attention_grab", "sit_long_remind"
        return             "direct_act",     "sit_long_force"

    if event_type == "LATE_NIGHT_ACTIVE":
        if score < 50: return "passive_speak", "late_night_soft"
        if score < 65: return "soft_hint",     "late_night_concern"
        if score < 80: return "attention_grab", "late_night_remind"
        return             "direct_act",     "late_night_lock_hint"

    if event_type == "SILENT_TOGETHER":
        # 封顶 soft_hint：沉默被破不该靠强通知
        if score < 50: return "passive_speak", "silent_companionable"
        if score < 65: return "soft_hint",     "silent_seeking"
        return             "soft_hint",     "silent_seeking_strong"

    if event_type == "FOCUS_SCATTERED":
        # 封顶 soft_hint：本来就分心了，不能再扰
        if score < 50: return "passive_speak", "scattered_observe"
        return             "soft_hint",     "scattered_concern"

    if event_type == "APP_CATEGORY_CHANGED":
        # 全档 passive_speak：只是注意到，不该升级
        if score < 50: return "passive_speak", "noticed_switch"
        return             "passive_speak", "noticed_switch_warm"

    return "passive_speak", "generic_speak"


def plan(event: dict, score: int) -> Optional[dict]:
    """
    根据 event["type"] 和 score 决定 candidate_behavior。
    score < 35 返回 None（丢弃）。

    返回结构：
      {
        "level":       str,   # "passive_speak" | "soft_hint" | "attention_grab" | "direct_act"
        "behavior_id": str,   # 语义标签
        "facts":       dict,  # event["context"] 透传
        "narrative":   str,   # event["narrative"] 透传
      }
    """
    if score < LEVEL_THRESHOLDS["passive_speak"]:
        return None

    level, behavior_id = _resolve_level_and_id(event.get("type", "UNKNOWN"), score)
    return {
        "level":       level,
        "behavior_id": behavior_id,
        "facts":       event.get("context", {}),
        "narrative":   event.get("narrative", ""),
    }


# ── 情境描述生成 ──────────────────────────────────────────────────────────────

_LEVEL_OPENERS = {
    "passive_speak":  "（{char}看着她，想说点什么。",
    "soft_hint":      "（{char}觉得该跟她说一句。",
    "attention_grab": "（{char}坐不住了，得让她注意到。",
    "direct_act":     "（{char}决定动一下。",
}


def build_situation_narrative(behavior: dict) -> str:
    """
    把 behavior 转成括号叙事，作为 _pipeline_send 的 prompt。
    LLM 看到后用叶瑄的语气产出一句回应。
    """
    char = _char_name()
    level = behavior["level"]
    ctx = behavior["facts"]
    narrative = behavior["narrative"]

    local_hour   = int(ctx.get("local_hour", datetime.now().hour))
    focus_app    = ctx.get("focus_app", "")
    title_hint   = ctx.get("focus_title_hint", "")
    at_desk_secs = int(ctx.get("continuous_at_desk_seconds") or 0)

    time_str     = _time_phrase(local_hour)
    presence_str = _presence_narrative(ctx)  # attribution-aware, never "没理我"
    at_desk_str  = _at_desk_phrase(at_desk_secs)

    if focus_app:
        from core.scheduler.sensor_events import _app_category as _get_app_category
        _APP_CATEGORY_PHRASES = {
            "work":     "在忙工作上的事",
            "leisure":  "在放松",
            "takeout":  "在点餐",
            "shopping": "在逛东西",
        }
        _cat = _get_app_category(focus_app)
        focus_str = _APP_CATEGORY_PHRASES.get(_cat, "在做自己的事")
    else:
        focus_str = ""

    state_parts = []
    if presence_str:
        state_parts.append(presence_str)
    if focus_str:
        state_parts.append(focus_str)
    state_line = "，".join(state_parts) if state_parts else "她的状态未知"

    opener = _LEVEL_OPENERS.get(level, "（{char}想说点什么。").format(char=char)

    return (
        f"{opener}现在是{time_str}，{state_line}，\n"
        f"已经{at_desk_str}了。{narrative}）"
    )


# ── Action Packet 组装 ────────────────────────────────────────────────────────

def build_action_packet(behavior: dict, reply_text: str) -> Optional[dict]:
    """
    返回传给 channel behavior 的 action dict。
    passive_speak 返回 None（只推 channel_message，无 action）。
    """
    action_type = LEVEL_TO_ACTION_TYPE.get(behavior["level"])
    if action_type is None:
        return None

    if action_type == "pet_emote":
        params: dict = {"behavior_id": behavior["behavior_id"]}
    elif action_type == "notify":
        params = {"text": reply_text, "bring_to_front": True}
    elif action_type == "execute":
        params = {"behavior_id": behavior["behavior_id"]}
    else:
        params = {}

    return {"action_type": action_type, "params": params}


# ── 对外接口 ─────────────────────────────────────────────────────────────────

async def handle_tick() -> None:
    """由 scheduler loop 调用。完整链路见模块文档。"""
    snapshot: dict = {
        "tick_at":                time.time(),
        "candidates":             None,
        "picked_event":           None,
        "judge_input_prompt":     None,
        "judge_output_raw":       None,
        "judge_score":            None,
        "judge_reason":           None,
        "tier":                   None,
        "candidate_behavior":     None,
        "pipeline_send_prompt":   None,
        "pipeline_send_reply":    None,
        "action_packet":          None,
        "final_stage":            None,
        "cooldown_remaining_seconds": None,
    }
    try:
        # ── 1. sensor_events.tick() ───────────────────────────────────────────
        try:
            candidates = sensor_events.tick()
        except Exception:
            logger.exception("[sensor_aware] sensor_events.tick() 异常，跳过本 tick")
            _record_decision(
                stage="tick_error",
                sent=False,
                reason="sensor_events.tick 异常",
                candidates_count=0,
            )
            snapshot["final_stage"] = "tick_error"
            return

        snapshot["candidates"] = [_event_summary(e) for e in candidates]

        if not candidates:
            logger.debug("[sensor_aware] candidates=0 本 tick 无候选事件")
            _record_decision(
                stage="no_candidates",
                sent=False,
                reason="本 tick 没有候选事件",
                candidates_count=0,
            )
            snapshot["final_stage"] = "silent"
            return

        # ── 2. sensor_judge.judge() ───────────────────────────────────────────
        scored: list[tuple[dict, dict]] = []
        for ev in candidates:
            try:
                result = await sensor_judge.judge(ev)
            except Exception:
                logger.exception("[sensor_aware] judge 异常 event=%s", ev.get("type"))
                continue
            scored.append((ev, result))

        if not scored:
            logger.info("[sensor_aware] candidates=%d 全部裁决失败，放弃", len(candidates))
            _record_decision(
                stage="judge_failed",
                sent=False,
                reason="候选事件全部裁决失败",
                candidates_count=len(candidates),
            )
            snapshot["final_stage"] = "silent"
            return

        scored.sort(key=lambda x: x[1]["score"], reverse=True)
        best_event, best_result = scored[0]
        best_type  = best_event.get("type", "UNKNOWN")
        best_score = best_result["score"]
        best_tier  = best_result["intent_tier"]

        snapshot["picked_event"]       = _event_summary(best_event)
        snapshot["judge_input_prompt"] = best_result.get("_audit_prompt")
        snapshot["judge_output_raw"]   = best_result.get("_audit_raw_response")
        snapshot["judge_score"]        = best_score
        snapshot["judge_reason"]       = best_result.get("reason")
        snapshot["tier"]               = best_tier

        # ── 3. BehaviorPlanner.plan() ─────────────────────────────────────────
        behavior = plan(best_event, best_score)
        snapshot["candidate_behavior"] = behavior

        if behavior is None:
            logger.info(
                "[sensor_aware] candidates=%d picked=%s score=%d tier=%s "
                "behavior=None sent=false",
                len(candidates), best_type, best_score, best_tier,
            )
            _record_decision(
                stage="silent",
                sent=False,
                reason=best_result.get("reason", "低于主动开口阈值"),
                candidates_count=len(candidates),
                picked=_event_summary(best_event),
                score=best_score,
                tier=best_tier,
                behavior=None,
            )
            snapshot["final_stage"] = "silent"
            return

        # 全局兜底：8 分钟内已有主动发言 → 拦截
        last_proactive = sensor_events.get_last_proactive_at()
        if last_proactive is not None and (time.time() - last_proactive) < _PROACTIVE_COOLDOWN_SECS:
            remaining = round(_PROACTIVE_COOLDOWN_SECS - (time.time() - last_proactive))
            logger.info(
                "[sensor_aware] candidates=%d picked=%s score=%d tier=%s "
                "level=%s behavior_id=%s proactive_cooldown=blocked sent=false",
                len(candidates), best_type, best_score, best_tier,
                behavior["level"], behavior["behavior_id"],
            )
            _record_decision(
                stage="cooldown_blocked",
                sent=False,
                reason="8 分钟主动发言冷却中",
                candidates_count=len(candidates),
                picked=_event_summary(best_event),
                score=best_score,
                tier=best_tier,
                behavior=behavior,
                cooldown_remaining_seconds=remaining,
            )
            snapshot["final_stage"] = "cooldown_blocked"
            snapshot["cooldown_remaining_seconds"] = remaining
            return

        # ── 4. _pipeline_send 入参组装 ────────────────────────────────────────
        prompt = build_situation_narrative(behavior)
        snapshot["pipeline_send_prompt"] = prompt

        logger.info(
            "[sensor_aware] candidates=%d picked=%s score=%d tier=%s "
            "level=%s behavior_id=%s proactive_cooldown=ok sent=true",
            len(candidates), best_type, best_score, best_tier,
            behavior["level"], behavior["behavior_id"],
        )

        # ── 5. _pipeline_send 返回 ────────────────────────────────────────────
        try:
            reply = await _pipeline_send(
                prompt,
                trigger_name="sensor_aware",
                output_mode="return",
                record_turn=False,
            )
        except Exception:
            logger.error("[sensor_aware] _pipeline_send 失败，不更新冷却")
            logger.exception("[sensor_aware] _pipeline_send 异常详情")
            _record_decision(
                stage="pipeline_error",
                sent=False,
                reason="LLM pipeline 失败",
                candidates_count=len(candidates),
                picked=_event_summary(best_event),
                score=best_score,
                tier=best_tier,
                behavior=behavior,
            )
            snapshot["final_stage"] = "pipeline_error"
            return

        snapshot["pipeline_send_reply"] = reply

        if not reply:
            logger.warning("[sensor_aware] _pipeline_send 返回空 reply，不更新冷却")
            _record_decision(
                stage="empty_reply",
                sent=False,
                reason="LLM 返回空内容",
                candidates_count=len(candidates),
                picked=_event_summary(best_event),
                score=best_score,
                tier=best_tier,
                behavior=behavior,
            )
            snapshot["final_stage"] = "empty_reply"
            return

        # ── 6. Action Packet 组装 + 统一写入/推送 ────────────────────────────
        try:
            action = build_action_packet(behavior, reply)
            snapshot["action_packet"] = action
            payload = {"behavior": action} if action is not None else None
            from core.write_envelope import stamp_sensor
            result = await record_assistant_turn(
                assistant_text=reply,
                uid=_owner_id(),
                source=TurnSource.SENSOR,
                trigger_name="sensor_aware",
                fanout=["desktop", "mobile"],
                payload=payload,
                envelope=stamp_sensor(),
            )
            if result.fanout_failures:
                logger.warning("[sensor_aware] fanout 部分失败: %s", result.fanout_failures)
        except Exception:
            logger.error("[sensor_aware] turn_sink 推送失败，不更新冷却")
            logger.exception("[sensor_aware] turn_sink 异常详情")
            _record_decision(
                stage="turn_sink_error",
                sent=False,
                reason="统一写入/推送失败",
                candidates_count=len(candidates),
                picked=_event_summary(best_event),
                score=best_score,
                tier=best_tier,
                behavior=behavior,
            )
            snapshot["final_stage"] = "turn_sink_error"
            return

        sensor_events.mark_proactive_sent()
        _record_decision(
            stage="sent",
            sent=True,
            reason="已广播到活跃通道",
            candidates_count=len(candidates),
            picked=_event_summary(best_event),
            score=best_score,
            tier=best_tier,
            behavior=behavior,
            reply_preview=reply[:120],
        )
        # ── 7. 最终阶段 ───────────────────────────────────────────────────────
        snapshot["final_stage"] = "sent"

    finally:
        try:
            _audit.record(snapshot)
        except Exception:
            logger.exception("[sensor_aware] audit.record() 异常")
