"""
core/memory/storyline.py — storyline 叙事弧层：append-only 存储 + 写 API（Brief 80 §1）。

identity.yaml 回答"他是个什么样的人"（稳定属性），storyline 回答"他在经历什么弧线"
（有时间跨度的叙事：职业转变、一个项目的推进、一段持续的情绪过程）。

00d 裁决 1（增量式 + 旧节点只读只追加）的执行面：公开写 API 只有
open_arc() / append_node() / set_arc_status() 三个函数——不存在修改既有 node 的公开路径。

调用方（storyline_weekly 聚合 trigger）必须持有 uid_lock 包住整批 ops 的执行，
本模块不加锁，同 episodic_memory 惯例：文件原子写靠 safe_write_json，
跨调用的读-改-写原子性由调用方的 uid_lock 负责。
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from core.data_paths import DEFAULT_CHAR_ID
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope, require_character_id
from core.safe_write import safe_write_json
from core.sandbox import safe_user_id

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

MAX_ACTIVE_ARCS = 8       # 软上限：由聚合 prompt 感知并自行收敛（关闭旧弧线），不做代码强制淘汰
MAX_TOTAL_ARCS = 24       # 硬上限：超出时淘汰最旧的 closed arc（见 _evict_if_needed）
MAX_NODES_PER_ARC = 40    # 硬上限：达到后 append_node 拒绝写入（弧线该 close 了）

_VALID_STATUS = frozenset({"active", "dormant", "closed"})


class StorylineCorruptError(Exception):
    """storyline.json 存在但无法解析——拒绝按空处理，防止静默丢弧线。"""


def _default_data() -> dict:
    return {
        "version": SCHEMA_VERSION,
        "meta": {"last_aggregated_at": 0.0, "event_log_cursor": ""},
        "arcs": [],
    }


def _read_file(uid: str, *, char_id: str = DEFAULT_CHAR_ID):
    require_character_id(char_id)
    scope = MemoryScope.reality_scope(safe_user_id(uid), char_id)
    return resolve_path(scope, "storyline")


def _write_file(uid: str, *, char_id: str = DEFAULT_CHAR_ID):
    p = _read_file(uid, char_id=char_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> dict:
    """加载 storyline.json；不存在时返回默认结构。存在但无法解析时 fail-loud。"""
    p = _read_file(uid, char_id=char_id)
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _default_data()
    try:
        data = json.loads(raw)
    except Exception as e:
        logger.error(
            "[storyline] 加载失败（疑似损坏），拒绝按空处理 uid=%s path=%s err=%s",
            uid, p, e,
        )
        raise StorylineCorruptError(str(p)) from e
    data.setdefault("version", SCHEMA_VERSION)
    data.setdefault("meta", {"last_aggregated_at": 0.0, "event_log_cursor": ""})
    data.setdefault("arcs", [])
    return data


def _save(uid: str, data: dict, *, char_id: str = DEFAULT_CHAR_ID) -> None:
    safe_write_json(_write_file(uid, char_id=char_id), data)


def save_meta(uid: str, *, char_id: str = DEFAULT_CHAR_ID, last_aggregated_at: float, event_log_cursor: str) -> None:
    """聚合 trigger 用：单独推进 meta 游标，不touch arcs。"""
    data = load(uid, char_id=char_id)
    data["meta"]["last_aggregated_at"] = last_aggregated_at
    data["meta"]["event_log_cursor"] = event_log_cursor
    _save(uid, data, char_id=char_id)


def list_recallable_arcs(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> list[dict]:
    """供 prompt_builder 召回层使用：返回 status in {active, dormant} 的弧线（closed 不参与召回）。"""
    data = load(uid, char_id=char_id)
    return [a for a in data["arcs"] if a.get("status") in ("active", "dormant")]


def _find_arc(data: dict, arc_id: str) -> dict | None:
    for arc in data["arcs"]:
        if arc["arc_id"] == arc_id:
            return arc
    return None


def _valid_tags() -> set[str]:
    from core.tag_rules import TAG_RULES
    return {r.tag for r in TAG_RULES}


def _archive_arc(uid: str, arc: dict, *, char_id: str) -> None:
    scope = MemoryScope.reality_scope(safe_user_id(uid), char_id)
    p = resolve_path(scope, "storyline_archive")
    p.parent.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    chunk = f"\n## {today} 淘汰归档：{arc.get('title', '')}\n{json.dumps(arc, ensure_ascii=False)}\n"
    with open(p, "a", encoding="utf-8") as f:
        f.write(chunk)


def _evict_if_needed(uid: str, data: dict, *, char_id: str) -> None:
    """总 arcs 数超限时，淘汰最旧的 closed arc 整条追加进 storyline_archive.md 再移除。
    无 closed arc 可淘汰时本轮跳过（fail-open），下次聚合再试。"""
    if len(data["arcs"]) <= MAX_TOTAL_ARCS:
        return
    closed = [a for a in data["arcs"] if a.get("status") == "closed"]
    if not closed:
        logger.warning(
            "[storyline] arcs 数(%d)超限但无 closed arc 可淘汰，本轮跳过 uid=%s char=%s",
            len(data["arcs"]), uid, char_id,
        )
        return
    oldest = min(closed, key=lambda a: a.get("updated_at", 0.0))
    _archive_arc(uid, oldest, char_id=char_id)
    data["arcs"] = [a for a in data["arcs"] if a["arc_id"] != oldest["arc_id"]]


def open_arc(uid: str, *, char_id: str = DEFAULT_CHAR_ID, title: str, tags: list[str] | None = None) -> str:
    """创建一条新弧线，返回 arc_id。

    幂等：若同名（去空白、忽略大小写）弧线已存在，直接返回其 arc_id，不新建——
    防止聚合器同一批次或跨周重复"开"同一条弧线，产生重复弧。
    tags 非法值（不在 core/tag_rules.py 受控集合内）会被静默过滤，不落盘。
    """
    data = load(uid, char_id=char_id)
    norm_title = title.strip().lower()
    for arc in data["arcs"]:
        if arc["title"].strip().lower() == norm_title:
            return arc["arc_id"]

    valid = _valid_tags()
    clean_tags = sorted({t for t in (tags or []) if t in valid})

    now = time.time()
    arc_id = f"arc_{uuid.uuid4().hex[:8]}"
    data["arcs"].append({
        "arc_id": arc_id,
        "title": title[:20],
        "status": "active",
        "tags": clean_tags,
        "nodes": [],
        "created_at": now,
        "updated_at": now,
    })
    _evict_if_needed(uid, data, char_id=char_id)
    _save(uid, data, char_id=char_id)

    from core.memory.provenance_log import append as _prov_append
    _prov_append(
        uid, char_id, artifact="storyline", field=arc_id,
        after_gist=title[:120], trigger_signal="open_arc",
    )
    return arc_id


def append_node(
    uid: str,
    *,
    char_id: str = DEFAULT_CHAR_ID,
    arc_id: str,
    summary: str,
    ts: float,
    span: list[float] | tuple[float, float] | None = None,
    source_ids: list[str] | None = None,
    node_id: str = "",
) -> str | None:
    """向已有 arc 追加一个节点，返回 node_id；不满足约束时返回 None（fail-open，不抛异常）。

    append-only 硬约束——以下情况一律拒绝写入，不存在"改写"路径：
      - arc_id 不存在
      - arc 已达 MAX_NODES_PER_ARC（弧线该 close 了）
      - ts 早于该 arc 最后一个 node 的 ts（防伪造历史）
      - node_id 与已有重复（防御性校验；node_id 缺省时内部生成，理论上不会撞）
    """
    data = load(uid, char_id=char_id)
    arc = _find_arc(data, arc_id)
    if arc is None:
        logger.warning("[storyline] append_node: arc_id 不存在 uid=%s arc_id=%s", uid, arc_id)
        return None
    if len(arc["nodes"]) >= MAX_NODES_PER_ARC:
        logger.warning(
            "[storyline] append_node: arc 已达节点上限(%d)，拒绝写入 uid=%s arc_id=%s",
            MAX_NODES_PER_ARC, uid, arc_id,
        )
        return None
    if arc["nodes"] and ts < arc["nodes"][-1]["ts"]:
        logger.warning(
            "[storyline] append_node: ts 早于最后节点，疑似伪造历史，拒绝 uid=%s arc_id=%s",
            uid, arc_id,
        )
        return None
    nid = node_id or f"n_{uuid.uuid4().hex[:8]}"
    if any(n["node_id"] == nid for n in arc["nodes"]):
        logger.warning(
            "[storyline] append_node: node_id 重复，拒绝 uid=%s arc_id=%s node_id=%s",
            uid, arc_id, nid,
        )
        return None

    span_val = list(span) if span else [ts, ts]
    arc["nodes"].append({
        "node_id": nid,
        "ts": ts,
        "span": span_val,
        "summary": summary[:80],
        "source_ids": list(source_ids or []),
    })
    arc["updated_at"] = time.time()
    _save(uid, data, char_id=char_id)

    from core.memory.provenance_log import append as _prov_append
    _prov_append(
        uid, char_id, artifact="storyline", field=arc_id,
        after_gist=summary[:120], trigger_signal="append_node",
    )
    return nid


def set_arc_status(uid: str, *, char_id: str = DEFAULT_CHAR_ID, arc_id: str, status: str) -> bool:
    """更新弧线 status（active/dormant/closed）。返回 False 表示 arc_id 不存在。"""
    if status not in _VALID_STATUS:
        raise ValueError(f"invalid status: {status!r}, must be one of {_VALID_STATUS}")
    data = load(uid, char_id=char_id)
    arc = _find_arc(data, arc_id)
    if arc is None:
        return False
    if arc["status"] == status:
        return True
    before = arc["status"]
    arc["status"] = status
    arc["updated_at"] = time.time()
    _save(uid, data, char_id=char_id)

    from core.memory.provenance_log import append as _prov_append
    _prov_append(
        uid, char_id, artifact="storyline", field=arc_id,
        before_gist=before, after_gist=status, trigger_signal="set_arc_status",
    )
    return True
