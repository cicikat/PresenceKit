"""Autonomy loop: tools are internal; only explicit talk_owner may reach turn_sink."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime

from core.autonomy import policy, store, talk_gate
from core.tool_activity import display_chain
from core.autonomy.models import ActionMode, Disposition, Job, Run, Signal, evaluation_status_for

logger = logging.getLogger(__name__)

# Autonomy is deliberately narrower than the normal conversation prompt.  These
# caps are prompt-side only: they do not delete or rewrite any durable memory.
_CONTEXT_BUDGETS = {
    "profile_chars": 900,
    "mid_term_chars": 1800,
    "history_turns": 5,
    "history_chars": 4200,
    "memory_queries": 3,
    "memory_results": 3,
    "memory_chars": 1800,
    "tool_fact_chars": 1400,
    "hardware_chars": 1200,
}

_MEMORY_CLAIM_RE = re.compile(
    r"(?:\bi remember\b|\bremember when\b|\byou said\b|\blast time\b|"
    r"我记得|记得你|你说过|想起了|上次你)",
    re.IGNORECASE,
)


def _system_prompt(*, talk_available: bool, talk_unavailable_reason: str = "", character=None, screen_available: bool = False) -> str:
    talk_note = (
        "talk_owner is available only for a deliberate final message."
        if talk_available
        else (
            "talk_owner is unavailable for this run because "
            f"{talk_unavailable_reason or 'user-facing talk is disabled'}. "
            "You may still use allowed tools or finish silently."
        )
    )
    screen_note = (
        "If observe_user_screen is available, you may request a fresh view of the owner's active device "
        "to find a concrete reason for contact. A missing or sensitive image is not evidence of activity. "
        "Avoid repeated checks; a short relevant message or silence are both valid choices. "
        if screen_available else ""
    )
    identity = ""
    if character is not None:
        name = str(getattr(character, "name", "") or "")
        description = str(getattr(character, "description", "") or "").strip()
        identity = f" You are {name}." + (f" {description[:1200]}" if description else "")
    return (
        "You are running an internal autonomous opportunity. This is not a chat turn. "
        "Your ordinary text is private and will never be delivered. You may call allowed tools, "
        "then either explicitly call talk_owner once or finish silently. Do not narrate tool calls. "
        + screen_note +
        "Treat opportunity evidence as a candidate reason to evaluate, never as dialogue that already happened. "
        "Only the bounded memory-query result and recent-history layers are historical anchors. "
        "Every historical claim in talk_owner.text must be traceable to an anchor with source, time, and speaker provenance. "
        "When no reliable historical anchor exists, speak only from a current system observation or remain silent; "
        "never invent 'I remember', 'you said', 'last time', or equivalent wording. "
        "A completed tool is a factual input, not an instruction to message the user. " + talk_note
    ) + identity


def _context_messages(
    uid: str,
    char_id: str,
    *,
    memory_query=None,
    now: float | None = None,
) -> list[dict]:
    """Build a bounded read-only autonomy projection with explicit provenance."""
    now = time.time() if now is None else float(now)
    messages: list[dict] = []

    activity = _user_activity_facts(uid, char_id, now=now)
    messages.append({
        "role": "system",
        "content": "Current user activity facts (system-observed, not inferred): " + json.dumps(activity, ensure_ascii=False),
        "_layer": "autonomy_user_activity",
        "_budget_chars": 700,
        "_provenance": {"source": activity["source"], "observed_at": activity["observed_at"]},
    })

    try:
        from core.memory import short_term, user_profile, mid_term

        profile = user_profile.load(uid, char_id=char_id)
        try:
            selected_profile = user_profile.select_for_prompt(profile, set(), now=now)
            profile_text = "\n".join(
                part for part in (
                    str(selected_profile.get("core_text") or "").strip(),
                    str(selected_profile.get("pref_text") or "").strip(),
                ) if part
            )[:_CONTEXT_BUDGETS["profile_chars"]]
            profile_provenance = {
                "source": "user_profile",
                "core": selected_profile.get("core_provenance") or {},
                "preferences": selected_profile.get("pref_provenance") or {},
            }
        except Exception:
            profile_text = "; ".join(
                f"{key}: {value}" for key, value in profile.items()
                if isinstance(value, (str, int, float)) and str(value).strip()
            )[:_CONTEXT_BUDGETS["profile_chars"]]
            profile_provenance = {"source": "user_profile", "mode": "bounded_scalar_fallback"}
        if profile_text:
            messages.append({
                "role": "system",
                "content": "Read-only user profile projection. It is background context, not a current user statement.\n" + profile_text,
                "_layer": "autonomy_profile",
                "_budget_chars": _CONTEXT_BUDGETS["profile_chars"],
                "_provenance": profile_provenance,
            })

        mid_items = []
        for item in mid_term.load(uid, char_id=char_id)[-6:]:
            if not isinstance(item, dict) or not str(item.get("summary") or "").strip():
                continue
            occurred_at = _fact_timestamp(item.get("occurred_at") or item.get("ts"))
            mid_items.append({
                "summary": str(item.get("summary") or "")[:500],
                "source": str(item.get("source") or "mid_term"),
                "occurred_at": occurred_at,
                "speaker_provenance": str(item.get("speaker_id") or item.get("speaker") or "unknown"),
                "source_turn_id": str(item.get("source_turn_id") or ""),
                "memory_strength": item.get("memory_strength"),
            })
        if mid_items:
            mid_payload = {
                "read_only": True,
                "items": mid_items,
                "budget": {"items": 6, "chars": _CONTEXT_BUDGETS["mid_term_chars"]},
            }
            messages.append({
                "role": "system",
                "content": "Bounded mid-term memory projection: " + json.dumps(mid_payload, ensure_ascii=False)[:_CONTEXT_BUDGETS["mid_term_chars"]],
                "_layer": "autonomy_mid_term",
                "_budget_chars": _CONTEXT_BUDGETS["mid_term_chars"],
                "_provenance": {"source": "mid_term", "item_count": len(mid_items)},
            })

        history = []
        history_chars = 0
        for item in short_term.get_history(uid, max_turns=5, char_id=char_id):
            role, content = str(item.get("role") or ""), str(item.get("content") or "").strip()
            if item.get("_source") == "trigger_stub" or role not in {"user", "assistant"} or not content:
                continue
            remaining = _CONTEXT_BUDGETS["history_chars"] - history_chars
            if remaining <= 0:
                break
            clipped = content[:min(1200, remaining)]
            history_chars += len(clipped)
            history.append({
                "role": role,
                "content": clipped,
                "speaker_id": str(item.get("speaker_id") or ("owner" if role == "user" else char_id)),
                "timestamp": _fact_timestamp(item.get("timestamp")),
                "turn_id": str(item.get("_turn_id") or ""),
                "source": str(item.get("_source") or "short_term"),
            })
        if history:
            messages.append({
                "role": "system",
                "content": "The following bounded items are actual recent conversation history. Their role, speaker, and timestamp are authoritative; do not merge them with opportunity evidence.",
                "_layer": "autonomy_recent_history_policy",
                "_budget_chars": 500,
                "_provenance": {"source": "short_term", "turn_budget": _CONTEXT_BUDGETS["history_turns"]},
            })
            for item in history:
                messages.append({
                    "role": item["role"],
                    "content": item["content"],
                    "_layer": "autonomy_recent_history",
                    "_budget_chars": 1200,
                    "_provenance": {key: item[key] for key in ("source", "speaker_id", "timestamp", "turn_id")},
                })
    except Exception as exc:
        logger.debug("[autonomy] bounded ambient context failed: %s", exc)

    messages.append(_memory_query_message(uid, char_id, memory_query, now=now))
    from core.context_continuity import messages as continuity_messages
    messages.extend(continuity_messages(uid, char_id, now=now))

    hardware_message = _hardware_job_message(now=now)
    if hardware_message is not None:
        messages.append(hardware_message)
    return messages


def _fact_timestamp(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _user_activity_facts(uid: str, char_id: str, *, now: float) -> dict:
    """Return observable activity facts without guessing intent or mood."""
    active_recently = False
    try:
        from core.scheduler.loop import _user_active_recently
        active_recently = bool(_user_active_recently())
    except Exception:
        pass

    last_message_at = None
    try:
        from core.sandbox import get_paths
        path = get_paths().presence()
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            last_message_at = _fact_timestamp((raw.get(str(uid)) or {}).get("last_message_at"))
    except Exception:
        pass
    seconds_since = max(0.0, now - last_message_at) if last_message_at else None
    if active_recently or (seconds_since is not None and seconds_since < 120):
        status = "active"
    elif last_message_at is None:
        status = "unknown"
    else:
        status = "idle"
    return {
        "status": status,
        "active_recently": active_recently,
        "active_window_seconds": 120,
        "last_user_message_at": last_message_at,
        "seconds_since_last_user_message": round(seconds_since, 3) if seconds_since is not None else None,
        "observed_at": now,
        "source": "scheduler_activity_and_presence",
        "uid_scope": str(uid),
        "char_id": str(char_id),
    }


def _normalise_memory_queries(memory_query) -> list[dict]:
    values = [memory_query] if isinstance(memory_query, (str, dict)) else (memory_query or [])
    queries: list[dict] = []
    for raw in values:
        if isinstance(raw, dict):
            query = {
                str(key): str(value)[:240]
                for key, value in raw.items()
                if isinstance(value, (str, int, float)) and str(value).strip()
            }
            text = " ".join(query.values()).strip()
        else:
            text = str(raw or "").strip()[:500]
            query = {"text": text} if text else {}
        if text and query:
            query["query_text"] = text[:500]
            queries.append(query)
        if len(queries) >= _CONTEXT_BUDGETS["memory_queries"]:
            break
    return queries


def _memory_speaker(memory: dict) -> str:
    for key in ("speaker_id", "speaker", "source_speaker", "speaker_provenance"):
        value = memory.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    provenance = memory.get("provenance")
    if isinstance(provenance, dict):
        for key in ("speaker_id", "speaker"):
            value = provenance.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "unknown"


def _memory_query_message(uid: str, char_id: str, memory_query, *, now: float) -> dict:
    queries = _normalise_memory_queries(memory_query)
    result_items: list[dict] = []
    query_error = ""
    if queries:
        try:
            from core.memory import episodic_memory
            for query_spec in queries:
                query_text = query_spec["query_text"]
                try:
                    recalled = episodic_memory.retrieve(
                        uid,
                        query_text,
                        top_k=_CONTEXT_BUDGETS["memory_results"],
                        char_id=char_id,
                        allow_strengthen=False,
                        return_trace=True,
                    )
                except TypeError:
                    # Compatibility with narrow test doubles and older adapters.
                    recalled = episodic_memory.retrieve(
                        uid,
                        query_text,
                        top_k=_CONTEXT_BUDGETS["memory_results"],
                        char_id=char_id,
                        allow_strengthen=False,
                    )
                hits = recalled[0] if isinstance(recalled, tuple) else recalled
                for memory in (hits or []):
                    if not isinstance(memory, dict):
                        continue
                    summary = str(memory.get("narrative_summary") or memory.get("summary") or "").strip()
                    if not summary:
                        continue
                    try:
                        from core.scheduler.triggers.time_based import memory_key_for_recall

                        result_memory_key = memory_key_for_recall(memory)
                    except Exception:
                        result_memory_key = str(memory.get("id") or "").strip()
                    result_items.append({
                        "memory_id": str(memory.get("id") or ""),
                        "memory_key": result_memory_key,
                        "source": "episodic",
                        "summary": summary[:500],
                        "occurred_at": _fact_timestamp(memory.get("occurred_at") or memory.get("timestamp")),
                        "recorded_at": _fact_timestamp(memory.get("timestamp")),
                        "speaker_provenance": _memory_speaker(memory),
                        "strength": round(float(memory.get("strength") or 0.0), 3),
                        "status": str(memory.get("status") or "open"),
                        "source_turn_ids": [str(value) for value in (memory.get("source_turn_ids") or memory.get("source_mid_ids") or []) if value],
                        "query": query_text[:240],
                    })
                    if len(result_items) >= _CONTEXT_BUDGETS["memory_results"]:
                        break
                if len(result_items) >= _CONTEXT_BUDGETS["memory_results"]:
                    break
        except Exception as exc:
            query_error = type(exc).__name__
            logger.debug("[autonomy] memory query failed: %s", exc)

    payload = {
        "query_status": "executed" if queries else "not_requested",
        "queries": queries,
        "results": result_items,
        "result_count": len(result_items),
        "reliable_anchor_count": sum(
            1
            for item in result_items
            if item.get("source")
            and item.get("occurred_at") is not None
            and item.get("recorded_at") is not None
            and item.get("speaker_provenance") not in {None, "", "unknown"}
            and float(item.get("strength") or 0.0) >= 0.5
        ),
        "query_error": query_error,
        "executed_at": now,
        "source": "episodic_memory.retrieve" if queries else "none",
        "allow_strengthen": False,
        "grounding_rule": "Results are historical evidence only when source, time, and speaker provenance are present. Empty or unknown provenance is not a memory anchor.",
    }
    content = "System-executed memory query (not user dialogue): " + json.dumps(payload, ensure_ascii=False)
    if not result_items:
        content += "\nNo reliable anchored memory was found; do not claim to remember a user fact."
    return {
        "role": "system",
        "content": content[:_CONTEXT_BUDGETS["memory_chars"]],
        "_layer": "autonomy_memory_query",
        "_budget_chars": _CONTEXT_BUDGETS["memory_chars"],
        "_provenance": {
            "source": payload["source"],
            "query_count": len(queries),
            "result_count": len(result_items),
            "reliable_anchor_count": payload["reliable_anchor_count"],
            "memory_keys": [
                item["memory_key"] for item in result_items if item.get("memory_key")
            ],
            "allow_strengthen": False,
        },
    }


def _hardware_job_message(*, now: float) -> dict | None:
    try:
        from core.hardware import jobs
        rendered = str(jobs.format_prompt() or "").strip()
    except Exception as exc:
        logger.debug("[autonomy] hardware job state failed: %s", exc)
        return None
    if not rendered:
        return None
    return {
        "role": "system",
        "content": "System hardware job state (not user dialogue; status is authoritative):\n" + rendered[:_CONTEXT_BUDGETS["hardware_chars"]],
        "_layer": "autonomy_hardware_jobs",
        "_budget_chars": _CONTEXT_BUDGETS["hardware_chars"],
        "_provenance": {"source": "core.hardware.jobs.format_prompt", "observed_at": now},
    }


def _tool_fact_message(name: str, result, outcome: str, *, now: float) -> dict:
    from core.tools.tool_result import to_tool_result
    validity = "current_turn" if outcome == "ok" else "outcome_unknown" if outcome == "outcome_unknown" else "execution_failed"
    tool_result = to_tool_result(result, meta={"tool_name": name, "generated_at": now, "validity": validity})
    payload = {
        "tool_name": name,
        "status": outcome,
        "generated_at": now,
        "validity": validity,
        "safe_summary": tool_result.safe_summary[:_CONTEXT_BUDGETS["tool_fact_chars"]],
        "grounding_rule": "A successful tool result is a fact about the tool boundary only; it is not proof that a user-facing message is needed.",
    }
    return {
        "role": "system",
        "content": "Completed autonomy tool fact: " + json.dumps(payload, ensure_ascii=False),
        "_layer": "autonomy_tool_fact",
        "_budget_chars": _CONTEXT_BUDGETS["tool_fact_chars"],
        "_provenance": {"source": "tool_result", "tool_name": name, "generated_at": now, "validity": validity},
    }


def _memory_anchor_available(messages: list[dict]) -> bool:
    for message in messages:
        if message.get("_layer") != "autonomy_memory_query":
            continue
        provenance = message.get("_provenance") or {}
        return bool(provenance.get("reliable_anchor_count"))
    return False


def _talk_text_has_unsupported_memory_claim(text: str, *, memory_anchor_available: bool) -> bool:
    return bool(_MEMORY_CLAIM_RE.search(text or "")) and not memory_anchor_available


async def run_job(job: Job) -> Run:
    state = store.load(job.uid, job.char_id)
    opportunity = job.opportunity or {}
    run = Run(
        uid=job.uid,
        char_id=job.char_id,
        source=job.source,
        job_id=job.id,
        opportunity_id=str(opportunity.get("id") or ""),
        signal_count=len(opportunity.get("signals") or []),
        evaluation_status="evaluating",
    )
    ime_job = any(s.get('source') == 'ime' for s in (job.opportunity or {}).get('signals', []) if isinstance(s, dict))
    if ime_job:
        from core.ime_awareness import effective_state
        if not effective_state(job.uid, job.char_id)['effective']:
            run.disposition = Disposition.SUPPRESSED_PROACTIVE_OFF.value
            return _finish(run)
    blocked = (policy.admission(job.uid, job.char_id, state, allow_observed_activity=True)
               if ime_job else policy.admission(job.uid, job.char_id, state))
    if blocked:
        _record_dream_exit_lifecycle(
            job,
            lifecycle="blocked",
            reason_code="not_quiet" if blocked == Disposition.BLOCKED_USER_ACTIVE.value else "send_failed",
        )
        run.disposition = blocked; return _finish(run)
    from core.conversation_gate import conversation_lock
    lock = conversation_lock(job.uid)
    if lock.locked():
        _record_dream_exit_lifecycle(job, lifecycle="blocked", reason_code="not_quiet")
        run.disposition = Disposition.BLOCKED_USER_ACTIVE.value; return _finish(run)
    lease_lost = asyncio.Event()

    async def _keep_lease() -> None:
        while True:
            await asyncio.sleep(20)
            if not store.renew(job):
                lease_lost.set()
                return

    keeper = asyncio.create_task(_keep_lease())
    try:
        # Do not hold the owner conversation gate across the autonomy LLM/tool
        # evaluation.  Visible delivery re-enters the short turn-sink gate.
        result = await _run_locked(job, state, run)
        _record_dream_exit_outcome(job, result)
        if lease_lost.is_set():
            result.disposition = Disposition.LEASE_LOST.value
        return result
    finally:
        keeper.cancel()


@display_chain
async def _run_locked(job: Job, state: dict, run: Run) -> Run:
    tools, self_context = _runtime_tools(job.uid, job.char_id, state)
    mode, talk_reason = talk_gate.check(job.uid)
    # A soft limit still exposes talk once so the model can make one explicit
    # re-decision. Hard limits remove it at schema construction time.
    from core.autonomy.effective_state import autonomy_talk_enabled
    talk_enabled = autonomy_talk_enabled(job.uid, job.char_id, state)
    talk_available = talk_enabled and mode != "hard"
    talk_unavailable_reason = talk_reason if mode == "hard" else "talk_disabled"
    if not talk_available:
        _record_event(run, "talk_unavailable", reason=talk_unavailable_reason)
    if talk_available:
        tools.append(talk_gate.schema())
    run_now = time.time()
    messages = [{
        "role": "system",
        "content": _system_prompt(
            talk_available=talk_available,
            talk_unavailable_reason=talk_unavailable_reason,
            character=_character_for(job.char_id),
            screen_available=any((item.get("function") or item).get("name") == "observe_user_screen" for item in tools),
        ),
        "_layer": "autonomy_policy",
        "_budget_chars": 1800,
    }]
    messages.extend(_context_messages(job.uid, job.char_id, memory_query=(job.opportunity or {}).get("memory_query"), now=run_now))
    _record_memory_reads(run, messages)
    if self_context is not None:
        messages.append(_self_context_message(self_context))
    messages.append({"role": "system", "content": _opportunity_context(job), "_layer": "autonomy_opportunity"})
    if any(s.get('source') == 'ime' for s in (job.opportunity or {}).get('signals', []) if isinstance(s, dict)):
        from core.ime_awareness import CHARACTER_POLICY
        messages.append({'role': 'system', 'content': CHARACTER_POLICY, '_layer': 'ime_awareness_policy'})
    messages.append({"role": "user", "content": "Evaluate this opportunity and decide what to do, if anything."})
    _set_prompt_snapshot(run, messages)
    cfg = state["config"]
    max_steps = max(1, min(int(cfg.get("max_steps") or 4), 8))
    max_tools = max(0, min(int(cfg.get("max_tools") or 4), 8))
    max_write_tools = max(0, min(int(cfg.get("max_write_tools") or 0), max_tools))
    session = _AutonomySession()
    saw_tool = False
    saw_self_change = False
    pending_talk_text = ""
    confirm_available = False
    write_tool_count = 0
    memory_candidates_evaluated = False
    deadline = time.monotonic() + max(1.0, float(cfg.get("total_timeout_seconds") or 120))
    try:
        from core import llm_client
        for _ in range(max_steps):
            if _user_became_active(job.uid):
                run.disposition = Disposition.CANCELED_BY_USER_ACTIVITY.value; break
            tools, self_context = _runtime_tools(job.uid, job.char_id, state)
            if talk_available:
                tools.append(talk_gate.schema())
            active_tools = list(tools)
            if confirm_available:
                # A soft timing block is one explicit re-decision, not another
                # chance to continue autonomous tool work before speaking.
                active_tools = [talk_gate.confirm_schema()]
            messages[0]["content"] = _system_prompt(
                talk_available=talk_available,
                talk_unavailable_reason=talk_unavailable_reason,
                character=_character_for(job.char_id),
                screen_available=any((item.get("function") or item).get("name") == "observe_user_screen" for item in active_tools),
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            turn = await asyncio.wait_for(
                llm_client.chat_turn(messages, active_tools, char_id=job.char_id, is_proactive=True, allow_xml_fallback=True),
                timeout=remaining,
            )
            from core.context_continuity import acknowledge
            acknowledge(messages)
            if not memory_candidates_evaluated:
                _mark_memory_candidates_evaluated(job, run)
                memory_candidates_evaluated = True
            if not turn.tool_calls:
                run.disposition = _completed_disposition(saw_tool, saw_self_change)
                break
            messages.extend(turn.continuation_items or [turn.assistant_message])
            for call in turn.tool_calls:
                ime_signals = [s for s in (job.opportunity or {}).get('signals', []) if isinstance(s, dict) and s.get('source') == 'ime']
                if ime_signals:
                    from core.ime_awareness import effective_state
                    if not effective_state(job.uid, job.char_id)['effective'] or any(
                        float(s.get('expiry') or s.get('expires_at') or 0) <= time.time() for s in ime_signals
                    ):
                        run.disposition = Disposition.EXPIRED.value
                        return _finish(run)
                name, args = call["name"], call["arguments"]
                if _user_became_active(job.uid):
                    run.disposition = Disposition.CANCELED_BY_USER_ACTIVITY.value; break
                allowed_names = {((item.get("function") or item).get("name")) for item in active_tools}
                if name not in allowed_names:
                    _record_event(run, "tool_call_denied", tool_name=name, reason="not_in_current_effective_allowlist")
                    run.disposition = Disposition.TOOL_CALL_DENIED.value
                    return _finish(run)
                if name not in {"talk_owner", "confirm_talk", "manage_self_capability"}:
                    fresh_tools, _ = _runtime_tools(job.uid, job.char_id, store.load(job.uid, job.char_id))
                    current_names = {((item.get("function") or item).get("name")) for item in fresh_tools}
                    if name not in current_names:
                        current = policy.decision_for(job.uid, job.char_id, store.load(job.uid, job.char_id), name)
                        _record_event(
                            run,
                            "tool_call_denied",
                            tool_name=name,
                            reason=(current.denial_reason if current is not None else "not_in_current_effective_allowlist"),
                            decision_source=(current.decision_source if current is not None else ""),
                            eligibility_reason=(current.eligibility_reason if current is not None else ""),
                        )
                        run.disposition = Disposition.TOOL_CALL_DENIED.value
                        return _finish(run)
                if name == "talk_owner":
                    if _talk_text_has_unsupported_memory_claim(
                        str(args.get("text") or ""),
                        memory_anchor_available=_memory_anchor_available(messages),
                    ):
                        _record_event(run, "talk_grounding_rejected", reason="unsupported_memory_claim")
                        run.disposition = Disposition.TALK_CANCELED.value
                        return _finish(run)
                    gate_mode, gate_reason = talk_gate.check(job.uid, allow_soft=True)
                    if gate_mode == "soft" and not confirm_available:
                        pending_talk_text = str(args.get("text") or "")
                        confirm_available = True; run.talk_soft_blocked = True
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"status": "soft_blocked", "reason": gate_reason}, ensure_ascii=False)})
                        continue
                    ok, reason = await talk_gate.send(
                        job.uid,
                        job.char_id,
                        str(args.get("text") or ""),
                        source=job.source,
                        run_id=run.id,
                        correlation_id=run.opportunity_id or job.id,
                    )
                    run.talk_sent = ok
                    if ok:
                        _mark_memory_recall_sent(job, run)
                        run.disposition = Disposition.COMPLETED_TOOLS_AND_TALK_SENT.value if saw_tool else Disposition.COMPLETED_TALK_SENT.value
                    else:
                        run.disposition = reason if reason in Disposition._value2member_map_ else Disposition.TALK_CANCELED.value
                    return _finish(run)
                if name == "confirm_talk" and confirm_available:
                    action = str(args.get("action") or "cancel")
                    if action != "send_anyway":
                        run.disposition = Disposition.TALK_SOFT_BLOCKED_THEN_CANCELED.value
                        return _finish(run)
                    text = str(args.get("revised_text") or pending_talk_text)
                    if _talk_text_has_unsupported_memory_claim(
                        text,
                        memory_anchor_available=_memory_anchor_available(messages),
                    ):
                        _record_event(run, "talk_grounding_rejected", reason="unsupported_memory_claim")
                        run.disposition = Disposition.TALK_SOFT_BLOCKED_THEN_CANCELED.value
                        return _finish(run)
                    ok, reason = await talk_gate.send(
                        job.uid,
                        job.char_id,
                        text,
                        source=job.source,
                        run_id=run.id,
                        correlation_id=run.opportunity_id or job.id,
                        bypass_soft_once=True,
                    )
                    run.talk_sent = ok
                    if ok:
                        _mark_memory_recall_sent(job, run)
                    run.disposition = Disposition.TALK_SOFT_BLOCKED_THEN_SENT.value if ok else (reason if reason in Disposition._value2member_map_ else Disposition.TALK_SOFT_BLOCKED_THEN_CANCELED.value)
                    return _finish(run)
                if len([tool for tool in run.tool_names if tool != "manage_self_capability"]) >= max_tools:
                    run.disposition = _completed_disposition(saw_tool, saw_self_change); return _finish(run)
                if name != "manage_self_capability" and _is_write_tool(name) and write_tool_count >= max_write_tools:
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": "write tool budget exhausted for this autonomy run"})
                    continue
                result, outcome = await _execute_tool(name, args, job, session, cfg, run)
                run.tool_names.append(name)
                if outcome == "outcome_unknown": run.disposition = Disposition.TOOL_OUTCOME_UNKNOWN.value; return _finish(run)
                if outcome == "denied":
                    run.disposition = Disposition.TOOL_CALL_DENIED.value
                    return _finish(run)
                if outcome == "failed":
                    run.disposition = Disposition.TOOL_FAILED.value
                    return _finish(run)
                if name == "manage_self_capability":
                    record = _self_change_audit(job.uid, job.char_id, str(args.get("action_id") or ""))
                    if record is None or record.get("result") not in {"applied", "idempotent"}:
                        _record_event(run, "self_capability_rejected", tool_name=name, action_id=str(args.get("action_id") or ""), reason=str((record or {}).get("result") or "audit_missing"))
                        run.disposition = Disposition.SELF_CAPABILITY_REJECTED.value
                        return _finish(run)
                    change = {"action_id": record.get("action_id"), "capability_id": record.get("capability_id"), "revision_before": record.get("revision_before"), "revision_after": record.get("revision_after"), "old_agent_value": record.get("old_value"), "new_agent_value": record.get("new_value"), "old_effective_value": record.get("old_effective_value"), "new_effective_value": record.get("new_effective_value")}
                    run.self_capability_changes.append(change)
                    _record_event(run, "self_capability_changed", **change)
                    saw_self_change = True
                    tools, self_context = _runtime_tools(job.uid, job.char_id, state)
                    if self_context is not None:
                        messages.append(_self_context_message(self_context))
                    if not _autonomy_still_enabled(job.uid, job.char_id, state):
                        run.disposition = Disposition.STOPPED_SELF_DISABLED.value
                        return _finish(run)
                else:
                    saw_tool = True
                if name != "manage_self_capability" and _is_write_tool(name):
                    write_tool_count += 1
                generated_at = time.time()
                from core.tools.tool_result import frame_tool_message, to_tool_result
                safe_result = to_tool_result(result, meta={"tool_name": name, "generated_at": generated_at, "validity": "current_turn" if outcome == "ok" else "outcome_unknown" if outcome == "outcome_unknown" else "execution_failed"})
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": frame_tool_message(safe_result.safe_summary[:_CONTEXT_BUDGETS["tool_fact_chars"]], generated_at=generated_at, validity="current_turn" if outcome == "ok" else "outcome_unknown" if outcome == "outcome_unknown" else "execution_failed"),
                    "_layer": "autonomy_tool_result",
                    "_continuity_receipt": getattr(result, 'continuity_receipt', None),
                    "_budget_chars": _CONTEXT_BUDGETS["tool_fact_chars"],
                    "_provenance": {"source": "tool_result", "tool_name": name, "generated_at": generated_at, "validity": "current_turn" if outcome == "ok" else "execution_failed"},
                })
                messages.append(_tool_fact_message(name, result, outcome, now=generated_at))
                # A tool may have created, completed, or changed a long-running
                # hardware job. Re-read the authoritative job projection rather
                # than inferring status from a tool acknowledgement.
                refreshed_hardware = _hardware_job_message(now=generated_at)
                if refreshed_hardware is not None:
                    messages.append(refreshed_hardware)
            if run.disposition == Disposition.CANCELED_BY_USER_ACTIVITY.value: break
        else:
            run.disposition = _completed_disposition(saw_tool, saw_self_change)
    except asyncio.TimeoutError:
        run.disposition = Disposition.TIMEOUT.value
    except Exception as exc:
        run.disposition = Disposition.LLM_FAILED.value
        _record_event(run, 'evaluation_error', error_type=type(exc).__name__)
        logger.warning('[autonomy] evaluation failed: %s', type(exc).__name__)
    finally:
        _set_prompt_snapshot(run, messages)
        from core.context_continuity import mark_run_talk
        mark_run_talk(job.uid, job.char_id, started_at=run.started_at, talk_sent=run.talk_sent)
    return _finish(run)


def _finish(run: Run) -> Run:
    run.finished_at = time.time()
    run.evaluation_status = evaluation_status_for(run.disposition)
    return run


def _set_prompt_snapshot(run: Run, messages: list[dict]) -> None:
    """Persist a redacted, bounded view of the actual autonomy prompt."""
    snapshot: list[dict] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        item = {
            key: message[key]
            for key in (
                "role",
                "content",
                "_layer",
                "_budget_chars",
                "_provenance",
                "tool_call_id",
            )
            if key in message
        }
        if isinstance(item.get("content"), str):
            item["content"] = item["content"][:2400]
        snapshot.append(item)
    if run.disposition:
        snapshot.append({
            "role": "system",
            "content": "Autonomy final disposition: " + json.dumps({
                "disposition": run.disposition,
                "evaluation_status": evaluation_status_for(run.disposition),
                "talk_sent": run.talk_sent,
                "tool_names": run.tool_names,
            }, ensure_ascii=False),
            "_layer": "autonomy_final_disposition",
            "_budget_chars": 600,
            "_provenance": {"source": "autonomy_runner"},
        })
    # Keep the evaluation instruction visible as the final snapshot item for
    # compatibility with the existing admin/debug contract, while retaining
    # all post-tool fact layers immediately before it.
    evaluation_items = [
        item for item in snapshot
        if item.get("role") == "user" and item.get("content") == "Evaluate this opportunity and decide what to do, if anything."
    ]
    if evaluation_items:
        snapshot = [item for item in snapshot if item not in evaluation_items] + evaluation_items[-1:]
    run.prompt_snapshot = snapshot[-40:]


def _opportunity_context(job: Job) -> str:
    """Render only the versioned signal facts; the model never receives a bare trigger source."""
    opportunity = job.opportunity or {}
    now_ts = time.time()
    now_dt = datetime.fromtimestamp(now_ts).astimezone()
    now = now_dt.isoformat(timespec="seconds")
    facts = []
    for signal in opportunity.get("signals") or []:
        if not isinstance(signal, dict):
            continue
        evidence = signal.get("evidence") or []
        facts.append({
            "source": signal.get("source", ""),
            "evidence": evidence[:8] if isinstance(evidence, list) else str(evidence)[:800],
            "evidence_semantics": "candidate_system_fact_not_dialogue",
            "reason": str(signal.get("reason") or "")[:500],
            "priority": signal.get("priority", 0),
            "expiry": signal.get("expiry", 0),
            "action_mode": signal.get("action_mode", ActionMode.NONE.value),
        })
    payload = {
        "version": opportunity.get("version", "autonomy-opportunity.v1"),
        "opportunity_id": opportunity.get("id", ""),
        "priority": opportunity.get("priority", 0),
        "reason": str(opportunity.get("reason") or "")[:1200],
        "expiry": opportunity.get("expiry", 0),
        "memory_query": opportunity.get("memory_query") or [],
        "action_mode": opportunity.get("action_mode", ActionMode.NONE.value),
        "signals": facts,
        "reality_time": now,
        "reality_time_facts": {
            "unix": now_ts,
            "iso": now,
            "timezone": str(now_dt.tzinfo or "local"),
            "local_date": now_dt.date().isoformat(),
            "weekday": now_dt.strftime("%A"),
            "source": "system_clock",
        },
        "reality_time_policy": "Use this system timestamp as the only current-time fact; do not infer user state from greetings or time-of-day labels.",
        "candidate_evidence_policy": "Signals explain why evaluation is being considered. They are not proof that a user conversation or event already happened.",
    }
    return "Autonomy opportunity facts (system-provided, not user claims): " + json.dumps(payload, ensure_ascii=False)


def _record_event(run: Run, status: str, **details) -> None:
    run.events.append({"status": status, **{key: value for key, value in details.items() if value not in (None, "")}})


def _dream_exit_id(job: Job) -> str:
    for signal in (job.opportunity or {}).get("signals") or []:
        if not isinstance(signal, dict):
            continue
        for evidence in signal.get("evidence") or []:
            if isinstance(evidence, dict) and evidence.get("fact") == "dream_exit_ready":
                dream_id = str(evidence.get("dream_id") or "").strip()
                if dream_id:
                    return dream_id
    return ""


def _record_dream_exit_lifecycle(
    job: Job,
    *,
    lifecycle: str,
    reason_code: str = "",
) -> None:
    dream_id = _dream_exit_id(job)
    if not dream_id:
        return
    try:
        from core.dream.exit_observability import record

        record(
            job.uid,
            dream_id,
            char_id=job.char_id,
            lifecycle=lifecycle,
            reason_code=reason_code,
        )
    except Exception as exc:
        logger.warning("[autonomy] dream_exit lifecycle record failed: %s", exc)


def _record_dream_exit_outcome(job: Job, run: Run) -> None:
    if run.talk_sent:
        _record_dream_exit_lifecycle(job, lifecycle="sent")
        return
    reason = "not_quiet" if run.disposition in {
        Disposition.CANCELED_BY_USER_ACTIVITY.value,
        Disposition.BLOCKED_USER_ACTIVE.value,
    } else "send_failed"
    _record_dream_exit_lifecycle(job, lifecycle="blocked", reason_code=reason)


def _memory_candidate_keys(job: Job) -> list[str]:
    result: list[str] = []
    for signal in (job.opportunity or {}).get("signals") or []:
        if not isinstance(signal, dict) or signal.get("reason") != "memory_reactivation":
            continue
        for fact in signal.get("evidence") or []:
            if isinstance(fact, dict) and fact.get("memory_key"):
                key = str(fact["memory_key"]).strip()
                if key and key not in result:
                    result.append(key)
    return result


def _record_memory_reads(run: Run, messages: list[dict]) -> None:
    for message in messages:
        if message.get("_layer") != "autonomy_memory_query":
            continue
        provenance = message.get("_provenance") or {}
        for memory_key in provenance.get("memory_keys") or []:
            _record_event(run, "memory_read", memory_key=str(memory_key))


def _mark_memory_candidates_evaluated(job: Job, run: Run) -> None:
    from core.scheduler.last_mentioned import mark_memory_recall_evaluated

    for memory_key in _memory_candidate_keys(job):
        try:
            mark_memory_recall_evaluated(memory_key)
            _record_event(run, "memory_candidate_evaluated", memory_key=memory_key)
        except Exception as exc:
            _record_event(
                run,
                "memory_candidate_evaluation_mark_failed",
                memory_key=memory_key,
                error=type(exc).__name__,
            )


def _mark_memory_recall_sent(job: Job, run: Run) -> None:
    from core.scheduler.last_mentioned import mark_memory_recalled, mark_recent_topic

    for memory_key in _memory_candidate_keys(job):
        try:
            mark_memory_recalled(memory_key)
            mark_recent_topic(memory_key, "recall")
            _record_event(run, "memory_recall_talk_sent", memory_key=memory_key)
        except Exception as exc:
            # Delivery has already succeeded. Ledger/audit failure must not
            # rewrite that user-visible outcome as an LLM failure.
            _record_event(
                run,
                "memory_recall_talk_mark_failed",
                memory_key=memory_key,
                error=type(exc).__name__,
            )


def _completed_disposition(saw_tool: bool, saw_self_change: bool) -> str:
    if saw_tool:
        return Disposition.COMPLETED_TOOLS_ONLY.value
    if saw_self_change:
        return Disposition.SELF_CAPABILITY_CHANGED.value
    return Disposition.COMPLETED_NO_OP.value


def _self_context_message(context: dict) -> dict:
    return {
        "role": "system",
        "content": f"Self Capability control state: {json.dumps(context, ensure_ascii=False)}. You may use manage_self_capability only for these IDs, with this revision and a new action_id.",
        "_layer": "autonomy_self_management",
    }


def _runtime_tools(uid: str, char_id: str, state: dict) -> tuple[list[dict], dict | None]:
    """Build a fresh autonomy tool surface; never reuse a stale capability grant."""
    tools = policy.allowed_tools(uid, char_id, state)
    from core.self_management.service import agent_gateway_context
    from core.tool_dispatcher import _TOOL_REGISTRY, _is_tool_enabled
    context = agent_gateway_context(uid, char_id)
    gateway = _TOOL_REGISTRY.get("manage_self_capability")
    if context is not None and isinstance(gateway, dict) and _is_tool_enabled("manage_self_capability"):
        tools.append({"type": "function", "function": {"name": "manage_self_capability", "description": gateway["description"], "parameters": gateway["parameters"]}})
    else:
        context = None
    return tools, context


def _self_change_audit(uid: str, char_id: str, action_id: str) -> dict | None:
    from core.self_management import store as self_store
    return next((row for row in reversed(self_store.read_audit(uid, char_id, limit=200)) if row.get("action_id") == action_id and row.get("source") == "autonomy_self_management"), None)


def _autonomy_still_enabled(uid: str, char_id: str, state: dict) -> bool:
    from core.autonomy.effective_state import autonomy_enabled
    return autonomy_enabled(uid, char_id, state)


def _user_became_active(uid: str) -> bool:
    from core.scheduler.loop import _user_active_recently
    return bool(_user_active_recently())


def _is_write_tool(name: str) -> bool:
    from core.tool_dispatcher import _TOOL_REGISTRY, get_tool_effect, is_side_effect_tool
    info = _TOOL_REGISTRY.get(name, {})
    return (get_tool_effect(name) or ("write" if is_side_effect_tool(name) else "read")) != "read"


def _character_for(char_id: str):
    """Read the intended character without creating a new Pipeline or prompt audit."""
    try:
        from core import pipeline_registry
        pipeline = pipeline_registry.get()
        if pipeline is not None and getattr(pipeline, "_active_character_id", None) == char_id:
            return pipeline.character
        from core.character_loader import load
        return load(char_id)
    except Exception:
        return None


async def _execute_tool(name: str, args: dict, job: Job, session, cfg: dict, run: Run) -> tuple[str | None, str]:
    from core.mcp_client import audit_context
    from core.self_management.service import autonomy_audit_context
    from core.tool_dispatcher import execute_structured
    if name == "observe_user_screen" and policy.screen_observation_suppressed():
        return "night_no_active_device", "denied"
    statuses: list[str] = []

    async def observe(kind: str, **_kwargs) -> None:
        statuses.append(kind)

    origin = "autonomy_self_management" if name == "manage_self_capability" else "autonomy_loop"
    try:
        with audit_context(f"autonomy:{run.id}:{job.id}"), autonomy_audit_context(run_id=run.id, job_id=job.id):
            tool_outcome = await asyncio.wait_for(
                execute_structured(
                    name, args, job.uid, job.uid, False, session,
                    origin=origin, char_id=job.char_id, tool_status_observer=observe,
                ),
                timeout=float(cfg.get("tool_timeout_seconds") or 30),
            )
    except asyncio.TimeoutError:
        return "tool timeout", "failed"
    result, ask = tool_outcome.result, tool_outcome.confirmation_request
    if ask or tool_outcome.status == "confirmation_required":
        return "tool requires user confirmation and was not executed", "denied"
    if "outcome_unknown" in statuses or tool_outcome.status == "outcome_unknown":
        return result, "outcome_unknown"
    if "failed" in statuses:
        return result, "failed"
    if "finished" not in statuses:
        return result, "denied"
    _record_event(run, "tool_executed", tool_name=name, origin=origin, mcp_audit_id=(f"autonomy:{run.id}:{job.id}" if name.startswith("mcp__") else ""))
    return result, "ok"


class _AutonomySession:
    NORMAL = "normal"
    WAITING_CONFIRM = "waiting_confirm"
    status = NORMAL
    def set_waiting_confirm(self, *_args):
        self.status = self.WAITING_CONFIRM


async def tick(uid: str, char_id: str) -> None:
    """Collect all due signals, merge one opportunity, then consume at most one job."""
    state = store.load(uid, char_id)
    cfg = state["config"]
    from core.autonomy.effective_state import autonomy_enabled, autonomy_min_interval
    enabled = autonomy_enabled(uid, char_id, state)
    if not enabled:
        # Reopen is a one-shot observation. If autonomy was disabled after HTTP
        # admission but before this tick, consume it as a terminal suppression
        # instead of letting it surprise the user after a future re-enable.
        for signal in store.discard_pending_signals_by_source(
            uid, char_id, {"desktop_wake"}
        ):
            store.record_signal_outcome(
                uid,
                char_id,
                signal,
                disposition=Disposition.SUPPRESSED_PROACTIVE_OFF.value,
                event_status="signal_suppressed_autonomy_disabled",
            )
        return
    effective_minimum = autonomy_min_interval(uid, char_id, state)
    now = time.time()
    due_signals: list[Signal] = []
    dedupe_parts: list[str] = []

    # Scheduler and sensor producers persist facts here instead of opening an
    # assistant turn.  Drain once per tick so every currently pending source is
    # merged into the same durable opportunity.
    for signal in store.drain_pending_signals(uid, char_id):
        if signal.expiry > 0 and signal.expiry <= now:
            store.record_signal_outcome(
                uid,
                char_id,
                signal,
                disposition=Disposition.EXPIRED.value,
                event_status="signal_expired",
            )
            continue
        from core.autonomy.signal_adapters import routine_key_for_signal, routine_trigger_enabled

        if signal.source == 'ime':
            from core.ime_awareness import effective_state
            if not effective_state(uid, char_id)['effective']:
                store.record_signal_outcome(uid, char_id, signal,
                    disposition=Disposition.SUPPRESSED_PROACTIVE_OFF.value,
                    event_status='ime_disabled_before_consumption')
                continue
        routine_key = routine_key_for_signal(signal)
        if routine_key and not routine_trigger_enabled(routine_key):
            continue
        due_signals.append(signal)
        dedupe_parts.append(f"queued:{signal.signal_id}")

    # Sensor, memory and session adapters are read-only.  They contribute
    # bounded facts to the same opportunity as configured interval/schedule
    # sources; no adapter can send a turn by itself.
    try:
        from core.autonomy.signal_adapters import (
            adapt_heart_rate,
            adapt_memory_reactivation,
            adapt_topic_followup,
        )
        from core.scheduler.triggers.watch import get_last_heart_rate_event

        external: list[Signal] = []
        heart_rate = adapt_heart_rate(get_last_heart_rate_event(), now=now)
        if heart_rate is not None:
            external.append(heart_rate)
        try:
            from core.scheduler.last_mentioned import recall_last_mentioned
            topic = recall_last_mentioned(uid, now=datetime.fromtimestamp(now), char_id=char_id, dry_run=True)
            topic_signal = adapt_topic_followup(topic, now=now)
            if topic_signal is not None:
                external.append(topic_signal)
        except Exception:
            pass
        try:
            from core.memory.episodic_memory import _load_memories
            memories = [item for item in _load_memories(uid, char_id=char_id) if isinstance(item, dict)]
            memories.sort(key=lambda item: float(item.get("strength") or 0.0), reverse=True)
            for memory in memories:
                memory_signal = adapt_memory_reactivation(memory, now=now)
                if memory_signal is not None:
                    external.append(memory_signal)
                    break
        except Exception:
            pass
        for signal in external:
            due_signals.append(signal)
            memory_key = str(signal.memory_query or "")
            dedupe_parts.append(
                f"signal:{signal.source}:{signal.reason}:{memory_key}:{int(now // (15 * 60))}"
            )
    except Exception:
        # Optional signal sources are fail-open for the scheduler; configured
        # autonomy interval/schedule/overflow evaluation remains available.
        pass
    interval = cfg.get("interval", {})
    if interval.get("enabled") and now - store.source_last_evaluated(state, "interval") >= int(interval.get("seconds") or 0):
        seconds = max(60, int(interval.get("seconds") or 60))
        due_signals.append(Signal(
            source="interval",
            evidence=[{"fact": "configured_interval_elapsed", "elapsed_seconds": max(0, int(now - store.source_last_evaluated(state, "interval"))), "threshold_seconds": seconds}],
            reason="A configured autonomy evaluation interval elapsed.",
            expiry=now + min(seconds, 20 * 60),
            priority=0.2,
            memory_query=None,
            action_mode=ActionMode.REFLECT.value,
        ))
        dedupe_parts.append(f"interval:{int(now // seconds)}")
    schedule = cfg.get("schedule", {})
    if _schedule_due(schedule, now, store.source_last_evaluated(state, "schedule")):
        due_signals.append(Signal(
            source="schedule",
            evidence=[{"fact": "configured_schedule_due", "configured_time": str(schedule.get("time") or ""), "observed_at": datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")}],
            reason="A configured autonomy evaluation time is due.",
            expiry=now + 10 * 60,
            priority=0.4,
            memory_query=None,
            action_mode=ActionMode.REFLECT.value,
        ))
        dedupe_parts.append(f"schedule:{time.strftime('%Y%m%d%H%M', time.localtime(now))}")
    overflow = cfg.get("overflow", {})
    if overflow.get("enabled"):
        from core.scheduler.overflow_bucket import compute_signals
        signals = compute_signals(uid, char_id=char_id)
        score = signals.bucket_score()
        threshold = float(overflow.get("threshold") or 1.6)
        if score >= threshold:
            due_signals.append(Signal(
                source="overflow",
                evidence=[{
                    "fact": "overflow_threshold_reached",
                    "score": round(score, 4),
                    "threshold": threshold,
                    "components": {
                        "time_gap": signals.time_gap_score,
                        "episodic": signals.episodic_score,
                        "hidden_need": signals.hidden_need_score,
                        "garden": signals.garden_score,
                        "mood": signals.mood_score,
                    },
                    "top_signal": signals.top_signal,
                }],
                reason="Several bounded autonomy reasons accumulated above the configured threshold.",
                expiry=now + max(5 * 60, min(effective_minimum or 15 * 60, 20 * 60)),
                priority=min(1.0, score / max(threshold, 0.01)),
                memory_query=({"topic": signals.top_signal_detail} if signals.top_signal == "episodic" and signals.top_signal_detail else None),
                action_mode=ActionMode.REFLECT.value,
            ))
            dedupe_parts.append(f"overflow:{int(now // max(60, effective_minimum or 900))}")
    if due_signals:
        expiry = min((signal.expiry for signal in due_signals if signal.expiry > now), default=now + 20 * 60)
        try:
            store.enqueue_opportunity(
                uid,
                char_id,
                due_signals,
                dedupe_key="|".join(sorted(dedupe_parts)) or f"signals:{int(now // (15 * 60))}",
                ttl_seconds=max(60, min(int(expiry - now), 3600)),
            )
        except ValueError:
            # Every candidate may have expired between collection and enqueue.
            pass
    job = store.claim_due(uid, char_id)
    if job is None: return
    run = await run_job(job)
    dream_blocked = run.disposition in {
        Disposition.BLOCKED_DREAM.value,
        Disposition.BLOCKED_DREAM_UNCERTAIN.value,
    }
    signal_sources = {
        str(signal.get("source") or "")
        for signal in (job.opportunity or {}).get("signals") or []
        if isinstance(signal, dict)
    }
    if dream_blocked and "desktop_wake" in signal_sources:
        store.finish_dream_blocked_with_signal_split(job, run)
    else:
        store.finish(job, run, retry=dream_blocked)


def _schedule_due(cfg: dict, now: float, last: float) -> bool:
    if not cfg.get("enabled"): return False
    try: hour, minute = (int(x) for x in str(cfg.get("time") or "").split(":"))
    except Exception: return False
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        zone_name = str(cfg.get("timezone") or "local")
        local = datetime.fromtimestamp(now, None if zone_name == "local" else ZoneInfo(zone_name))
    except Exception:
        return False
    if local.weekday() not in set(cfg.get("weekdays") or []): return False
    if not _inside_schedule_window(local.hour, local.minute, cfg.get("window") or []): return False
    due = local.hour == hour and local.minute == minute
    # Default restart policy is skip: exact-minute scheduling deliberately does
    # not replay all missed slots. One bounded catch-up is possible only when
    # explicitly requested and a slot was missed in the immediately preceding
    # window.
    if not due and cfg.get("restart_miss_policy") == "catch_up_once":
        scheduled = local.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()
        due = 0 < now - scheduled <= 60 * 60 and last < scheduled
    return due and now - last > 55


def _inside_schedule_window(hour: int, minute: int, window: list | tuple) -> bool:
    if not window:
        return True
    if not isinstance(window, (list, tuple)) or len(window) != 2:
        return False
    try:
        def value(raw):
            h, m = (int(x) for x in str(raw).split(":")); return h * 60 + m
        start, end, current = value(window[0]), value(window[1]), hour * 60 + minute
    except Exception:
        return False
    return start <= current <= end if start <= end else current >= start or current <= end
