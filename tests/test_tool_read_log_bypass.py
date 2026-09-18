from tests.fixtures.public_assets import TEST_CHAR_ID
"""Brief 82 · P2-1：显式「再读一遍」绕过工具已读指纹。"""

import importlib

import pytest


def _fresh_tool_dispatcher():
    """一些测试直接 `_td._TOOL_REGISTRY = {}` 且不还原（见 test_r5_author_note_tool_alignment.py
    同名 helper 的注释），并行跑位到同一 worker 后面时会看到空/缺键的 registry。
    reload 拿到规范的已注册 registry，与执行顺序无关。"""
    import core.tool_dispatcher as _td
    if "read_diary" not in _td._TOOL_REGISTRY:
        importlib.reload(_td)
    return _td


def test_detect_bypass_intent_matches_controlled_phrases():
    from core.memory.tool_read_log import detect_bypass_intent

    assert detect_bypass_intent("帮我再读一遍今天的日记") is True
    assert detect_bypass_intent("重新读一下") is True
    assert detect_bypass_intent("再看一次昨天写的") is True
    assert detect_bypass_intent("重新看看那篇") is True
    assert detect_bypass_intent("帮我看看今天的日记") is False
    assert detect_bypass_intent("") is False
    assert detect_bypass_intent(None) is False


def test_is_recently_read_bypass_skips_block_but_does_not_touch_fingerprint(sandbox, monkeypatch):
    from core.memory import tool_read_log as trl

    trl.record_read("u1", TEST_CHAR_ID, "diary:2026-07-16")

    # 无 bypass：正常拦截
    assert trl.is_recently_read("u1", TEST_CHAR_ID, "diary:2026-07-16") is True
    # bypass=True：放行，但不改变磁盘上已记录的指纹集合
    assert trl.is_recently_read("u1", TEST_CHAR_ID, "diary:2026-07-16", bypass=True) is False
    assert trl.is_recently_read("u1", TEST_CHAR_ID, "diary:2026-07-16") is True


@pytest.mark.asyncio
async def test_execute_bypass_read_log_allows_reread_and_refreshes_fingerprint(sandbox, monkeypatch):
    _td = _fresh_tool_dispatcher()

    read_diary_calls: list = []

    async def _fake_read_diary(user_id: str, date: str = "") -> str:
        read_diary_calls.append(user_id)
        return "模拟日记内容"

    _td._TOOL_REGISTRY["read_diary"]["func"] = _fake_read_diary

    class _FakeState:
        status = "idle"
        WAITING_CONFIRM = "waiting_confirm"

    async def _run(uid: str, *, bypass: bool):
        return await _td.execute_structured(
            tool_name="read_diary",
            tool_args={},
            user_id=uid,
            target_id=uid,
            is_group=False,
            session_state=_FakeState(),
            origin="user_live",
            char_id=TEST_CHAR_ID,
            bypass_read_log=bypass,
        )

    # 第一次读取：正常执行并落指纹
    await _run("u_bypass_case", bypass=False)
    assert len(read_diary_calls) == 1

    # 第二次普通读取（无显式短语）：被已读指纹拦，不再调用底层 func（回归）
    result = await _run("u_bypass_case", bypass=False)
    assert len(read_diary_calls) == 1
    assert "跳过" in (result.result or "")

    # 第三次带显式重读短语（bypass=True）：放行且指纹继续刷新
    await _run("u_bypass_case", bypass=True)
    assert len(read_diary_calls) == 2

    # 之后没有显式短语的第四次读取：仍被拦（bypass 不是永久解除）
    result = await _run("u_bypass_case", bypass=False)
    assert len(read_diary_calls) == 2
    assert "跳过" in (result.result or "")
