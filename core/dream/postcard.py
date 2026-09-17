"""Archive-only dream postcards.

This is the sole programmatic archive reader besides summary generation.  It is
strictly one-way (dream archive -> frozen email -> SMTP): it never writes a
memory, mood, hidden-state, impression, or prompt-facing store.
"""
from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from core.data_paths import DEFAULT_CHAR_ID
from core.safe_write import safe_write_json

logger = logging.getLogger(__name__)
MIN_ASSISTANT_TURNS = 5
_TEMPLATES = ("postcard", "sms", "diary_fragment", "note", "untitled")


@dataclass(frozen=True)
class PostcardEligibility:
    eligible: bool
    reason_code: str
    assistant_turns: int
    legacy_inferred: bool = False


@dataclass(frozen=True)
class ArchiveSnapshot:
    """One read of an archive, including the evidence that it was readable."""

    turns: list[dict[str, Any]]
    readable: bool


def evaluate_postcard_eligibility(
    *,
    dream_id: str,
    dream_mode: str,
    completion: str | None,
    turns: list[dict[str, Any]],
    existing_entries: list[dict[str, Any]],
    archive_readable: bool = True,
    legacy_inferred: bool = False,
) -> PostcardEligibility:
    """Pure postcard qualification decision; never reads or writes state."""
    assistants = sum(1 for turn in turns if turn.get("role") == "assistant" and str(turn.get("content") or "").strip())
    if dream_mode != "sandbox":
        return PostcardEligibility(False, "not_solo_sandbox", assistants, legacy_inferred)
    if any(str(item.get("dream_id")) == str(dream_id) and item.get("generation_status") != "generation_failed" for item in existing_entries):
        return PostcardEligibility(False, "duplicate", assistants, legacy_inferred)
    if not archive_readable:
        return PostcardEligibility(False, "archive_unreadable", assistants, legacy_inferred)
    if assistants < MIN_ASSISTANT_TURNS:
        return PostcardEligibility(False, "short_dream", assistants, legacy_inferred)
    if completion == "interrupted":
        return PostcardEligibility(False, "interrupted", assistants, legacy_inferred)
    if completion != "complete":
        return PostcardEligibility(False, "unknown_completion", assistants, legacy_inferred)
    return PostcardEligibility(True, "eligible", assistants, legacy_inferred)


def _schedule_path(char_id: str) -> Path:
    from core.sandbox import get_paths
    return get_paths().dreams_postcards_dir(char_id=char_id) / "schedule.json"

def _load_schedule(char_id: str) -> list[dict[str, Any]]:
    path = _schedule_path(char_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        return value if isinstance(value, list) else []
    except Exception as exc:
        logger.warning("[postcard] unreadable schedule: %s", exc)
        return []

def _save_schedule(char_id: str, entries: list[dict[str, Any]]) -> bool:
    return safe_write_json(_schedule_path(char_id), entries)

def _archive_turns(dream_id: str, char_id: str) -> ArchiveSnapshot:
    from core.sandbox import get_paths
    path = get_paths().dreams_archive_dir(char_id=char_id) / f"dream_{dream_id}.jsonl"
    turns: list[dict[str, Any]] = []
    readable = path.is_file()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    turns.append(value)
                else:
                    readable = False
    except Exception as exc:
        logger.warning("[postcard] archive read failed: %s", exc)
        readable = False
    return ArchiveSnapshot(turns=turns, readable=readable)


def _due_date(entries: list[dict[str, Any]], today: date) -> date:
    used = {str(item.get("scheduled_date")) for item in entries if not item.get("sent")}
    for _ in range(10):
        candidate = today + timedelta(days=random.randint(1, 356))
        if candidate.isoformat() not in used:
            return candidate
    candidate = today + timedelta(days=random.randint(1, 356))
    while candidate.isoformat() in used:
        candidate += timedelta(days=1)
    return candidate

async def generate_postcard(
    uid: str,
    dream_id: str,
    exit_type: str,
    *,
    char_id: str = DEFAULT_CHAR_ID,
    completion: str | None = None,
    exit_metadata: dict[str, Any] | None = None,
) -> None:
    """Freeze a qualifying sandbox dream as a scheduled postcard; fail open."""
    entries = _load_schedule(char_id)
    if any(str(item.get("dream_id")) == dream_id and item.get("generation_status") != "generation_failed" for item in entries):
        return
    archive = _archive_turns(dream_id, char_id)
    turns = archive.turns
    inferred = completion is None
    if completion is None:
        # Historical summaries have no completion field.  Infer conservatively
        # from explicit legacy exit metadata plus the valid-turn threshold.
        completion = "complete" if exit_type != "hard_exit" and turns else (
            "complete" if sum(1 for turn in turns if turn.get("role") == "assistant") >= MIN_ASSISTANT_TURNS else "interrupted"
        )
    eligibility = evaluate_postcard_eligibility(
        dream_id=dream_id,
        dream_mode=str((exit_metadata or {}).get("dream_mode") or "sandbox"),
        completion=completion,
        turns=turns,
        existing_entries=entries,
        archive_readable=bool(turns) and archive.readable,
        legacy_inferred=inferred,
    )
    if not eligibility.eligible:
        return
    failed_entry = next(
        (item for item in entries if str(item.get("dream_id")) == dream_id and item.get("generation_status") == "generation_failed"),
        None,
    )
    template_id = random.choice(_TEMPLATES)
    try:
        from core import llm_client
        template = _template_text(template_id)
        from core.dream.invariants import select_for_postcard
        invariant = select_for_postcard(uid, char_id=char_id)
        invariant_hint = "" if not invariant else ("\n\u53ef\u81ea\u7136\u5730\u81f3\u591a\u4e00\u6b21\u63d0\u53ca\u8fd9\u6761\u8de8\u68a6\u89c2\u5bdf\uff08\u4e0d\u89e3\u91ca\u5176\u6765\u6e90\uff0c\u4e0d\u8981\u7167\u6284\uff09\uff1a" + f"\u5f53{invariant['situation']}\uff0c\u4ed6\u5f80\u5f80{invariant['response']}\u3002")
        dream_ts = next((float(t["ts"]) for t in turns if t.get("ts")), 0.0)
        dream_time = datetime.fromtimestamp(dream_ts or datetime.now().timestamp()).strftime("%Y-%m-%d %H:%M")
        dialogue = "\n".join(f"[{t.get('role')}] {str(t.get('content') or '')[:240]}" for t in turns[-12:])
        letter = await llm_client.chat([
            {"role": "system", "content": template + invariant_hint + "\n只输出信正文。信内日期必须是：" + dream_time},
            {"role": "user", "content": "梦境归档片段：\n" + dialogue},
        ], call_category="chat", char_id=char_id, max_tokens_override=450)
        letter = str(letter).strip()
        if not letter:
            raise ValueError("empty_letter")
        entry = {"dream_id": dream_id, "uid": str(uid), "dream_time_iso": dream_time,
                 "template_id": template_id, "letter_text": letter,
                 "scheduled_date": _due_date(entries, date.today()).isoformat(), "sent": False,
                 "attempts": 0, "last_error": "", "generation_status": "generated",
                 "delivery_status": "scheduled", "eligibility_reason": eligibility.reason_code,
                 "legacy_inferred": eligibility.legacy_inferred,
                 "completion": completion,
                 "exit_mechanism": (exit_metadata or {}).get("exit_mechanism", ""),
                 "exit_initiator": (exit_metadata or {}).get("exit_initiator", "")}
        if failed_entry is None:
            entries.append(entry)
        else:
            failed_entry.clear()
            failed_entry.update(entry)
        _save_schedule(char_id, entries)
    except Exception as exc:
        logger.warning("[postcard] generation failed uid=%s dream=%s: %s", uid, dream_id, exc)
        failed = next((item for item in entries if str(item.get("dream_id")) == dream_id), None)
        if failed is None:
            failed = {"dream_id": dream_id, "uid": str(uid), "sent": False, "attempts": 0}
            entries.append(failed)
        failed.update({
            "generation_status": "generation_failed",
            "delivery_status": "not_scheduled",
            "eligibility_reason": "generation_failed",
            "last_error": "generation_failed",
        })
        _save_schedule(char_id, entries)

def _template_text(template_id: str) -> str:
    from core.sandbox import get_paths

    bundled = get_paths().bundled_templates_dir() / "dream_postcards" / f"{template_id}.md"
    path = bundled if bundled.exists() else Path("characters") / "dream_postcards" / "templates" / f"{template_id}.md"
    return path.read_text(encoding="utf-8") if path.exists() else "写一封克制的梦后短笺，以角色第一人称写给用户。"

async def deliver_due_postcards(*, char_id: str = DEFAULT_CHAR_ID, today: date | None = None) -> int:
    """Retry every due unsent entry; only SMTP success flips sent=True."""
    entries = _load_schedule(char_id)
    today_text = (today or date.today()).isoformat()
    changed = sent_count = 0
    from core.mail.mail_sender import send_letter
    for entry in entries:
        if entry.get("sent") or entry.get("generation_status") == "generation_failed" or str(entry.get("scheduled_date", "")) > today_text:
            continue
        ok = await send_letter("一封从梦里寄出的明信片", str(entry.get("letter_text") or ""))
        entry["attempts"] = int(entry.get("attempts") or 0) + 1
        if ok:
            entry["sent"] = True; entry["last_error"] = ""; entry["delivery_status"] = "sent"; sent_count += 1
        else:
            entry["last_error"] = "smtp_failed"; entry["delivery_status"] = "smtp_failed"
        changed = True
    if changed:
        _save_schedule(char_id, entries)
    return sent_count
