"""
core/memory/user_hidden_state_reality_signals.py
=================================================
Brief 88 — user_hidden_state 现实侧接线：全量信号映射（§1 对话侧 + §3 body_memory）.

Wired from core/pipeline.py::post_process_slow, right after detect_emotion
(emotion 可用). Judgment uses only data already on hand — tags / emotion /
prior-turn gap / constant word lists — zero LLM calls.

Envelope contract (deliberate, see cc-tasks/88):
  §1（中期层：sensitivity.current / touch_need.deficit）永远用本模块自建的
  stamp_user_chat()，不看调用方传入的 envelope —— 这套状态机自带阻尼
  （MAX_NUDGE_PER_EVENT 封顶 / current 持续回归 baseline），激进映射被吸收的
  是幅度不是方向。唯一硬闸门是 trigger_name：trigger 轮（scheduler 发起）零参与。
  §3（长期层：body_memory，经 integrate_body_cue_and_save）遵守调用方传入的
  envelope.can_write_memory —— 长期层落盘更保守。

Fail-open: any exception here is caught and logged; it must never affect the
main reality reply path.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.data_paths import DEFAULT_CHAR_ID
from core.write_envelope import stamp_user_chat

logger = logging.getLogger(__name__)

# ── 常量词表（Hard Rule 9：行为词，非角色名，无插值问题）─────────────────────
# 初值，观察期后可调。

_COMPANIONSHIP_WORDS: tuple[str, ...] = ("在吗", "陪我", "想你", "好想你", "抱抱我")
"""SEEK_COMPANIONSHIP 判定 (b)：陪伴意图词表。"""

_AFFECTION_WORDS: tuple[str, ...] = ("抱抱", "贴贴", "摸摸", "亲亲", "牵手")
"""AFFECTION_EXPRESSED 判定：亲昵表达词表。"""

_SEEK_GAP_SECONDS: float = 6 * 3600
"""SEEK_COMPANIONSHIP 判定 (a)：距上次 owner 轮的开场轮 gap 阈值。"""

_COMFORT_USER_TAGS: frozenset[str] = frozenset({"emotion.down", "emotion.indirect", "topic.health"})
_COMFORT_ASSISTANT_EMOTIONS: frozenset[str] = frozenset({"gentle", "sad"})

_BODY_TOPIC_TAGS: frozenset[str] = frozenset({"body_intimate", "physical_closeness", "query.body_state"})
"""BODY_TOPIC 触发标签集 —— 与 Dream D4.5 门控（body_intimate/physical_closeness）
语义一致，另加 query.body_state（现实侧问询类身体话题，Dream 侧不需要）。"""

_BODY_CUE_TAGS: frozenset[str] = frozenset({"body_intimate", "physical_closeness"})
"""§3 body_memory cue 抽取标签集 —— 与 Dream D4.5 门控完全同集（query.body_state
的问询类词汇不是可条件化的身体线索，不纳入 cue 抽取）。"""

BODY_CUE_STRENGTH: float = 0.3
"""§3 integrate_body_cue_and_save 的 strength 参数，初值，观察期后可调。"""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _first_hit(text: str, words: tuple[str, ...]) -> str | None:
    for w in words:
        if w in text:
            return w
    return None


def _first_cue_tag_word(content: str, tags: set[str]) -> str | None:
    """从命中的 body_intimate/physical_closeness 标签规则里找出实际命中的关键词，
    作为 body_memory 的 cue（与 tag_rules.TAG_RULES 的 patterns 保持单一数据源）。
    """
    from core.tag_rules import TAG_RULES

    for rule in TAG_RULES:
        if rule.tag in _BODY_CUE_TAGS and rule.tag in tags:
            for pattern in rule.patterns:
                if pattern in content:
                    return pattern
    return None


def _fire_event(uid, event_type, write_envelope, now: str, char_id: str, triggered: list[str], ref: str = "") -> None:
    try:
        from core.memory.user_hidden_state_integrator import integrate_event_and_save

        _, result = integrate_event_and_save(uid, event_type, write_envelope, now, char_id=char_id, ref=ref)
        if result.accepted:
            triggered.append(event_type.value)
    except Exception:
        logger.warning(
            "[user_hidden_state_reality_signals] event=%s failed uid=%s", event_type, uid, exc_info=True
        )


def process_reality_turn(
    *,
    uid: str,
    content: str,
    tags: set[str],
    assistant_emotion: str,
    trigger_name: str,
    envelope,
    prior_gap_seconds: float | None,
    char_id: str = DEFAULT_CHAR_ID,
    turn_id: str = "",
) -> list[str]:
    """判定并落地本轮现实对话对 user_hidden_state 的全部信号映射。

    trigger 轮（trigger_name 非空）不参与本函数的任何事件——scheduler 发起的
    发言不是 owner 的现实行为信号。

    Returns:
        本轮实际被 integrator 接受（accepted）的 event_type.value 列表，供调用方
        观测/日志用；异常或无命中均返回空列表。
    """
    if trigger_name:
        return []

    triggered: list[str] = []
    try:
        from core.memory.user_hidden_state_integrator import RealityEventType

        now = _utcnow_iso()
        write_envelope = stamp_user_chat()

        # SEEK_COMPANIONSHIP：(a) 开场轮 gap ≥ 6h，或 (b) 陪伴意图词表命中其一即触发
        is_opening = prior_gap_seconds is not None and prior_gap_seconds >= _SEEK_GAP_SECONDS
        if is_opening or _first_hit(content, _COMPANIONSHIP_WORDS) is not None:
            _fire_event(uid, RealityEventType.SEEK_COMPANIONSHIP, write_envelope, now, char_id, triggered, ref=turn_id)

        # RECEIVED_COMFORT：用户消息 tags 命中安抚相关 且 本轮 assistant emotion 为 gentle/sad
        if tags & _COMFORT_USER_TAGS and assistant_emotion in _COMFORT_ASSISTANT_EMOTIONS:
            _fire_event(uid, RealityEventType.RECEIVED_COMFORT, write_envelope, now, char_id, triggered, ref=turn_id)

        # BODY_TOPIC：tags 命中 body_intimate/physical_closeness/query.body_state
        body_topic_hit = bool(tags & _BODY_TOPIC_TAGS)
        if body_topic_hit:
            _fire_event(uid, RealityEventType.BODY_TOPIC, write_envelope, now, char_id, triggered, ref=turn_id)

        # AFFECTION_EXPRESSED：亲昵表达词表命中
        affection_word = _first_hit(content, _AFFECTION_WORDS)
        if affection_word is not None:
            _fire_event(uid, RealityEventType.AFFECTION_EXPRESSED, write_envelope, now, char_id, triggered, ref=turn_id)

        # §3 body_memory 长期层：AFFECTION_EXPRESSED / BODY_TOPIC 命中且调用方 envelope
        # 允许写记忆时，才以命中词为 cue 强化长期条件化线索。这里看的是调用方原始
        # envelope（不是上面固定构造的 stamp_user_chat()）——长期层落盘更保守。
        if envelope.can_write_memory:
            from core.memory.user_hidden_state_integrator import integrate_body_cue_and_save

            if affection_word is not None:
                try:
                    integrate_body_cue_and_save(
                        uid, affection_word, assistant_emotion, BODY_CUE_STRENGTH,
                        write_envelope, now, char_id=char_id,
                    )
                except Exception:
                    logger.warning(
                        "[user_hidden_state_reality_signals] body_cue(affection) failed uid=%s",
                        uid, exc_info=True,
                    )
            if body_topic_hit:
                cue_word = _first_cue_tag_word(content, tags)
                if cue_word:
                    try:
                        integrate_body_cue_and_save(
                            uid, cue_word, assistant_emotion, BODY_CUE_STRENGTH,
                            write_envelope, now, char_id=char_id,
                        )
                    except Exception:
                        logger.warning(
                            "[user_hidden_state_reality_signals] body_cue(body_topic) failed uid=%s",
                            uid, exc_info=True,
                        )
    except Exception:
        logger.warning(
            "[user_hidden_state_reality_signals] process_reality_turn failed uid=%s", uid, exc_info=True
        )

    return triggered
