from tests.fixtures.public_assets import TEST_CHAR_ID
"""Brief 26 · 角色日记生成与主动发言解耦。

`_check_inner_diary_write` 是静默维护任务，与 daily_journal 主动发言完全解耦：
不管发言是否被 gating 五道闸拦下，日记都应该按 23:00–次日05:00 窗口正常写。
"""
from datetime import datetime

import pytest


@pytest.fixture(autouse=True)
def diary_attempts(monkeypatch):
    from core.scheduler import loop
    monkeypatch.setattr(loop, "_attempt_backoff_secs", {})
    monkeypatch.setattr(loop, "_attempt_cooldown_until", {})


@pytest.fixture
def diary_run(monkeypatch, sandbox):
    """Real scheduler/task/session/storage path; replace only clock and LLM."""
    from core.scheduler.triggers import time_based
    _patch_now(monkeypatch, time_based, 2026, 5, 25, 23, 30)
    marks, calls = [], []
    monkeypatch.setattr(time_based, "_is_ready", lambda name: True)
    monkeypatch.setattr(time_based, "_mark", marks.append)
    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    monkeypatch.setattr("core.config_loader.get_config", lambda: {
        "scheduler": {"enabled": True}, "diary": {"characters": [TEST_CHAR_ID]},
    })
    monkeypatch.setattr("core.memory.event_log.get_recent_days", lambda *a, **kw: "## 14:30\nTest conversation")
    monkeypatch.setattr("core.llm_client.chat", _fake_chat_factory(calls))
    return time_based, marks, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", [TEST_CHAR_ID, TEST_CHAR_ID + ".json", "\u53f6\u7444"])
async def test_configured_reference_writes_canonical_diary(diary_run, monkeypatch, sandbox, reference):
    """Display names and registered filenames must resolve before any runtime path."""
    import json
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.task_manager import list_tasks
    from core.agent_runtime.work_sessions import list_work_sessions
    time_based, marks, calls = diary_run
    card = sandbox.character_card_write_path(TEST_CHAR_ID)
    data = json.loads(card.read_text(encoding="utf-8"))
    data["name"] = "\u53f6\u7444"
    card.write_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"diary": {"characters": [reference]}})
    await time_based._check_inner_diary_write()
    path = sandbox.character_inner_diary(char_id=TEST_CHAR_ID) / "2026-05-25.md"
    assert path.relative_to(sandbox._base).as_posix() == f"runtime/characters/{TEST_CHAR_ID}/inner/diary/2026-05-25.md"
    assert "## 今日事件" in path.read_text(encoding="utf-8")
    assert "## 今日感受" in path.read_text(encoding="utf-8")
    principal = TaskPrincipal.reality("u1", TEST_CHAR_ID)
    assert list_tasks(principal)[0]["status"] == "succeeded"
    assert list_work_sessions(principal)[0]["status"] == "succeeded"
    assert len(calls) == 2
    assert marks == ["inner_diary_write"]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["unknown", "missing.json", "../fixture_character.json", "\u4e0d\u5b58\u5728"])
async def test_bad_whitelist_entry_does_not_block_peer(diary_run, monkeypatch, sandbox, invalid):
    """Invalid explicit entries neither fall back nor abort valid peers or mark success."""
    time_based, marks, calls = diary_run
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"diary": {"characters": [invalid, TEST_CHAR_ID]}})
    await time_based._check_inner_diary_write()
    assert (sandbox.character_inner_diary(char_id=TEST_CHAR_ID) / "2026-05-25.md").is_file()
    assert len(calls) == 2
    assert marks == []
    assert [p.name for p in (sandbox._base / "runtime/characters").iterdir()] == [TEST_CHAR_ID]


@pytest.mark.asyncio
async def test_ambiguous_label_does_not_select_character(diary_run, monkeypatch, sandbox):
    """Duplicate display labels are errors, even with an active role available."""
    import json
    from tests.fixtures.public_assets import TEST_PEER_CHAR_ID
    time_based, marks, calls = diary_run
    for cid in (TEST_CHAR_ID, TEST_PEER_CHAR_ID):
        card = sandbox.character_card_write_path(cid)
        data = json.loads(card.read_text(encoding="utf-8"))
        data["name"] = "Duplicate"
        card.write_bytes(json.dumps(data).encode("utf-8"))
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"diary": {"characters": ["Duplicate"]}})
    await time_based._check_inner_diary_write()
    assert calls == []
    assert marks == []
    assert not list(sandbox._base.glob("runtime/characters/*/inner/diary/*.md"))


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["path", "context", "task", "session", "complete"])
async def test_setup_or_terminalization_failure_is_not_success(diary_run, monkeypatch, sandbox, boundary):
    """Failures outside the worker must remain retryable and never mark the day done."""
    time_based, marks, calls = diary_run
    def fail(*a, **kw):
        raise RuntimeError("injected failure")
    targets = {
        "path": "core.data_paths.DataPaths.character_inner_diary",
        "context": "core.scheduler.triggers.time_based._prepare_diary_work_context",
        "task": "core.scheduler.triggers.time_based._open_authored_diary_task",
        "session": "core.agent_runtime.work_sessions.create_work_session",
        "complete": "core.agent_runtime.task_manager.complete_task",
    }
    monkeypatch.setattr(targets[boundary], fail)
    await time_based._check_inner_diary_write()
    assert marks == []
    if boundary != "complete":
        assert calls == []
        assert not list(sandbox._base.glob("runtime/characters/*/inner/diary/*.md"))


@pytest.mark.asyncio
async def test_no_event_log_backs_off_then_rechecks(diary_run, monkeypatch, sandbox, caplog):
    """No material must not mint tasks, spend LLM calls, or close the night window."""
    import logging
    from core.scheduler import loop
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.task_manager import list_tasks
    time_based, marks, calls = diary_run
    clock = [100000.0]
    monkeypatch.setattr(loop.time, "time", lambda: clock[0])
    monkeypatch.setattr("core.memory.event_log.get_recent_days", lambda *a, **kw: "")
    with caplog.at_level(logging.INFO):
        await time_based._check_inner_diary_write()
        await time_based._check_inner_diary_write()
    assert sum(getattr(r, "diary_status", None) == "no_event_log" for r in caplog.records) == 1
    assert marks == []
    assert calls == []
    assert list_tasks(TaskPrincipal.reality("u1", TEST_CHAR_ID)) == []
    assert not list(sandbox._base.glob("runtime/characters/*/inner/diary/*.md"))
    clock[0] += 901
    monkeypatch.setattr("core.memory.event_log.get_recent_days", lambda *a, **kw: "New event")
    await time_based._check_inner_diary_write()
    assert len(calls) == 2
    assert marks == ["inner_diary_write"]


@pytest.mark.asyncio
@pytest.mark.parametrize("hour,expected", [(23, True), (1, True), (5, False)])
async def test_window_edges(diary_run, monkeypatch, sandbox, hour, expected):
    """23:00 inclusive and 05:00 exclusive, retaining logical-date naming."""
    time_based, marks, calls = diary_run
    _patch_now(monkeypatch, time_based, 2026, 5, 26, hour, 0)
    await time_based._check_inner_diary_write()
    assert bool(calls) is expected
    assert bool(list(sandbox._base.glob("runtime/characters/*/inner/diary/*.md"))) is expected


@pytest.mark.asyncio
async def test_loop_runs_diary_when_speech_proposer_fails(diary_run, monkeypatch, sandbox):
    """A speech proposal failure cannot starve the real diary maintenance worker."""
    import asyncio
    from unittest.mock import AsyncMock
    from core.scheduler import loop
    time_based, marks, calls = diary_run
    monkeypatch.setattr(loop, "_owner_id", lambda: "u1")
    monkeypatch.setattr(loop, "_active_char_id_or_none", lambda: None)
    monkeypatch.setattr("core.scheduler.gating.run_shadow_tick", AsyncMock(side_effect=RuntimeError("proposal failed")))
    monkeypatch.setattr("core.wake_bridge.WakeBridge.drain_due", AsyncMock())
    # Unrelated sources are external boundaries of this maintenance test.
    for module, names in {
        "core.scheduler.loop": ["_check_reminders", "_check_log_maintenance", "_check_sensor_aware"],
        "core.scheduler.triggers.time_based": ["_check_weather", "_check_episodic_decay", "_check_dlq_monitor"],
        **{f"core.scheduler.triggers.{name}": [f"_check_{name}"] for name in (
            "episodic_sweep", "garden_water", "garden_daily",
            "event_log_salvage", "memory_janitor", "memory_consolidation", "event_edge_proposer",
            "private_exchange", "storyline_weekly", "coplay_watch", "spend_monitor",
        )},
        "core.scheduler.triggers.diary": ["_check_diary_inject"],
        "core.scheduler.triggers.hidden_state_decay": ["_check_hidden_state_decay", "_check_hidden_state_consolidate"],
        "core.scheduler.triggers.gmail_poll": ["check_gmail_poll"],
    }.items():
        for name in names:
            monkeypatch.setattr(f"{module}.{name}", AsyncMock())
    async def stop_after_tick(seconds):
        raise asyncio.CancelledError()
    monkeypatch.setattr(loop.asyncio, "sleep", stop_after_tick)
    with pytest.raises(asyncio.CancelledError):
        await loop._loop()
    assert (sandbox.character_inner_diary(char_id=TEST_CHAR_ID) / "2026-05-25.md").is_file()
    assert len(calls) == 2
    assert marks == ["inner_diary_write"]


@pytest.mark.asyncio
async def test_failed_worker_does_not_block_peer(diary_run, monkeypatch, sandbox):
    """A failing model call stays failed/queued while a second role writes its own diary."""
    from tests.fixtures.public_assets import TEST_PEER_CHAR_ID
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.task_manager import list_tasks
    time_based, marks, calls = diary_run
    monkeypatch.setattr("core.config_loader.get_config", lambda: {
        "diary": {"characters": [TEST_CHAR_ID, TEST_PEER_CHAR_ID]},
    })
    good_chat = _fake_chat_factory(calls)
    async def chat(**kwargs):
        if kwargs["char_id"] == TEST_CHAR_ID:
            raise RuntimeError("first character model failure")
        return await good_chat(**kwargs)
    monkeypatch.setattr("core.llm_client.chat", chat)
    await time_based._check_inner_diary_write()
    assert not (sandbox.character_inner_diary(char_id=TEST_CHAR_ID) / "2026-05-25.md").exists()
    assert (sandbox.character_inner_diary(char_id=TEST_PEER_CHAR_ID) / "2026-05-25.md").is_file()
    assert list_tasks(TaskPrincipal.reality("u1", TEST_CHAR_ID))[0]["status"] == "queued"
    assert list_tasks(TaskPrincipal.reality("u1", TEST_PEER_CHAR_ID))[0]["status"] == "succeeded"
    assert marks == []


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
async def test_inner_diary_write_retries_after_task_is_terminal(monkeypatch, sandbox):
    """Exhausted Reality tasks must not lock the remaining 23:00–05:00 window."""
    import time as time_mod
    from core.agent_runtime import TaskPrincipal
    from core.agent_runtime.task_manager import list_tasks
    from core.scheduler.triggers import time_based

    _patch_now(monkeypatch, time_based, 2026, 5, 25, 23, 30)
    marks = []
    clock = {"now": time_mod.time()}
    monkeypatch.setattr(time_based, "_is_ready", lambda name: True)
    monkeypatch.setattr(time_based, "_mark", lambda name: marks.append(name))
    monkeypatch.setattr(time_based, "_owner_id", lambda: "u1")
    monkeypatch.setattr(time_based, "_diary_char_ids", lambda: [TEST_CHAR_ID])
    monkeypatch.setattr(
        "core.agent_runtime.task_manager._now",
        lambda value=None: clock["now"] if value is None else float(value),
    )
    monkeypatch.setattr(
        "core.agent_runtime.work_sessions.time.time",
        lambda: clock["now"],
    )
    monkeypatch.setattr(
        "core.memory.event_log.get_recent_days",
        lambda oid, days=1, **kw: "## 14:30\n**用户**：测试失败\n---\n",
    )

    async def failed_chat(**kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr("core.llm_client.chat", failed_chat)
    await time_based._check_inner_diary_write()
    clock["now"] += 301
    await time_based._check_inner_diary_write()

    principal = TaskPrincipal.reality("u1", TEST_CHAR_ID)
    assert marks == []
    assert [row["status"] for row in list_tasks(principal)] == ["failed"]
    diary_file = sandbox.yexuan_inner_diary(char_id=TEST_CHAR_ID) / "2026-05-25.md"
    assert not diary_file.exists()

    chat_calls = []
    monkeypatch.setattr("core.llm_client.chat", _fake_chat_factory(chat_calls))
    clock["now"] += 1
    await time_based._check_inner_diary_write()

    assert diary_file.exists()
    assert marks == ["inner_diary_write"]
    assert len(chat_calls) == 2
    statuses = [row["status"] for row in list_tasks(principal)]
    assert statuses.count("succeeded") == 1
    assert statuses.count("failed") == 1


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
