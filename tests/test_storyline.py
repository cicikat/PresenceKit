"""
tests/test_storyline.py — Brief 80 §1 storyline 存储层验收

Covers:
1. open_arc 幂等（同名不重复创建）+ 非法 tag 被过滤
2. append_node 正常追加
3. append_node 拒绝 ts 回退（防伪造历史）
4. append_node 拒绝重复 node_id
5. append_node 达节点上限后拒绝写入
6. set_arc_status 正常更新 + 非法 status 报错
7. 总 arcs 超限淘汰最旧 closed arc → storyline_archive.md
8. 不存在修改既有 node 的公开路径（模块导出面断言）
9. 路径与 resolver 一致
"""

import json
import time

import pytest

from core.memory import storyline as sl
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope


def _scope(uid, char_id):
    return MemoryScope.reality_scope(uid, char_id)


# ── 1. open_arc 幂等 + tag 过滤 ──────────────────────────────────────────────

def test_open_arc_idempotent_by_title(sandbox):
    uid, char_id = "u1", "yexuan"
    id1 = sl.open_arc(uid, char_id=char_id, title="职业方向转变", tags=["topic.learning"])
    id2 = sl.open_arc(uid, char_id=char_id, title="职业方向转变", tags=["topic.writing"])
    assert id1 == id2
    data = sl.load(uid, char_id=char_id)
    assert len(data["arcs"]) == 1


def test_open_arc_filters_invalid_tags(sandbox):
    uid, char_id = "u2", "yexuan"
    arc_id = sl.open_arc(uid, char_id=char_id, title="弧线A", tags=["topic.learning", "not_a_real_tag"])
    data = sl.load(uid, char_id=char_id)
    arc = next(a for a in data["arcs"] if a["arc_id"] == arc_id)
    assert arc["tags"] == ["topic.learning"]


# ── 2. append_node 正常追加 ──────────────────────────────────────────────────

def test_append_node_basic(sandbox):
    uid, char_id = "u3", "yexuan"
    arc_id = sl.open_arc(uid, char_id=char_id, title="弧线B", tags=[])
    now = time.time()
    nid = sl.append_node(uid, char_id=char_id, arc_id=arc_id, summary="第一阶段", ts=now)
    assert nid is not None
    data = sl.load(uid, char_id=char_id)
    arc = next(a for a in data["arcs"] if a["arc_id"] == arc_id)
    assert len(arc["nodes"]) == 1
    assert arc["nodes"][0]["node_id"] == nid


# ── 3. append-only：拒绝 ts 回退 ─────────────────────────────────────────────

def test_append_node_rejects_ts_regression(sandbox):
    uid, char_id = "u4", "yexuan"
    arc_id = sl.open_arc(uid, char_id=char_id, title="弧线C", tags=[])
    now = time.time()
    sl.append_node(uid, char_id=char_id, arc_id=arc_id, summary="节点1", ts=now)
    result = sl.append_node(uid, char_id=char_id, arc_id=arc_id, summary="伪造的早期节点", ts=now - 1000)
    assert result is None
    data = sl.load(uid, char_id=char_id)
    arc = next(a for a in data["arcs"] if a["arc_id"] == arc_id)
    assert len(arc["nodes"]) == 1


# ── 4. append-only：拒绝重复 node_id ─────────────────────────────────────────

def test_append_node_rejects_duplicate_node_id(sandbox):
    uid, char_id = "u5", "yexuan"
    arc_id = sl.open_arc(uid, char_id=char_id, title="弧线D", tags=[])
    now = time.time()
    sl.append_node(uid, char_id=char_id, arc_id=arc_id, summary="节点1", ts=now, node_id="n_fixed")
    result = sl.append_node(uid, char_id=char_id, arc_id=arc_id, summary="节点2", ts=now + 10, node_id="n_fixed")
    assert result is None
    data = sl.load(uid, char_id=char_id)
    arc = next(a for a in data["arcs"] if a["arc_id"] == arc_id)
    assert len(arc["nodes"]) == 1


# ── 5. 节点上限 ──────────────────────────────────────────────────────────────

def test_append_node_rejects_when_arc_at_node_cap(sandbox):
    uid, char_id = "u6", "yexuan"
    arc_id = sl.open_arc(uid, char_id=char_id, title="弧线E", tags=[])
    now = time.time()
    for i in range(sl.MAX_NODES_PER_ARC):
        r = sl.append_node(uid, char_id=char_id, arc_id=arc_id, summary=f"节点{i}", ts=now + i)
        assert r is not None
    overflow = sl.append_node(uid, char_id=char_id, arc_id=arc_id, summary="超限节点", ts=now + 999)
    assert overflow is None
    data = sl.load(uid, char_id=char_id)
    arc = next(a for a in data["arcs"] if a["arc_id"] == arc_id)
    assert len(arc["nodes"]) == sl.MAX_NODES_PER_ARC


# ── 6. set_arc_status ────────────────────────────────────────────────────────

def test_set_arc_status_updates(sandbox):
    uid, char_id = "u7", "yexuan"
    arc_id = sl.open_arc(uid, char_id=char_id, title="弧线F", tags=[])
    ok = sl.set_arc_status(uid, char_id=char_id, arc_id=arc_id, status="dormant")
    assert ok is True
    data = sl.load(uid, char_id=char_id)
    arc = next(a for a in data["arcs"] if a["arc_id"] == arc_id)
    assert arc["status"] == "dormant"


def test_set_arc_status_invalid_raises(sandbox):
    uid, char_id = "u8", "yexuan"
    arc_id = sl.open_arc(uid, char_id=char_id, title="弧线G", tags=[])
    with pytest.raises(ValueError):
        sl.set_arc_status(uid, char_id=char_id, arc_id=arc_id, status="not_a_status")


def test_set_arc_status_unknown_arc_returns_false(sandbox):
    uid, char_id = "u9", "yexuan"
    assert sl.set_arc_status(uid, char_id=char_id, arc_id="arc_nonexistent", status="closed") is False


# ── 7. 总 arcs 超限淘汰 ──────────────────────────────────────────────────────

def test_total_arcs_eviction_archives_oldest_closed(sandbox):
    uid, char_id = "u10", "yexuan"
    arc_ids = []
    for i in range(sl.MAX_TOTAL_ARCS):
        aid = sl.open_arc(uid, char_id=char_id, title=f"弧线{i}", tags=[])
        arc_ids.append(aid)
    # 把第一个标为 closed，且更新时间最旧
    sl.set_arc_status(uid, char_id=char_id, arc_id=arc_ids[0], status="closed")

    # 再开一条，触发超限淘汰
    sl.open_arc(uid, char_id=char_id, title="新弧线", tags=[])

    data = sl.load(uid, char_id=char_id)
    assert len(data["arcs"]) <= sl.MAX_TOTAL_ARCS
    remaining_ids = {a["arc_id"] for a in data["arcs"]}
    assert arc_ids[0] not in remaining_ids

    archive_path = resolve_path(_scope(uid, char_id), "storyline_archive")
    assert archive_path.exists()
    content = archive_path.read_text(encoding="utf-8")
    assert "弧线0" in content


def test_arcs_over_limit_without_closed_skips_eviction(sandbox):
    uid, char_id = "u11", "yexuan"
    for i in range(sl.MAX_TOTAL_ARCS + 1):
        sl.open_arc(uid, char_id=char_id, title=f"活跃弧线{i}", tags=[])
    data = sl.load(uid, char_id=char_id)
    # 没有 closed arc 可淘汰，允许暂时超限（fail-open）
    assert len(data["arcs"]) == sl.MAX_TOTAL_ARCS + 1


# ── 8. 公开写 API 面：不存在修改既有 node 的路径 ─────────────────────────────

def test_no_public_api_to_mutate_existing_node(sandbox):
    """append-only 硬约束的静态断言：模块公开函数只有 open_arc/append_node/set_arc_status/
    load/save_meta/list_recallable_arcs，不存在任何 update_node / edit_node / modify 类接口。"""
    public_funcs = {n for n in dir(sl) if not n.startswith("_") and callable(getattr(sl, n))}
    forbidden_keywords = ("update_node", "edit_node", "modify_node", "rewrite_node", "set_node")
    for name in public_funcs:
        assert not any(kw in name for kw in forbidden_keywords), f"发现疑似改写既有 node 的公开接口: {name}"


# ── 9. 路径一致性 ────────────────────────────────────────────────────────────

def test_storyline_path_matches_resolver(sandbox):
    uid, char_id = "u12", "yexuan"
    sl.open_arc(uid, char_id=char_id, title="路径测试", tags=[])
    expected = resolve_path(_scope(uid, char_id), "storyline")
    assert expected.exists()
    stored = json.loads(expected.read_text(encoding="utf-8"))
    assert stored["version"] == sl.SCHEMA_VERSION
