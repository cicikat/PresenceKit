"""Read-only recall tools for character-authored notes and scoped uploads."""
from __future__ import annotations

from datetime import date

from core.character_document_library import read as read_document_record
from core.character_document_library import search as search_document_records
from core.tools.diary_tool import _parse_date

_NOTE_CAP = 1_500


def _query_terms(query: str) -> list[str]:
    value = str(query or "").strip().casefold()
    if not value:
        return []
    terms = {value}
    for length in (2, 3, 4):
        terms.update(value[index:index + length] for index in range(len(value) - length + 1))
    return sorted((term for term in terms if term.strip()), key=len, reverse=True)


def _character_diary(char_id: str, target: date) -> str:
    from core.sandbox import get_paths
    path = get_paths().character_inner_diary(char_id=char_id) / f"{target.isoformat()}.md"
    try:
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    except OSError:
        return ""


async def read_character_diary_for_user(user_id: str, char_id: str, date_str: str = "") -> str:
    del user_id
    target = _parse_date(date_str) if date_str else date.today()
    target = target or date.today()
    text = _character_diary(char_id, target)
    if not text:
        return f"No character diary found for {target.isoformat()}."
    return f"Character diary {target.isoformat()}:\n{text[:_NOTE_CAP]}"


async def search_character_diary_for_user(user_id: str, char_id: str, query: str = "", date_str: str = "") -> str:
    del user_id
    from core.sandbox import get_paths
    root = get_paths().character_inner_diary(char_id=char_id)
    terms = _query_terms(query)
    requested_date = _parse_date(date_str) if date_str else None
    rows: list[str] = []
    if root.exists():
        for path in sorted(root.glob("*.md"), reverse=True):
            if requested_date and path.stem != requested_date.isoformat():
                continue
            try:
                text = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if not text or (terms and not all(term in text.casefold() for term in terms)):
                continue
            rows.append(f"[{path.stem}] {' '.join(text.split())[:260]}")
            if len(rows) >= 6:
                break
    if rows:
        return "Character diary search results:\n" + "\n".join(rows)
    if requested_date:
        return f"No character diary found for {requested_date.isoformat()}."
    return f"No character diary entries matched {query!r}." if query.strip() else "No character diary entries available."


async def search_documents_for_user(user_id: str, char_id: str, query: str = "", media_type: str = "") -> str:
    rows = search_document_records(user_id, char_id, query, media_type=media_type)
    if not rows:
        return "No related character documents found."
    lines = [f"{row['document_id']} | {row['filename']} | {row['media_type']} | {str(row['created_at'])[:10]} | sha256={row['sha256']}\n内容摘录（非模型概括）: {row['summary']}" for row in rows]
    return "Document search results:\n" + "\n".join(lines)


async def read_document_for_user(user_id: str, char_id: str, document_id: str, offset: int = 0, *, mode: str = "context", query: str = "") -> str:
    row = read_document_record(user_id, char_id, document_id, offset=offset, mode=mode, query=query)
    if row is None:
        return "Character document not found."
    more = f" Continue with offset={row['next_offset']}." if row["next_offset"] is not None else ""
    label = "内容摘录（非模型概括）" if mode == "summary" else "已保存正文/识别描述"
    return f"Document {row['filename']} | {label} | {row.get('created_at', '')}:{more}\n{row['content']}"


async def search_character_notes_for_user(user_id: str, char_id: str, query: str = "") -> str:
    """Search character diary entries and this scope's toybox mirrors."""
    results: list[str] = []
    diary = await search_character_diary_for_user(user_id, char_id, query)
    if not diary.startswith("No character diary"):
        results.append(diary)
    toybox_rows = search_document_records(user_id, char_id, query, source="character_note")
    if toybox_rows:
        rows = [f"{row['filename']} | {str(row['created_at'])[:10]}\nSummary: {row['summary']}" for row in toybox_rows]
        results.append("Toybox search results:\n" + "\n".join(rows))
    return "\n".join(results)[:_NOTE_CAP] if results else "No related character notes found."
