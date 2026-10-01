"""工单 F：日记素材按时段取样，不再尾部硬截断。

不跑真实 LLM：生成层用假 chat 记录 prompt，只验证输入素材的覆盖面与结构。
"""
import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from core.memory.event_log_sampling import sample_event_log_by_period
from tests.fixtures.public_assets import TEST_CHAR_ID


def _turn(hhmm: str, tag: str, filler: int = 800, source: str = "") -> str:
    meta = f"> emotion:calm speaker:owner{(' source:' + source) if source else ''}"
    return f"\n## {hhmm}\n**用户**：{tag}{'啊' * filler}\n{meta}\n**Companion**：回应{tag}\n---\n"


def _long_log(date: str = "2026-09-19") -> str:
    parts = [f"# {date}"]
    for hh in range(8, 24):
        for mm in ("05", "35"):
            parts.append(_turn(f"{hh:02d}:{mm}", f"事件{hh:02d}{mm}"))
    return "\n".join(parts)


def test_early_periods_survive_when_log_far_exceeds_budget():
    raw = _long_log()
    assert len(raw) > 9000 * 2
    sampled = sample_event_log_by_period(raw, 9000)
    assert len(sampled) <= 9000
    # 旧实现 [-9000:] 只剩傍晚/夜间；现在上午、下午都在
    assert "事件0805" in sampled or "事件0835" in sampled
    assert any(f"事件{h}" in sampled for h in ("12", "13", "14", "15"))
    assert "事件2335" in sampled
    assert "时段中间部分略" in sampled
    assert sampled.count("# 2026-09-19") == 1  # 日期头只留一份


def test_under_budget_keeps_everything_in_order():
    raw = _long_log().replace("啊" * 800, "啊")
    sampled = sample_event_log_by_period(raw, 20000)
    assert "略" not in sampled
    assert sampled.index("事件0805") < sampled.index("事件2335")
    assert sampled.count("**用户**：") == 32
    assert "> emotion:calm speaker:owner" in sampled


def test_isolated_source_blocks_are_filtered():
    raw = "# 2026-09-19\n" + _turn("09:00", "普通") + _turn("10:00", "梦里", source="dream_echo") \
        + _turn("11:00", "网页", source="web")
    sampled = sample_event_log_by_period(raw, 5000)
    assert "普通" in sampled and "梦里" not in sampled and "网页" not in sampled


def test_single_huge_block_keeps_head_and_tail():
    raw = "## 10:00\n" + "头" * 100 + "中" * 20000 + "尾" * 100
    sampled = sample_event_log_by_period(raw, 1000)
    assert len(sampled) <= 1000 and "头" in sampled and "尾" in sampled


def test_cross_midnight_dates_each_get_budget():
    raw = _long_log("2026-09-19") + "\n" + _long_log("2026-09-20")
    sampled = sample_event_log_by_period(raw, 6000)
    assert len(sampled) <= 6000
    assert "# 2026-09-19" in sampled and "# 2026-09-20" in sampled


def _prep(monkeypatch, raw, **kw):
    from core.scheduler.triggers import time_based

    monkeypatch.setattr("core.memory.event_log.get_recent_days", lambda *a, **k: raw)
    monkeypatch.setattr(time_based, "_collect_diary_voice", lambda cid: ("p" * 500, "v" * 400, "m" * 200))
    monkeypatch.setattr(time_based, "get_char_name", lambda cid: "Companion")
    return time_based, time_based._prepare_diary_work_context("owner", TEST_CHAR_ID, **kw)


def test_work_context_budget_is_actual_and_keeps_early_events(monkeypatch):
    from core.agent_runtime.work_sessions import MAX_CONTEXT_CHARS

    time_based, context = _prep(monkeypatch, _long_log())
    assert context is not None
    serialized = json.dumps(context, ensure_ascii=False, sort_keys=True)
    assert len(serialized) <= MAX_CONTEXT_CHARS
    log = context["today_log"]
    assert any(f"事件{h}" in log for h in ("08", "09", "10", "11"))
    assert "事件2335" in log
    # 预算按实际可用值算：接近上限而非写死 9000 的保守值
    assert len(serialized) > MAX_CONTEXT_CHARS - 1500


def test_work_context_escape_heavy_log_still_fits(monkeypatch):
    from core.agent_runtime.work_sessions import MAX_CONTEXT_CHARS

    raw = "# 2026-09-19\n" + "".join(
        f"\n## {h:02d}:00\n**用户**：\"引号\"\n换行\\\n" * 40 for h in (9, 15, 21)
    )
    _, context = _prep(monkeypatch, raw)
    assert context is not None
    assert len(json.dumps(context, ensure_ascii=False, sort_keys=True)) <= MAX_CONTEXT_CHARS
    assert "## 09:00" in context["today_log"]


def test_backfill_uses_same_path_and_keeps_early_events(monkeypatch):
    """diary_backfill 走同一 _prepare_diary_work_context(target_date=)。"""
    _, context = _prep(monkeypatch, _long_log("2026-09-19"), target_date="2026-09-19")
    assert context["target_date"] == "2026-09-19"
    assert "事件0805" in context["today_log"] or "事件0835" in context["today_log"]


@pytest.mark.asyncio
async def test_generation_prompts_embed_sampled_log_and_keep_structure(monkeypatch):
    time_based, context = _prep(monkeypatch, _long_log())
    prompts = []

    async def fake_chat(*, messages, max_tokens_override=None, char_id=None):
        prompts.append(messages[0]["content"])
        return "## 今日事件\n- 09:05 早上的事" if len(prompts) == 1 else "感受"

    monkeypatch.setattr("core.llm_client.chat", fake_chat)
    material = await time_based._generate_diary_material(context, TEST_CHAR_ID)
    assert material and "## 今日事件" in material["facts"]
    assert "提取今天发生的客观事件" in prompts[0] and "## 今日事件" in prompts[0]
    assert "事件0805" in prompts[0] or "事件0835" in prompts[0]
    assert "事件0805" in prompts[1] or "事件0835" in prompts[1]
