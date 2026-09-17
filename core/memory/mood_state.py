"""
mood_state — 角色情绪状态持久化。
scope = character only，无 uid 维度。
情绪不硬切，每轮做加权漂移：新情绪占30%，旧情绪占70%。
"""
import json
import logging
import time
from pathlib import Path

from core.sandbox import get_paths
from core.safe_write import safe_write_json
from core.llm_output_validator import record_failure, is_paused, reset
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

# 情绪强度映射（用于漂移计算）
EMOTION_INTENSITY = {
    "neutral":   0.0,
    "gentle":    0.3,
    "thinking":  0.2,
    "happy":     0.6,
    "sad":       0.6,
    "surprised": 0.7,
    "angry":     0.8,
    "sleepy":    0.3,
    "yandere":   1.0,
}

# 情绪相邻关系（漂移时优先往相邻情绪过渡，不直接跳跃）
EMOTION_NEIGHBORS = {
    "neutral":   ["gentle", "thinking", "sleepy"],
    "gentle":    ["neutral", "happy", "sad"],
    "thinking":  ["neutral", "gentle"],
    "happy":     ["gentle", "surprised", "neutral"],
    "sad":       ["gentle", "neutral", "sleepy"],
    "surprised": ["happy", "neutral"],
    "angry":     ["surprised", "neutral"],
    "sleepy":    ["neutral", "sad"],
    "yandere":   ["surprised", "angry"],
}

_DEFAULT = {
    "current": "neutral",
    "intensity": 0.0,
    "previous": "neutral",
    "updated_at": 0.0,
}


def _read_path(char_id: str = DEFAULT_CHAR_ID) -> Path:
    return get_paths().mood_state(char_id=char_id)


def _write_path(char_id: str = DEFAULT_CHAR_ID) -> Path:
    return get_paths().mood_state(char_id=char_id)


def load(*, char_id: str = DEFAULT_CHAR_ID) -> dict:
    try:
        return json.loads(_read_path(char_id).read_text(encoding="utf-8"))
    except Exception:
        return dict(_DEFAULT)


def save(state: dict, *, char_id: str = DEFAULT_CHAR_ID) -> None:
    if is_paused("mood_state"):
        logger.warning("[mood_state] 写入已暂停（连续失败过多），跳过本次 save")
        return

    if (
        not isinstance(state.get("current"), str)
        or not isinstance(state.get("previous"), str)
        or not isinstance(state.get("intensity"), (int, float))
        or not (0.0 <= float(state["intensity"]) <= 1.0)
    ):
        record_failure("mood_state", str(state), "")
        return

    safe_write_json(_write_path(char_id), state)
    reset("mood_state")


def update(
    new_emotion: str,
    new_intensity: float | None = None,
    source: str = "detect",
    *,
    char_id: str = DEFAULT_CHAR_ID,
    force: bool = False,
) -> dict:
    """
    根据本轮检测到的情绪，做加权漂移更新情绪状态。
    新情绪占30%，旧情绪占70%。
    force=True 时跳过切换门槛和 pending，强度仍按相同权重漂移。
    返回更新后的状态。
    """
    state = load(char_id=char_id)
    current = state.get("current", "neutral")

    if new_intensity is None:
        new_intensity = EMOTION_INTENSITY.get(new_emotion, 0.3)

    old_intensity = state.get("intensity", 0.0)

    # 强度加权漂移
    blended_intensity = old_intensity * 0.7 + new_intensity * 0.3

    # 情绪切换：只有新情绪强度足够高（>0.4）且持续两轮才切换
    # 用 pending 字段记录"上轮想切换的情绪"
    pending = state.get("pending", None)

    if force:
        state["previous"] = current
        state["current"] = new_emotion
        state["pending"] = None
        logger.info(
            f"[mood] 情绪强制切换: {current} → {new_emotion} "
            f"(intensity={blended_intensity:.2f})"
        )
    elif new_emotion != current:
        if new_intensity >= 0.4:
            if pending == new_emotion:
                # 连续两轮相同新情绪，执行切换
                state["previous"] = current
                state["current"] = new_emotion
                state["pending"] = None
                logger.info(f"[mood] 情绪切换: {current} → {new_emotion} (intensity={blended_intensity:.2f})")
            else:
                # 第一轮先记录 pending
                state["pending"] = new_emotion
        else:
            # 强度不够，清空 pending
            state["pending"] = None
    else:
        state["pending"] = None

    state["intensity"] = round(blended_intensity, 3)
    state["updated_at"] = time.time()
    save(state, char_id=char_id)
    return state


def get_current(*, char_id: str = DEFAULT_CHAR_ID) -> str:
    """快速获取当前情绪，不更新状态。"""
    return load(char_id=char_id).get("current", "neutral")


def get_intensity(*, char_id: str = DEFAULT_CHAR_ID) -> float:
    return load(char_id=char_id).get("intensity", 0.0)


def nudge_from_memory(memory_emotion: str, memory_strength: float, *, char_id: str = DEFAULT_CHAR_ID) -> None:
    """
    召回了强烈情绪记忆时，轻微推动当前情绪向该方向漂移。
    只在 memory_strength > 0.7 时生效，幅度最多 +0.1。
    """
    if memory_strength < 0.7:
        return
    state = load(char_id=char_id)
    current = state.get("current", "neutral")
    # 只在记忆情绪是当前情绪的邻居时才推动
    neighbors = EMOTION_NEIGHBORS.get(current, [])
    if memory_emotion in neighbors or memory_emotion == current:
        nudge = min(0.1, memory_strength * 0.1)
        state["intensity"] = min(1.0, state.get("intensity", 0.0) + nudge)
        save(state, char_id=char_id)
        logger.debug(f"[mood] 记忆推动情绪强度: +{nudge:.3f} (from {memory_emotion})")
