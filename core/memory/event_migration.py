"""Offline, resumable import of legacy Markdown conversation evidence.

This module never participates in the chat path.  It reads legacy sources,
creates deterministic ledger events in bounded batches, and records only
content-free import progress.  A malformed legacy block becomes a
``legacy_unknown`` reference instead of an inferred conversation event.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from core.memory import event_store
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope
from core.safe_write import safe_write_json
from core.sandbox import get_paths, safe_user_id

_DAY_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
_TIME_RE = re.compile(r"^##\s+(\d{2}:\d{2})\s*$")
_MESSAGE_RE = re.compile(r"^\*\*([^*]+)\*\*[：:](.*)$")
_SPEAKER_RE = re.compile(r"\bspeaker:(user|assistant)\b")
_TURN_RE = re.compile(r"\bturn_id:([^\s]+)")
_SOURCE_RE = re.compile(r"\bsource:([^\s]+)")
_TRIGGER_RE = re.compile(r"\btrigger:([^\s]+)")
_ALLOWED_SOURCES = frozenset({"", "web", "dream_echo", "coplay", "legacy_migration"})
STATE_VERSION = 3
_LEGACY_ARTIFACTS = (
    ("short_term", "history", "history"),
    ("mid_term", "mid_term", "mid_term"),
    ("episodic", "episodic_memory", "episodic"),
    ("storyline", "storyline", "storyline"),
)


@dataclass(frozen=True)
class MigrationEntry:
    event_id: str
    legacy_ref: str
    block_hash: str
    occurred_at: float
    kind: str
    actor: str
    text: str = ""
    turn_id: str = ""
    seq: int = 0
    source: str = "legacy_migration"
    trigger: str = ""
    unknown_time: bool = False
    unknown_source: bool = False

    def event(self) -> dict[str, Any]:
        unknown = self.kind == "legacy_unknown"
        reference = f"[legacy_unknown:{self.legacy_ref}]"
        return {
            "event_id": self.event_id,
            "turn_id": self.turn_id or f"legacy:{self.block_hash[:24]}",
            "seq": self.seq,
            "occurred_at": self.occurred_at,
            "ingested_at": time.time(),
            "realm": "reality",
            "kind": self.kind,
            "actor": self.actor,
            "channel": "legacy_markdown",
            "stream": "legacy_markdown",
            "source": self.source,
            # The original Markdown remains in place.  The ledger stores a
            # stable reference and hash, not a second raw copy for unknowns.
            "raw_payload_json": {
                "legacy_ref": self.legacy_ref,
                "legacy_block_sha256": self.block_hash,
                "legacy_unknown": unknown,
                "legacy_time_unknown": self.unknown_time,
                "legacy_trigger": self.trigger,
                "legacy_source_unknown": self.unknown_source,
            },
            "raw_text": "" if unknown else self.text,
            "visible_text": reference if unknown else self.text,
            "memory_text": reference if unknown else self.text,
            "media_refs_json": [],
        }


def legacy_event_log_dir(scope: MemoryScope) -> Path:
    """Return the old uid-only Markdown location for import compatibility."""
    return get_paths()._p("event_log") / safe_user_id(scope.uid)


def current_event_log_dir(scope: MemoryScope) -> Path:
    """Return the canonical per-character Markdown event-log directory."""
    return resolve_path(scope, "event_log")


def _state_path(scope: MemoryScope) -> Path:
    return resolve_path(scope, "event_migration_state")


def read_state(scope: MemoryScope) -> dict[str, Any]:
    path = _state_path(scope)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def migration_status(scope: MemoryScope) -> dict[str, Any]:
    """Content-free admin projection of persisted import progress."""
    state = read_state(scope)
    allowed = {
        "state_version", "status", "source_digest", "total", "next_offset",
        "parsed", "malformed", "legacy_unknown", "duplicate", "conflict",
        "written", "failed", "backup", "updated_at", "last_error", "sources",
        "artifacts", "already_live", "would_write", "source_isolated",
        "unknown_source", "assistant_trigger_unknown_time", "plan_duplicate",
        "plan_conflict", "ledger_duplicate", "ledger_conflict", "comparison_status",
        "indeterminate", "indeterminate_statuses",
    }
    return {key: state[key] for key in allowed if key in state}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _date_time(path: Path, time_text: str | None) -> float | None:
    if time_text is None:
        return None
    try:
        return datetime.strptime(f"{path.stem} {time_text}", "%Y-%m-%d %H:%M").timestamp()
    except ValueError:
        return None


def _message_metadata(block: list[str], start: int, end: int) -> tuple[str, str, str, str]:
    """Read only metadata belonging to one rendered Markdown message."""
    speaker = ""
    turn_id = ""
    source = ""
    trigger = ""
    for line in block[start + 1:end]:
        if not line.lstrip().startswith(">"):
            continue
        speaker_match = _SPEAKER_RE.search(line)
        turn_match = _TURN_RE.search(line)
        source_match = _SOURCE_RE.search(line)
        trigger_match = _TRIGGER_RE.search(line)
        if speaker_match:
            speaker = speaker_match.group(1)
        if turn_match:
            turn_id = turn_match.group(1).strip()
        if source_match:
            source = source_match.group(1).strip()
        if trigger_match:
            trigger = trigger_match.group(1).strip()
    return speaker, turn_id, source, trigger


def _block_entries(path: Path, text: str) -> tuple[list[MigrationEntry], int, int]:
    entries: list[MigrationEntry] = []
    parsed = malformed = 0
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current:
                blocks.append(current)
            current = [line]
        elif line.strip() == "---":
            if current:
                current.append(line)
                blocks.append(current)
                current = []
        elif current or line.strip():
            current.append(line)
    if current:
        blocks.append(current)

    for block in blocks:
        raw_block = "\n".join(block).strip()
        if not raw_block:
            continue
        block_hash = _sha(raw_block)
        legacy_ref = f"{path.name}#{block_hash[:16]}"
        time_match = _TIME_RE.match(block[0]) if block else None
        occurred_at = _date_time(path, time_match.group(1) if time_match else None)
        message_indices = [index for index, line in enumerate(block) if _MESSAGE_RE.match(line)]
        emitted = 0
        for position, index in enumerate(message_indices):
            match = _MESSAGE_RE.match(block[index])
            assert match is not None
            label, first_text = match.groups()
            # A legacy label can be a character display name, but cannot prove
            # which actor it denotes.  Only the stable user label or matching
            # speaker metadata is admitted; contradictory fake metadata stays
            # a legacy_unknown reference.
            next_index = message_indices[position + 1] if position + 1 < len(message_indices) else len(block)
            speaker, turn_id, source, trigger = _message_metadata(block, index, next_index)
            source_unknown = source not in _ALLOWED_SOURCES
            preserved_source = source if source in {"web", "dream_echo", "coplay"} else "legacy_migration"
            if label.strip() == "用户":
                actor = "user" if speaker in {"", "user"} else ""
            else:
                actor = "assistant" if speaker == "assistant" else ""
            trigger_unknown_time = actor == "assistant" and bool(trigger) and occurred_at is None
            if actor not in {"user", "assistant"} or occurred_at is None or source_unknown:
                # A valid user message in the same block must not make an
                # unrelated ambiguous assistant line silently disappear.
                event_id = f"legacy-{_sha(f'{legacy_ref}:{index}:unknown')[:40]}"
                entries.append(MigrationEntry(
                    event_id, legacy_ref, block_hash, 0.0, "legacy_unknown",
                    "legacy_unknown", turn_id=turn_id, source="legacy_unknown",
                    trigger=trigger, unknown_time=occurred_at is None, unknown_source=source_unknown,
                ))
                malformed += 1
                continue
            continuations = [
                line.strip() for line in block[index + 1:next_index]
                if line.strip() and not line.lstrip().startswith(">") and line.strip() != "---"
            ]
            body = "\n".join([first_text.strip(), *continuations]).strip()
            if not body:
                continue
            stable_ref = f"{path.name}:{turn_id}:{actor}" if turn_id else f"{legacy_ref}:{index}:{actor}"
            event_id = f"{turn_id}:{actor}" if turn_id else f"legacy-{_sha(stable_ref)[:40]}"
            entries.append(MigrationEntry(
                event_id, legacy_ref, block_hash, occurred_at, "legacy_message", actor, body,
                turn_id=turn_id or f"legacy:{block_hash[:24]}", seq=0 if actor == "user" else 1,
                source=preserved_source, trigger=trigger, unknown_time=trigger_unknown_time,
            ))
            emitted += 1
            parsed += 1
        if emitted == 0 and not message_indices:
            # Import time is explicitly not a claim about original occurrence.
            # The original actor/time/causality remain unknown in the retained MD.
            event_id = f"legacy-{_sha(f'{legacy_ref}:unknown')[:40]}"
            entries.append(MigrationEntry(event_id, legacy_ref, block_hash, 0.0, "legacy_unknown", "legacy_unknown", source="legacy_unknown", unknown_time=True))
            malformed += 1
    return entries, parsed, malformed


def _json_item_count(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return 0, 1
    if isinstance(value, list):
        return len(value), 0
    if isinstance(value, dict):
        return len(value.get("entries") or value.get("events") or value.get("arcs") or value), 0
    return 0, 1


def _event_log_sources(scope: MemoryScope, source_dir: Path | None) -> list[tuple[str, Path]]:
    if source_dir is not None:
        return [("override", Path(source_dir))]
    return [("current", current_event_log_dir(scope)), ("legacy", legacy_event_log_dir(scope))]


def _entry_identity(entry: MigrationEntry) -> tuple[Any, ...]:
    return (
        entry.turn_id, entry.actor, entry.source, entry.text, entry.seq,
        entry.occurred_at, entry.kind, entry.unknown_time, entry.unknown_source,
    )


def _deduplicate_entries(entries: list[MigrationEntry]) -> tuple[list[MigrationEntry], int, int, set[str]]:
    """Keep the first deterministic event ID and classify duplicate/conflict plans."""
    unique: dict[str, MigrationEntry] = {}
    duplicate = conflict = 0
    conflicted: set[str] = set()
    for entry in entries:
        existing = unique.get(entry.event_id)
        if existing is None:
            unique[entry.event_id] = entry
        elif _entry_identity(existing) == _entry_identity(entry):
            duplicate += 1
        else:
            conflict += 1
            conflicted.add(entry.event_id)
    return list(unique.values()), duplicate, conflict, conflicted


def scan_legacy(scope: MemoryScope, *, source_dir: Path | None = None) -> dict[str, Any]:
    """Read current and legacy Markdown sources and return a content-free plan."""
    if scope.domain != "reality":
        raise ValueError("event migration requires a reality scope")
    entries: list[MigrationEntry] = []
    parsed = malformed = 0
    source_digests: list[str] = []
    source_counts = {"current": 0, "legacy": 0, "override": 0}
    for source_kind, directory in _event_log_sources(scope, source_dir):
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if not path.is_file() or not _DAY_FILE_RE.fullmatch(path.name):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                malformed += 1
                continue
            source_digests.append(f"{source_kind}/{path.name}:{_sha(content)}")
            file_entries, file_parsed, file_malformed = _block_entries(path, content)
            entries.extend(file_entries)
            source_counts[source_kind] += len(file_entries)
            parsed += file_parsed
            malformed += file_malformed
    entries, plan_duplicate, plan_conflict, conflicted_event_ids = _deduplicate_entries(entries)
    parsed = sum(1 for entry in entries if entry.kind == "legacy_message")
    malformed = sum(1 for entry in entries if entry.kind == "legacy_unknown")
    artifacts: dict[str, dict[str, int | bool | str]] = {}
    for name, legacy_dir, resolver_key in _LEGACY_ARTIFACTS:
        legacy_path = get_paths()._p(legacy_dir, f"{safe_user_id(scope.uid)}.json")
        current_path = resolve_path(scope, resolver_key)
        legacy_count, legacy_invalid = _json_item_count(legacy_path)
        current_count, current_invalid = _json_item_count(current_path)
        artifacts[name] = {
            "mode": "inventory_only",
            "present": legacy_path.exists() or current_path.exists(),
            "current_items": current_count,
            "legacy_items": legacy_count,
            "malformed": current_invalid + legacy_invalid,
        }
    already_live = ledger_duplicate = ledger_conflict = 0
    would_write = 0
    comparison_statuses: list[str] = []
    indeterminate_statuses: set[str] = set()
    ledger_classifications: dict[str, str] = {}
    for entry in entries:
        if entry.event_id in conflicted_event_ids:
            continue
        status, existing = event_store.migration_evidence_status(scope, entry.event_id)
        comparison_statuses.append(status)
        if status in {"missing_ledger", "not_found"}:
            ledger_classifications[entry.event_id] = "would_write"
            would_write += 1
            continue
        if status != "ok" or existing is None:
            ledger_classifications[entry.event_id] = status
            indeterminate_statuses.add(status)
            continue
        expected = entry.event()
        same = (
            existing["turn_id"] == expected["turn_id"]
            and existing["actor"] == expected["actor"]
            and existing["source"] == expected["source"]
            and existing["text_fingerprint"] == event_store.safe_text_fingerprint(expected)
        )
        live_equivalent = (
            expected["source"] == "legacy_migration"
            and existing["source"] not in {"legacy_migration", "web", "dream_echo", "coplay", "legacy_unknown"}
            and existing["turn_id"] == expected["turn_id"]
            and existing["actor"] == expected["actor"]
            and existing["text_fingerprint"] == event_store.safe_text_fingerprint(expected)
        )
        if live_equivalent:
            ledger_classifications[entry.event_id] = "already_live"
            already_live += 1
        elif same:
            ledger_classifications[entry.event_id] = "duplicate"
            ledger_duplicate += 1
        else:
            ledger_classifications[entry.event_id] = "conflict"
            ledger_conflict += 1
    if indeterminate_statuses:
        comparison_status = sorted(indeterminate_statuses)[0]
    elif comparison_statuses and all(status == "missing_ledger" for status in comparison_statuses):
        comparison_status = "missing_ledger"
    else:
        comparison_status = "ok"
    return {
        "entries": entries,
        "total": len(entries),
        "parsed": parsed,
        "malformed": malformed,
        "legacy_unknown": malformed,
        "duplicate": plan_duplicate + ledger_duplicate,
        "conflict": plan_conflict + ledger_conflict,
        "plan_duplicate": plan_duplicate,
        "plan_conflict": plan_conflict,
        "ledger_duplicate": ledger_duplicate,
        "ledger_conflict": ledger_conflict,
        "already_live": already_live,
        "would_write": 0 if indeterminate_statuses else would_write,
        "comparison_status": comparison_status,
        "indeterminate": bool(indeterminate_statuses),
        "indeterminate_statuses": sorted(indeterminate_statuses),
        "conflicted_event_ids": sorted(conflicted_event_ids),
        "ledger_classifications": ledger_classifications,
        "source_isolated": sum(entry.source in {"web", "dream_echo", "coplay"} for entry in entries),
        "unknown_source": sum(entry.unknown_source for entry in entries),
        "assistant_trigger_unknown_time": sum(bool(entry.actor == "legacy_unknown" and entry.trigger and entry.unknown_time) for entry in entries),
        "source_digest": _sha("\n".join(source_digests)),
        "sources": source_counts,
        "artifacts": artifacts,
    }


def apply_batch(
    scope: MemoryScope,
    plan: dict[str, Any],
    *,
    batch_size: int,
    backup: dict[str, Any],
    skip_conflicts: bool = False,
) -> dict[str, Any]:
    """Append at most one deterministic batch and persist resumable progress.

    ``skip_conflicts`` is reserved for the reconciler: a conflicting entry is
    advanced as deferred while independent entries in the same batch continue.
    The default remains fail-closed and pauses at the first conflict.
    """
    if batch_size < 1 or batch_size > 100:
        raise ValueError("batch_size must be between 1 and 100")
    if backup.get("verified") is not True:
        raise ValueError("backup_not_verified")
    entries = list(plan["entries"])
    previous = read_state(scope)
    same_source = (
        previous.get("state_version") == STATE_VERSION
        and previous.get("source_digest") == plan["source_digest"]
    )
    start = int(previous.get("next_offset", 0)) if same_source else 0
    start = max(0, min(start, len(entries)))
    state: dict[str, Any] = {
        "state_version": STATE_VERSION,
        "status": "running",
        "source_digest": plan["source_digest"],
        "total": len(entries),
        "next_offset": start,
        "parsed": int(plan["parsed"]),
        "malformed": int(plan["malformed"]),
        "legacy_unknown": int(plan["legacy_unknown"]),
        "duplicate": int(plan["duplicate"]),
        "conflict": int(plan["conflict"]),
        "plan_duplicate": int(plan.get("plan_duplicate", 0)),
        "plan_conflict": int(plan.get("plan_conflict", 0)),
        "ledger_duplicate": int(plan.get("ledger_duplicate", 0)),
        "ledger_conflict": int(plan.get("ledger_conflict", 0)),
        "comparison_status": str(plan.get("comparison_status") or ""),
        "indeterminate": bool(plan.get("indeterminate", False)),
        "indeterminate_statuses": list(plan.get("indeterminate_statuses") or []),
        "written": int(previous.get("written", 0)) if same_source else 0,
        "failed": int(previous.get("failed", 0)) if same_source else 0,
        "already_live": int(plan.get("already_live", 0)),
        "would_write": int(plan.get("would_write", len(entries))),
        "source_isolated": int(plan.get("source_isolated", 0)),
        "unknown_source": int(plan.get("unknown_source", 0)),
        "assistant_trigger_unknown_time": int(plan.get("assistant_trigger_unknown_time", 0)),
        "backup": dict(backup),
        "sources": dict(plan.get("sources") or {}),
        "artifacts": dict(plan.get("artifacts") or {}),
        "last_error": "",
    }
    if state["indeterminate"]:
        state["status"] = "paused"
        state["last_error"] = state["comparison_status"] or "indeterminate"
        state["updated_at"] = time.time()
        if not safe_write_json(_state_path(scope), state, keep_bak=True):
            raise RuntimeError("migration_state_write_failed")
        return migration_status(scope)
    for index, entry in enumerate(entries[start:start + batch_size], start=start):
        if entry.event_id in set(plan.get("conflicted_event_ids") or ()):
            state["last_error"] = "plan_conflict"
            if skip_conflicts:
                state["next_offset"] = index + 1
                continue
            break
        evidence_status, existing = event_store.migration_evidence_status(scope, entry.event_id)
        if evidence_status not in {"ok", "not_found", "missing_ledger"}:
            state["last_error"] = evidence_status
            state["failed"] += 1
            break
        if existing is not None:
            expected = entry.event()
            from core.memory.source_policy import is_isolated
            planned_classification = (plan.get("ledger_classifications") or {}).get(entry.event_id)
            source_matches = (
                existing["source"] == expected["source"]
                or (expected["source"] == "legacy_migration" and not is_isolated(existing["source"]))
            )
            same_identity = (
                existing["turn_id"] == expected["turn_id"]
                and existing["actor"] == expected["actor"]
                and source_matches
                and existing["text_fingerprint"] == event_store.safe_text_fingerprint(expected)
            )
            if same_identity:
                if expected["source"] == "legacy_migration" and existing["source"] != "legacy_migration":
                    if planned_classification != "already_live":
                        state["already_live"] += 1
                        state["would_write"] = max(0, state["would_write"] - 1)
                elif planned_classification != "duplicate":
                    state["duplicate"] += 1
                    state["ledger_duplicate"] += 1
                    state["would_write"] = max(0, state["would_write"] - 1)
                state["next_offset"] = index + 1
                continue
            if planned_classification != "conflict":
                state["conflict"] += 1
                state["ledger_conflict"] += 1
                state["would_write"] = max(0, state["would_write"] - 1)
            state["last_error"] = "conflict"
            if skip_conflicts:
                state["next_offset"] = index + 1
                continue
            break
        result = event_store.append_event(scope, entry.event())
        if result.inserted:
            state["written"] += 1
        elif result.ok:
            marker = event_store.migration_marker(scope, entry.event_id)
            if marker == entry.block_hash:
                state["duplicate"] += 1
            else:
                state["conflict"] += 1
        else:
            state["failed"] += 1
            state["last_error"] = result.error_code or "append_failed"
            break
        state["next_offset"] = index + 1
    state["status"] = "completed" if state["next_offset"] >= len(entries) else "paused"
    state["updated_at"] = time.time()
    if not safe_write_json(_state_path(scope), state, keep_bak=True):
        raise RuntimeError("migration_state_write_failed")
    return migration_status(scope)
