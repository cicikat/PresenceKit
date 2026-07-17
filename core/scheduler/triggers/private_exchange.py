"""private_exchange — 深夜/闲时低频角色间私聊（受限形态，Brief 86）。

DESIGN.md §十一 决策 9.5（2026-07-17 修订）落地：产物只回流关系层
（char_relations summary/valence/recent_moments + 12h presence 提示），全文按
决策 3（自产内容不固化）排除在五大记忆库/event_log/向量库之外——唯一落盘是
`data/runtime/groups/_private/{a}__{b}/transcript.jsonl`（管理面板只读观测，
Hard Rule 7）。

模式仿 memory_janitor.py：调度器注册、深夜时段判断（同 memory_janitor）、
`stamp_trigger()`，不发言、不进 pipeline。预算双闸：
  - daily_limit（跨所有 pair 合计的会话数，按 rhythm 逻辑日重置，默认 1）
  - max_turns（单次会话硬顶的 LLM 调用数，双方轮流各半，默认 6）
pair 选择纯规则零 LLM：候选 = 有 char_relations 记录或共处任一 Stage roster
的角色对；加权 = 距上次私下往来时间（小时）/ (interaction_count + 1)，
interaction_count 低、间隔久的组合优先。

fail-open：任意一方生成失败或空回复 → 整段放弃，不落盘、不回流；本日额度已在
生成前扣减，不返还（防止坏 pair 每 tick 重试整段 pipeline）。
"""
from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

_DEFAULT_DAILY_LIMIT = 1
_DEFAULT_MAX_TURNS = 6
_MIN_SPACING_SECONDS = 2 * 3600  # trigger-level cooldown floor between sessions


def _cfg() -> dict:
    from core.config_loader import get_config

    return get_config().get("private_exchange", {}) or {}


def _in_deep_night_window(now=None) -> bool:
    """同 memory_janitor.py：23:00 起，跨午夜宽限到 LOGICAL_DAY_CUTOFF_HOUR。"""
    from datetime import datetime
    from core.scheduler.rhythm import LOGICAL_DAY_CUTOFF_HOUR

    now = now or datetime.now()
    return now.hour >= 23 or now.hour < LOGICAL_DAY_CUTOFF_HOUR


# ── 每日会话预算（跨所有 pair 合计）───────────────────────────────────────────

def _load_budget_state() -> dict:
    from core.sandbox import get_paths

    path = get_paths().private_exchange_budget_state()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _consume_daily_budget(daily_limit: int) -> bool:
    """占用今天的一个会话名额；额度用尽返回 False。

    在生成前调用（不是成功后）——失败的会话也不归还名额（fail-open，本日不重试）。
    """
    from core.sandbox import get_paths
    from core.safe_write import safe_write_json
    from core.scheduler.rhythm import logical_day

    today = logical_day().isoformat()
    state = _load_budget_state()
    if state.get("logical_day") != today:
        state = {"logical_day": today, "count": 0}
    count = int(state.get("count", 0) or 0)
    if count >= daily_limit:
        return False
    state["count"] = count + 1
    safe_write_json(get_paths().private_exchange_budget_state(), state)
    return True


# ── pair 选择（纯规则，零 LLM）─────────────────────────────────────────────────

def _all_char_ids() -> list[str]:
    from core.asset_registry import get_registry

    return [e.id for e in get_registry().list_all("character")]


def _stage_roster_pairs() -> set[tuple[str, str]]:
    from itertools import combinations
    from core.sandbox import get_paths
    from core.stage.store import load_stage

    pairs: set[tuple[str, str]] = set()
    groups_dir = get_paths().stage_group_dir(group_id="_dummy").parent
    if not groups_dir.exists():
        return pairs
    for meta_path in groups_dir.glob("*/meta.json"):
        stage = load_stage(meta_path.parent.name)
        if stage is None:
            continue
        for char_a, char_b in combinations(stage.roster, 2):
            pairs.add(tuple(sorted((char_a, char_b))))
    return pairs


def _relation_pairs(char_ids: list[str]) -> set[tuple[str, str]]:
    from itertools import combinations
    from core.stage.char_relations import load_relation

    pairs: set[tuple[str, str]] = set()
    for char_a, char_b in combinations(char_ids, 2):
        if load_relation(char_a, char_b) is not None:
            pairs.add((char_a, char_b))
    return pairs


def select_pair(char_ids: list[str]) -> tuple[str, str] | None:
    """候选 = 有 char_relations 记录或共处任一 Stage roster 的角色对；
    加权 = 距上次私下往来时间（小时）/ (interaction_count + 1)，取最大者。"""
    from core.stage.char_relations import load_relation
    from core.stage.private_exchange import last_exchange_ts

    candidates = _relation_pairs(char_ids) | _stage_roster_pairs()
    if not candidates:
        return None

    now = time.time()
    best_pair: tuple[str, str] | None = None
    best_score = -1.0
    for char_a, char_b in sorted(candidates):
        relation = load_relation(char_a, char_b)
        interaction_count = int(relation.get("interaction_count", 0) or 0) if relation else 0
        last_ts = last_exchange_ts(char_a, char_b)
        hours_since = (now - last_ts) / 3600.0 if last_ts else 24.0 * 365
        score = hours_since / (interaction_count + 1)
        if score > best_score:
            best_score = score
            best_pair = (char_a, char_b)
    return best_pair


# ── 话头素材（同 85 §4 引子素材，零新检索）────────────────────────────────────

def _opener_material(char_id: str, other_id: str) -> str:
    parts: list[str] = []
    try:
        from core import activity_manager

        activity = activity_manager.get_prompt_fragment(char_id=char_id)
    except Exception:
        activity = ""
    if activity:
        parts.append(f"你最近在忙：{activity}")
    try:
        from core.character_name_provider import get_char_name
        from core.stage.char_relations import recent_moments

        moments = recent_moments(char_id, other_id)
        if moments:
            parts.append(f"你和{get_char_name(other_id)}之间：{moments[-1]}")
    except Exception:
        pass
    from datetime import datetime

    parts.append(f"现在是{datetime.now().strftime('%m月%d日 %H:%M')}。")
    return "\n".join(parts)


# ── 会话生成（复用 Brief 85 §1 lightweight 视图）────────────────────────────────

def _owner_uid() -> str:
    from core.config_loader import get_config

    return str(get_config().get("scheduler", {}).get("owner_id", "") or "owner")


async def _run_session(char_a: str, char_b: str, *, max_turns: int) -> None:
    from core.stage.views import StageCharacterView

    owner_uid = _owner_uid()
    views = {char_a: StageCharacterView(char_a), char_b: StageCharacterView(char_b)}
    order = [char_a, char_b]

    turns: list[tuple[str, str]] = []
    for i in range(max_turns):
        speaker = order[i % 2]
        other = order[1 - (i % 2)]
        opener_material = _opener_material(speaker, other) if not turns else ""
        try:
            content = await views[speaker].generate_private(
                other, turns, owner_uid=owner_uid, opener_material=opener_material,
            )
        except Exception:
            logger.info(
                "[private_exchange] 生成失败，整段放弃 pair=%s/%s turn=%d",
                char_a, char_b, i, exc_info=True,
            )
            return
        if not content:
            logger.info(
                "[private_exchange] 空回复，整段放弃 pair=%s/%s turn=%d", char_a, char_b, i,
            )
            return
        turns.append((speaker, content))

    _persist_session(char_a, char_b, turns, owner_uid=owner_uid)


def _persist_session(
    char_a: str, char_b: str, turns: list[tuple[str, str]], *, owner_uid: str
) -> None:
    from core.character_name_provider import get_char_name
    from core.post_process import slow_queue
    from core.stage import private_exchange as pe_store
    from core.write_envelope import stamp_trigger

    now = time.time()
    for speaker, content in turns:
        pe_store.append_entry(char_a, char_b, speaker_id=speaker, content=content, ts=now)

    envelope = stamp_trigger()
    excerpt = "\n".join(f"{get_char_name(speaker)}：{content}" for speaker, content in turns)
    slow_queue.enqueue("update_char_relations", {
        "uid": owner_uid,
        "char_a": char_a,
        "char_b": char_b,
        "excerpt": excerpt,
        "timestamp": now,
        "write_envelope": {"source": envelope.source.value, "can_write_memory": envelope.can_write_memory},
    })

    pe_store.write_presence_stamp(char_a, char_b, ts=now)
    pe_store.write_presence_stamp(char_b, char_a, ts=now)

    logger.info(
        "[private_exchange] 会话完成 pair=%s/%s turns=%d", char_a, char_b, len(turns),
    )


# ── 调度器入口 ────────────────────────────────────────────────────────────────

async def _check_private_exchange() -> None:
    from core.scheduler.loop import _is_ready, _mark
    from core.write_envelope import stamp_trigger

    cfg = _cfg()
    if not bool(cfg.get("enabled", True)):
        return
    if not _in_deep_night_window():
        return
    if not _is_ready("private_exchange"):
        return

    daily_limit = int(cfg.get("daily_limit", _DEFAULT_DAILY_LIMIT))
    max_turns = int(cfg.get("max_turns", _DEFAULT_MAX_TURNS))
    if daily_limit <= 0 or max_turns <= 0:
        return

    char_ids = _all_char_ids()
    if len(char_ids) < 2:
        return

    pair = select_pair(char_ids)
    if pair is None:
        return

    if not _consume_daily_budget(daily_limit):
        return
    _mark("private_exchange")
    _envelope = stamp_trigger()  # noqa: F841 — documents caller authority

    char_a, char_b = pair
    try:
        await _run_session(char_a, char_b, max_turns=max_turns)
    except Exception:
        logger.warning(
            "[private_exchange] session error pair=%s/%s", char_a, char_b, exc_info=True,
        )
