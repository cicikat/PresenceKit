"""
sensor_aware.py — sensor 候选事实的 signal-first 出口。

  sensor_events.tick()
    → sensor_judge.judge()  (客观评分)
    → BehaviorPlanner.plan()  (硬代码行为决策)
    → emit_trigger_signal()  (入 autonomy signal store；不直发)
"""
import logging
import time
from typing import Optional

from core.scheduler import sensor_events, sensor_judge
from core.scheduler.loop import _owner_id
from core.scheduler.triggers import sensor_aware_audit as _audit

logger = logging.getLogger(__name__)

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
            # 电平（本 tick 无有效结果，非状态转换）：降 DEBUG（Brief 54-C）。
            logger.debug("[sensor_aware] candidates=%d 全部裁决失败，放弃", len(candidates))
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
        snapshot["judge_input_prompt"] = best_result.get("judge_input_prompt")
        snapshot["judge_output_raw"]   = best_result.get("judge_output_raw")
        snapshot["judge_score"]        = best_score
        snapshot["judge_reason"]       = best_result.get("reason")
        snapshot["tier"]               = best_tier

        # ── 3. BehaviorPlanner.plan() ─────────────────────────────────────────
        behavior = plan(best_event, best_score)
        snapshot["candidate_behavior"] = behavior

        if behavior is None:
            # 低于主动开口阈值：每 tick 都可能命中此分支，非状态转换，降 DEBUG。
            logger.debug(
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

        # ── A3/B: sensor_aware 纳管 —— ProactiveLedger 全局间隔+预算 + DND ──────
        # handle_tick() 旁路 gating._decide()：无状态机。judge 之后、入队之前做
        # ledger/DND 只读闸门；真正送达由 autonomy talk_owner 记账。
        from core.scheduler.proactive_ledger import can_send as _ledger_can_send
        from core.scheduler.triggers.dnd import is_dnd

        _ledger_ok, _ledger_reason = _ledger_can_send(
            "sensor_aware",
            priority="normal",
            uid=_owner_id(),
        )
        if not _ledger_ok:
            # 全局间隔/预算拦截：持续状态，非转换，降 DEBUG。
            logger.debug(
                "[sensor_aware] candidates=%d picked=%s score=%d tier=%s "
                "level=%s behavior_id=%s %s sent=false",
                len(candidates), best_type, best_score, best_tier,
                behavior["level"], behavior["behavior_id"], _ledger_reason,
            )
            _record_decision(
                stage="global_gap_blocked" if _ledger_reason == "gap_not_elapsed" else "daily_budget_blocked",
                sent=False,
                reason="全局主动发言间隔未到" if _ledger_reason == "gap_not_elapsed" else "当日主动发言预算已用完",
                candidates_count=len(candidates),
                picked=_event_summary(best_event),
                score=best_score,
                tier=best_tier,
                behavior=behavior,
            )
            snapshot["final_stage"] = "global_gap_blocked"
            return

        _dnd_oid = _owner_id()
        if _dnd_oid and is_dnd(_dnd_oid):
            # DND 期间每 tick 都会命中，持续状态非转换，降 DEBUG。
            logger.debug(
                "[sensor_aware] candidates=%d picked=%s score=%d tier=%s "
                "level=%s behavior_id=%s dnd_blocked sent=false",
                len(candidates), best_type, best_score, best_tier,
                behavior["level"], behavior["behavior_id"],
            )
            _record_decision(
                stage="dnd_blocked",
                sent=False,
                reason="请勿打扰中",
                candidates_count=len(candidates),
                picked=_event_summary(best_event),
                score=best_score,
                tier=best_tier,
                behavior=behavior,
            )
            snapshot["final_stage"] = "dnd_blocked"
            return

        _sensor_oid = _owner_id()
        try:
            from core.scheduler.loop import _active_char_id_or_none
            _sensor_char = _active_char_id_or_none()
        except Exception:
            _sensor_char = None
        if _sensor_oid and _sensor_char:
            from core.autonomy.signal_adapters import emit_trigger_signal
            emit_trigger_signal(
                _sensor_oid,
                _sensor_char,
                "sensor_aware",
                evidence=[{
                    "fact": "sensor_candidate",
                    "event_type": best_type,
                    "score": best_score,
                    "tier": best_tier,
                    "behavior_id": behavior.get("behavior_id"),
                }],
                reason="A bounded sensor state change is eligible for autonomy evaluation.",
                priority=min(1.0, max(0.1, best_score / 100.0)),
                urgency=min(1.0, max(0.1, best_score / 100.0)),
            )
        snapshot["final_stage"] = "signal_queued"

    finally:
        try:
            _audit.record(snapshot)
        except Exception:
            logger.exception("[sensor_aware] audit.record() 异常")
