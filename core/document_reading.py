"""Bounded document structure and resumable derived overviews, not memory."""
from __future__ import annotations

import asyncio
import hashlib
import re
import time

PAGE_CHARS = 6000
TEXT_CHARS = 500_000
SUMMARY_CHARS = 10_000
_jobs: set[tuple[str, str]] = set()
_summary_locks: dict[tuple[str, str, str], asyncio.Lock] = {}


def revision(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sections(text: str) -> list[dict]:
    """Character offsets in extracted text; no guessed Word page numbers."""
    result, offset, fenced = [], 0, False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
        if not fenced and (re.match(r"^#{1,6}\s+\S", stripped)
                           or re.match(r"^第[一二三四五六七八九十百千万零〇\d]+[章回节幕卷部篇]", stripped)):
            result.append({"title": stripped.lstrip("# ")[:100], "offset": offset})
        offset += len(line)
    return result[:500]


def merged_ranges(ranges: list, start: int, end: int) -> list[list[int]]:
    valid = [list(pair) for pair in ranges if isinstance(pair, list) and len(pair) == 2]
    if end > start:
        valid.append([start, end])
    merged = []
    for left, right in sorted(valid):
        if merged and left <= merged[-1][1]:
            merged[-1][1] = max(right, merged[-1][1])
        else:
            merged.append([left, right])
    return merged


def progress(row: dict) -> dict:
    total = len(str(row.get("searchable_text") or ""))
    ranges = (row.get("reading") or {}).get("provided_ranges", [])
    contiguous = ranges[0][1] if ranges and ranges[0][0] == 0 else 0
    return {"provided_chars": sum(end - start for start, end in ranges),
            "next_unread_offset": min(contiguous, total),
            "provided_complete": contiguous >= total and total > 0}


async def summarize(uid: str, char_id: str, document_id: str, *, max_chunks: int = 4) -> None:
    """Checkpoint each part, merge only successful parts; retry without rereading."""
    from core import character_document_library as library, llm_client
    lock = _summary_locks.setdefault((uid, char_id, document_id), asyncio.Lock())
    async with lock:
        row = library.get_record(uid, char_id, document_id)
        if not row or row.get("source") != "upload_file":
            return
        text = str(row.get("searchable_text") or "")
        rev = revision(text)
        state = dict(row.get("overview") or {})
        if state.get("status") == "ready":
            return
        state["last_attempt_at"] = time.time()
        if not library.update_derived(uid, char_id, document_id, rev, overview=state):
            return
        parts = list(state.get("parts") or [])
        covered = int(state.get("covered_chars") or 0)
        cursor = parts[-1]["end"] if parts else 0
        try:
            # Finish an interrupted merge before accepting more parts.
            if cursor == covered:
                for _ in range(max_chunks):
                    if cursor >= len(text):
                        break
                    end = min(cursor + SUMMARY_CHARS, len(text))
                    abstract = await llm_client.summarize_document_text(text[cursor:end], char_id=char_id)
                    if not abstract:
                        library.record_failure(uid=uid, char_id=char_id, reason="document_summary")
                        break
                    parts.append({"start": cursor, "end": end, "summary": abstract[:600]})
                    cursor = end
                    state.update(parts=parts, status="partial", text_revision=rev)
                    if not library.update_derived(uid, char_id, document_id, rev, overview=state):
                        return
            pending = [part for part in parts if part["end"] > covered]
            if pending:
                inputs = ("此前已覆盖部分的概要：\n" + str(state.get("text") or "") + "\n新增连续分段概要：\n"
                          + "\n".join(part["summary"] for part in pending))
                combined = await llm_client.summarize_document_text(inputs, combine=True, char_id=char_id)
                if combined:
                    state.update(text=combined[:1200], covered_chars=cursor)
                else:
                    library.record_failure(uid=uid, char_id=char_id, reason="document_summary_merge")
            state.update(status="ready" if state.get("covered_chars", 0) == len(text) else
                         "partial" if parts else "pending", text_revision=rev)
            library.update_derived(uid, char_id, document_id, rev, overview=state)
        except Exception:
            library.record_failure(uid=uid, char_id=char_id, reason="document_summary")


def schedule_summaries(uid: str, char_id: str) -> None:
    """After send, finish one document within a bounded two-minute background run."""
    key = (uid, char_id)
    if key in _jobs:
        return
    _jobs.add(key)

    async def run():
        from core import character_document_library as library
        try:
            for row in library.pending_overviews(uid, char_id):
                deadline = asyncio.get_running_loop().time() + 120
                while True:
                    before = library.get_record(uid, char_id, row["document_id"])
                    if not before or (before.get("overview") or {}).get("status") == "ready":
                        break
                    previous = (before.get("overview") or {}).get("covered_chars", 0)
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        break
                    await asyncio.wait_for(summarize(uid, char_id, row["document_id"]), timeout=remaining)
                    after = library.get_record(uid, char_id, row["document_id"])
                    if not after or (after.get("overview") or {}).get("covered_chars", 0) <= previous:
                        break
                break
        except asyncio.TimeoutError:
            library.record_failure(uid=uid, char_id=char_id, reason="document_summary_background_timeout")
        except Exception:
            library.record_failure(uid=uid, char_id=char_id, reason="document_summary_schedule")
        finally:
            _jobs.discard(key)

    try:
        asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        _jobs.discard(key)
