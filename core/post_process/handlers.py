"""Slow queue registration and payload handlers, owned by application startup."""

import logging
from core.memory.scope import MemoryScope
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger("core.pipeline")


def _get_scope_from_payload(payload: dict, handler_name: str) -> MemoryScope:
    """从 payload 解析 MemoryScope。

    优先读 payload["scope"]（新格式）；无 scope 时 fallback 到 legacy uid+char_id；
    两者均缺时 WARN + fallback yexuan（DLQ 兼容层）。
    payload["scope"] 存在但解析失败或 domain 非 reality → fail-loud，不 fallback。
    """
    raw = payload.get("scope")
    if raw is not None:
        scope = MemoryScope.from_payload(raw)  # 坏数据 fail-loud
        if scope.domain != "reality":
            raise ValueError(
                f"[pipeline.{handler_name}] scope domain must be 'reality', got {scope.domain!r}"
            )
        return scope

    # legacy fallback：旧 payload 无 scope 字段
    uid = payload.get("uid", "unknown")
    char_id = payload.get("char_id")
    if char_id:
        return MemoryScope.reality_scope(str(uid), char_id)

    logger.warning(
        "[pipeline.%s] payload 缺少 scope/char_id，使用 legacy DLQ fallback char_id=%s "
        "(uid=%s)",
        handler_name, DEFAULT_CHAR_ID, uid,
    )
    return MemoryScope.reality_scope(str(uid), DEFAULT_CHAR_ID)


async def _handler_consistency_check(payload: dict, *, read_character, write_author_note) -> None:
    from core import character_loader
    char_id, character = read_character()
    if payload["char_id"] != char_id:
        logger.debug("[pipeline.consistency] discarded queued check for inactive character: %s", payload["char_id"])
        return
    check_result = await character_loader.consistency_check(character, payload["reply"])
    if not check_result.get("ok"):
        issue = check_result.get("issue", "")
        if issue:
            if write_author_note(char_id, issue):
                logger.info(f"[pipeline.consistency] 一致性问题，下轮追加纠偏: {issue}")
            else:
                logger.debug("[pipeline.consistency] discarded stale character check: %s", char_id)


async def _handler_user_profile_update(payload: dict) -> None:
    from core.memory import locks as _locks, user_profile
    scope = _get_scope_from_payload(payload, "_handler_user_profile_update")
    uid = scope.uid
    char_id = scope.character_id
    async with _locks.uid_lock(uid):
        await user_profile.extract_and_update(uid, payload["recent"], char_id=char_id)
    logger.info(f"[pipeline.user_profile] 画像更新完成: {uid}")


async def _handler_trait_tracker_update(payload: dict) -> None:
    # R8-B: independent trait refresh — detached from character_growth.update()
    import yaml
    from core.memory import locks as _locks, short_term as _st
    from core.memory.trait_tracker import count_traits_in_history, update_trait_state
    from core.sandbox import get_paths

    scope = _get_scope_from_payload(payload, "_handler_trait_tracker_update")
    uid = scope.uid
    char_id = scope.character_id

    from core.character_data import load_traits
    try:
        traits = load_traits(char_id)
    except (ValueError, OSError) as exc:
        logger.warning("[pipeline.trait_tracker] traits definition unavailable: char_id=%s error=%s", char_id, exc)
        return
    if not traits:
        logger.debug("[pipeline.trait_tracker] traits 为空，跳过: char_id=%s", char_id)
        return

    async with _locks.uid_lock(uid):
        recent = _st.load(uid, char_id=char_id)[-40:]
        # 只统计 assistant 行：user 行包含关键词不代表角色表达过该特质
        history_lines = [msg["content"] for msg in recent if msg.get("role") == "assistant"]
        counts = count_traits_in_history(history_lines, traits)
        trait_path = get_paths().trait_state(char_id=char_id)
        update_trait_state(counts, trait_path, write_path=trait_path)
    logger.info("[pipeline.trait_tracker] 更新完成: uid=%s char_id=%s", uid, char_id)


def register_slow_handlers(*, read_character, write_author_note) -> None:
    """main.py 启动时调用一次，注册所有慢任务 handler。"""
    if not callable(read_character) or not callable(write_author_note):
        raise TypeError("slow handlers require read_character and write_author_note capabilities")
    from core.post_process import slow_queue
    from functools import partial
    from core.memory.fixation_pipeline import (
        handler_capture_turn_retry,
        handler_summarize_to_midterm,
        handler_reflect_to_episodic,
        handler_consolidate_to_identity,
        handler_storyline_evicted_input,
    )
    # 新 pipeline handler
    slow_queue.register_handler("capture_turn_retry",       handler_capture_turn_retry)
    slow_queue.register_handler("summarize_to_midterm",     handler_summarize_to_midterm)
    slow_queue.register_handler("reflect_to_episodic",      handler_reflect_to_episodic)
    slow_queue.register_handler("consolidate_to_identity",  handler_consolidate_to_identity)
    slow_queue.register_handler("storyline_evicted_input",  handler_storyline_evicted_input)
    slow_queue.register_handler("consistency_check",       partial(_handler_consistency_check, read_character=read_character, write_author_note=write_author_note))
    slow_queue.register_handler("user_profile_update",     _handler_user_profile_update)
    from core.food_memory import process_pending as process_food_pending

    async def handle_food(payload):
        scope = _get_scope_from_payload(payload, "food_memory_update")
        await process_food_pending(scope.uid, scope.character_id)

    slow_queue.register_handler("food_memory_update", handle_food)
    slow_queue.register_handler("trait_tracker_update",    _handler_trait_tracker_update)
    from core.post_process.toy_autogrow import handler_toy_autogrow
    slow_queue.register_handler("toy_autogrow",            handler_toy_autogrow)
    from core.stage.char_relations import handler_update_char_relations
    slow_queue.register_handler("update_char_relations",   handler_update_char_relations)
