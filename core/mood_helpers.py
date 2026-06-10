"""
core/mood_helpers.py — N2-A/N2-B: 显式 mood 写操作 helper。

所有对 mood_state 的主动写入（非 post_process detect 路径）都必须通过这里，
禁止在 fetch_context / retrieve 等读路径中直接调用 mood_state.update。

# 锁语义（N2-B 说明）
─────────────────────────────────────────────────────────────────────────────
mood_state 是 char 级共享文件（非 uid 级）。多个用户的 post_process 可以并发
执行，每个都会写同一个 char_id 的 mood_state 文件。

安全层次：
  ① safe_write_json：tmp → replace，保证单次写入的文件完整性（无撕裂文件）。
  ② global_lock("mood_state")：asyncio 命名锁，保证 load-modify-save 原子性，
     避免两个并发调用同时 load 到旧值后各自覆盖。

N2-B 前状态：
  - post_process detect 路径已持 global_lock("mood_state")
  - sleepy / thinking helper 只依赖 safe_write_json（① 层，无 ② 层）

N2-B 修复：
  两个 helper 升级为 async def，内部持 global_lock("mood_state")，
  与 detect 路径保持同等锁强度。
─────────────────────────────────────────────────────────────────────────────
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


async def maybe_mark_sleepy_from_time(uid: str, char_id: str, envelope=None) -> bool:
    """深夜自动写 sleepy mood。返回 bool 表示是否实际写入。

    N2-A: 显式写操作 — 必须在写路径调用（不得在 fetch_context 等读路径内调用）。
    N2-B: async + global_lock("mood_state") 补全锁语义，与 detect 路径等强度。

    从 fetch_context 迁出后的唯一 sleepy 写入点。当前调用点：
      Pipeline.post_process 开始处（uid_lock 外，detect global_lock 前）。

    envelope 语义（N2-B 明确化）：
      - envelope is None  → legacy 兼容入口（允许写入）；测试命名须含 "legacy"。
      - envelope.can_affect_mood is False → 禁止写入，return False。
      - envelope.can_affect_mood is True  → 正常路径，proceed。

    锁：持有 global_lock("mood_state")，与 post_process detect 路径互斥。
    文件原子性：由 mood_state.save → safe_write_json 保证（tmp→replace）。
    """
    hour = datetime.now().hour
    if not (hour >= 23 or hour < 6):
        return False

    # N2-B: envelope 检查（None = legacy 入口，视为允许）
    if envelope is not None and not envelope.can_affect_mood:
        logger.debug(
            "[mood_helpers.maybe_mark_sleepy] 跳过: envelope.can_affect_mood=False uid=%s", uid
        )
        return False

    from core.memory import locks as _locks
    from core.memory.mood_state import get_current as _get_mood, update as _mood_update

    async with _locks.global_lock("mood_state"):
        if _get_mood(char_id=char_id) not in ("yandere", "angry"):
            _mood_update("sleepy", source="schedule", char_id=char_id)
            logger.debug(
                "[mood_helpers.maybe_mark_sleepy] sleepy mood 写入 uid=%s char_id=%s", uid, char_id
            )
            return True
        return False


async def mark_tool_thinking_mood(uid: str, char_id: str, envelope=None) -> bool:
    """工具命中时写 thinking mood。返回 bool 表示是否实际写入。

    N2-A: 显式写操作 — 工具执行层的唯一 thinking 写入点。
    N2-B: async + global_lock("mood_state") 补全锁语义。

    main.py 不得直接 import mood_state.update，必须通过此 helper。
    调用点：main.py handle_message 工具命中分支。

    envelope 语义（N2-B 明确化）：
      - envelope is None  → legacy 兼容入口（允许写入）；测试命名须含 "legacy"。
      - envelope.can_affect_mood is False → 禁止写入，return False。
      - envelope.can_affect_mood is True  → 正常路径，proceed。

    锁：持有 global_lock("mood_state")，与 post_process detect 路径互斥。
    文件原子性：由 mood_state.save → safe_write_json 保证（tmp→replace）。
    """
    # N2-B: envelope 检查（None = legacy 入口，视为允许）
    if envelope is not None and not envelope.can_affect_mood:
        logger.debug(
            "[mood_helpers.mark_tool_thinking] 跳过: envelope.can_affect_mood=False uid=%s", uid
        )
        return False

    from core.memory import locks as _locks
    from core.memory.mood_state import update as _mood_update

    async with _locks.global_lock("mood_state"):
        _mood_update("thinking", source="trigger", char_id=char_id)
        logger.debug(
            "[mood_helpers.mark_tool_thinking] thinking mood 写入 uid=%s char_id=%s", uid, char_id
        )
    return True
