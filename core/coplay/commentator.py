"""
core/coplay/commentator.py — Brief 41: 主动开口。

不在这里直接发言——只注册一个 proposer（同 core/scheduler/triggers/garden_water.py
的 propose_garden_bloom 模式），实际是否发送由 scheduler gating 统一决策
（TriggerState.QUIET + 冷却 + 其他常规闸门）。

D5 静默规则（打扰比说错更劝退）：
  - combat_start（判定为战斗/高强度画面）：直接丢弃，不攒起来延迟触发——
    战斗中任何插话都是打扰，等战斗结束再补发也早就不是那个话题了。
  - idle / death / achievement / save_point / combat_end：可以触发。

频率上限：复用标准 _is_ready/_mark 机制（core/scheduler/loop.py 的
`_COOLDOWNS["coplay_commentary"] = 300` 秒），与 garden_bloom 同款——
execute_prompt() 的 would_mark=["coplay_commentary"] 在真正发送成功后打标记，
gating._proposal_cooldown_ready() 在下一次评估前挡掉未到冷却期的提案。

由头已经在种子 prompt 里（moment 的中文描述），不需要旧记忆带偏，故
recall_policy="none"（同 garden_bloom）。
"""

import logging
import time

from core.coplay.observer import GameMoment

logger = logging.getLogger(__name__)

# 同 garden_bloom 的 TTL 量级：moment 超过这个新鲜度就不再值得主动提起。
MOMENT_FRESHNESS_SECONDS = 10 * 60

# D5：战斗中一律丢弃，不进入候选。
DISCARD_KINDS = frozenset({"combat_start"})

# 数字越大越优先被选中开口（同一 tick 内若有多个候选，挑"最值得说"的一个）。
_KIND_PRIORITY: dict[str, int] = {
    "death": 5,
    "achievement": 4,
    "save_point": 3,
    "combat_end": 2,
    "idle": 1,
    "scene_change": 1,
}

_KIND_LINE: dict[str, str] = {
    "death": "刚看到你在游戏里挂了",
    "achievement": "刚解锁了一个成就",
    "save_point": "刚存了个档",
    "combat_end": "打完了一场高强度的场面",
    "idle": "你好像停下来了，不知道是卡关了还是在想事情",
    "scene_change": "画面好像发生了点变化",
}


def _pick_moment(uid: str) -> GameMoment | None:
    """从 observer 的 moment 队列（peek，不消费）里挑一个"最值得开口"的候选。

    不销毁队列内容——game_state.build_coplay_context_text() 的"最近发生的事"
    也读同一份队列，两者都是只读旁观者，互不冲突。
    """
    from core.coplay import observer

    now = time.time()
    candidates = [
        m for m in observer.peek_moments(uid)
        if m.kind not in DISCARD_KINDS and (now - m.ts) <= MOMENT_FRESHNESS_SECONDS
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda m: (_KIND_PRIORITY.get(m.kind, 0), m.ts))
    return candidates[-1]


def propose_coplay_commentary(ctx: dict | None = None):
    from core.config_loader import get_config
    from core.coplay import session

    uid = str(get_config().get("scheduler", {}).get("owner_id", "") or "")
    if not uid:
        return None

    from core.pipeline_registry import get as _get_pipeline
    pl = _get_pipeline()
    char_id = (pl._active_character_id if pl else None) or "yexuan"

    if not session.is_active(uid, char_id=char_id):
        return None

    moment = _pick_moment(uid)
    if moment is None:
        return None

    # 死亡/成就/存档命中就顺手记一笔 highlight，供 Brief 42 的 session 收尾/
    # game_log 引用——即便 moment 队列后续被更新的动态挤出滚动窗口，高光时刻
    # 也不会跟着丢。
    if moment.kind in ("death", "achievement", "save_point"):
        try:
            from core.coplay.game_state import add_highlight
            state = session.read_state(uid, char_id=char_id)
            game_id = state.get("game_id")
            if game_id:
                add_highlight(uid, game_id, moment.summary, char_id=char_id)
        except Exception:
            logger.exception("[coplay_commentator] add_highlight 失败（不影响主动开口）")

    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.urgency import UrgencyTier, urgency_in_tier

    ratio = 1 - min(1.0, max(0.0, (time.time() - moment.ts) / MOMENT_FRESHNESS_SECONDS))
    return TriggerProposal(
        trigger_name="coplay_commentary",
        urgency=urgency_in_tier(UrgencyTier.REACTIVE, ratio),
        topic_source="mood_match",
        requires_state=[TriggerState.QUIET],
        bypass_state_machine=False,
        execute=_make_execute(moment, char_id),
    )


def _make_execute(moment: GameMoment, char_id: str):
    async def execute(*, dry_run: bool):
        from core.scheduler.execution import execute_prompt

        line = _KIND_LINE.get(moment.kind, moment.summary)
        return await execute_prompt(
            trigger_name="coplay_commentary",
            prompt_factory=lambda: f"（{line}：{moment.summary}）",
            dry_run=dry_run,
            would_mark=["coplay_commentary"],
            reads_cache_ok=True,
            recall_policy="none",
            char_id=char_id,
        )

    return execute


def _register_proposers() -> None:
    from core.scheduler.proposer_registry import register_proposer

    register_proposer("coplay_commentary", propose_coplay_commentary)


_register_proposers()
