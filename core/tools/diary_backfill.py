"""Bounded, missing-only backfill of the current character's own diary."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
import uuid


def _target_date(value: str, now: datetime) -> str:
    today = now.date()
    yesterday = today - timedelta(days=1)
    value = value.strip()
    if not value:
        target = today if now.hour >= 23 else yesterday
    elif value in {"今天", "today"}:
        target = today
    elif value in {"昨天", "yesterday"}:
        target = yesterday
    else:
        try:
            target = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("日期须为今天、昨天或 YYYY-MM-DD。") from None
    if target not in {today, yesterday}:
        raise ValueError("仅能补写当天或昨天的日记，不能补更早日期或未来日期。")
    if target == today and now.hour < 23:
        raise ValueError("当天日记仅能在本地时间 23:00 后补写；昨天日记可在今天全天补写。")
    return target.isoformat()


async def backfill_diary(user_id: str, char_id: str, date: str = "") -> str:
    from core.config_loader import get_config
    from core.agent_runtime import CausationRef, TaskPrincipal
    from core.agent_runtime.task_manager import claim_next, complete_task, create_task, fail_task
    from core.agent_runtime.work_sessions import (
        WorkSessionError, create_work_session, fail_work_session, run_work_session,
    )
    from core.sandbox import get_paths
    from core.scheduler.triggers.time_based import _prepare_diary_work_context, _write_missing_diary

    owner = str(get_config().get("scheduler", {}).get("owner_id") or "")
    if not owner or str(user_id) != owner:
        return "补写未执行：仅允许 owner 请求补写角色日记。"
    principal = TaskPrincipal.reality(user_id, char_id)
    try:
        target = _target_date(date, datetime.now())
    except ValueError as exc:
        return f"补写未执行：{exc}"
    path = get_paths().character_inner_diary(char_id=char_id) / f"{target}.md"
    if path.exists():
        return f"补写未执行：{target} 的日记已经存在，保留原文，不覆盖。"
    context = _prepare_diary_work_context(user_id, char_id, target_date=target)
    if not context:
        return f"补写未执行：{target} 没有可用记录，不编造日记。"

    # Each explicit request may retry an earlier failed/expired task. File
    # existence and the shared writer lock, rather than old task status, are
    # the authority for whether this date can still be written.
    request_id = uuid.uuid4().hex
    task, _ = create_task(
        principal, capability="authored_diary", source="tool",
        idempotency_key=f"diary-backfill:{target}:{request_id}", ttl_seconds=600,
        causation_ref=CausationRef("tool_request", request_id),
    )
    lease = claim_next(principal, task_id=task["task_id"], capabilities={"authored_diary"})
    if lease is None:
        return "补写未完成：任务暂时无法执行，请稍后重试。"
    session = None
    try:
        session = create_work_session(
            principal, task_id=task["task_id"], capability="authored_diary",
            artifact_kind="authored_diary", context=json.dumps(context, ensure_ascii=False, sort_keys=True),
            idempotency_key=request_id,
        )

        async def worker():
            def assert_window():
                # A request waiting across midnight must still satisfy the window.
                try:
                    _target_date(target, datetime.now())
                except ValueError as exc:
                    raise WorkSessionError("diary_window_closed") from exc

            assert_window()
            return await _write_missing_diary(
                char_id, context, target, before_write=assert_window,
            )

        await run_work_session(principal, session["work_session_id"], worker)
        complete_task(principal, lease, result_metadata={
            "outcome_code": "authored_diary", "artifact_ids": [f"diary-{target}"],
        })
    except asyncio.CancelledError:
        if session:
            fail_work_session(principal, session["work_session_id"], error_code="request_cancelled")
        fail_task(principal, lease, error_code="request_cancelled")
        raise
    except Exception as exc:
        fail_task(principal, lease, error_code="diary_backfill_failed")
        if isinstance(exc, WorkSessionError) and exc.code == "diary_already_exists":
            return f"补写未执行：{target} 的日记已经存在，保留原文，不覆盖。"
        if isinstance(exc, WorkSessionError) and exc.code == "diary_window_closed":
            reason = exc.__cause__ or "补写窗口已过。"
            return f"补写未执行：{reason}"
        return f"{target} 补写未完成，请稍后重试；不会覆盖已有日记。"
    return f"已补写 {target} 的角色日记（今日事件与今日感受），可用 read_diary 读取。"
