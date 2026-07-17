"""hidden_state_decay — 用户隐性状态时间衰减 + 基线收敛调度触发器。

触发器:
  _check_hidden_state_decay       12小时冷却，遍历所有注册角色下存在 hidden_state 的 uid，运行 apply_time_decay
                                   （Brief 88 §2：同一 tick 内顺带做 NO_INTERACTION 判定）
  _check_hidden_state_consolidate 7天冷却，运行 consolidate_baselines

均不发言、不影响 mood、不入 pipeline。
WriteEnvelope: stamp_trigger()（can_write_memory=True）。

P1: 遍历所有注册角色，对每个 (char_id, uid) 做 decay。不依赖 active_character。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

NO_INTERACTION_GAP_SECONDS: float = 24 * 3600
"""Brief 88 §2：presence.json 记录的 gap 达到此阈值即视为 NO_INTERACTION。"""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_presence_gap_seconds(uid: str, char_id: str) -> float | None:
    """读 presence.json 里该 uid 的 last_message_at，返回距今秒数；无记录 → None。"""
    import json
    import time as _time
    from core.sandbox import get_paths

    p = get_paths().presence(char_id=char_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        last = data.get(uid, {}).get("last_message_at")
    except Exception:
        return None
    if not last:
        return None
    try:
        return _time.time() - float(last)
    except (TypeError, ValueError):
        return None


def _read_no_interaction_stamp(uid: str, char_id: str) -> str | None:
    """读取 NO_INTERACTION 逻辑日去重 stamp（hidden_state.json 旁的小文件）。"""
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope

    path = resolve_path(MemoryScope.reality_scope(str(uid), char_id), "hidden_state_no_interaction_stamp")
    if not path.exists():
        return None
    try:
        import json
        return json.loads(path.read_text(encoding="utf-8")).get("last_date")
    except Exception:
        return None


def _write_no_interaction_stamp(uid: str, char_id: str, date_str: str) -> None:
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope
    from core.safe_write import safe_write_json

    path = resolve_path(MemoryScope.reality_scope(str(uid), char_id), "hidden_state_no_interaction_stamp")
    safe_write_json(path, {"last_date": date_str})


def _check_no_interaction_for_uid(uid: str, char_id: str, now_iso: str) -> None:
    """Brief 88 §2：presence.json gap ≥ 24h 且本逻辑日尚未记账 → accrue NO_INTERACTION。

    每逻辑日至多一次；已触发日期落盘在 hidden_state_no_interaction_stamp.json，
    重启后仍能读到，不会重复 accrue。fail-open：任何异常只记 log，不影响主 decay tick。
    """
    try:
        from core.scheduler.rhythm import logical_day
        from core.memory.user_hidden_state_integrator import RealityEventType, integrate_event_and_save
        from core.write_envelope import stamp_trigger

        gap = _read_presence_gap_seconds(uid, char_id)
        if gap is None or gap < NO_INTERACTION_GAP_SECONDS:
            return

        today_str = logical_day(datetime.now()).isoformat()
        if _read_no_interaction_stamp(uid, char_id) == today_str:
            return

        _, result = integrate_event_and_save(
            uid, RealityEventType.NO_INTERACTION, stamp_trigger(), now_iso, char_id=char_id
        )
        if result.accepted:
            _write_no_interaction_stamp(uid, char_id, today_str)
    except Exception as exc:
        logger.error(
            "[hidden_state_decay] no_interaction check failed uid=%s char_id=%s: %s", uid, char_id, exc
        )


async def _check_hidden_state_decay() -> None:
    """12-hour tick: apply_time_decay for all registered chars × uids with hidden_state.json."""
    from core.memory.user_hidden_state import apply_time_decay
    from core.memory.user_hidden_state_store import load_hidden_state, save_hidden_state
    from core.scheduler.loop import _is_ready, _mark
    from core.write_envelope import stamp_trigger
    from core.asset_registry import get_registry
    from core.sandbox import get_paths

    if not _is_ready("hidden_state_decay"):
        return
    _mark("hidden_state_decay")

    char_ids = [e.id for e in get_registry().list_all("character")]
    if not char_ids:
        logger.warning("[hidden_state_decay] 无已注册角色，跳过")
        return

    now = _utcnow_iso()
    _envelope = stamp_trigger()  # noqa: F841 — documents caller authority

    for char_id in char_ids:
        char_root = get_paths().memory_char_root(char_id=char_id)
        if not char_root.exists():
            continue
        uids = [
            d.name for d in char_root.iterdir()
            if d.is_dir() and (d / "hidden_state.json").exists()
        ]
        for uid in uids:
            try:
                state = load_hidden_state(uid, char_id=char_id)
                state = apply_time_decay(state, now)
                if not save_hidden_state(uid, state, char_id=char_id):
                    logger.error(
                        "[hidden_state_decay] save failed uid=%s char_id=%s", uid, char_id
                    )
            except Exception as exc:
                logger.error(
                    "[hidden_state_decay] error uid=%s char_id=%s: %s", uid, char_id, exc
                )

            # Brief 88 §2：挂现有 12h tick，不新建 trigger。
            _check_no_interaction_for_uid(uid, char_id, now)


async def _check_hidden_state_consolidate() -> None:
    """7-day tick: consolidate_baselines for all registered chars × uids with hidden_state.json."""
    from core.memory.user_hidden_state import consolidate_baselines
    from core.memory.user_hidden_state_store import load_hidden_state, save_hidden_state
    from core.scheduler.loop import _is_ready, _mark
    from core.write_envelope import stamp_trigger
    from core.asset_registry import get_registry
    from core.sandbox import get_paths

    if not _is_ready("hidden_state_consolidate"):
        return
    _mark("hidden_state_consolidate")

    char_ids = [e.id for e in get_registry().list_all("character")]
    if not char_ids:
        logger.warning("[hidden_state_consolidate] 无已注册角色，跳过")
        return

    now = _utcnow_iso()
    _envelope = stamp_trigger()  # noqa: F841 — documents caller authority

    for char_id in char_ids:
        char_root = get_paths().memory_char_root(char_id=char_id)
        if not char_root.exists():
            continue
        uids = [
            d.name for d in char_root.iterdir()
            if d.is_dir() and (d / "hidden_state.json").exists()
        ]
        for uid in uids:
            try:
                state = load_hidden_state(uid, char_id=char_id)
                state = consolidate_baselines(state, now)
                if not save_hidden_state(uid, state, char_id=char_id):
                    logger.error(
                        "[hidden_state_consolidate] save failed uid=%s char_id=%s", uid, char_id
                    )
            except Exception as exc:
                logger.error(
                    "[hidden_state_consolidate] error uid=%s char_id=%s: %s", uid, char_id, exc
                )
