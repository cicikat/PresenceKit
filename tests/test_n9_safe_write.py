"""
N9 原子写测试

1. grep 型：scheduler/loop.py、channels/desktop.py、channels/mobile.py
   对 canonical JSON 文件不得再有裸 write_text 调用。
2. safe_write_json 写后读回内容一致。
3. _mark() 行为不变：写入 cooldowns 文件，内存状态更新。
4. mark_diary_shared() 行为不变：写入 user_state 文件。
5. desktop channel _write_to_queue 写入后文件内容一致。
6. mobile channel 写入/取出后内容一致。
"""

import json
import re
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ── 1. grep 型：不允许裸 write_text 写 canonical JSON ────────────────────────

CANONICAL_FILES = {
    "scheduler_cooldowns",
    "scheduler_user_state",
    "channel_queue",
    "mobile_queue",
    "agent_actions",
}

# 匹配 .write_text( 但不在注释行里
_BARE_WRITE_RE = re.compile(r"^\s*[^#].*\.write_text\(")


def _find_bare_write_text_lines(src_path: Path) -> list[tuple[int, str]]:
    hits = []
    for i, line in enumerate(src_path.read_text(encoding="utf-8").splitlines(), 1):
        if _BARE_WRITE_RE.match(line):
            hits.append((i, line.rstrip()))
    return hits


BASE = Path(__file__).parent.parent  # repo root


def test_no_bare_write_text_in_scheduler_loop():
    """core/scheduler/loop.py 不得对 canonical JSON 裸 write_text。"""
    path = BASE / "core" / "scheduler" / "loop.py"
    hits = _find_bare_write_text_lines(path)
    # 如果存在 write_text，必须都不是写 canonical 冷却/user_state 文件的语句
    # 简单策略：该文件不应再有任何 .write_text( 调用
    assert hits == [], (
        f"core/scheduler/loop.py 仍有裸 write_text (行 {[h[0] for h in hits]}); "
        "应改用 safe_write_json"
    )


def test_no_bare_write_text_in_desktop_channel():
    path = BASE / "channels" / "desktop.py"
    hits = _find_bare_write_text_lines(path)
    assert hits == [], (
        f"channels/desktop.py 仍有裸 write_text (行 {[h[0] for h in hits]}); "
        "应改用 safe_write_json"
    )


def test_no_bare_write_text_in_mobile_channel():
    path = BASE / "channels" / "mobile.py"
    hits = _find_bare_write_text_lines(path)
    assert hits == [], (
        f"channels/mobile.py 仍有裸 write_text (行 {[h[0] for h in hits]}); "
        "应改用 safe_write_json"
    )


# ── 2. safe_write_json 写后读回内容一致 ──────────────────────────────────────

def test_safe_write_json_roundtrip(tmp_path):
    from core.safe_write import safe_write_json
    target = tmp_path / "test.json"
    data = {"triggers": {"diary": 1234567890.0}, "extra": [1, 2, 3]}
    ok = safe_write_json(target, data)
    assert ok is True
    assert target.exists()
    result = json.loads(target.read_text(encoding="utf-8"))
    assert result == data


def test_safe_write_json_no_tmp_leftover(tmp_path):
    """写入成功后不应留下 .tmp 文件。"""
    from core.safe_write import safe_write_json
    target = tmp_path / "state.json"
    safe_write_json(target, {"k": "v"})
    tmp = target.with_suffix(".json.tmp")
    assert not tmp.exists()


# ── 3. _mark() 行为：内存 + 持久化 ──────────────────────────────────────────

def test_mark_updates_memory_and_file(tmp_path):
    """_mark 调用后 _last_trigger 更新，且 cooldowns 文件被原子写入。"""
    cooldowns_file = tmp_path / "scheduler_cooldowns.json"

    import core.scheduler.loop as loop_mod
    original_last = dict(loop_mod._last_trigger)

    fake_paths = MagicMock()
    fake_paths.scheduler_cooldowns.return_value = cooldowns_file

    with patch("core.scheduler.loop.get_paths", return_value=fake_paths):
        before = time.time()
        loop_mod._mark("test_trigger_n9")
        after = time.time()

    assert "test_trigger_n9" in loop_mod._last_trigger
    ts = loop_mod._last_trigger["test_trigger_n9"]
    assert before <= ts <= after

    assert cooldowns_file.exists()
    stored = json.loads(cooldowns_file.read_text(encoding="utf-8"))
    assert "test_trigger_n9" in stored.get("triggers", {})

    # 清理：从内存里移除本次测试写入的 key
    loop_mod._last_trigger.pop("test_trigger_n9", None)


# ── 4. mark_diary_shared() 行为不变 ──────────────────────────────────────────

def test_mark_diary_shared_persists(tmp_path):
    user_state_file = tmp_path / "scheduler_user_state.json"

    import core.scheduler.loop as loop_mod

    fake_paths = MagicMock()
    fake_paths.scheduler_user_state.return_value = user_state_file

    with patch("core.scheduler.loop.get_paths", return_value=fake_paths):
        before = time.time()
        loop_mod.mark_diary_shared()
        after = time.time()

    assert before <= loop_mod._last_diary_share <= after
    stored = json.loads(user_state_file.read_text(encoding="utf-8"))
    assert before <= stored["last_diary_share"] <= after


# ── 5. desktop channel _write_to_queue 内容一致 ───────────────────────────────

@pytest.mark.asyncio
async def test_desktop_write_to_queue_roundtrip(tmp_path):
    q_file = tmp_path / "channel_queue.json"

    import channels.desktop as desktop_mod

    fake_paths = MagicMock()
    fake_paths.channel_queue.return_value = q_file

    with patch("channels.desktop.get_paths", return_value=fake_paths):
        ch = desktop_mod.DesktopChannel()
        await ch._write_to_queue("hello world")

    assert q_file.exists()
    queue = json.loads(q_file.read_text(encoding="utf-8"))
    assert isinstance(queue, list)
    assert queue[0]["content"] == "hello world"


# ── 6. mobile channel 写入/取出内容一致 ─────────────────────────────────────

@pytest.mark.asyncio
async def test_mobile_queue_write_poll_and_ack(tmp_path):
    q_file = tmp_path / "mobile_queue.json"
    seq_file = tmp_path / "mobile_queue_seq"

    import channels.mobile as mobile_mod

    fake_paths = MagicMock()
    fake_paths.mobile_queue.return_value = q_file
    fake_paths.mobile_queue_seq.return_value = seq_file

    with patch("channels.mobile.get_paths", return_value=fake_paths):
        ch = mobile_mod.MobileChannel()
        # 写入两条
        await ch._write_to_queue("msg1", user_id="u1")
        await ch._write_to_queue("msg2", user_id="u1")

        # 取 1 条
        polled = ch._read_from_queue(after=None, limit=1)
        remaining_count = await ch.ack(polled[0]["seq"])

    assert len(polled) == 1
    assert polled[0]["content"] == "msg1"
    assert remaining_count == 1

    # 剩余 1 条仍在文件里
    remaining = json.loads(q_file.read_text(encoding="utf-8"))
    assert len(remaining) == 1
    assert remaining[0]["content"] == "msg2"
