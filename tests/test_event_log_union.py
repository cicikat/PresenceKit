"""
V2' event_log union 读取层验证测试

历史默认角色（测试配置 character.default = fixture_character）仍可 union
uid-only 旧树；非归属角色不得读到同一份旧数据。
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.memory import event_log
from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID


# ── 辅助 ─────────────────────────────────────────────────────────────────────

_UID = "test_union_uid"


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _day_block(time_str: str, user_text: str, reply_text: str, turn_id: str, intensity: int = 0) -> str:
    return (
        f"## {time_str}\n"
        f"**用户**：{user_text}\n"
        f"> turn_id:{turn_id}\n"
        f"**Companion**：{reply_text}\n"
        f"> emotion:neutral intensity:{intensity} turn_id:{turn_id}\n"
        "---\n"
    )


# ── 断言1：跨天 union ────────────────────────────────────────────────────────

def test_union_cross_days(sandbox, tmp_path):
    """
    旧路径：有 -10 天、-5 天的日志
    新路径：只有 -1 天的日志
    get_recent_days(30) 应返回全部三天
    """
    old_dir = sandbox._p("event_log") / _UID
    uid_str = _UID
    new_dir = sandbox.user_memory_root(uid_str) / "event_log"

    today = datetime.now()

    old_days = [-10, -5]
    for offset in old_days:
        d = today + timedelta(days=offset)
        date_str = d.strftime("%Y-%m-%d")
        block = _day_block("10:00", f"旧路径 {date_str}", "好的", f"tid-old-{date_str}")
        _write(old_dir / f"{date_str}.md", block)

    new_day_offset = -1
    d_new = today + timedelta(days=new_day_offset)
    new_date_str = d_new.strftime("%Y-%m-%d")
    block_new = _day_block("14:00", "新路径最近", "嗯嗯", f"tid-new-{new_date_str}")
    _write(new_dir / f"{new_date_str}.md", block_new)

    result = event_log.get_recent_days(_UID, days=30, char_id=TEST_CHAR_ID)

    for offset in old_days:
        d = today + timedelta(days=offset)
        date_str = d.strftime("%Y-%m-%d")
        assert date_str in result, f"旧路径日期 {date_str} 应出现在结果中"
        assert f"旧路径 {date_str}" in result

    assert new_date_str in result, "新路径日期应出现在结果中"
    assert "新路径最近" in result


# ── 断言2：同一天双路径合并 ──────────────────────────────────────────────────

def test_union_same_day_merge(sandbox):
    """
    同一天：旧路径有上午条目（10:00, turn_id=am），新路径有下午条目（15:00, turn_id=pm）
    get_recent_days(1) 应返回两条且无重复
    """
    old_dir = sandbox._p("event_log") / _UID
    new_dir = sandbox.user_memory_root(_UID) / "event_log"

    today = datetime.now()
    date_str = today.strftime("%Y-%m-%d")

    block_am = _day_block("10:00", "上午聊天", "早上好", "tid-am")
    block_pm = _day_block("15:00", "下午聊天", "下午好", "tid-pm")

    _write(old_dir / f"{date_str}.md", block_am)
    _write(new_dir / f"{date_str}.md", block_pm)

    result = event_log.get_recent_days(_UID, days=1, char_id=TEST_CHAR_ID)

    assert "上午聊天" in result, "旧路径上午条目应出现"
    assert "下午聊天" in result, "新路径下午条目应出现"

    # ## HH:MM 头是每个块唯一的，各出现一次即说明无重复块
    assert result.count("## 10:00") == 1, "上午块不应重复"
    assert result.count("## 15:00") == 1, "下午块不应重复"


# ── 断言3：同一天同一条目不重复（两处路径完全相同内容）──────────────────────

def test_union_same_day_dedup(sandbox):
    """
    同一天两处路径写入完全相同的块，去重后只出现一次
    """
    old_dir = sandbox._p("event_log") / _UID
    new_dir = sandbox.user_memory_root(_UID) / "event_log"

    today = datetime.now()
    date_str = today.strftime("%Y-%m-%d")

    block = _day_block("12:00", "重复内容", "嗯", "tid-dup")

    _write(old_dir / f"{date_str}.md", block)
    _write(new_dir / f"{date_str}.md", block)

    result = event_log.get_recent_days(_UID, days=1, char_id=TEST_CHAR_ID)

    # 相同块去重后 ## 12:00 应只出现一次
    assert result.count("## 12:00") == 1, "重复块应只保留一份"
    assert result.count("重复内容") == 1, "相同用户内容不应重复"


# ── 断言4：search(days=30) 语义不变 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_covers_old_path(sandbox):
    """
    search() 内部调用 get_recent_days(days=30)，应能命中旧路径中 20 天前的条目
    """
    old_dir = sandbox._p("event_log") / _UID

    today = datetime.now()
    old_date = today + timedelta(days=-20)
    date_str = old_date.strftime("%Y-%m-%d")

    # intensity=1 让这条 20 天前的记录通过 search 的 7 天强度过滤（>7天需 intensity>=1）
    block = _day_block("09:00", "猫猫咖啡馆", "好玩吧", "tid-cafe", intensity=1)
    _write(old_dir / f"{date_str}.md", block)

    result = await event_log.search(_UID, "猫猫咖啡馆", char_id=TEST_CHAR_ID)
    assert result, "search 应能命中旧路径 20 天前含关键词的条目"
    assert "猫猫" in result or "咖啡" in result

    leaked = await event_log.search(_UID, "猫猫咖啡馆", char_id=TEST_PEER_CHAR_ID)
    assert not leaked, "非历史默认角色不得经 search 读到 uid-only 旧树"
