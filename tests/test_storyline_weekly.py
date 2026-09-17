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
from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID

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
         patch("core.asset_registry.get_registry", return_value=_make_registry(TEST_CHAR_ID)):
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
    _write_episode(uid, TEST_CHAR_ID, "很久以前的事", ts=1.0)

    from core.memory import storyline as sl
    sl.save_meta(uid, char_id=TEST_CHAR_ID, last_aggregated_at=time.time(), event_log_cursor="")

    _run_weekly()

    fake_llm.chat.assert_not_awaited()


# ── 3. 合法 ops 正常落盘 + cursor 前进 + inbox 清空 ───────────────────────────

def test_valid_ops_applied_and_cursor_advances(sandbox, fake_llm):
    uid = "u_valid"
    _write_episode(uid, TEST_CHAR_ID, "决定转行做程序员", ts=time.time())

    from core.memory import storyline as sl
    inbox_entry = {"id": "old1", "summary": "旧碎片", "ts": time.time(), "strength": 0.3}
    sl.append_to_inbox(uid, [inbox_entry],
                        char_id=TEST_CHAR_ID)
    episode = __import__("core.memory.episodic_memory", fromlist=["_load_memories"])._load_memories(uid, char_id=TEST_CHAR_ID)[0]
    episode_material_id = sl.stable_material_id("episode", episode)

    node_ts = time.time()
    fake_llm.chat = AsyncMock(return_value=json.dumps([
        {"op": "open_arc", "title": "职业转型", "tags": ["topic.learning"]},
        {"op": "append_node", "arc_title": "职业转型", "summary": "决定转行做程序员",
             "ts": node_ts, "span": [node_ts, node_ts], "source_material_ids": [episode_material_id]},
    ], ensure_ascii=False))

    _run_weekly()

    data = sl.load(uid, char_id=TEST_CHAR_ID)
    assert len(data["arcs"]) == 1
    arc = data["arcs"][0]
    assert arc["title"] == "职业转型"
    assert len(arc["nodes"]) == 1
    assert data["meta"]["last_aggregated_at"] > 0
    assert sl.load_inbox(uid, char_id=TEST_CHAR_ID) == []


def test_invalid_batch_is_all_or_nothing(sandbox):
    from core.memory import storyline as sl
    from core.scheduler.triggers.storyline_weekly import _apply_ops

    uid = "storyline-atomic-invalid"
    before = sl.load(uid, char_id=TEST_CHAR_ID)
    with pytest.raises(ValueError):
        _apply_ops(uid, TEST_CHAR_ID, [
            {"op": "open_arc", "title": "有效标题", "tags": []},
            {"op": "append_node", "arc_title": "不存在", "summary": "无效", "ts": time.time(),
             "span": [time.time(), time.time()], "source_material_ids": []},
        ], material_sources={})
    assert sl.load(uid, char_id=TEST_CHAR_ID) == before


def test_event_log_cursor_consumes_same_day_append_only_once(sandbox):
    from core.scheduler.triggers.storyline_weekly import _collect_event_log_since

    uid = "storyline-same-day-cursor"
    day = datetime.now().strftime("%Y-%m-%d")
    _write_day_file(sandbox, TEST_CHAR_ID, uid, day, "## 09:00\n**用户**：第一段\n---\n")
    first, cursor, first_ids = _collect_event_log_since(uid, TEST_CHAR_ID, "")
    assert "第一段" in first and first_ids
    path = sandbox.memory_char_root(char_id=TEST_CHAR_ID) / uid / "event_log" / f"{day}.md"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("## 10:00\n**用户**：第二段\n---\n")
    second, cursor2, second_ids = _collect_event_log_since(uid, TEST_CHAR_ID, cursor)
    assert "第一段" not in second and "第二段" in second and second_ids
    assert cursor2["offset"] > cursor["offset"]


def test_event_log_union_reads_canonical_and_legacy_with_independent_checkpoints(sandbox):
    from core.scheduler.triggers.storyline_weekly import _collect_event_log_since

    uid = "storyline-physical-union"
    day = datetime.now().strftime("%Y-%m-%d")
    _write_day_file(
        sandbox, TEST_CHAR_ID, uid, day,
        "## 09:00\n**user**: canonical-only\n---\n",
    )
    legacy_dir = sandbox._p("event_log") / uid
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / f"{day}.md").write_text(
        "## 10:00\n**user**: legacy-only\n---\n", encoding="utf-8",
    )

    text, cursor, material_ids = _collect_event_log_since(uid, TEST_CHAR_ID, "")
    assert "canonical-only" in text and "legacy-only" in text
    assert cursor["version"] == 3
    assert cursor["sources"]["canonical"]["offset"] > 0
    assert cursor["sources"]["legacy"]["offset"] > 0
    assert len(material_ids) == 2

    peer_text, peer_cursor, peer_ids = _collect_event_log_since(uid, TEST_PEER_CHAR_ID, "")
    assert "legacy-only" not in peer_text
    assert peer_cursor["version"] == 3
    assert "legacy" in peer_cursor["sources"]
    assert peer_ids == []


def test_v2_cursor_rescan_is_stopped_by_legacy_receipt(sandbox):
    from core.scheduler.triggers.storyline_weekly import _collect_event_log_since

    uid = "storyline-v2-receipt"
    day = "2026-08-01"
    block = "## 09:00\n**user**: already-consumed"
    _write_day_file(sandbox, TEST_CHAR_ID, uid, day, block + "\n---\n")
    old_receipt = "eventlog:b5420c384371ef030f67e92a"

    text, cursor, material_ids = _collect_event_log_since(
        uid, TEST_CHAR_ID, {"version": 2, "day": day, "offset": 0},
        consumed_ids={old_receipt},
    )
    assert text == "" and material_ids == []
    assert cursor["version"] == 3
    assert cursor["sources"]["canonical"]["offset"] > 0


def test_isolated_only_event_log_advances_checkpoint_without_llm(sandbox, fake_llm):
    from core.memory import storyline as sl
    from core.scheduler.triggers.storyline_weekly import _aggregate_one

    uid = "storyline-isolated-checkpoint"
    day = datetime.now().strftime("%Y-%m-%d")
    _write_day_file(
        sandbox, TEST_CHAR_ID, uid, day,
        "## 09:00\n**user**: isolated-body\n> speaker:user source:web\n---\n",
    )
    assert asyncio.run(_aggregate_one(TEST_CHAR_ID, uid)) == 0
    fake_llm.chat.assert_not_awaited()
    meta = sl.load(uid, char_id=TEST_CHAR_ID)["meta"]
    assert meta["event_log_cursor"]["version"] == 3
    assert meta["event_log_cursor"]["sources"]["canonical"]["offset"] > 0


def test_invalid_llm_records_content_free_failure_without_cursor_advance(sandbox, fake_llm):
    from core.memory import storyline as sl
    from core.scheduler.triggers.storyline_weekly import _aggregate_one

    uid = "storyline-failure-observe"
    _write_episode(uid, TEST_CHAR_ID, "failure material", ts=time.time())
    before = sl.load(uid, char_id=TEST_CHAR_ID)["meta"]["event_log_cursor"]
    fake_llm.chat = AsyncMock(return_value="invalid-json")
    assert asyncio.run(_aggregate_one(TEST_CHAR_ID, uid)) == 0
    meta = sl.load(uid, char_id=TEST_CHAR_ID)["meta"]
    assert meta["event_log_cursor"] == before
    assert meta["aggregation"]["status"] == "failed"
    assert meta["aggregation"]["last_failure_code"] == "invalid_llm_output"


def test_storyline_admin_meta_projection_is_content_free():
    from admin.routers.memory import _storyline_meta_projection

    projected = _storyline_meta_projection({
        "last_aggregated_at": 12.0,
        "event_log_cursor": {"version": 3, "sources": {
            "canonical": {"day": "2026-08-18", "offset": 10},
            "legacy": {"day": "2026-08-17", "offset": 20},
        }},
        "consumed_material_ids": ["eventlog:private-id"],
        "aggregation": {"status": "failed", "last_failure_code": "invalid_llm_output"},
    })
    assert projected["cursor_version"] == 3
    assert projected["event_log_checkpoints"]["legacy"]["offset"] == 20
    assert projected["consumed_count"] == 1
    assert "private-id" not in str(projected)


# ── 4. 非法 JSON → fail-open，不动 cursor ────────────────────────────────────

def test_invalid_llm_output_does_not_advance_cursor(sandbox, fake_llm):
    uid = "u_invalid"
    _write_episode(uid, TEST_CHAR_ID, "某件事", ts=time.time())

    from core.memory import storyline as sl
    before = sl.load(uid, char_id=TEST_CHAR_ID)["meta"]["last_aggregated_at"]

    fake_llm.chat = AsyncMock(return_value="不是JSON也不是数组")

    _run_weekly()  # 不应抛异常

    after = sl.load(uid, char_id=TEST_CHAR_ID)["meta"]["last_aggregated_at"]
    assert after == before, "LLM 输出不合法时不应推进 last_aggregated_at"


# ── 5. event_log 带 source 标记的块不得进入 LLM 输入 ─────────────────────────

def test_event_log_source_tagged_blocks_filtered_from_llm_input(sandbox, fake_llm):
    uid = "u_source_filter"
    _write_episode(uid, TEST_CHAR_ID, "触发遍历用", ts=time.time())

    date_str = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
    content = (
        "## 09:00\n**用户**：帮我查下天气\n**叶瑄**：查到了，明天晴，记得带伞\n"
        "> emotion:gentle intensity:0 speaker:assistant source:web\n---\n"
        "## 10:00\n**用户**：我决定辞职去学画画\n**叶瑄**：好呀，我支持你\n"
        "> emotion:gentle intensity:0 speaker:assistant\n---\n"
    )
    _write_day_file(sandbox, TEST_CHAR_ID, uid, date_str, content)

    _run_weekly()

    fake_llm.chat.assert_awaited()
    llm_input = fake_llm.chat.call_args.args[0][0]["content"]
    assert "明天晴" not in llm_input, "source:web 块不应出现在 storyline 聚合 LLM 输入里"
    assert "辞职去学画画" in llm_input, "无 source 标记的块应正常进入聚合输入"


def test_event_log_same_text_is_kept_across_days_and_deduped_within_day(sandbox):
    from core.scheduler.triggers.storyline_weekly import _collect_event_log_since

    uid = "storyline-physical-day-dedupe"
    body = "## 09:00\n**user**: repeated real occurrence\n> speaker:user\n---\n"
    _write_day_file(sandbox, TEST_CHAR_ID, uid, "2026-08-01", body)
    _write_day_file(sandbox, TEST_CHAR_ID, uid, "2026-08-02", body)
    legacy = sandbox._p("event_log") / uid
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "2026-08-02.md").write_text(body, encoding="utf-8")

    text, _cursor, material_ids = _collect_event_log_since(uid, TEST_CHAR_ID, {})
    assert text.count("repeated real occurrence") == 2
    assert len(material_ids) == 2


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
