"""
core/mood_helpers.py — N2-A: 显式 mood 写操作 helper。

所有对 mood_state 的主动写入（非 post_process detect 路径）都必须通过这里，
禁止在 fetch_context / retrieve 等读路径中直接调用 mood_state.update。

文件级安全性：mood_state.update() → save() → safe_write_json()，原子写入。
与 post_process 的 global_lock("mood_state") 不同，这两个 helper 不持全局锁，
但依赖 safe_write_json 的原子性保证文件完整性。
如果将来需要强一致性，可在 caller 处加 global_lock，不影响此 helper 接口。
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def maybe_mark_sleepy_from_time(uid: str, char_id: str, envelope=None) -> None:
    """深夜自动写 sleepy mood。

    N2-A: 显式写操作 — 必须在写路径调用（不得在 fetch_context 等读路径内调用）。
    从 fetch_context 迁出后的唯一入口。当前调用点：Pipeline.post_process 开始处。

    兼容注意（N2-A 兼容残留）：
    - 如果 envelope 存在且 can_affect_mood=False，跳过写入。
    - 如果 envelope 为 None（无 envelope 入口），保持老行为，不检查。
    - 文件安全性由 mood_state.update -> safe_write_json 保证。
    """
    hour = datetime.now().hour
    if not (hour >= 23 or hour < 6):
        return

    # N2-A: envelope guard — 有 envelope 时尊重 can_affect_mood
    if envelope is not None and not envelope.can_affect_mood:
        logger.debug(
            "[mood_helpers.maybe_mark_sleepy] 跳过: envelope.can_affect_mood=False uid=%s", uid
        )
        return

    from core.memory.mood_state import get_current as _get_mood, update as _mood_update
    if _get_mood(char_id=char_id) not in ("yandere", "angry"):
        _mood_update("sleepy", source="schedule", char_id=char_id)
        logger.debug("[mood_helpers.maybe_mark_sleepy] sleepy mood 写入 uid=%s char_id=%s", uid, char_id)


def mark_tool_thinking_mood(uid: str, char_id: str, envelope=None) -> None:
    """工具命中时写 thinking mood。

    N2-A: 显式写操作 — 工具执行层的唯一入口。
    main.py 不得直接 import mood_state.update，必须通过此 helper。

    兼容注意（N2-A 兼容残留）：
    - 如果 envelope 存在且 can_affect_mood=False，跳过写入。
    - 如果 envelope 为 None，保持老行为（probe 命中就写 thinking）。
    - 文件安全性由 mood_state.update -> safe_write_json 保证。
    """
    # N2-A: envelope guard — 有 envelope 时尊重 can_affect_mood
    if envelope is not None and not envelope.can_affect_mood:
        logger.debug(
            "[mood_helpers.mark_tool_thinking] 跳过: envelope.can_affect_mood=False uid=%s", uid
        )
        return

    from core.memory.mood_state import update as _mood_update
    _mood_update("thinking", source="trigger", char_id=char_id)
    logger.debug("[mood_helpers.mark_tool_thinking] thinking mood 写入 uid=%s char_id=%s", uid, char_id)
