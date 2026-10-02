"""工单 M4：混合分桶召回、去情绪同调、兜底改 long/repair、9.5 层去重、get_episodic 不加强度。"""
import time
from unittest.mock import patch

import pytest

import core.memory.episodic_memory as em
from core.memory.episodic_memory import retrieve_mixed, write_episode
from tests.fixtures.public_assets import TEST_CHAR_ID

CHAR = TEST_CHAR_ID
DAY = 86400.0


def _ep(eid, age_days, **kw):
    now = time.time()
    ts = now - age_days * DAY
    base = {
        "id": eid, "timestamp": ts, "occurred_at": ts, "raw_facts": ["讨论周末计划"],
        "topic_keywords": ["周末", "计划"], "emotion_peak": "neutral",
        "narrative_summary": f"独有叙述{eid}内容", "strength": 0.6, "status": "open",
    }
    base.update(kw)
    return base


def _seed(uid, eps):
    for e in eps:
        write_episode(uid, e, char_id=CHAR)


def test_mixed_one_per_bucket_with_repair(sandbox):
    uid = "m4a"
    _seed(uid, [
        _ep("e_recent", 1, narrative_summary="甲乙丙丁戊近期"),
        _ep("e_mid", 10, narrative_summary="庚辛壬癸子中期"),
        _ep("e_long", 90, narrative_summary="丑寅卯辰巳长期"),
        _ep("e_rep", 20, narrative_summary="午未申酉戌修复", episode_kind="conflict",
            outcome="repaired", repair_note="互相道歉"),
        _ep("e_recent2", 2, narrative_summary="亥天地玄黄另一条", strength=0.3),
    ])
    out = retrieve_mixed(uid, "周末计划", char_id=CHAR, history=[])
    buckets = {m["id"]: m["_bucket"] for m in out}
    assert buckets == {"e_recent": "recent", "e_mid": "mid", "e_long": "long", "e_rep": "repair"}
    assert len(out) == 4
    assert out[0]["_bucket"] in ("repair", "long")  # 顺序：repair → long → mid → recent
    # 修复点连同结果一起渲染
    text = em.format_for_prompt([m for m in out if m["id"] == "e_rep"], char_name="X")
    assert "后来：互相道歉（已和好）" in text


def test_recent_bucket_drops_items_already_in_history(sandbox):
    uid = "m4b"
    _seed(uid, [_ep("e_recent", 1, narrative_summary="我们聊了周末去爬山的计划")])
    out = retrieve_mixed(uid, "周末计划", char_id=CHAR, history=["我们聊了周末去爬山的计划"])
    assert out == []


def test_long_term_mode_zero_recent_and_widened_long_repair(sandbox):
    uid = "m4c"
    _seed(uid, [
        _ep("r1", 1, narrative_summary="近期条目一一一一一"),
        _ep("l1", 60, narrative_summary="长期条目二二二二二"),
        _ep("l2", 80, narrative_summary="长期条目三三三三三"),
        _ep("l3", 120, narrative_summary="长期条目四四四四四"),
        _ep("p1", 15, narrative_summary="修复条目五五五五五", outcome="repaired", episode_kind="conflict"),
        _ep("p2", 25, narrative_summary="修复条目六六六六六", outcome="clarified", episode_kind="conflict"),
    ])
    # 话题没有词面命中（长期问题典型）：仍应从 long / repair 桶补足
    out = retrieve_mixed(uid, "你怎么看我", char_id=CHAR, history=[], long_term=True)
    by = {}
    for m in out:
        by.setdefault(m["_bucket"], []).append(m["id"])
    assert "recent" not in by
    assert len(by["long"]) == 2 and len(by["repair"]) == 2


def test_mood_resonance_no_longer_boosts(sandbox):
    uid = "m4d"
    _seed(uid, [
        _ep("angry_mem", 10, emotion_peak="angry", narrative_summary="愤怒记忆甲甲甲甲甲"),
        _ep("calm_mem", 10, emotion_peak="neutral", narrative_summary="平静记忆乙乙乙乙乙"),
    ])
    with patch("core.memory.mood_state.get_current", return_value="angry"), \
         patch("core.memory.mood_state.get_intensity", return_value=1.0):
        _, trace = em.retrieve(uid, "周末计划", top_k=5, char_id=CHAR, allow_strengthen=False,
                               return_trace=True)
    scores = {t["id"]: t["score"] for t in trace}
    assert scores["angry_mem"] == scores["calm_mem"]


def test_fallback_no_longer_returns_recent_high_strength(sandbox):
    uid = "m4e"
    _seed(uid, [_ep("hot", 1, strength=0.95, emotion_peak="angry")])
    assert em.retrieve_fallback(uid, [], char_id=CHAR) == []


@pytest.mark.parametrize("bucket,expected", [
    ("recent", False), ("mid", False), ("long", True), ("repair", True), ("", True),
])
def test_episodic_top_layer_depends_on_bucket(sandbox, monkeypatch, bucket, expected):
    from tests.test_prompt_ablation import _apply_build_stubs, _base_build_kwargs
    import core.prompt_builder as _pb
    _apply_build_stubs(monkeypatch)
    messages, _ = _pb.build(**_base_build_kwargs(
        episodic_result="- 她记得一起看过日落", episodic_top_bucket=bucket,
    ))
    layers = [m.get("_layer") for m in messages]
    assert ("9.5_episodic_top" in layers) is expected
    assert "6c_episodic" in layers  # 6c 本体不受影响


def test_get_episodic_tool_does_not_strengthen(sandbox):
    import asyncio
    from core.tool_dispatcher import _get_episodic_wrapper
    uid = "m4f"
    _seed(uid, [_ep("x", 10, strength=0.5)])
    asyncio.run(_get_episodic_wrapper(uid, "周末计划", char_id=CHAR))
    mem = em._load_memories(uid, char_id=CHAR)[0]
    assert mem["strength"] == 0.5 and mem.get("retrieval_count", 0) == 0
