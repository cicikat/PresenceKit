"""
慢任务队列：uid_lock 释放后执行 LLM 写入等耗时任务。

- 单 worker，失败退避重试（0.5s × retry，最多 2 次）
- 超限写入 DLQ：data/dead_letter_queue/{ms_ts}_{task_type}.json
  内容：{task, error(traceback 字符串), failed_at}
- capture_turn_retry / summarize_to_midterm / reflect_to_episodic / consolidate_to_identity 持久化；其余仍是内存队列。
"""

import asyncio
import json
import logging
import time
import traceback
from collections.abc import Callable

logger = logging.getLogger(__name__)

_queue: asyncio.Queue = asyncio.Queue()
_handlers: dict[str, Callable] = {}
_worker_task: asyncio.Task | None = None
_current_task_type: str | None = None

_MAX_RETRIES = 2  # 首次 + 最多 2 次重试，共 3 次尝试


def queue_size() -> int:
    return _queue.qsize()


def worker_alive() -> bool:
    return _worker_task is not None and not _worker_task.done()


def current_task_type() -> str | None:
    return _current_task_type


def register_handler(task_type: str, fn: Callable) -> None:
    """启动时注册 handler，task_type 为字符串键。"""
    _handlers[task_type] = fn


def enqueue(task_type: str, payload: dict) -> str | None:
    """Only explicitly replay-safe memory jobs enter the durable outbox."""
    from core.post_process import memory_outbox
    if task_type in memory_outbox.DURABLE_TYPES:
        key = memory_outbox.put(task_type, payload)
        _queue.put_nowait({"outbox_wakeup": True})
        return key
    _queue.put_nowait({"task_type": task_type, "payload": payload})


def start_worker() -> "asyncio.Task":
    """启动单 worker（幂等：已在运行则直接返回）。须在 async 上下文中调用。"""
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return _worker_task
    from core.post_process import memory_outbox
    memory_outbox.recover()
    _queue.put_nowait({"outbox_wakeup": True})
    _worker_task = asyncio.create_task(worker(), name="slow_queue_worker")
    logger.info("[slow_queue] worker 已启动")
    return _worker_task


async def drain() -> None:
    """等待所有已入队任务处理完毕（测试用，基于 asyncio.Queue.join）。"""
    await _queue.join()


async def shutdown(timeout: float = 10.0) -> None:
    """等待积压任务跑完（带超时），再 cancel worker。关停路径调用。"""
    global _worker_task
    try:
        await asyncio.wait_for(drain(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"[slow_queue] shutdown drain 超时 ({timeout}s)，仍有积压任务")
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
    _worker_task = None
    logger.info("[slow_queue] worker 已停止")


async def _write_dlq(task: dict, error: str) -> None:
    from core.sandbox import get_paths

    dlq_dir = get_paths().dead_letter_queue()
    dlq_dir.mkdir(parents=True, exist_ok=True)

    ts_ms = int(time.time() * 1000)
    task_type = task.get("task_type", "unknown")
    filename = f"{ts_ms}_{task_type}.json"
    path = dlq_dir / filename

    record = {
        "task": task,
        "error": error,
        "failed_at": time.time(),
    }
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.error(f"[slow_queue] DLQ written: {filename}")


async def worker() -> None:
    """Own queue accounting even when shutdown cancels an in-flight handler."""
    global _current_task_type
    while True:
        item = await _queue.get()
        try:
            if item.get("outbox_wakeup"):
                from core.post_process import memory_outbox
                item = memory_outbox.claim()
                if item is None:
                    continue
            await _process_item(item)
        finally:
            _current_task_type = None
            _queue.task_done()


async def _process_item(item: dict) -> None:
    global _current_task_type
    from core.post_process import memory_outbox
    task_type = item.get("task_type", "")
    payload = item.get("payload", {})
    if task_type == "practice_session":
        from core.runtime_signal_observability import record
        record(category="slow_queue", code="retired_practice_skipped", status="ok", context={"reason": "subsystem_retired"})
        return
    handler = _handlers.get(task_type)
    if handler is None:
        if item.get("outbox_id"):
            memory_outbox.finish(item["outbox_id"], succeeded=False)
            _queue.put_nowait({"outbox_wakeup": True})
        logger.error(f"[slow_queue] 无 handler，丢弃任务: {task_type}")
        return
    _current_task_type = task_type
    last_error = ""
    succeeded = False
    for attempt in range(_MAX_RETRIES + 1):
        try:
            if item.get("outbox_id") and not memory_outbox.attempt(item["outbox_id"]):
                last_error = "memory task retry limit reached"
                break
            if item.get("outbox_id") and task_type == "summarize_to_midterm":
                from core.memory.scope import MemoryScope
                scope = MemoryScope.from_payload(payload["scope"]) if payload.get("scope") else MemoryScope.reality_scope(payload["uid"],payload["char_id"])
                memory_outbox.record_turn_stage(scope.uid,scope.character_id,payload["turn_id"],"slow_pending")
            await handler(payload)
            if item.get("outbox_id") and task_type == "summarize_to_midterm":
                memory_outbox.record_turn_stage(scope.uid,scope.character_id,payload["turn_id"],"slow_completed")
            succeeded = True
            break
        except Exception:
            last_error = traceback.format_exc()
            logger.warning(f"[slow_queue] {task_type} attempt={attempt} failed")
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(0.5 * (attempt + 1))
    if item.get("outbox_id"):
        memory_outbox.finish(item["outbox_id"], succeeded=succeeded)
        _queue.put_nowait({"outbox_wakeup": True})
    if not succeeded:
        await _write_dlq(item, last_error)
        from core import silent_failure
        silent_failure.note(f"slow_queue.{task_type}", last_error.splitlines()[-1] if last_error else "unknown")
