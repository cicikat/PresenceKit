"""
tests/test_activity_pool_weighted.py — Brief 78 池结构升级：加权 + domain 锚定

覆盖：
  1. 带 weight 的池 → 加权分布符合预期（固定 random seed）
  2. growth domain 对齐：active 兴趣命中 domain → 权重上调；未命中/空列表 → 权重下调
  3. active_interests 读取异常 → fail-open，退回纯 weight 加权
  4. 全池 weight=0 → 退回均匀抽样，不抛错
  5. suppress_growth 覆盖面：domain 非空的池活动同样被抑制
  6. thinking_pool：episodic 来源优先；thinking_pool 文本不套"好像"前缀
"""
from __future__ import annotations

import random

import pytest

import core.activity_manager as am


def _count_picks(arc: str, char_id: str, n: int, seed: int) -> dict:
    random.seed(seed)
    counts: dict = {}
    for _ in range(n):
        picked = am._pick_activity(arc, char_id=char_id)
        counts[picked["id"]] = counts.get(picked["id"], 0) + 1
    return counts


# ── 1. 加权分布（固定 seed） ───────────────────────────────────────────────────

def test_weighted_pick_favors_higher_weight_entry(monkeypatch):
    pool = [
        {"id": "low", "text": "低权重", "arcs": ["afternoon"], "weight": 1.0},
        {"id": "high", "text": "高权重", "arcs": ["afternoon"], "weight": 3.0},
    ]
    monkeypatch.setattr(am, "_load_pool", lambda char_id="yexuan": pool)
    monkeypatch.setattr(
        "core.growth.interest_state.active_interests",
        lambda _: (_ for _ in ()).throw(RuntimeError("no growth module")),
    )

    counts = _count_picks("afternoon", "yexuan", 2000, seed=42)
    fraction_high = counts.get("high", 0) / 2000
    assert 0.65 <= fraction_high <= 0.85, f"权重3:1 期望约0.75，实际 {fraction_high}"


def test_zero_total_weight_falls_back_to_uniform(monkeypatch):
    pool = [
        {"id": "a", "text": "A", "arcs": ["afternoon"], "weight": 0},
        {"id": "b", "text": "B", "arcs": ["afternoon"], "weight": 0},
        {"id": "c", "text": "C", "arcs": ["afternoon"], "weight": 0},
    ]
    monkeypatch.setattr(am, "_load_pool", lambda char_id="yexuan": pool)
    monkeypatch.setattr("core.growth.interest_state.active_interests", lambda _: [])

    for _ in range(20):
        picked = am._pick_activity("afternoon", char_id="yexuan")
        assert picked["id"] in {"a", "b", "c"}


def test_legacy_pool_without_new_fields_behaves_unchanged(monkeypatch):
    """老池只有 id/text/arcs（无 weight/domain）→ 视为 weight=1.0 均匀抽，不抛错。"""
    pool = [
        {"id": "x", "text": "X", "arcs": ["afternoon"]},
        {"id": "y", "text": "Y", "arcs": ["afternoon"]},
    ]
    monkeypatch.setattr(am, "_load_pool", lambda char_id="yexuan": pool)
    monkeypatch.setattr("core.growth.interest_state.active_interests", lambda _: [])

    counts = _count_picks("afternoon", "yexuan", 1000, seed=7)
    fraction_x = counts.get("x", 0) / 1000
    assert 0.40 <= fraction_x <= 0.60


# ── 2. growth domain 对齐 ─────────────────────────────────────────────────────

def test_domain_matching_active_interest_is_upweighted(monkeypatch):
    pool = [
        {"id": "flavor", "text": "漫游", "arcs": ["afternoon"], "weight": 1.0},
        {"id": "writing_practice", "text": "写作", "arcs": ["afternoon"], "weight": 1.0, "domain": "writing"},
    ]
    monkeypatch.setattr(am, "_load_pool", lambda char_id="yexuan": pool)
    monkeypatch.setattr(
        "core.growth.interest_state.active_interests",
        lambda _: [{"id": "int1", "name": "写作", "domain": "writing", "status": "active"}],
    )

    counts = _count_picks("afternoon", "yexuan", 2000, seed=1)
    fraction_writing = counts.get("writing_practice", 0) / 2000
    # 1.5 : 1 → 期望约 0.6
    assert 0.50 <= fraction_writing <= 0.70


def test_domain_without_matching_active_interest_is_downweighted(monkeypatch):
    pool = [
        {"id": "flavor", "text": "漫游", "arcs": ["afternoon"], "weight": 1.0},
        {"id": "writing_practice", "text": "写作", "arcs": ["afternoon"], "weight": 1.0, "domain": "writing"},
    ]
    monkeypatch.setattr(am, "_load_pool", lambda char_id="yexuan": pool)
    monkeypatch.setattr(
        "core.growth.interest_state.active_interests",
        lambda _: [{"id": "int1", "name": "弹琴", "domain": "music", "status": "active"}],
    )

    counts = _count_picks("afternoon", "yexuan", 2000, seed=2)
    fraction_writing = counts.get("writing_practice", 0) / 2000
    # 0.3 : 1 → 期望约 0.23
    assert 0.12 <= fraction_writing <= 0.34


def test_empty_active_interests_downweights_all_domain_entries(monkeypatch):
    """active_interests 返回空列表（无兴趣/growth 数据缺失）→ 按"无该 domain"处理，×0.3。"""
    pool = [
        {"id": "flavor", "text": "漫游", "arcs": ["afternoon"], "weight": 1.0},
        {"id": "writing_practice", "text": "写作", "arcs": ["afternoon"], "weight": 1.0, "domain": "writing"},
    ]
    monkeypatch.setattr(am, "_load_pool", lambda char_id="yexuan": pool)
    monkeypatch.setattr("core.growth.interest_state.active_interests", lambda _: [])

    counts = _count_picks("afternoon", "yexuan", 2000, seed=3)
    fraction_writing = counts.get("writing_practice", 0) / 2000
    assert 0.12 <= fraction_writing <= 0.34


def test_active_interests_read_error_fails_open_to_pure_weight(monkeypatch):
    """读取抛异常 → fail-open，退回纯 weight 加权，不因 domain 被压低。"""
    pool = [
        {"id": "flavor", "text": "漫游", "arcs": ["afternoon"], "weight": 1.0},
        {"id": "writing_practice", "text": "写作", "arcs": ["afternoon"], "weight": 1.0, "domain": "writing"},
    ]
    monkeypatch.setattr(am, "_load_pool", lambda char_id="yexuan": pool)

    def _raise(_):
        raise RuntimeError("growth module unavailable")

    monkeypatch.setattr("core.growth.interest_state.active_interests", _raise)

    counts = _count_picks("afternoon", "yexuan", 2000, seed=4)
    fraction_writing = counts.get("writing_practice", 0) / 2000
    # 两条都是 weight=1.0，忽略 domain → 约 0.5
    assert 0.40 <= fraction_writing <= 0.60


# ── 3. suppress_growth 覆盖面扩展 ─────────────────────────────────────────────

def test_suppress_growth_also_hides_domain_anchored_pool_activity(sandbox, monkeypatch):
    """domain 锚定的池活动（source 仍是 "pool"）在 suppress_growth=True 时同样返回""。"""
    monkeypatch.setattr(
        am, "_pick_activity",
        lambda arc, char_id="yexuan": {
            "id": "writing_practice", "text": "在写一段东西", "domain": "writing",
        },
    )
    monkeypatch.setattr(am, "_pick_recent_growth_activity", lambda char_id="yexuan", **kw: None)

    am.switch_activity(char_id="char_suppress_domain")

    assert am.get_prompt_fragment("char_suppress_domain", suppress_growth=True) == ""
    assert am.get_prompt_fragment("char_suppress_domain", suppress_growth=False) == "在写一段东西"


def test_suppress_growth_leaves_domainless_pool_activity_untouched(sandbox, monkeypatch):
    """domain 留空的纯 flavor 活动不受 suppress_growth 影响。"""
    monkeypatch.setattr(
        am, "_pick_activity",
        lambda arc, char_id="yexuan": {"id": "roaming", "text": "在时空中漫游"},
    )
    monkeypatch.setattr(am, "_pick_recent_growth_activity", lambda char_id="yexuan", **kw: None)

    am.switch_activity(char_id="char_suppress_flavor")

    assert am.get_prompt_fragment("char_suppress_flavor", suppress_growth=True) == "在时空中漫游"


# ── 4. thinking_pool：episodic 优先，静态文案不套"好像"前缀 ──────────────────

def test_thinking_pool_used_when_episodic_empty(sandbox, monkeypatch):
    monkeypatch.setattr(
        am, "_pick_activity",
        lambda arc, char_id="yexuan": {
            "id": "deducing", "text": "在推演一个可能性",
            "thinking_pool": ["每次都想换一种说法"],
        },
    )
    monkeypatch.setattr(am, "_pick_recent_growth_activity", lambda char_id="yexuan", **kw: None)
    monkeypatch.setattr(am, "_load_thinking_about", lambda uid, *, char_id="yexuan": "")

    state = am.switch_activity(char_id="char_thinking_pool")
    assert state["thinking_about"] == "每次都想换一种说法"
    assert state["thinking_source"] == "pool"

    # thinking_pool 文本不套"好像"前缀，即便命中 _PATTERN_WORDS
    text = am.get_prompt_fragment("char_thinking_pool")
    assert "好像" not in text
    assert "每次都想换一种说法" in text


def test_episodic_thinking_takes_priority_over_thinking_pool(sandbox, monkeypatch):
    monkeypatch.setattr(
        am, "_pick_activity",
        lambda arc, char_id="yexuan": {
            "id": "watching_you", "text": "在看你",
            "thinking_about_eligible": True,
            "thinking_pool": ["静态候选文案"],
        },
    )
    monkeypatch.setattr(am, "_pick_recent_growth_activity", lambda char_id="yexuan", **kw: None)
    monkeypatch.setattr(am, "_load_thinking_about", lambda uid, *, char_id="yexuan": "每次都在等你消息")

    state = am.switch_activity(char_id="char_thinking_episodic")
    assert state["thinking_about"] == "每次都在等你消息"
    assert state["thinking_source"] == "episodic"

    # episodic 来源命中 _PATTERN_WORDS 时套"好像"前缀
    text = am.get_prompt_fragment("char_thinking_episodic")
    assert "好像每次都在等你消息" in text
