"""
tests/test_coplay_session_close.py — Brief 42: session 收尾链
（summarizer + game_log + provenance_log + afterglow + close_session）。

主记忆验证核心：整个 session 结束后 mid_term/episodic/identity 无本 session
逐轮内容——这条已经由 Brief 38 的 coplay_echo 通路（tests/test_coplay_echo_skip.py）
在轮次层面保证；本文件验证的是收尾链本身（game_log/provenance/afterglow/状态转换）。
"""

from unittest.mock import AsyncMock, patch

import pytest

from core.coplay import afterglow, game_state, observer, session, session_close
from core.coplay.observer import GameMoment

UID = "u1"
CHAR = "yexuan"
GAME_ID = "steam:123"
GAME_NAME = "黑暗之魂"


def _enter_closing():
    session.arm(UID, char_id=CHAR)
    session.enter_active(UID, game_id=GAME_ID, game_name=GAME_NAME, char_id=CHAR)
    session.enter_closing(UID, char_id=CHAR)


@pytest.fixture(autouse=True)
def _reset(sandbox):
    observer.clear_moments_for_test()
    yield
    observer.clear_moments_for_test()


def test_run_session_close_noop_when_not_closing(sandbox):
    session.arm(UID, char_id=CHAR)
    session.enter_active(UID, game_id=GAME_ID, game_name=GAME_NAME, char_id=CHAR)
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        session_close.run_session_close(UID, char_id=CHAR)
    )
    # 仍是 active，没被误收尾
    assert session.read_state(UID, char_id=CHAR)["status"] == session.CoplayStatus.ACTIVE.value


@pytest.mark.asyncio
async def test_run_session_close_writes_log_and_returns_to_armed(sandbox):
    _enter_closing()
    game_state.add_highlight(UID, GAME_ID, "打败了第一个boss", char_id=CHAR)
    observer.push_moment(UID, GameMoment(kind="idle", summary="停下来研究地图"))

    with patch("core.coplay.session_close._summarize_session", new=AsyncMock(return_value="她和角色一起探索了地图")):
        await session_close.run_session_close(UID, char_id=CHAR)

    state = session.read_state(UID, char_id=CHAR)
    assert state["status"] == session.CoplayStatus.ARMED.value

    log_text = game_state.read_game_log_text(UID, GAME_ID, char_id=CHAR)
    assert GAME_NAME in log_text
    assert "她和角色一起探索了地图" in log_text
    assert "打败了первый boss" in log_text or "停下来研究地图" in log_text


@pytest.mark.asyncio
async def test_run_session_close_drains_moment_queue(sandbox):
    _enter_closing()
    observer.push_moment(UID, GameMoment(kind="idle", summary="停下来研究地图"))

    with patch("core.coplay.session_close._summarize_session", new=AsyncMock(return_value="")):
        await session_close.run_session_close(UID, char_id=CHAR)

    assert observer.peek_moments(UID) == []


@pytest.mark.asyncio
async def test_run_session_close_sets_last_summary_for_recall(sandbox):
    _enter_closing()
    observer.push_moment(UID, GameMoment(kind="idle", summary="停下来研究地图"))

    with patch("core.coplay.session_close._summarize_session", new=AsyncMock(return_value="探索了新区域")):
        await session_close.run_session_close(UID, char_id=CHAR)

    game = game_state.read_game_state(UID, GAME_ID, char_id=CHAR)
    assert game["last_summary"] == "探索了新区域"


@pytest.mark.asyncio
async def test_run_session_close_writes_afterglow(sandbox):
    _enter_closing()
    with patch("core.coplay.session_close._summarize_session", new=AsyncMock(return_value="")):
        await session_close.run_session_close(UID, char_id=CHAR)

    text = afterglow.load_afterglow_text(UID, char_id=CHAR)
    assert GAME_NAME in text


@pytest.mark.asyncio
async def test_run_session_close_writes_provenance():
    _enter_closing()
    with patch("core.coplay.session_close._summarize_session", new=AsyncMock(return_value="探索了新区域")), \
         patch("core.memory.provenance_log.append") as mock_append:
        await session_close.run_session_close(UID, char_id=CHAR)

    mock_append.assert_called_once()
    _, kwargs = mock_append.call_args
    assert kwargs["artifact"] == "coplay_game_log"
    assert kwargs["field"] == GAME_ID


@pytest.mark.asyncio
async def test_run_session_close_summarizer_failure_does_not_block(sandbox):
    """summarizer LLM 失败 → 退化为只有清晰词句，仍完成收尾（fail-open）。"""
    _enter_closing()
    observer.push_moment(UID, GameMoment(kind="idle", summary="停下来研究地图"))

    with patch("core.coplay.session_close._summarize_session", new=AsyncMock(return_value="")):
        await session_close.run_session_close(UID, char_id=CHAR)

    assert session.read_state(UID, char_id=CHAR)["status"] == session.CoplayStatus.ARMED.value
    log_text = game_state.read_game_log_text(UID, GAME_ID, char_id=CHAR)
    assert "停下来研究地图" in log_text


@pytest.mark.asyncio
async def test_run_session_close_missing_game_id_returns_to_armed(sandbox):
    session.arm(UID, char_id=CHAR)
    session.enter_active(UID, game_id=GAME_ID, game_name=GAME_NAME, char_id=CHAR)
    session.enter_closing(UID, char_id=CHAR)
    # 手工破坏 state，模拟缺 game_id 的防御性分支
    state = session.read_state(UID, char_id=CHAR)
    state.pop("game_id", None)
    session.write_state(UID, state, char_id=CHAR)

    await session_close.run_session_close(UID, char_id=CHAR)
    assert session.read_state(UID, char_id=CHAR)["status"] == session.CoplayStatus.ARMED.value


# ═══════════════════════════════════════════════════════════════════════════
# game_log 列表 + tag 门控回忆
# ═══════════════════════════════════════════════════════════════════════════

def test_match_game_by_text_hits_alias(sandbox):
    game_state.write_game_state(UID, GAME_ID, game_state.default_game_state(GAME_ID, GAME_NAME), char_id=CHAR)
    game_state.set_aliases(UID, GAME_ID, ["黑魂", "DS1"], char_id=CHAR)
    matched = game_state.match_game_by_text(UID, "我们上次玩黑魂玩到哪了", char_id=CHAR)
    assert matched is not None
    assert matched["game_id"] == GAME_ID


def test_match_game_by_text_none_when_no_hit(sandbox):
    game_state.write_game_state(UID, GAME_ID, game_state.default_game_state(GAME_ID, GAME_NAME), char_id=CHAR)
    assert game_state.match_game_by_text(UID, "今天天气怎么样", char_id=CHAR) is None


def test_build_game_log_recall_text_requires_last_summary(sandbox):
    game_state.write_game_state(UID, GAME_ID, game_state.default_game_state(GAME_ID, GAME_NAME), char_id=CHAR)
    # 没有 last_summary 时不产出回忆层
    assert game_state.build_game_log_recall_text(UID, "黑暗之魂怎么样了", char_id=CHAR) == ""

    game_state.set_last_summary(UID, GAME_ID, "打到了第二个BOSS", char_id=CHAR)
    text = game_state.build_game_log_recall_text(UID, "黑暗之魂怎么样了", char_id=CHAR)
    assert "<陪玩回忆>" in text
    assert "打到了第二个BOSS" in text
