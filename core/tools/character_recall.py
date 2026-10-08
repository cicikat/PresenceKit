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


async def search_documents_for_user(user_id: str, char_id: str, query: str = "", media_type: str = ""):
    rows = search_document_records(user_id, char_id, query, media_type=media_type)
    if not rows:
        return "No related character documents found."
    from core.tools.tool_result import ToolResult
    # All eight scoped identities and image digests must survive projection.
    lines = [f"{row['document_id']} | {str(row['filename'])[:80]} | {row['media_type']} | {row['total_chars']} 字符 | 概要={row['overview_status']} | sha256={row['sha256']}\n开头摘录: {str(row['summary'])[:80]}" for row in rows]
    text = "Document search results:\n" + "\n".join(lines)
    return ToolResult(raw_data=text, safe_summary=text)


async def read_document_for_user(user_id: str, char_id: str, document_id: str, offset: int = 0, *, mode: str = "context", query: str = "", limit: int = 6000):
    from core.document_reading import summarize
    from core.character_document_library import record_provided
    from core.tools.tool_result import ToolResult
    if mode == "summary":
        await summarize(user_id, char_id, document_id)
    row = read_document_record(user_id, char_id, document_id, offset=offset, mode=mode, query=query, limit=limit)
    if row is None:
        return "Character document not found."
    if "total_chars" not in row:
        return row["content"]
    state = row["progress"]
    heading = (f"Document {str(row['filename'])[:100]} | document_id={document_id}\n"
               f"总长度={row['total_chars']} 字符；本段 offset={row['offset']} end_offset={row['end_offset']}；"
               f"章节={row['section'][:100]}；起始行={row['line']}。\n")
    if mode in {"context", "continue"}:
        heading += (f"next_offset={row['next_offset']}（None 表示本段到末尾）；"
                    "按此偏移继续，不能把一段当成全篇。\n")
        # This result has its own 8000-character bound. Do not route its body
        # through the generic 2000-character sanitizer and skip the lost tail.
        content = heading + row["content"]
        saved = record_provided(user_id, char_id, document_id, row["offset"], row["end_offset"])
        if not saved:
            content += "\n阅读进度未能保存，下次请显式传 next_offset。"
    else:
        heading += (f"概要状态={row['overview_status']}，覆盖原文前 {row['overview_covered_chars']}/{row['total_chars']} 字符。"
                    "pending 时只显示开头摘录，partial 不代表全文已概括。\n")
        if mode == "overview":
            heading += f"目录 {offset + 1} 起；next_section_offset={row['next_section_offset']}（传入 offset 翻目录）。\n"
            heading += "\n".join(f"offset={item['offset']} {item['title']}" for item in row["sections"])
            if not row["section_count"]:
                heading += "没有可识别标题，按字符偏移和起始行阅读；Word 不提供推测页码。"
        content = heading + "\n" + row["content"]
    content += (f"\n此前工具已提供 {state['provided_chars']}/{row['total_chars']} 字符，"
                f"下一未提供位置={state['next_unread_offset']}（本次读取前；提供不等于理解）。"
                "下轮可用 mode=continue 接续，或 mode=overview 看目录。")
    return ToolResult(raw_data=content, safe_summary=content, meta={"document_id": document_id})


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
