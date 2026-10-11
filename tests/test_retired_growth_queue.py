import asyncio
from unittest.mock import AsyncMock

import pytest

from core.post_process import slow_queue


@pytest.mark.asyncio
async def test_retired_practice_is_not_executed_even_with_stale_handler(monkeypatch):
    queue = asyncio.Queue()
    handler = AsyncMock()
    monkeypatch.setattr(slow_queue, "_queue", queue)
    monkeypatch.setattr(slow_queue, "_handlers", {"practice_session": handler})
    slow_queue.enqueue("practice_session", {"private": "ignored"})
    worker = asyncio.create_task(slow_queue.worker())
    try:
        await asyncio.wait_for(queue.join(), 2)
        handler.assert_not_awaited()
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker
