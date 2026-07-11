"""
tests/test_event_log_salvage.py — Brief 46 §2 event_log_salvage 调度触发器测试

覆盖（Brief 46 §4.3 / §4.4）：
  - age 28 天文件被处理且 fixation_state.salvaged_dates 记录
  - 再跑一遍时同一文件被跳过（幂等）
  - age 10 天文件不处理
  - 单日 >3 个到期文件只处理 3 个
  - 抢救产物经 Brief 45 的 noop 去重：种子 profile 已含同义事实时 facts 数量不增
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── 辅助 fixture / helper ─────────────────────────────────────────────────────

@pytest.fixture
def fake_llm():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="[]")
    return llm


@pytest.fixture(autouse=True)
def patch_llm_client(fake_llm):
    with patch("core.llm_client", fake_llm, create=True):
        yield fake_llm


def _make_registry(*char_ids: str) -> MagicMock:
    reg = MagicMock()
    entries = []
    for cid in char_ids:
        e = MagicMock()
        e.id = cid
        entries.append(e)
    reg.list_all.return_value = entries
    return reg


def _write_day_file(sandbox, char_id: str, uid: str, date_str: str, content: str = "## 10:00\n**用户**：我喜欢喝咖啡\n**叶瑄**：好呀\n> emotion:gentle intensity:0 speaker:assistant\n---\n") -> None:
    day_dir = sandbox.memory_char_root(char_id=char_id) / uid / "event_log"
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / f"{date_str}.md").write_text(content, encoding="utf-8")


def _date_n_days_ago(n: int) -> str:
    return (datetime.now().date() - timedelta(days=n)).strftime("%Y-%m-%d")


def _run_salvage():
    from core.scheduler.triggers.event_log_salvage import _check_event_log_salvage
    with patch("core.scheduler.loop._is_ready", return_value=True), \
         patch("core.scheduler.loop._mark"), \
         patch("core.asset_registry.get_registry", return_value=_make_registry("yexuan")):
        asyncio.run(_check_event_log_salvage())


def _salvaged_dates(uid: str, char_id: str = "yexuan") -> set:
    from core.memory.fixation_pipeline import _load_fixation_state
    return set(_load_fixation_state(uid, char_id=char_id).get("salvaged_dates") or [])


# ── age 28 天：应被处理并记录 salvaged_dates ──────────────────────────────────

def test_salvage_processes_file_at_age_28_and_marks_salvaged(sandbox, fake_llm):
    uid = "u_salvage_28"
    date_str = _date_n_days_ago(28)
    _write_day_file(sandbox, "yexuan", uid, date_str)

    _run_salvage()

    assert date_str in _salvaged_dates(uid)
    fake_llm.chat.assert_awaited()


# ── 幂等：再跑一遍跳过 ────────────────────────────────────────────────────────

def test_salvage_second_run_is_idempotent(sandbox, fake_llm):
    uid = "u_salvage_idem"
    date_str = _date_n_days_ago(28)
    _write_day_file(sandbox, "yexuan", uid, date_str)

    _run_salvage()
    assert fake_llm.chat.await_count == 1

    _run_salvage()
    assert fake_llm.chat.await_count == 1, "已抢救过的日期不应重复调用 LLM"


# ── age 10 天：不在窗口内，不处理 ──────────────────────────────────────────────

def test_salvage_skips_file_at_age_10(sandbox, fake_llm):
    uid = "u_salvage_10"
    date_str = _date_n_days_ago(10)
    _write_day_file(sandbox, "yexuan", uid, date_str)

    _run_salvage()

    assert date_str not in _salvaged_dates(uid)
    fake_llm.chat.assert_not_awaited()


# ── 单日 >3 个到期文件：只处理 3 个 ────────────────────────────────────────────

def test_salvage_caps_at_three_files_per_run(sandbox, fake_llm):
    """需要 >3 个候选文件；用两个 uid 各放几份，凑出 5 个候选（27/28/29 天窗口内）。"""
    uid = "u_salvage_cap"
    for d in (27, 28, 29):
        _write_day_file(sandbox, "yexuan", f"{uid}_a", _date_n_days_ago(d))
    for d in (27, 28):
        _write_day_file(sandbox, "yexuan", f"{uid}_b", _date_n_days_ago(d))

    _run_salvage()

    processed = len(_salvaged_dates(f"{uid}_a")) + len(_salvaged_dates(f"{uid}_b"))
    assert processed == 3, f"单轮应只处理 3 个到期文件，实际处理了 {processed} 个"
    assert fake_llm.chat.await_count == 3


# ── noop 去重：种子 profile 已含同义事实时 facts 数量不增（Brief 45 联动）────────

def test_salvage_facts_route_through_noop_dedup(sandbox, fake_llm):
    from core.memory import user_profile as _up

    uid = "u_salvage_noop"
    date_str = _date_n_days_ago(28)
    _write_day_file(sandbox, "yexuan", uid, date_str)

    asyncio.run(_up.update(uid, {
        "important_facts": [{"text": "喜欢喝咖啡", "tag": "pref.food", "ts": 0.0}],
    }))
    before_count = len(_up.load(uid).get("important_facts") or [])

    fake_llm.chat = AsyncMock(return_value=json.dumps([
        {"op": "noop", "target_index": 0, "text": "", "tag": "pref.food", "ts": 1000.0},
    ], ensure_ascii=False))

    _run_salvage()

    after_count = len(_up.load(uid).get("important_facts") or [])
    assert after_count == before_count, "noop 应丢弃语义重复候选，facts 数量不应增加"
    assert date_str in _salvaged_dates(uid)


def test_salvage_facts_add_op_appends_new_fact(sandbox, fake_llm):
    """add op 应正常追加新事实（走 Brief 45 冲突裁决入口，行为与 add 一致）。"""
    from core.memory import user_profile as _up

    uid = "u_salvage_add"
    date_str = _date_n_days_ago(28)
    _write_day_file(sandbox, "yexuan", uid, date_str)

    fake_llm.chat = AsyncMock(return_value=json.dumps([
        {"op": "add", "target_index": None, "text": "打算下个月搬家", "tag": "status.project", "ts": 1000.0},
    ], ensure_ascii=False))

    _run_salvage()

    facts = _up.load(uid).get("important_facts") or []
    assert any(f.get("text") == "打算下个月搬家" for f in facts)
    assert date_str in _salvaged_dates(uid)


# ── LLM 失败：不标记 salvaged，留待窗口内下次重试 ─────────────────────────────

def test_salvage_llm_failure_does_not_mark_salvaged(sandbox, fake_llm):
    uid = "u_salvage_fail"
    date_str = _date_n_days_ago(28)
    _write_day_file(sandbox, "yexuan", uid, date_str)

    fake_llm.chat = AsyncMock(side_effect=RuntimeError("boom"))

    _run_salvage()  # 不应抛异常

    assert date_str not in _salvaged_dates(uid)
