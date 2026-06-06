"""
episodic_sweep 触发器 — 扫描所有角色下所有 uid 的 mid_term，
找出 age > 11h 且 promoted_to_episodic_id 为 null 的条目，批量触发 reflect_to_episodic。
冷却 30 分钟，触发类型 "sweep"。
"""

import logging
import time

from core.error_handler import log_error
from core.memory.scope import MemoryScope
from core.sandbox import get_paths

logger = logging.getLogger(__name__)


async def _check_episodic_sweep() -> None:
    from core.scheduler.loop import _is_ready, _mark
    from core.asset_registry import get_registry

    if not _is_ready("episodic_sweep"):
        return

    _mark("episodic_sweep")

    char_ids = [e.id for e in get_registry().list_all("character")]
    if not char_ids:
        logger.warning("[scheduler.episodic_sweep] 无已注册角色，跳过")
        return

    for char_id in char_ids:
        uids: set[str] = set()

        # v1 布局：runtime/memory/{char_id}/ 下有 mid_term.json 的子目录
        char_root = get_paths().memory_char_root(char_id=char_id)
        if char_root.exists():
            uids.update(
                d.name for d in char_root.iterdir()
                if d.is_dir() and (d / "mid_term.json").exists()
            )

        # legacy 布局：chars/{char_id}/mid_term/{uid}.json
        mid_term_dir = get_paths().mid_term(char_id=char_id)
        if mid_term_dir.exists():
            uids.update(f.stem for f in mid_term_dir.glob("*.json"))

        if not uids:
            continue

        logger.debug(f"[scheduler.episodic_sweep] char={char_id} 扫描 {len(uids)} 个 uid")

        for uid in uids:
            try:
                await _sweep_uid(uid, char_id=char_id)
            except Exception as e:
                log_error(f"scheduler.episodic_sweep.sweep_uid.{char_id}.{uid}", e)


async def _sweep_uid(uid: str, *, char_id: str) -> None:
    from core.memory import mid_term as _mt
    from core.post_process import slow_queue

    events = _mt.load(uid, char_id=char_id)
    now = time.time()

    aged_ids = [
        e["mid_id"]
        for e in events
        if e.get("mid_id")
        and (now - e.get("ts", 0)) > 11 * 3600
        and not e.get("promoted_to_episodic_id")
    ]

    if not aged_ids:
        return

    slow_queue.enqueue("reflect_to_episodic", {
        "uid": uid,
        "char_id": char_id,
        "mid_ids": aged_ids,
        "trigger": "sweep",
        "scope": MemoryScope.reality_scope(str(uid), char_id).to_payload(),
    })
    logger.info(
        f"[scheduler.episodic_sweep] uid={uid} char={char_id} 入队 reflect_to_episodic sweep "
        f"mid_ids={aged_ids}"
    )
