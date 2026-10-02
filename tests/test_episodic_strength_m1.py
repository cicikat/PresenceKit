"""工单 M1：强度不再因情绪/冲突加成、统一衰减不复利、非默认桶被衰减、重算脚本 dry-run/apply。"""
import importlib.util
import json
import math
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.fixtures.public_assets import TEST_CHAR_ID

DAY = 86400.0


def _ep(eid, **kw):
    base = {
        "id": eid, "timestamp": time.time(), "raw_facts": [eid], "topic_keywords": ["a"],
        "emotion_peak": "neutral", "narrative_summary": f"独特叙述{eid}", "summary": eid,
        "strength": 0.5,
    }
    base.update(kw)
    return base


def test_write_episode_no_emotion_or_conflict_bonus(sandbox):
    from core.memory.episodic_memory import write_episode, _load_memories
    write_episode("u1", _ep("e_angry", emotion_peak="angry", topic_keywords=["吵架", "和好"],
                            narrative_summary="甲甲甲甲甲", strength=0.5), char_id=TEST_CHAR_ID)
    write_episode("u1", _ep("e_happy", emotion_peak="happy", narrative_summary="乙乙乙乙乙",
                            strength=0.5), char_id=TEST_CHAR_ID)
    got = {m["id"]: m for m in _load_memories("u1", char_id=TEST_CHAR_ID)}
    assert got["e_angry"]["strength"] == 0.5
    assert got["e_happy"]["strength"] == 0.5


def test_decay_is_emotion_independent_and_initialises_first(sandbox):
    from core.memory.episodic_memory import write_episode, decay_all, _load_memories
    write_episode("u2", _ep("s", emotion_peak="sad", narrative_summary="丙丙丙丙丙"), char_id=TEST_CHAR_ID)
    write_episode("u2", _ep("n", emotion_peak="neutral", narrative_summary="丁丁丁丁丁"), char_id=TEST_CHAR_ID)
    t0 = time.time()
    decay_all("u2", char_id=TEST_CHAR_ID, now=t0)  # 初始化 last_decay_at，不衰减
    assert all(m["strength"] == 0.5 for m in _load_memories("u2", char_id=TEST_CHAR_ID))
    decay_all("u2", char_id=TEST_CHAR_ID, now=t0 + DAY)
    got = {m["id"]: m["strength"] for m in _load_memories("u2", char_id=TEST_CHAR_ID)}
    assert got["s"] == got["n"]
    assert math.isclose(got["s"], 0.5 * math.exp(-0.03), rel_tol=1e-6)


def test_two_daily_decays_equal_one_two_day_decay(sandbox):
    from core.memory.episodic_memory import write_episode, decay_all, _load_memories
    write_episode("u3", _ep("a", narrative_summary="戊戊戊戊戊"), char_id=TEST_CHAR_ID)
    t0 = time.time()
    decay_all("u3", char_id=TEST_CHAR_ID, now=t0)
    decay_all("u3", char_id=TEST_CHAR_ID, now=t0 + DAY)
    decay_all("u3", char_id=TEST_CHAR_ID, now=t0 + 2 * DAY)
    two_step = _load_memories("u3", char_id=TEST_CHAR_ID)[0]["strength"]
    assert math.isclose(two_step, 0.5 * math.exp(-0.03 * 2), rel_tol=1e-6)


def test_decay_hits_non_default_char_bucket_and_scheduler_iterates_chars(sandbox):
    from core.memory.episodic_memory import write_episode, _load_memories
    write_episode("u4", _ep("x", narrative_summary="己己己己己", last_decay_at=time.time() - 2 * DAY),
                  char_id=TEST_CHAR_ID)
    reg = MagicMock()
    entry = MagicMock()
    entry.id = TEST_CHAR_ID
    reg.list_all.return_value = [entry]
    import asyncio
    from datetime import datetime
    from core.scheduler.triggers import time_based
    with patch.object(time_based, "datetime") as dt, \
         patch.object(time_based, "_is_ready", return_value=True), \
         patch.object(time_based, "_mark"), \
         patch.object(time_based, "_owner_id", return_value="u4"), \
         patch("core.asset_registry.get_registry", return_value=reg):
        dt.now.return_value = datetime(2026, 1, 1, 23, 30)
        asyncio.run(time_based._check_episodic_decay())
    assert _load_memories("u4", char_id=TEST_CHAR_ID)[0]["strength"] < 0.5


def _script():
    spec = importlib.util.spec_from_file_location(
        "rebalance_script", Path(__file__).resolve().parent.parent / "scripts" / "rebalance_episodic_strength.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed(sandbox):
    from core.memory.episodic_memory import _save_memories
    mems = [
        _ep("c1", emotion_peak="angry", topic_keywords=["吵架"], strength=0.9),
        _ep("c2", emotion_peak="happy", strength=0.7),
        _ep("c3", is_core=True, emotion_peak="sad", strength=0.9),
        _ep("c4", strength=0.15, emotion_peak="sad"),
    ]
    _save_memories("u5", mems, char_id=TEST_CHAR_ID)
    return sandbox.memory_char_root(char_id=TEST_CHAR_ID) / "u5" / "episodic.json"


def test_rebalance_dry_run_does_not_touch_file(sandbox, capsys):
    path = _seed(sandbox)
    before = path.read_bytes()
    mod = _script()
    rep = mod.process_bucket(TEST_CHAR_ID, "u5", path, apply=False)
    assert rep["total"] == 4 and rep["will_change"] == 3
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.bak*"))


def test_rebalance_apply_backs_up_and_applies_rules(sandbox):
    path = _seed(sandbox)
    mod = _script()
    rep = mod.process_bucket(TEST_CHAR_ID, "u5", path, apply=True)
    assert rep["backup"] and list(path.parent.glob("episodic.json.pre_rebalance_*.bak"))
    got = {m["id"]: m for m in json.loads(path.read_text(encoding="utf-8"))}
    assert got["c1"]["strength"] == 0.6 and got["c1"]["emotional_intensity"] == 0.9  # -0.1 -0.2
    assert got["c2"]["strength"] == 0.65
    assert got["c3"]["strength"] == 0.9  # core 不动
    assert got["c4"]["strength"] == 0.1  # 下限
    # 幂等：再次执行不二次扣减
    mod.process_bucket(TEST_CHAR_ID, "u5", path, apply=True)
    again = {m["id"]: m for m in json.loads(path.read_text(encoding="utf-8"))}
    assert again["c1"]["strength"] == 0.6
