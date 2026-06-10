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

_MAX_RETRIES = 2  # 首次 + 最多 2 次重试，共 3 次尝试

# R8-A: task types kept only for DLQ backward-compat — no longer enqueued by current code.
# These are candidates for automatic 30-day expiry in the DLQ monitor sweep.
LEGACY_TASK_TYPES: frozenset[str] = frozenset({
    "mid_term_append",   # superseded by summarize_to_midterm (S6 migration)
    "episodic_compress", # superseded by reflect_to_episodic (S6 migration)
    # "consolidate_to_growth" removed in R8-E1: was DEAD (name-only residue, never registered,
    # no enqueue, no DLQ files). See docs/known-issues.md TD-2 / TD-3.
})


def is_dlq_task_expired(
    record: dict,
    *,
    filename: str = "",
    file_mtime: float | None = None,
    now: float | None = None,
    ttl_days: int = 30,
) -> bool:
    """Return True if this DLQ record has exceeded ttl_days.

    Timestamp resolution priority:
    1. record["failed_at"]  — written by _write_dlq since day 1
    2. filename ms-prefix   — {ms_ts}_{task_type}.json (~= failed_at in practice)
    3. file_mtime           — filesystem fallback for very old files
    4. (none reachable)     — returns False (safe: never expire undatable tasks)
    """
    if now is None:
        now = time.time()
    ttl_sec = ttl_days * 86400.0

    failed_at = record.get("failed_at")
    if failed_at is not None:
        try:
            return (now - float(failed_at)) > ttl_sec
        except (ValueError, TypeError):
            pass

    if filename:
        stem = filename.rsplit(".", 1)[0]
        prefix = stem.split("_", 1)[0]
        if prefix.isdigit():
            try:
                return (now - int(prefix) / 1000.0) > ttl_sec
            except (ValueError, OverflowError):
                pass

    if file_mtime is not None:
        return (now - file_mtime) > ttl_sec

    return False


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
    while True:
        item = await _queue.get()
        task_type = item.get("task_type", "")
        payload = item.get("payload", {})
        handler = _handlers.get(task_type)

        if handler is None:
            logger.error(f"[slow_queue] 无 handler，丢弃任务: {task_type}")
            _queue.task_done()
            continue

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

        if not succeeded:
            await _write_dlq(item, last_error)

        _queue.task_done()
