"""
tests/test_coplay_echo_skip.py — Brief 38 验收：coplay_echo=True 的轮次不得
触发 mid_term/episodic/identity 主链（与既有 dream_echo/web_echo 同款跳过通路）。
"""

from unittest.mock import AsyncMock, patch

import pytest

from core.memory.fixation_pipeline import handler_summarize_to_midterm

_BASE_PAYLOAD = {
    "turn_id": "t1",
    "uid": "u1",
    "char_id": "yexuan",
    "user_content": "今天打了个游戏",
    "reply": "好玩吗",
}


@pytest.mark.asyncio
async def test_coplay_echo_skips_summarize_to_midterm():
    payload = {**_BASE_PAYLOAD, "coplay_echo": True}
    with patch(
        "core.memory.fixation_pipeline.summarize_to_midterm", new=AsyncMock(),
    ) as mock_summarize:
        await handler_summarize_to_midterm(payload)
    mock_summarize.assert_not_called()


@pytest.mark.asyncio
async def test_without_coplay_echo_summarize_to_midterm_runs():
    payload = dict(_BASE_PAYLOAD)
    with patch(
        "core.memory.fixation_pipeline.summarize_to_midterm", new=AsyncMock(),
    ) as mock_summarize:
        await handler_summarize_to_midterm(payload)
    mock_summarize.assert_awaited_once()


@pytest.mark.asyncio
async def test_coplay_echo_false_does_not_skip():
    payload = {**_BASE_PAYLOAD, "coplay_echo": False}
    with patch(
        "core.memory.fixation_pipeline.summarize_to_midterm", new=AsyncMock(),
    ) as mock_summarize:
        await handler_summarize_to_midterm(payload)
    mock_summarize.assert_awaited_once()
