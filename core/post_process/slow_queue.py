"""
慢任务队列：uid_lock 释放后执行 LLM 写入等耗时任务。

- 单 worker，失败退避重试（0.5s × retry，最多 2 次）
- 超限写入 DLQ：data/dead_letter_queue/{ms_ts}_{task_type}.json
  内容：{task, error(traceback 字符串), failed_at}
- 不持久化队列，进程退出丢失（有意设计）
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


def enqueue(task_type: str, payload: dict) -> None:
    """入队。不持久化，进程退出即丢失。"""
    _queue.put_nowait({"task_type": task_type, "payload": payload})


def start_worker() -> "asyncio.Task":
    """启动单 worker（幂等：已在运行则直接返回）。须在 async 上下文中调用。"""
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return _worker_task
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
    """单 worker 循环，永不主动退出。task 失败时退避重试，超限写 DLQ。"""
    global _current_task_type
    while True:
        item = await _queue.get()
        task_type = item.get("task_type", "")
        payload = item.get("payload", {})
        if task_type == "practice_session":
            from core.runtime_signal_observability import record
            record(category="slow_queue", code="retired_practice_skipped", status="ok", context={"reason": "subsystem_retired"})
            logger.info("[slow_queue] practice_session skipped: subsystem_retired")
            _queue.task_done()
            continue
        handler = _handlers.get(task_type)

        if handler is None:
            logger.error(f"[slow_queue] 无 handler，丢弃任务: {task_type}")
            _queue.task_done()
            continue

        _current_task_type = task_type
        last_error = ""
        succeeded = False
        for attempt in range(_MAX_RETRIES + 1):  # 0, 1, 2
            try:
                await handler(payload)
                succeeded = True
                break
            except Exception:
                last_error = traceback.format_exc()
                logger.warning(
                    f"[slow_queue] {task_type} attempt={attempt} 失败:\n{last_error.splitlines()[-1]}"
                )
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(0.5 * (attempt + 1))

        _current_task_type = None
        if not succeeded:
            await _write_dlq(item, last_error)
            from core import silent_failure
            silent_failure.note(f"slow_queue.{task_type}", last_error.splitlines()[-1] if last_error else "unknown")

        _queue.task_done()
