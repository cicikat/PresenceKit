"""Dry-run and future real execution helpers for scheduler proposals."""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from core.safe_write import rotate_jsonl_if_needed, safe_append_jsonl
from core.sandbox import get_paths


EXECUTE_MODE = "live"


def _forensic_rotation_params() -> tuple[int, int]:
    from core.config_loader import get_config
    cfg = get_config().get("forensic_logs", {})
    return int(cfg.get("max_size_mb", 5) * 1024 * 1024), int(cfg.get("keep", 5))


def is_live_mode() -> bool:
    return EXECUTE_MODE == "live"


def legacy_tick_should_send(*, force: bool = False) -> bool:
    """force=True（manual_trigger）无条件放行，语义与此前一致，不受 proactive=off 影响。

    force=False 时叠加 Brief 29 · 3.3 proactive 闸门：活跃角色卡 proactive="off" 时
    legacy speaking 路径同样让路（与 gating._decide 的闸门保持一致）。
    """
    if force:
        return True
    if is_live_mode():
        return False
    from core.character_loader import is_proactive_disabled
    return not is_proactive_disabled()


@dataclass(frozen=True)
class ExecuteResult:
    trigger_name: str
    would_send_prompt: str
    would_mark: list[str] = field(default_factory=list)
    would_mark_done: list[str] = field(default_factory=list)
    topic_key: str = ""
    reads_cache_ok: bool = True
    dry_run: bool = True
    sent: bool = False


ExecuteFn = Callable[..., Awaitable[ExecuteResult]]
PromptFactory = Callable[[], str]
AfterSend = Callable[[], object]
BehaviorFactory = Callable[[str], dict]


async def execute_prompt(
    *,
    trigger_name: str,
    prompt_factory: PromptFactory,
    dry_run: bool,
    search_query: str = "",
    would_mark: list[str] | tuple[str, ...] | None = None,
    would_mark_done: list[str] | tuple[str, ...] | None = None,
    topic_key: str = "",
    reads_cache_ok: bool = True,
    after_send: Optional[AfterSend] = None,
    char_id: str | None = None,
    behavior_factory: Optional[BehaviorFactory] = None,
    fanout="all",
    recall_policy: str = "seed",   # C: "seed" | "anchored" | "none" — see pipeline.fetch_context()
    perceive_event=None,
    pipeline_outcome: dict | None = None,
    write_trigger_stub: bool = True,
) -> ExecuteResult:
    """Execute a scheduler prompt, or log what would happen in dry-run mode."""

    prompt = str(prompt_factory() or "")
    if not dry_run:
        from core.scheduler.proactive_ledger import continuity_hint as _ledger_hint
        hint = _ledger_hint()
        if hint:
            prompt = f"{prompt}\n{hint}"
    result = ExecuteResult(
        trigger_name=trigger_name,
        would_send_prompt=prompt,
        would_mark=list(would_mark or []),
        would_mark_done=[str(x) for x in (would_mark_done or [])],
        topic_key=str(topic_key or ""),
        reads_cache_ok=reads_cache_ok,
        dry_run=dry_run,
        sent=False,
    )

    if dry_run:
        write_execute_dryrun(result)
        return result

    from core.scheduler import loop
    resolved_char_id = char_id or loop._active_char_id_or_none()

    sent_text = await loop._pipeline_send(
        prompt,
        search_query=search_query,
        trigger_name=trigger_name,
        char_id=resolved_char_id,
        behavior_factory=behavior_factory,
        fanout=fanout,
        recall_policy=recall_policy,
        perceive_event=perceive_event,
        pipeline_outcome=pipeline_outcome,
        write_trigger_stub=write_trigger_stub,
    )
    if not sent_text:
        blocked = ExecuteResult(
            trigger_name=result.trigger_name,
            would_send_prompt=result.would_send_prompt,
            would_mark=result.would_mark,
            would_mark_done=result.would_mark_done,
            topic_key=result.topic_key,
            reads_cache_ok=result.reads_cache_ok,
            dry_run=False,
            sent=False,
        )
        write_execute_blocked(blocked)
        # A4: sent=False (DUPLICATE / Dream Guard / LLM 空回复等) 不再无退避地
        # 让下个 tick 立即重跑一遍完整 pipeline（RC4）。
        loop._record_attempt_failure(trigger_name, char_id=resolved_char_id)
        return blocked
    if after_send is not None:
        maybe = after_send()
        if inspect.isawaitable(maybe):
            await maybe
    for name in result.would_mark:
        mark_params = inspect.signature(loop._mark).parameters
        if resolved_char_id and "char_id" in mark_params:
            loop._mark(name, char_id=resolved_char_id)
        else:
            loop._mark(name)
    loop._clear_attempt_backoff(trigger_name, char_id=resolved_char_id)
    from core.scheduler.proactive_ledger import record_send as _ledger_record
    _ledger_record(
        trigger_name,
        channel=str(fanout),
        gist=prompt.replace("（", "").replace("）", ""),
    )
    return ExecuteResult(
        trigger_name=result.trigger_name,
        would_send_prompt=result.would_send_prompt,
        would_mark=result.would_mark,
        would_mark_done=result.would_mark_done,
        topic_key=result.topic_key,
        reads_cache_ok=result.reads_cache_ok,
        dry_run=False,
        sent=True,
    )


def write_execute_dryrun(result: ExecuteResult) -> None:
    path = get_paths().execute_dryrun_log()
    safe_append_jsonl(
        path,
        {
            "ts": time.time(),
            "trigger_name": result.trigger_name,
            "would_send_prompt": result.would_send_prompt,
            "would_mark": result.would_mark,
            "would_mark_done": result.would_mark_done,
            "topic_key": result.topic_key,
            "reads_cache_ok": result.reads_cache_ok,
        },
    )
    max_bytes, keep_n = _forensic_rotation_params()
    rotate_jsonl_if_needed(path, max_bytes=max_bytes, keep_n=keep_n)


def write_execute_blocked(result: ExecuteResult) -> None:
    """记录"本该发但 pipeline 返回空"的事实；不改任何发送/mark/重试行为。"""
    path = get_paths().execute_dryrun_log()
    safe_append_jsonl(
        path,
        {
            "ts": time.time(),
            "trigger_name": result.trigger_name,
            "reason": "sent_false",
            "would_mark": result.would_mark,
            "would_mark_done": result.would_mark_done,
            "sent": False,
            "blocked": True,
        },
    )
    max_bytes, keep_n = _forensic_rotation_params()
    rotate_jsonl_if_needed(path, max_bytes=max_bytes, keep_n=keep_n)
