"""Diary backfill date, isolation, retry and shared-writer contracts."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.fixtures.public_assets import TEST_CHAR_ID
from core.tools import diary_backfill as backfill


@pytest.mark.parametrize("hour,value,expected", [
    (22, "", "2026-09-19"), (23, "", "2026-09-20"),
    (0, "昨天", "2026-09-19"), (23, "昨天", "2026-09-19"),
    (23, "今天", "2026-09-20"), (12, "2026-09-19", "2026-09-19"),
    (23, "today", "2026-09-20"), (8, "yesterday", "2026-09-19"),
])
def test_date_window(hour, value, expected):
    assert backfill._target_date(value, datetime(2026, 9, 20, hour)) == expected


@pytest.mark.parametrize("value", ["今天", "2026-09-18", "2026-09-21", "garbage", "../2026-09-19"])
def test_rejected_dates(value):
    with pytest.raises(ValueError):
        backfill._target_date(value, datetime(2026, 9, 20, 22, 59, 59))


@pytest.fixture
def setup_backfill(monkeypatch, sandbox):
    from core.scheduler.triggers import time_based

    class Clock(datetime):
        current = datetime(2026, 9, 20, 12)

        @classmethod
        def now(cls):
            return cls.current

    monkeypatch.setattr(backfill, "datetime", Clock)
    owner_config = lambda: {"scheduler": {"owner_id": "owner"}}
    monkeypatch.setattr("core.config_loader.get_config", owner_config)
    monkeypatch.setattr("core.tool_dispatcher.get_config", owner_config)
    monkeypatch.setattr(time_based, "_collect_diary_voice", lambda cid: ("", "", ""))
    monkeypatch.setattr(time_based, "get_char_name", lambda cid: "Companion")
    monkeypatch.setattr("core.memory.event_log.get_recent_days", lambda *a, **kw: "yesterday evidence")
    generator = AsyncMock(return_value={"facts": "## 今日事件\n- 聊天", "feeling": "安静"})
    monkeypatch.setattr(time_based, "_generate_diary_material", generator)
    return sandbox.character_inner_diary(char_id=TEST_CHAR_ID) / "2026-09-19.md", generator, Clock


@pytest.mark.asyncio
async def test_success_then_existing_even_empty(setup_backfill):
    path, generator, _clock = setup_backfill
    assert "已补写" in await backfill.backfill_diary("owner", TEST_CHAR_ID, "昨天")
    assert "## 今日感受" in path.read_text(encoding="utf-8")
    path.write_bytes(b"")
    assert "已经存在" in await backfill.backfill_diary("owner", TEST_CHAR_ID, "昨天")
    assert generator.await_count == 1
    assert path.read_bytes() == b""


@pytest.mark.asyncio
async def test_no_records_and_non_owner_do_not_generate(setup_backfill, monkeypatch):
    path, generator, _clock = setup_backfill
    assert "owner" in await backfill.backfill_diary("stranger", TEST_CHAR_ID, "昨天")
    monkeypatch.setattr("core.memory.event_log.get_recent_days", lambda *a, **kw: "")
    assert "没有可用记录" in await backfill.backfill_diary("owner", TEST_CHAR_ID, "昨天")
    generator.assert_not_awaited()
    assert not path.exists()


@pytest.mark.asyncio
async def test_failed_generation_can_retry(setup_backfill):
    path, generator, _clock = setup_backfill
    generator.side_effect = [RuntimeError("offline"), {"feeling": "重新写好"}]
    assert "未完成" in await backfill.backfill_diary("owner", TEST_CHAR_ID, "昨天")
    assert not path.exists()
    assert "已补写" in await backfill.backfill_diary("owner", TEST_CHAR_ID, "昨天")
    assert path.exists()


@pytest.mark.asyncio
async def test_concurrent_requests_generate_once(setup_backfill):
    path, generator, _clock = setup_backfill

    async def generate(*a):
        await asyncio.sleep(0)
        return {"feeling": "一次"}

    generator.side_effect = generate
    results = await asyncio.gather(*[backfill.backfill_diary("owner", TEST_CHAR_ID, "昨天") for _ in range(2)])
    assert sum("已补写" in item for item in results) == 1
    assert sum("已经存在" in item for item in results) == 1
    assert generator.await_count == 1
    assert path.exists()


@pytest.mark.asyncio
async def test_writer_never_overwrites_file_created_during_generation(setup_backfill):
    path, generator, _clock = setup_backfill

    async def generate(*a):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"original")
        return {"feeling": "new"}

    generator.side_effect = generate
    assert "已经存在" in await backfill.backfill_diary("owner", TEST_CHAR_ID, "昨天")
    assert path.read_bytes() == b"original"


@pytest.mark.asyncio
async def test_window_closed_during_generation_does_not_write(setup_backfill):
    path, generator, clock = setup_backfill

    async def generate(*a):
        clock.current = datetime(2026, 9, 22, 0, 1)
        return {"feeling": "too late"}

    generator.side_effect = generate
    assert "补写未执行" in await backfill.backfill_diary("owner", TEST_CHAR_ID, "昨天")
    assert not path.exists()


def test_target_date_reads_only_target_scope_and_day(setup_backfill, monkeypatch):
    from core.scheduler.triggers import time_based
    calls = []

    def read(uid, **kw):
        calls.append((uid, kw))
        return "target day only"

    monkeypatch.setattr("core.memory.event_log.get_recent_days", read)
    context = time_based._prepare_diary_work_context("owner", TEST_CHAR_ID, target_date="2026-09-19")
    assert calls == [("owner", {"char_id": TEST_CHAR_ID,
        "since_ts": datetime(2026, 9, 19).timestamp(), "until_ts": datetime(2026, 9, 20).timestamp()})]
    assert context["target_date"] == "2026-09-19"
    assert context["today_log"] == "target day only"


@pytest.mark.asyncio
@pytest.mark.parametrize("is_group,origin,user_id", [
    (True, "user_live", "owner"),
    (False, "autonomy_loop", "owner"),
    (False, "assistant_loop", "stranger"),
])
async def test_dispatcher_rejects_non_chat_context(setup_backfill, is_group, origin, user_id):
    from core.tool_dispatcher import execute_structured
    _, generator, _clock = setup_backfill
    result = await execute_structured("backfill_diary", {}, user_id, user_id, is_group,
        SimpleNamespace(), origin=origin, char_id=TEST_CHAR_ID)
    assert result.status == "tool_failed"
    generator.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatcher_passes_frozen_character_scope(setup_backfill, monkeypatch):
    from core import tool_dispatcher as dispatcher
    from core.session_state import SessionState
    monkeypatch.setattr("core.self_management.policy.tool_allowed", lambda *a: True)
    monkeypatch.setattr(dispatcher, "_is_tool_enabled", lambda name: True)
    result = await dispatcher.execute_structured("backfill_diary", {"date": "昨天"}, "owner", "owner", False,
        SessionState(), origin="assistant_loop", char_id=TEST_CHAR_ID)
    assert result.status == "tool_executed"
    assert "已补写" in (result.result or "")
    assert setup_backfill[0].exists()
