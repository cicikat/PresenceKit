"""memory_janitor — 闲时整合 pass：episodic 存量近似重复合并 + 向量库一致性核对（Brief 49）。

模式仿 event_log_salvage.py / hidden_state_decay.py：调度器注册、深夜时段（23:00 起，
跨午夜宽限到 LOGICAL_DAY_CUTOFF_HOUR）、冷却 24h、stamp_trigger()，不发言，遍历所有
注册角色 × 现存 uid 目录（存在 episodic.json 才算）。全程 uid_lock 内执行。

v1 零 LLM：
  (a) episodic 存量近似重复合并——复用 episodic_memory._is_similar() 与写入时同一相似度
      函数/阈值做全量两两比对；核心记忆（is_core）不参与合并，无论作为保留方还是被并方；
      单次运行合并上限 10 对（跨全部角色/用户合计），防首跑存量大时一次动太多。
  (b) 向量库一致性核对——vec_meta 对照 episodic.json / 近 30 天 event_log 两个真相源，
      孤儿数 > 20 或占比 > 10% 时调用现有 vector_store.rebuild()，否则只记观测日志。

运行状态（janitor_last_run_at / janitor_merged_count 累计）记入 fixation_state.json，
不新建文件。幂等：连跑两遍第二遍必须 no-op（重复已消除、孤儿已清零）。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_MERGE_PAIR_LIMIT = 10          # 单次运行合并上限（跨全部角色/用户）
_ORPHAN_ABS_THRESHOLD = 20      # 孤儿向量数超过此值触发 rebuild
_ORPHAN_FRAC_THRESHOLD = 0.10   # 孤儿占比超过此值触发 rebuild
_EVENT_LOG_LOOKBACK_DAYS = 30   # 与 vector_store.rebuild() 的 event_log 回填窗口一致


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def _check_memory_janitor() -> None:
    from core.scheduler.loop import _is_ready, _mark
    from core.write_envelope import stamp_trigger
    from core.asset_registry import get_registry
    from core.sandbox import get_paths
    from core.memory.locks import uid_lock
    from core.scheduler.rhythm import LOGICAL_DAY_CUTOFF_HOUR
    from core.memory.fixation_pipeline import _load_fixation_state, _save_fixation_state

    now = datetime.now()
    if not (now.hour >= 23 or now.hour < LOGICAL_DAY_CUTOFF_HOUR):
        return
    if not _is_ready("memory_janitor"):
        return
    _mark("memory_janitor")

    char_ids = [e.id for e in get_registry().list_all("character")]
    if not char_ids:
        logger.warning("[memory_janitor] 无已注册角色，跳过")
        return

    _envelope = stamp_trigger()  # noqa: F841 — documents caller authority
    run_ts = _utcnow_iso()
    merge_budget = _MERGE_PAIR_LIMIT
    total_merged = 0

    for char_id in char_ids:
        char_root = get_paths().memory_char_root(char_id=char_id)
        if not char_root.exists():
            continue
        uids = [
            d.name for d in char_root.iterdir()
            if d.is_dir() and (d / "episodic.json").exists()
        ]
        for uid in uids:
            async with uid_lock(uid):
                merged = 0
                if merge_budget > 0:
                    try:
                        merged = _merge_duplicates(char_id, uid, merge_budget)
                        merge_budget -= merged
                        total_merged += merged
                    except Exception as exc:
                        logger.error(
                            "[memory_janitor] merge error uid=%s char_id=%s: %s",
                            uid, char_id, exc,
                        )
                try:
                    await _check_vector_consistency(char_id, uid)
                except Exception as exc:
                    logger.error(
                        "[memory_janitor] vector check error uid=%s char_id=%s: %s",
                        uid, char_id, exc,
                    )
                try:
                    state = _load_fixation_state(uid, char_id=char_id)
                    state["janitor_last_run_at"] = run_ts
                    if merged:
                        state["janitor_merged_count"] = int(
                            state.get("janitor_merged_count", 0) or 0
                        ) + merged
                    _save_fixation_state(uid, state, char_id=char_id)
                except Exception as exc:
                    logger.error(
                        "[memory_janitor] state write error uid=%s char_id=%s: %s",
                        uid, char_id, exc,
                    )

    logger.info("[memory_janitor] 本轮完成，合计合并 %d 对", total_merged)


# ── (a) episodic 存量近似重复合并 ────────────────────────────────────────────

def _merge_duplicates(char_id: str, uid: str, budget: int) -> int:
    """全量两两比对非核心 episodic 条目，合并近似重复；返回本次实际合并对数（<= budget）。"""
    from core.memory.episodic_memory import _load_memories, _is_similar

    if budget <= 0:
        return 0

    memories = _load_memories(uid, char_id=char_id)
    eligible = [m for m in memories if not m.get("is_core") and m.get("id")]
    if len(eligible) < 2:
        return 0

    def _summary(m: dict) -> str:
        return m.get("narrative_summary") or m.get("summary", "")

    consumed: set[str] = set()
    plan: list[tuple[dict, dict]] = []

    for i in range(len(eligible)):
        if len(plan) >= budget:
            break
        a = eligible[i]
        if a["id"] in consumed:
            continue
        for j in range(i + 1, len(eligible)):
            if len(plan) >= budget:
                break
            b = eligible[j]
            if b["id"] in consumed:
                continue
            if not _is_similar(_summary(a), _summary(b)):
                continue
            if (a.get("strength", 0) or 0) >= (b.get("strength", 0) or 0):
                survivor, loser = a, b
            else:
                survivor, loser = b, a
            plan.append((survivor, loser))
            consumed.add(a["id"])
            consumed.add(b["id"])
            break

    merged_count = 0
    for survivor_snap, loser_snap in plan:
        try:
            _apply_merge(char_id, uid, survivor_snap["id"], loser_snap["id"])
            merged_count += 1
        except Exception as exc:
            logger.error(
                "[memory_janitor] apply_merge failed uid=%s char_id=%s survivor=%s loser=%s: %s",
                uid, char_id, survivor_snap["id"], loser_snap["id"], exc,
            )
    return merged_count


def _occurred_at(m: dict) -> float:
    v = m.get("occurred_at")
    return v if isinstance(v, (int, float)) else m.get("timestamp", 0) or 0


_REPAIR_FIELDS = ("episode_kind", "outcome", "repair_note", "repaired_by", "repaired_at")
_OUTCOME_RANK = {"repaired": 3, "clarified": 2, "paused": 1, "unresolved": 0}


def _merge_repair_fields(survivor: dict, loser: dict) -> None:
    """合并时保留修复字段（M2）：优先 repaired，不能被高强度一方覆盖丢失。"""
    def rank(m: dict) -> int:
        return _OUTCOME_RANK.get(m.get("outcome"), -1)

    if rank(loser) > rank(survivor):
        for key in _REPAIR_FIELDS:
            if key in loser:
                survivor[key] = loser[key]
            else:
                survivor.pop(key, None)
    elif rank(survivor) < 0 and loser.get("episode_kind"):
        survivor.setdefault("episode_kind", loser["episode_kind"])


def _apply_merge(char_id: str, uid: str, survivor_id: str, loser_id: str) -> None:
    """保留 strength 较高者，血缘并集、召回次数求和、取较早 occurred_at；被并方经
    delete_episode() 删除（自动连删向量）；落一条 janitor_merge provenance。"""
    from core.memory.episodic_memory import (
        _load_memories, _save_memories, _rebuild_index, delete_episode,
    )
    from core.memory import provenance_log

    memories = _load_memories(uid, char_id=char_id)
    survivor = next((m for m in memories if m.get("id") == survivor_id), None)
    loser = next((m for m in memories if m.get("id") == loser_id), None)
    if survivor is None or loser is None:
        return  # 已不存在（防御性，避免并发/重入下的重复处理）
    if survivor.get("is_core") or loser.get("is_core"):
        return  # 防御性：核心记忆绝不参与合并

    survivor_mids = set(survivor.get("source_mid_ids") or [])
    loser_mids = set(loser.get("source_mid_ids") or [])
    survivor["source_mid_ids"] = sorted(survivor_mids | loser_mids)
    survivor["retrieval_count"] = int(survivor.get("retrieval_count", 0) or 0) + int(
        loser.get("retrieval_count", 0) or 0
    )
    survivor["occurred_at"] = min(_occurred_at(survivor), _occurred_at(loser))
    _merge_repair_fields(survivor, loser)

    _save_memories(uid, memories, char_id=char_id)
    _rebuild_index(uid, memories, char_id=char_id)

    before_gist = (loser.get("narrative_summary") or loser.get("summary", ""))[:120]
    after_gist = (survivor.get("narrative_summary") or survivor.get("summary", ""))[:120]

    delete_episode(uid, loser_id, char_id=char_id)

    provenance_log.append(
        uid, char_id,
        artifact="episodic",
        field=survivor_id,
        before_gist=before_gist,
        after_gist=after_gist,
        trigger_signal="janitor_merge",
    )
    logger.info(
        "[memory_janitor] 合并完成 uid=%s char_id=%s survivor=%s loser=%s",
        uid, char_id, survivor_id, loser_id,
    )


# ── (b) 向量库一致性核对 ──────────────────────────────────────────────────────

_DAY_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")


def _recent_event_log_turn_ids(uid: str, char_id: str) -> set[str]:
    """近 30 天 event_log 日文件里出现过的 turn_id 集合（真相源）。"""
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope
    from core.memory.event_log import _TURN_ID_RE

    scope = MemoryScope.reality_scope(str(uid), char_id)
    log_dir = resolve_path(scope, "event_log")
    if not log_dir.exists():
        return set()

    cutoff = datetime.now().date() - timedelta(days=_EVENT_LOG_LOOKBACK_DAYS)
    ids: set[str] = set()
    for f in log_dir.iterdir():
        m = _DAY_FILE_RE.match(f.name)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        ids.update(_TURN_ID_RE.findall(text))
    return ids


async def _check_vector_consistency(char_id: str, uid: str) -> None:
    """统计 vec_meta 里 episodic / event_log 两路孤儿向量占比；超阈值触发 rebuild，否则只记观测日志。"""
    from core.memory import vector_store as _vs
    from core.memory.episodic_memory import _load_memories

    entries = _vs.list_entries(uid, char_id, limit=1_000_000)
    if not entries:
        return

    episodic_ids = {str(m.get("id")) for m in _load_memories(uid, char_id=char_id) if m.get("id")}
    valid_turn_ids = _recent_event_log_turn_ids(uid, char_id)

    checked = 0
    orphan_count = 0
    for e in entries:
        source = e.get("source")
        source_id = str(e.get("source_id") or "")
        if source == "episodic":
            checked += 1
            if source_id not in episodic_ids:
                orphan_count += 1
        elif source == "event_log":
            checked += 1
            # rebuild() 写入的聚合行以 "recent_" 前缀命名，不对应单条 turn_id，视为有效。
            if not (source_id.startswith("recent_") or source_id in valid_turn_ids):
                orphan_count += 1
        # 其他 source（profile/web）不在本 brief 真相源核对范围内，跳过。

    if checked == 0:
        return

    orphan_frac = orphan_count / checked
    if orphan_count > _ORPHAN_ABS_THRESHOLD or orphan_frac > _ORPHAN_FRAC_THRESHOLD:
        count = await _vs.rebuild(uid, char_id)
        logger.info(
            "[memory_janitor] 向量库孤儿超阈值，已 rebuild uid=%s char_id=%s "
            "orphan=%d/%d(%.1f%%) new_count=%d",
            uid, char_id, orphan_count, checked, orphan_frac * 100, count,
        )
    else:
        logger.info(
            "[memory_janitor] 向量库孤儿观测 uid=%s char_id=%s orphan=%d/%d(%.1f%%)（未触发 rebuild）",
            uid, char_id, orphan_count, checked, orphan_frac * 100,
        )
