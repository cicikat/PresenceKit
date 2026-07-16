"""
tests/test_storyline_weekly.py — Brief 80 §2 storyline 周频聚合触发器测试

Covers:
1. 冷却未到时不运行
2. 无任何新素材（无新 episodic/无 inbox/无 event_log 新内容）→ 不调用 LLM，幂等 no-op
3. LLM 输出合法 ops → 落盘 open_arc/append_node/set_status，cursor 前进，inbox 被清空
4. LLM 输出非法 JSON → fail-open：不动 cursor、不抛异常
5. event_log 里带 source: 标记的块不得进入 LLM 输入（复用 Brief 79 过滤）
6. 空 registry → warning + 不调用 LLM
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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


def _run_weekly():
    from core.scheduler.triggers.storyline_weekly import _check_storyline_weekly
    with patch("core.scheduler.loop._is_ready", return_value=True), \
         patch("core.scheduler.loop._mark"), \
         patch("core.asset_registry.get_registry", return_value=_make_registry("yexuan")):
        asyncio.run(_check_storyline_weekly())


def _write_episode(uid: str, char_id: str, summary: str, ts: float) -> None:
    from core.memory.episodic_memory import write_episode
    write_episode(uid, {
        "id": f"ep_{int(ts * 1000)}",
        "timestamp": ts,
        "raw_facts": [summary],
        "topic_keywords": [],
        "emotion_peak": "neutral",
        "narrative_summary": summary,
        "summary": summary,
        "strength": 0.6,
    }, char_id=char_id)


def _write_day_file(sandbox, char_id: str, uid: str, date_str: str, content: str) -> None:
    day_dir = sandbox.memory_char_root(char_id=char_id) / uid / "event_log"
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / f"{date_str}.md").write_text(content, encoding="utf-8")


# ── 1. 冷却未到时不运行 ───────────────────────────────────────────────────────

def test_skips_when_not_ready(fake_llm):
    with patch("core.scheduler.loop._is_ready", return_value=False):
        asyncio.run(__import__(
            "core.scheduler.triggers.storyline_weekly", fromlist=["_check_storyline_weekly"]
        )._check_storyline_weekly())
    fake_llm.chat.assert_not_awaited()


# ── 2. 无新素材 → 不调用 LLM，幂等 no-op ──────────────────────────────────────

def test_no_new_material_skips_llm_call(sandbox, fake_llm):
    uid = "u_empty"
    # 只需要 episodic.json 存在以进入遍历（write_episode 即建文件），但 timestamp 早于 last_aggregated_at
    _write_episode(uid, "yexuan", "很久以前的事", ts=1.0)

    from core.memory import storyline as sl
    sl.save_meta(uid, char_id="yexuan", last_aggregated_at=time.time(), event_log_cursor="")

    _run_weekly()

    fake_llm.chat.assert_not_awaited()


# ── 3. 合法 ops 正常落盘 + cursor 前进 + inbox 清空 ───────────────────────────

def test_valid_ops_applied_and_cursor_advances(sandbox, fake_llm):
    uid = "u_valid"
    _write_episode(uid, "yexuan", "决定转行做程序员", ts=time.time())

    from core.memory import storyline as sl
    sl.append_to_inbox(uid, [{"id": "old1", "summary": "旧碎片", "ts": time.time(), "strength": 0.3}],
                        char_id="yexuan")

    fake_llm.chat = AsyncMock(return_value=json.dumps([
        {"op": "open_arc", "title": "职业转型", "tags": ["topic.learning"]},
        {"op": "append_node", "arc_title": "职业转型", "summary": "决定转行做程序员",
         "ts": time.time(), "span": [time.time(), time.time()]},
    ], ensure_ascii=False))

    _run_weekly()

    data = sl.load(uid, char_id="yexuan")
    assert len(data["arcs"]) == 1
    arc = data["arcs"][0]
    assert arc["title"] == "职业转型"
    assert len(arc["nodes"]) == 1
    assert data["meta"]["last_aggregated_at"] > 0
    assert sl.load_inbox(uid, char_id="yexuan") == []


# ── 4. 非法 JSON → fail-open，不动 cursor ────────────────────────────────────

def test_invalid_llm_output_does_not_advance_cursor(sandbox, fake_llm):
    uid = "u_invalid"
    _write_episode(uid, "yexuan", "某件事", ts=time.time())

    from core.memory import storyline as sl
    before = sl.load(uid, char_id="yexuan")["meta"]["last_aggregated_at"]

    fake_llm.chat = AsyncMock(return_value="不是JSON也不是数组")

    _run_weekly()  # 不应抛异常

    after = sl.load(uid, char_id="yexuan")["meta"]["last_aggregated_at"]
    assert after == before, "LLM 输出不合法时不应推进 last_aggregated_at"


# ── 5. event_log 带 source 标记的块不得进入 LLM 输入 ─────────────────────────

def test_event_log_source_tagged_blocks_filtered_from_llm_input(sandbox, fake_llm):
    uid = "u_source_filter"
    _write_episode(uid, "yexuan", "触发遍历用", ts=time.time())

    date_str = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
    content = (
        "## 09:00\n**用户**：帮我查下天气\n**叶瑄**：查到了，明天晴，记得带伞\n"
        "> emotion:gentle intensity:0 speaker:assistant source:web\n---\n"
        "## 10:00\n**用户**：我决定辞职去学画画\n**叶瑄**：好呀，我支持你\n"
        "> emotion:gentle intensity:0 speaker:assistant\n---\n"
    )
    _write_day_file(sandbox, "yexuan", uid, date_str, content)

    _run_weekly()

    fake_llm.chat.assert_awaited()
    llm_input = fake_llm.chat.call_args.args[0][0]["content"]
    assert "明天晴" not in llm_input, "source:web 块不应出现在 storyline 聚合 LLM 输入里"
    assert "辞职去学画画" in llm_input, "无 source 标记的块应正常进入聚合输入"


# ── 6. 空 registry → warning + 不调用 LLM ────────────────────────────────────

def test_empty_registry_skips(fake_llm, caplog):
    import logging
    from core.scheduler.triggers.storyline_weekly import _check_storyline_weekly
    with patch("core.scheduler.loop._is_ready", return_value=True), \
         patch("core.scheduler.loop._mark"), \
         patch("core.asset_registry.get_registry", return_value=_make_registry()), \
         caplog.at_level(logging.WARNING, logger="core.scheduler.triggers.storyline_weekly"):
        asyncio.run(_check_storyline_weekly())

    fake_llm.chat.assert_not_awaited()
    assert caplog.text
