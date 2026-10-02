"""工单 M5：长期问题强制关系召回 + 相识日期事实。"""
import time
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from core.tag_rules import get_tags
from tests.fixtures.public_assets import TEST_CHAR_ID

CHAR = TEST_CHAR_ID
DAY = 86400.0


@pytest.mark.parametrize("text,hit", [
    ("我们认识多久了", True), ("你怎么看我", True), ("我们之间发生过什么", True),
    ("一路走来你有什么感觉", True), ("你还记得我们第一次吗", True), ("我们的关系现在怎样", True),
    ("我们去吃饭吧", False), ("之前那家店不错", False), ("你好呀", False), ("我今天好累", False),
])
def test_long_term_tag_rule(text, hit):
    assert ("query.relationship_longterm" in get_tags(text)) is hit


def test_format_span_fact_is_a_bare_date_sentence():
    from core.memory.relationship_span import format_span_fact
    now = datetime(2026, 10, 3, 12, 0).timestamp()
    first = datetime(2026, 6, 25, 9, 0).timestamp()
    text = format_span_fact(first, now)
    assert text == "你们第一次对话是在 2026-06-25（约 100 天前）。"
    assert format_span_fact(None) == ""


def _ep(eid, ts, **kw):
    base = {"id": eid, "timestamp": ts, "occurred_at": ts, "raw_facts": ["x"], "topic_keywords": ["x"],
            "emotion_peak": "neutral", "narrative_summary": f"独有叙述{eid}内容", "strength": 0.6,
            "status": "open"}
    base.update(kw)
    return base


def test_first_interaction_three_level_fallback(sandbox):
    from core.memory import relationship_span as rs
    from core.memory.episodic_memory import write_episode

    uid = "m5span"
    now = time.time()
    early = now - 200 * DAY
    write_episode(uid, _ep("e1", early + DAY), char_id=CHAR)
    write_episode(uid, _ep("e2", early + 5 * DAY, narrative_summary="另一条完全不同的叙述"), char_id=CHAR)

    # 级别 3：只有 episodic
    rs._reset_cache_for_tests()
    ts, src = rs.first_interaction_info(uid, CHAR)
    assert src == "episodic" and abs(ts - (early + DAY)) < 1

    # 级别 2：event_log 日文件（更早）优先于 episodic
    day_dir = sandbox.memory_char_root(char_id=CHAR) / uid / "event_log"
    day_dir.mkdir(parents=True, exist_ok=True)
    older = (datetime.now() - timedelta(days=300)).strftime("%Y-%m-%d")
    (day_dir / f"{older}.md").write_text("# x\n", encoding="utf-8")
    rs._reset_cache_for_tests()
    ts, src = rs.first_interaction_info(uid, CHAR)
    assert src == "event_log" and datetime.fromtimestamp(ts).strftime("%Y-%m-%d") == older

    # 级别 1：账本优先
    rs._reset_cache_for_tests()
    with patch.object(rs, "_from_ledger", return_value=123456.0):
        assert rs.first_interaction_info(uid, CHAR) == (123456.0, "ledger")

    # 全失败 → None
    rs._reset_cache_for_tests()
    with patch.object(rs, "_from_ledger", return_value=None), \
         patch.object(rs, "_from_event_log", return_value=None), \
         patch.object(rs, "_from_episodic", return_value=None):
        assert rs.first_interaction_at("nobody", CHAR) is None
    # 异常也逐级回退
    rs._reset_cache_for_tests()
    with patch.object(rs, "_from_ledger", side_effect=RuntimeError("boom")), \
         patch.object(rs, "_from_event_log", return_value=42.0):
        assert rs.first_interaction_info("u-x", CHAR) == (42.0, "event_log")
    rs._reset_cache_for_tests()


def test_first_interaction_from_real_ledger(sandbox):
    from core.memory import event_store, relationship_span as rs
    from core.memory.scope import MemoryScope
    uid = "m5ledger"
    scope = MemoryScope.reality_scope(uid, CHAR)
    t1 = time.time() - 50 * DAY
    for i, ts in enumerate((t1 + DAY, t1)):
        event_store.append_event(scope, {
            "event_id": f"{uid}_{i}:user", "turn_id": f"{uid}_{i}", "seq": i, "occurred_at": ts,
            "uid": uid, "char_id": CHAR, "realm": "reality", "kind": "message", "actor": "user",
            "raw_text": "hi", "visible_text": "hi", "memory_text": "hi",
        })
    rs._reset_cache_for_tests()
    ts, src = rs.first_interaction_info(uid, CHAR)
    assert src == "ledger" and abs(ts - t1) < 1
    rs._reset_cache_for_tests()


@pytest.mark.parametrize("span_text,expected", [("你们第一次对话是在 2026-06-25（约 100 天前）。", True), ("", False)])
def test_span_layer_only_when_text_given(sandbox, monkeypatch, span_text, expected):
    from tests.test_prompt_ablation import _apply_build_stubs, _base_build_kwargs
    import core.prompt_builder as _pb
    _apply_build_stubs(monkeypatch)
    messages, _ = _pb.build(**_base_build_kwargs(relationship_span_text=span_text))
    spans = [m for m in messages if m.get("_layer") == "2.56_relationship_span"]
    assert bool(spans) is expected
    if expected:
        assert spans[0]["content"] == span_text  # 只有日期句，无评价


def test_retrieve_mixed_long_term_shape_matches_m5_spec(sandbox):
    from core.memory.episodic_memory import retrieve_mixed, write_episode
    uid = "m5mix"
    now = time.time()
    texts = ["甲乙丙丁戊己庚", "辛壬癸子丑寅卯", "辰巳午未申酉戌", "亥天地玄黄宇宙", "洪荒日月盈昃", "辰宿列张寒来暑"]
    write_episode(uid, _ep("r", now - DAY, narrative_summary=texts[0]), char_id=CHAR)
    for i, age in enumerate((40, 60, 90)):
        write_episode(uid, _ep(f"l{i}", now - age * DAY, narrative_summary=texts[1 + i % 3] if i < 2 else texts[3]),
                      char_id=CHAR)
    for i, age in enumerate((10, 20)):
        write_episode(uid, _ep(f"p{i}", now - age * DAY, narrative_summary=texts[4 + i],
                               episode_kind="conflict", outcome="repaired"), char_id=CHAR)
    out = retrieve_mixed(uid, "我们的关系", char_id=CHAR, history=[], long_term=True)
    buckets = [m["_bucket"] for m in out]
    assert "recent" not in buckets
    assert buckets.count("long") == 2 and buckets.count("repair") == 2


from tests.test_n2_fetch_context_side_effects import (  # noqa: E402,F401  (fixtures + helpers)
    chars_tree, registry, _make_pipeline, _write_active, _apply_base_stubs, _run_fetch,
)


def _spy_env(monkeypatch):
    import core.memory.event_log as _el
    import core.memory.episodic_memory as _ep
    from unittest.mock import AsyncMock
    search = AsyncMock(return_value=("", []))
    monkeypatch.setattr(_el, "search", search)
    fb_calls, retrieve_calls = [], []

    def _fb(*a, **kw):
        fb_calls.append(1)
        return ([], []) if kw.get("return_trace") else []

    def _retrieve(*a, **kw):
        retrieve_calls.append(kw)
        return ([], []) if kw.get("return_trace") else []

    monkeypatch.setattr(_ep, "retrieve_fallback", _fb)
    monkeypatch.setattr(_ep, "retrieve", _retrieve)
    return search, fb_calls, retrieve_calls


def test_fetch_context_long_term_question_wires_everything(chars_tree, monkeypatch, sandbox, registry):
    from core.memory import relationship_span as rs
    pipeline = _make_pipeline(TEST_CHAR_ID, registry)
    _write_active(sandbox, TEST_CHAR_ID)
    _apply_base_stubs(monkeypatch)
    search, fb_calls, _ = _spy_env(monkeypatch)
    day_dir = sandbox.memory_char_root(char_id=TEST_CHAR_ID) / "u1" / "event_log"
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d.md")).write_text("# x", encoding="utf-8")
    monkeypatch.setattr(rs, "first_interaction_info", lambda uid, cid: (time.time() - 100 * DAY, "ledger"))

    ctx = _run_fetch(pipeline, content="我们认识多久了")
    assert ctx["long_term_query"] is True
    assert "你们第一次对话是在" in ctx["relationship_span_text"] and "约 100 天前" in ctx["relationship_span_text"]
    assert fb_calls == []  # 兜底不跑
    assert search.call_args.kwargs["days"] >= 100  # event_log 时间窗放宽到全部可用天数


def test_fetch_context_ordinary_question_has_no_span_and_default_window(chars_tree, monkeypatch, sandbox, registry):
    pipeline = _make_pipeline(TEST_CHAR_ID, registry)
    _write_active(sandbox, TEST_CHAR_ID)
    _apply_base_stubs(monkeypatch)
    search, fb_calls, retrieve_calls = _spy_env(monkeypatch)
    ctx = _run_fetch(pipeline, content="我们去吃饭吧")
    assert ctx["long_term_query"] is False and ctx["relationship_span_text"] == ""
    assert "days" not in search.call_args.kwargs  # 默认窗口（30 天）不额外传参
    assert fb_calls == [1]
