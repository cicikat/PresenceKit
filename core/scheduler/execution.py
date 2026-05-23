"""Dry-run and future real execution helpers for scheduler proposals."""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from core.safe_write import safe_append_jsonl
from core.sandbox import get_paths


# TODO(policy.yaml): 3.5a keeps execution in dry-run; 3.5b will wire real mode.
EXECUTE_MODE = "dry_run"


@dataclass(frozen=True)
class ExecuteResult:
    trigger_name: str
    would_send_prompt: str
    would_mark: list[str] = field(default_factory=list)
    would_mark_done: list[str] = field(default_factory=list)
    reads_cache_ok: bool = True
    dry_run: bool = True
    sent: bool = False


ExecuteFn = Callable[..., Awaitable[ExecuteResult]]
PromptFactory = Callable[[], str]
AfterSend = Callable[[], object]


async def execute_prompt(
    *,
    trigger_name: str,
    prompt_factory: PromptFactory,
    dry_run: bool,
    search_query: str = "",
    would_mark: list[str] | tuple[str, ...] | None = None,
    would_mark_done: list[str] | tuple[str, ...] | None = None,
    reads_cache_ok: bool = True,
    after_send: Optional[AfterSend] = None,
) -> ExecuteResult:
    """Execute a scheduler prompt, or log what would happen in dry-run mode."""

    prompt = str(prompt_factory() or "")
    result = ExecuteResult(
        trigger_name=trigger_name,
        would_send_prompt=prompt,
        would_mark=list(would_mark or []),
        would_mark_done=[str(x) for x in (would_mark_done or [])],
        reads_cache_ok=reads_cache_ok,
        dry_run=dry_run,
        sent=False,
    )

    if dry_run:
        write_execute_dryrun(result)
        return result

    from core.scheduler import loop

    await loop._pipeline_send(prompt, search_query=search_query, trigger_name=trigger_name)
    if after_send is not None:
        maybe = after_send()
        if inspect.isawaitable(maybe):
            await maybe
    for name in result.would_mark:
        loop._mark(name)
    return ExecuteResult(
        trigger_name=result.trigger_name,
        would_send_prompt=result.would_send_prompt,
        would_mark=result.would_mark,
        would_mark_done=result.would_mark_done,
        reads_cache_ok=result.reads_cache_ok,
        dry_run=False,
        sent=True,
    )


def write_execute_dryrun(result: ExecuteResult) -> None:
    safe_append_jsonl(
        get_paths().execute_dryrun_log(),
        {
            "ts": time.time(),
            "trigger_name": result.trigger_name,
            "would_send_prompt": result.would_send_prompt,
            "would_mark": result.would_mark,
            "would_mark_done": result.would_mark_done,
            "reads_cache_ok": result.reads_cache_ok,
        },
    )
