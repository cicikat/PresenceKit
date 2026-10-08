"""Persistent, source-anchored annotations; never a user-memory write."""
from __future__ import annotations

import uuid
import json

from core import character_document_library as library
from core.document_reading import revision
from core.sandbox import safe_user_id


def entries(row: dict) -> list[dict]:
    text_revision = revision(str(row.get("searchable_text") or ""))
    automatic = [{"note_id": f"auto_{part['start']}_{part['end']}", "source": "summary_model",
                  "revision": 0, "text_revision": text_revision,
                  "start_offset": part["start"], "end_offset": part["end"],
                  "content": str(part.get("summary") or "")[:600]}
                 for part in (row.get("overview") or {}).get("parts", [])]
    return automatic + list(row.get("notes") or [])


def read(uid: str, char_id: str, document_id: str = "", query: str = "", offset: int = 0) -> dict:
    uid, char_id = safe_user_id(uid), safe_user_id(char_id)
    matches = []
    needle = query.strip().casefold()
    for row in library._load(uid, char_id):
        if (row.get("deleted_at") or row.get("uid") != uid or row.get("char_id") != char_id
                or (document_id and row.get("document_id") != document_id)):
            continue
        for note in entries(row):
            if needle and needle not in (str(row.get("filename", "")) + " " + note["content"]).casefold():
                continue
            matches.append({**note, "document_id": row["document_id"], "filename": str(row.get("filename", ""))[:100]})
    offset = max(0, int(offset))
    page = []
    for item in matches[offset:offset + 3]:
        if page and len(json.dumps({"notes": page + [item]}, ensure_ascii=False)) > 6500:
            break
        page.append(item)
    return {"notes": page, "total": len(matches),
            "next_offset": offset + len(page) if offset + len(page) < len(matches) else None}


def write(uid: str, char_id: str, document_id: str, content: str, start_offset: int,
          end_offset: int, note_id: str = "", expected_revision: int = 0,
          mode: str = "replace") -> dict:
    uid, char_id = safe_user_id(uid), safe_user_id(char_id)
    if (mode not in {"append", "replace"} or not content.strip() or len(content) > 1500
            or len(json.dumps(content, ensure_ascii=False)) > 6000):
        return {"ok": False, "error": "invalid_content_or_mode", "content_limit": 1500}
    with library.scope_lock(uid, char_id):
        rows = library._load(uid, char_id)
        row = next((item for item in rows if item.get("document_id") == document_id
                    and item.get("uid") == uid and item.get("char_id") == char_id
                    and not item.get("deleted_at") and item.get("source") == "upload_file"), None)
        if row is None:
            return {"ok": False, "error": "document_not_found"}
        text = str(row.get("searchable_text") or "")
        if not 0 <= start_offset < end_offset <= len(text):
            return {"ok": False, "error": "invalid_source_range", "total_chars": len(text)}
        notes = row.setdefault("notes", [])
        previous = next((item for item in notes if item.get("note_id") == note_id), None)
        if note_id and previous is None:
            return {"ok": False, "error": "note_not_found_or_automatic_readonly"}
        if (previous and previous["revision"] != expected_revision) or (not previous and expected_revision != 0):
            return {"ok": False, "error": "revision_conflict", "revision": previous["revision"] if previous else 0}
        if not previous and len(notes) >= 100:
            return {"ok": False, "error": "note_limit", "limit": 100}
        updated = previous["content"] + "\n" + content if previous and mode == "append" else content
        if len(updated) > 1500 or len(json.dumps(updated, ensure_ascii=False)) > 6000:
            return {"ok": False, "error": "content_limit", "limit": 1500}
        note = {"note_id": note_id or "note_" + uuid.uuid4().hex, "source": "character",
                "revision": expected_revision + 1, "text_revision": revision(text),
                "start_offset": start_offset, "end_offset": end_offset,
                "content": updated, "updated_at": library._now()}
        if previous:
            notes[notes.index(previous)] = note
        else:
            notes.append(note)
        if not library._write(uid, char_id, rows):
            library.record_failure(uid=uid, char_id=char_id, reason="document_note_write")
            return {"ok": False, "error": "storage_failed"}
        return {"ok": True, **note, "document_id": document_id}


def _result(payload: dict):
    from core.tools.tool_result import ToolResult
    text = json.dumps(payload, ensure_ascii=False)
    text += "\n笔记不是原文。用 read_document(document_id, offset=start_offset) 回看，按 next_offset 续读到 end_offset。自动概要只读；纠正时新建角色笔记。更新角色笔记需传当前 revision。"
    return ToolResult(raw_data=text, safe_summary=text)


async def read_for_user(user_id: str, *, char_id: str, **kwargs):
    return _result(read(user_id, char_id, **kwargs))


async def write_for_user(user_id: str, *, char_id: str, **kwargs):
    return _result(write(user_id, char_id, **kwargs))
