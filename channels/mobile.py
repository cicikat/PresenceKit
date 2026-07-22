"""
channels/mobile - mobile polling channel.

The mobile client does not use the desktop WebSocket. Scheduler broadcasts to
the active mobile channel are written into data/mobile_queue.json, and the
mobile client reads them through /mobile/poll.
"""

import asyncio
import json
import logging
import time
from uuid import uuid4

from channels.base import BaseChannel
from channels.relay_publisher import schedule_signal_publish
from core.sandbox import get_paths
from core.safe_write import safe_write_json

logger = logging.getLogger(__name__)

_queue_condition = asyncio.Condition()
_ACTIVE_TTL_SECONDS = 120
# Safety valve for unacked relay messages. Ack remains the normal deletion path.
_QUEUE_MAX_ITEMS = 500
_QUEUE_MAX_AGE_SECONDS = 24 * 60 * 60


class MobileChannel(BaseChannel):
    def __init__(self):
        self._active = False
        self._last_seen = 0.0

    @property
    def name(self) -> str:
        return "mobile"

    @property
    def is_active(self) -> bool:
        if not self._active:
            return False
        return time.time() - self._last_seen <= _ACTIVE_TTL_SECONDS

    def set_active(self, active: bool) -> None:
        # touch()/poll() 会在每次移动端轮询时调用本方法，是典型的"轮询完成"
        # 高频路径；只在 active 状态真正发生转换时打 INFO，电平（重复同值）降
        # DEBUG（Brief 54-C：记边沿，不记电平）。
        changed = self._active != active
        self._active = active
        if active:
            self._last_seen = time.time()
        if changed:
            logger.info(f"[mobile_channel] active={active}")
        else:
            logger.debug(f"[mobile_channel] active={active}（无变化）")

    def touch(self) -> None:
        self.set_active(True)

    async def send(
        self,
        content: str,
        user_id: str,
        behavior: dict | None = None,
        msg_id: str | None = None,
        *,
        char_id: str | None = None,
        sticker: dict | None = None,
    ) -> None:
        await self._write_to_queue(
            content,
            user_id,
            behavior=behavior,
            msg_id=msg_id,
            char_id=char_id,
            sticker=sticker,
        )

    async def send_with_behavior(
        self,
        content: str,
        user_id: str,
        behavior: dict,
        msg_id: str | None = None,
        *,
        char_id: str | None = None,
        sticker: dict | None = None,
    ) -> None:
        await self._write_to_queue(
            content,
            user_id,
            behavior=behavior,
            msg_id=msg_id,
            char_id=char_id,
            sticker=sticker,
        )

    async def poll(
        self,
        after: int | None = None,
        limit: int = 20,
        wait_seconds: float = 0,
    ) -> list[dict]:
        self.touch()
        after = int(after) if after is not None else None
        limit = max(1, min(int(limit), 50))
        wait_seconds = max(0.0, min(float(wait_seconds), 60.0))
        deadline = time.monotonic() + wait_seconds

        async with _queue_condition:
            while True:
                messages = self._read_from_queue(after=after, limit=limit)
                if messages:
                    return messages

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []

                try:
                    await asyncio.wait_for(_queue_condition.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    return []

    async def ack(self, up_to_seq: int) -> int:
        up_to_seq = int(up_to_seq)
        async with _queue_condition:
            queue = self._load_queue()
            remaining = [
                item for item in queue
                if int(item.get("seq", 0)) > up_to_seq
            ]
            if remaining != queue:
                safe_write_json(get_paths().mobile_queue(), remaining)
            return len(remaining)

    async def _write_to_queue(
        self,
        content: str,
        user_id: str,
        behavior: dict | None = None,
        msg_id: str | None = None,
        *,
        char_id: str | None = None,
        sticker: dict | None = None,
    ) -> None:
        item = None
        try:
            async with _queue_condition:
                paths = get_paths()
                q_file = paths.mobile_queue()
                q_file.parent.mkdir(parents=True, exist_ok=True)
                queue = self._load_queue()
                seq = self._next_seq(queue)
                safe_write_json(paths.mobile_queue_seq(), {"next_seq": seq + 1})
                item = {
                    "id": msg_id if msg_id is not None else uuid4().hex,
                    "seq": seq,
                    "content": content,
                    "user_id": str(user_id),
                    "timestamp": time.time(),
                }
                if behavior:
                    item["behavior"] = behavior
                if char_id is not None:
                    item["char_id"] = char_id
                if sticker is not None:
                    item["sticker"] = sticker
                queue.append(item)
                queue = self._prune_queue(queue)
                safe_write_json(q_file, queue)
                _queue_condition.notify_all()
        except Exception as e:
            logger.warning(f"[mobile_channel] write queue failed: {e}")
            return

        schedule_signal_publish(item)

    def _read_from_queue(self, after: int | None, limit: int) -> list[dict]:
        queue = self._load_queue()
        if after is None:
            return queue[:limit]
        return [item for item in queue if int(item["seq"]) > after][:limit]

    def _load_queue(self) -> list[dict]:
        q_file = get_paths().mobile_queue()
        if not q_file.exists():
            return []
        try:
            queue = json.loads(q_file.read_text(encoding="utf-8"))
            if not isinstance(queue, list):
                queue = []
        except Exception:
            logger.warning("[mobile_channel] read queue failed; reset")
            queue = []

        valid_queue = [item for item in queue if isinstance(item, dict)]
        had_invalid_items = len(valid_queue) != len(queue)
        queue, changed = self._ensure_sequences(valid_queue)
        changed = changed or had_invalid_items
        pruned = self._prune_queue(queue)
        if changed or pruned != queue:
            safe_write_json(q_file, pruned)
        return pruned

    def _ensure_sequences(self, queue: list[dict]) -> tuple[list[dict], bool]:
        next_seq = self._next_seq(queue)
        seen: set[int] = set()
        changed = False

        for item in queue:
            seq = item.get("seq")
            if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1 or seq in seen:
                item["seq"] = next_seq
                next_seq += 1
                changed = True
            seen.add(item["seq"])

        queue.sort(key=lambda item: int(item["seq"]))
        stored_next = self._read_stored_next_seq()
        required_next = max((int(item["seq"]) for item in queue), default=0) + 1
        if stored_next < required_next or changed:
            safe_write_json(
                get_paths().mobile_queue_seq(),
                {"next_seq": max(next_seq, required_next)},
            )
        return queue, changed

    def _next_seq(self, queue: list[dict]) -> int:
        max_queue_seq = max(
            (
                int(item["seq"])
                for item in queue
                if isinstance(item.get("seq"), int)
                and not isinstance(item.get("seq"), bool)
                and item["seq"] > 0
            ),
            default=0,
        )
        return max(self._read_stored_next_seq(), max_queue_seq + 1)

    def _read_stored_next_seq(self) -> int:
        seq_file = get_paths().mobile_queue_seq()
        if not seq_file.exists():
            return 1
        try:
            raw = json.loads(seq_file.read_text(encoding="utf-8"))
            value = raw.get("next_seq") if isinstance(raw, dict) else raw
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value
        except Exception:
            logger.warning("[mobile_channel] read queue seq failed; recover from queue")
        return 1

    @staticmethod
    def _prune_queue(queue: list[dict]) -> list[dict]:
        cutoff = time.time() - _QUEUE_MAX_AGE_SECONDS
        retained = [
            item for item in queue
            if not isinstance(item.get("timestamp"), (int, float))
            or item["timestamp"] >= cutoff
        ]
        return retained[-_QUEUE_MAX_ITEMS:]
