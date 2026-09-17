from tests.fixtures.public_assets import TEST_CHAR_ID
"""Brief 26 · 角色日记生成与主动发言解耦。

`_check_inner_diary_write` 是静默维护任务，与 daily_journal 主动发言完全解耦：
不管发言是否被 gating 五道闸拦下，日记都应该按 23:00–次日05:00 窗口正常写。
"""
from datetime import datetime

import pytest


def _fake_datetime(*ymdhms):
    class FakeDatetime(datetime):
        @classmethod
        def now(cls):
            return cls(*ymdhms)

    return FakeDatetime


def _patch_now(monkeypatch, time_based, *ymdhms):
    """Patch both time_based.datetime and rhythm.datetime so logical_day() agrees."""
    fake = _fake_datetime(*ymdhms)
    monkeypatch.setattr(time_based, "datetime", fake)
    monkeypatch.setattr("core.scheduler.rhythm.datetime", fake)
    return fake


def _fake_chat_factory(calls):
    async def fake_chat(*, messages, max_tokens_override=None, char_id=None):
        calls.append(messages)
        if len(calls) % 2 == 1:
            return "## 今日事件\n- 14:30 聊了几句"
        return "有点想她"

    return fake_chat


@pytest.mark.asyncio
async def test_inner_diary_write_ignores_gating_and_writes_file(monkeypatch, sandbox):
    """解耦生效：无需过 legacy_tick_should_send/gating，直接调用即写日记。"""
    from core.scheduler.triggers import time_based

    _patch_now(monkeypatch, time_based, 2026, 5, 25, 23, 30)
    marks = []
    monkeypatch.setattr(time_based, "_is_ready", lambda name: True)
    monkeypatch.setattr(time_based, "_mark", lambda name: marks.append(name))
    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    monkeypatch.setattr(time_based, "_diary_char_ids", lambda: [TEST_CHAR_ID])
    monkeypatch.setattr(
        "core.memory.event_log.get_recent_days",
        lambda oid, days=1, **kw: "## 14:30\n**用户**：在干嘛\n**Companion**：想你了\n---\n",
    )
    chat_calls = []
    monkeypatch.setattr("core.llm_client.chat", _fake_chat_factory(chat_calls))
    monkeypatch.setattr(
        time_based,
        "_generate_and_store_diary",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy composition called")),
    )

    await time_based._check_inner_diary_write()

    diary_file = sandbox.yexuan_inner_diary(char_id=TEST_CHAR_ID) / "2026-05-25.md"
    assert diary_file.exists()
    assert marks == ["inner_diary_write"]
    assert len(chat_calls) == 2


def test_diary_work_context_serialization_is_bounded(monkeypatch):
    from core.agent_runtime.work_sessions import MAX_CONTEXT_CHARS
    from core.scheduler.triggers import time_based

    monkeypatch.setattr(
        "core.memory.event_log.get_recent_days",
        lambda *args, **kwargs: ("\\\n" * 9000),
    )
    monkeypatch.setattr(time_based, "_collect_diary_voice", lambda char_id: ("p" * 500, "v" * 400, "m" * 200))
    monkeypatch.setattr(time_based, "get_char_name", lambda char_id: "character")
    context = time_based._prepare_diary_work_context("owner", TEST_CHAR_ID)

    assert context is not None
    assert len(time_based.json.dumps(context, ensure_ascii=False, sort_keys=True)) <= MAX_CONTEXT_CHARS


@pytest.mark.asyncio
async def test_inner_diary_write_idempotent_skips_llm(monkeypatch, sandbox):
    """幂等：当日文件已存在则跳过，不发 LLM 调用。"""
    from core.scheduler.triggers import time_based

    _patch_now(monkeypatch, time_based, 2026, 5, 25, 23, 30)
    marks = []
    monkeypatch.setattr(time_based, "_is_ready", lambda name: True)
    monkeypatch.setattr(time_based, "_mark", lambda name: marks.append(name))
    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    monkeypatch.setattr(time_based, "_diary_char_ids", lambda: [TEST_CHAR_ID])

    diary_dir = sandbox.yexuan_inner_diary(char_id=TEST_CHAR_ID)
    diary_dir.mkdir(parents=True, exist_ok=True)
    (diary_dir / "2026-05-25.md").write_text("# 已经写过了\n", encoding="utf-8")

    chat_calls = []
    monkeypatch.setattr("core.llm_client.chat", _fake_chat_factory(chat_calls))

    await time_based._check_inner_diary_write()

    assert chat_calls == []
    # 全部角色文件已存在时同样 _mark，避免每 60s 空转扫描
    assert marks == ["inner_diary_write"]


@pytest.mark.asyncio
async def test_inner_diary_write_cross_midnight_uses_previous_logical_day(monkeypatch, sandbox):
    """跨午夜：01:00 写入前一逻辑日文件名，get_recent_days 收到 days=2。"""
    from core.scheduler.triggers import time_based

    _patch_now(monkeypatch, time_based, 2026, 5, 26, 1, 0)
    monkeypatch.setattr(time_based, "_is_ready", lambda name: True)
    monkeypatch.setattr(time_based, "_mark", lambda name: None)
    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    monkeypatch.setattr(time_based, "_diary_char_ids", lambda: [TEST_CHAR_ID])

    seen_days = []

    def fake_get_recent_days(oid, days=1, **kw):
        seen_days.append(days)
        return "## 23:50\n**用户**：还没睡\n**Companion**：早点休息\n---\n"

    monkeypatch.setattr("core.memory.event_log.get_recent_days", fake_get_recent_days)
    chat_calls = []
    monkeypatch.setattr("core.llm_client.chat", _fake_chat_factory(chat_calls))

    await time_based._check_inner_diary_write()

    # logical_day(cutoff=5) at 2026-05-26 01:00 → 2026-05-25
    diary_file = sandbox.yexuan_inner_diary(char_id=TEST_CHAR_ID) / "2026-05-25.md"
    assert diary_file.exists()
    assert seen_days == [2]


@pytest.mark.asyncio
async def test_inner_diary_write_outside_window_noop(monkeypatch, sandbox):
    """窗口外（14:00）不跑：不写文件、不 mark。"""
    from core.scheduler.triggers import time_based

    _patch_now(monkeypatch, time_based, 2026, 5, 25, 14, 0)
    marks = []
    monkeypatch.setattr(time_based, "_is_ready", lambda name: True)
    monkeypatch.setattr(time_based, "_mark", lambda name: marks.append(name))
    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    monkeypatch.setattr(time_based, "_diary_char_ids", lambda: [TEST_CHAR_ID])
    chat_calls = []
    monkeypatch.setattr("core.llm_client.chat", _fake_chat_factory(chat_calls))

    await time_based._check_inner_diary_write()

    assert marks == []
    assert chat_calls == []
    diary_file = sandbox.yexuan_inner_diary(char_id=TEST_CHAR_ID) / "2026-05-25.md"
    assert not diary_file.exists()


@pytest.mark.asyncio
async def test_inner_diary_write_failure_remains_retryable_without_cooldown(monkeypatch, sandbox):
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.task_manager import list_tasks
    from core.agent_runtime.work_sessions import list_work_sessions
    from core.scheduler.triggers import time_based

    _patch_now(monkeypatch, time_based, 2026, 5, 25, 23, 30)
    marks = []
    monkeypatch.setattr(time_based, "_is_ready", lambda name: True)
    monkeypatch.setattr(time_based, "_mark", lambda name: marks.append(name))
    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    monkeypatch.setattr(time_based, "_diary_char_ids", lambda: [TEST_CHAR_ID])
    monkeypatch.setattr(
        "core.memory.event_log.get_recent_days",
        lambda oid, days=1, **kw: "## 14:30\n**用户**：测试失败\n---\n",
    )

    async def failed_chat(**kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr("core.llm_client.chat", failed_chat)
    await time_based._check_inner_diary_write()

    principal = TaskPrincipal.reality("u1", TEST_CHAR_ID)
    assert marks == []
    assert list_tasks(principal)[0]["status"] == "queued"
    assert list_work_sessions(principal)[0]["status"] == "failed"
    diary_file = sandbox.yexuan_inner_diary(char_id=TEST_CHAR_ID) / "2026-05-25.md"
    assert not diary_file.exists()


@pytest.mark.asyncio
async def test_daily_journal_proposal_no_longer_writes_diary(monkeypatch, sandbox):
    """回归：propose_daily_journal 的 proposal 不再有写日记的 after_send 副作用。"""
    from core.scheduler.triggers import time_based

    assert not hasattr(time_based, "_write_inner_daily_journal")

    monkeypatch.setattr(time_based, "_cfg", lambda: {"enabled": True})
    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    monkeypatch.setattr("core.scheduler.rhythm.quiet_floor_elapsed", lambda uid, now_ts=None: True)
    monkeypatch.setattr("core.scheduler.rhythm.triggered_on_logical_day", lambda name, now=None, **_kwargs: False)
    chat_calls = []
    monkeypatch.setattr("core.llm_client.chat", _fake_chat_factory(chat_calls))

    now = datetime(2026, 5, 25, 23, 30)
    proposal = time_based.propose_daily_journal({"now_dt": now, "now_ts": now.timestamp()})
    assert proposal is not None

    result = await proposal.execute(dry_run=True)

    assert result.trigger_name == "daily_journal"
    assert chat_calls == []
    diary_file = sandbox.yexuan_inner_diary(char_id=TEST_CHAR_ID) / "2026-05-25.md"
    assert not diary_file.exists()
