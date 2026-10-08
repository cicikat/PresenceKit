"""Managed, fail-open semantic stage reconciliation for Scenario Dreams."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_VALID_DECISIONS = frozenset({"stay", "advance_next", "uncertain"})
_TASKS: set[asyncio.Task[Any]] = set()
_TASK_KEYS: set[str] = set()
_SEMAPHORE: asyncio.Semaphore | None = None
_SEMAPHORE_LOOP: asyncio.AbstractEventLoop | None = None


def _config() -> tuple[float, int]:
    timeout = 8.0
    concurrency = 2
    try:
        from core.config_loader import get_config

        raw = (get_config() or {}).get("dream", {}).get("scenario", {})
        if isinstance(raw, dict):
            timeout = max(0.25, min(float(raw.get("reconciler_timeout_s", timeout)), 30.0))
            concurrency = max(1, min(int(raw.get("reconciler_max_concurrency", concurrency)), 8))
    except Exception:
        pass
    return timeout, concurrency


def stall_threshold() -> int:
    try:
        from core.config_loader import get_config

        raw = (get_config() or {}).get("dream", {}).get("scenario", {}).get("reconciler_stall_turns", 2)
        return max(1, min(int(raw), 10))
    except Exception:
        return 2


def _bounded_text(value: Any, maximum: int = 900) -> str:
    text = str(value or "").strip()
    return text[:maximum]


def _stage_fields(stage: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(stage, dict):
        return {}
    return {
        "id": _bounded_text(stage.get("id"), 64),
        "dramatic_task": _bounded_text(stage.get("dramatic_task")),
        "entry_pressure": _bounded_text(stage.get("entry_pressure")),
        "exit_signs": [_bounded_text(item, 300) for item in (stage.get("exit_signs") or [])[:8]],
        "not_yet_allowed": [_bounded_text(item, 300) for item in (stage.get("not_yet_allowed") or [])[:8]],
    }


def build_reconcile_messages(request: dict[str, Any]) -> list[dict[str, str]]:
    """Build the minimum semantic input; no memory, prompt, or private truth."""
    current = request.get("current_stage") or {}
    next_stage = request.get("next_stage") or {}
    snippets = request.get("dialogue") or []
    visible = []
    for item in snippets[-6:]:
        if not isinstance(item, dict):
            continue
        role = "user" if item.get("role") == "user" else "assistant"
        visible.append(f"{role}: {_bounded_text(item.get('text'))}")
    payload = {
        "current_stage": _stage_fields(current),
        "next_stage": _stage_fields(next_stage),
        "completion_signals": request.get("completion_signals") or [],
        "visible_dialogue": visible,
    }
    system = (
        "You are a conservative Scenario Dream stage reconciler. "
        "Return JSON only: {\"decision\":\"stay\"|\"advance_next\"|\"uncertain\"}. "
        "Use advance_next only when the visible exchange clearly completes the current stage "
        "and moves into the adjacent next-stage task. Never skip a stage. "
        "Threats, plans, negations, hypotheticals, mentions, quoted text, and asides do not "
        "count as events that happened. If evidence is ambiguous, return uncertain."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    ]


def parse_decision(value: Any) -> str:
    """Parse only the closed decision enum; all other output is uncertain."""
    if isinstance(value, dict):
        data = value
    else:
        text = str(value or "").strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            data = json.loads(text)
        except Exception:
            return "uncertain"
    decision = data.get("decision") if isinstance(data, dict) else None
    return decision if decision in _VALID_DECISIONS else "uncertain"


def _record(request: dict[str, Any], **fields: Any) -> None:
    try:
        from core.dream.scenario_progress_audit import record_reconciler

        record_reconciler(
            str(request.get("dream_id") or ""),
            char_id=str(request.get("char_id") or ""),
            turn_index=int(request.get("turn_index") or 0),
            current_stage_id=str(request.get("from_stage_id") or ""),
            assistant_turn_id=str(request.get("assistant_turn_id") or ""),
            trigger=str(request.get("trigger") or ""),
            effective_profile=str(request.get("effective_profile") or ""),
            preset_name=str(request.get("preset_name") or ""),
            route_source=str(request.get("route_source") or ""),
            **fields,
        )
    except Exception as exc:
        logger.debug("[scenario_reconciler] audit skipped: %s", exc)


def _get_semaphore() -> asyncio.Semaphore:
    global _SEMAPHORE, _SEMAPHORE_LOOP
    loop = asyncio.get_running_loop()
    _, concurrency = _config()
    if _SEMAPHORE is None or _SEMAPHORE_LOOP is not loop:
        _SEMAPHORE = asyncio.Semaphore(concurrency)
        _SEMAPHORE_LOOP = loop
    return _SEMAPHORE


async def _run(request: dict[str, Any]) -> None:
    started = time.perf_counter()
    expected_version = int(request.get("state_version") or 0)
    _record(request, status="running", expected_state_version=expected_version)
    try:
        from core.dream.dream_state import (
            DreamStatus,
            read_state,
            write_state_if_version,
        )
        from core.dream.scenario_core import ScenarioCore
        from core.dream.scenario_loader import get_next_stage, load_script

        state = read_state(str(request.get("uid") or ""))
        if (
            state.get("status") != DreamStatus.DREAM_ACTIVE.value
            or state.get("dream_mode") != "scenario"
            or str(state.get("dream_id") or "") != str(request.get("dream_id") or "")
            or int(state.get("state_version") or 0) != expected_version
            or str(state.get("last_assistant_turn_id") or "") != str(request.get("assistant_turn_id") or "")
            or str((state.get("scenario_core") or {}).get("current_stage_id") or "") != str(request.get("from_stage_id") or "")
        ):
            _record(
                request,
                status="stale",
                failure_code="stale_state",
                expected_state_version=expected_version,
                state_version=int(state.get("state_version") or 0),
                state_version_match=False,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return

        script = load_script(str((state.get("scenario_core") or {}).get("script_id") or ""))
        next_stage = get_next_stage(script, str(request.get("from_stage_id") or ""))
        if not next_stage:
            _record(request, status="completed", decision="uncertain", failure_code="no_next_stage", duration_ms=int((time.perf_counter() - started) * 1000))
            return
        request = {**request, "next_stage": next_stage}

        from core.decision_contract import closed, DecisionRequest, Question

        timeout, _ = _config()
        messages = build_reconcile_messages(request)
        decision_request = DecisionRequest('scenario_reconcile', json.loads(messages[1]['content']), {
            'decision': Question('choice', messages[0]['content'], {
                'stay': 'Current stage is still underway',
                'advance_next': 'Visible exchange clearly completes the current stage and moves into the adjacent stage',
                'uncertain': 'Evidence is ambiguous or missing',
            }),
        }, failure_policy='fail_uncertain', confidence_gate=.65)
        raw = await asyncio.wait_for(
            closed(
                decision_request, messages,
                call_category="scenario_reconcile",
                char_id=str(request.get("char_id") or ""),
                max_tokens=32,
            ),
            timeout=timeout,
        )
        decision = parse_decision(raw)
        if decision != "advance_next":
            _record(
                request,
                status="completed",
                decision=decision,
                failure_code="parse_uncertain" if decision == "uncertain" else "",
                expected_state_version=expected_version,
                state_version=expected_version,
                state_version_match=True,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return

        current_state = read_state(str(request.get("uid") or ""))
        current_core = ScenarioCore.from_dict(current_state.get("scenario_core") or {})
        next_id = str(next_stage.get("id") or "")
        candidate = dict(current_state)
        candidate["scenario_core"] = current_core.advance_to_stage(next_id).to_dict()
        applied = write_state_if_version(
            str(request.get("uid") or ""), candidate, expected_version=expected_version
        )
        if not applied:
            _record(
                request,
                status="stale",
                decision=decision,
                failure_code="cas_conflict",
                expected_state_version=expected_version,
                state_version=int(current_state.get("state_version") or 0),
                state_version_match=False,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return
        _record(
            request,
            status="completed",
            decision=decision,
            applied=True,
            from_stage_id=str(request.get("from_stage_id") or ""),
            to_stage_id=next_id,
            expected_state_version=expected_version,
            state_version=int(candidate.get("state_version") or expected_version + 1),
            state_version_match=True,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except asyncio.CancelledError:
        _record(request, status="cancelled", failure_code="cancelled", duration_ms=int((time.perf_counter() - started) * 1000))
        raise
    except asyncio.TimeoutError:
        _record(request, status="failed", failure_code="timeout", duration_ms=int((time.perf_counter() - started) * 1000))
    except Exception:
        logger.warning("[scenario_reconciler] run failed", exc_info=True)
        _record(request, status="failed", failure_code="llm_error", duration_ms=int((time.perf_counter() - started) * 1000))


async def _worker(request: dict[str, Any]) -> None:
    async with _get_semaphore():
        await _run(request)


def _done(task: asyncio.Task[Any], key: str) -> None:
    _TASKS.discard(task)
    _TASK_KEYS.discard(key)
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


def schedule(request: dict[str, Any]) -> bool:
    """Schedule at most one reconciler for a Dream assistant turn."""
    if not request.get("effective_profile"):
        try:
            from core.model_registry import resolve_category_info

            route = resolve_category_info(
                "scenario_reconcile", char_id=str(request.get("char_id") or "")
            )
            request = {
                **request,
                "effective_profile": route.get("effective_profile", ""),
                "preset_name": route.get("effective_preset", ""),
                "route_source": route.get("source", ""),
            }
        except Exception:
            pass
    key = "{}:{}:{}".format(
        request.get("dream_id") or "",
        request.get("assistant_turn_id") or "",
        request.get("from_stage_id") or "",
    )
    if not key.replace(":", "") or key in _TASK_KEYS:
        return False
    try:
        loop = asyncio.get_running_loop()
        _record(request, status="queued")
        task = loop.create_task(_worker(dict(request)), name="scenario-reconciler")
    except RuntimeError:
        return False
    _TASK_KEYS.add(key)
    _TASKS.add(task)
    task.add_done_callback(lambda done: _done(done, key))
    return True


async def shutdown() -> None:
    """Cancel managed work during runtime shutdown; never starts at import time."""
    tasks = list(_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _TASKS.clear()
    _TASK_KEYS.clear()
