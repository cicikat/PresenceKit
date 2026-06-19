"""
Reading Grounding — deterministic page context for companion LLM grounding.

Provides build_reading_grounding_facts(session, page_text) -> dict.

Rules:
- page_text is TRUNCATED before injection — never dumps full page into prompt.
- Output does NOT include full page text — only a short excerpt.
- Designed to be injected into companion LLM prompt as <page_context>.
- No LLM calls, no external I/O, pure computation.
"""
from __future__ import annotations

from typing import Optional

_MAX_PAGE_EXCERPT = 200  # characters
_EXCERPT_SUFFIX = "……"


def _truncate_page_text(text: str, limit: int = _MAX_PAGE_EXCERPT) -> str:
    """Return first *limit* characters of text, appending suffix if truncated."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + _EXCERPT_SUFFIX


def build_reading_grounding_facts(
    current_page: int,
    total_pages: int,
    filename: str,
    page_text: Optional[str],
) -> dict:
    """
    Build deterministic grounding facts for a reading session.

    Args:
        current_page: 1-indexed current page number.
        total_pages: total page count.
        filename: the book/document filename.
        page_text: raw text of the current page (may be None or very long).

    Returns a dict with a short page excerpt, safe to inject into LLM prompt.
    """
    excerpt = _truncate_page_text(page_text or "", _MAX_PAGE_EXCERPT)
    progress_pct = round(current_page / total_pages * 100) if total_pages > 0 else 0

    return {
        "current_page": current_page,
        "total_pages": total_pages,
        "filename": filename,
        "progress_pct": progress_pct,
        "page_excerpt": excerpt,
        "has_text": bool(excerpt),
    }


def format_reading_grounding_for_prompt(facts: dict) -> str:
    """Format reading grounding facts as a <page_context> block for LLM injection."""
    lines = ["<page_context>"]
    lines.append(f"书名/文件：{facts.get('filename', '未知')}")
    lines.append(f"当前页码：第 {facts.get('current_page', '?')} 页 / 共 {facts.get('total_pages', '?')} 页（已读约 {facts.get('progress_pct', 0)}%）")

    excerpt = facts.get("page_excerpt", "")
    if excerpt:
        lines.append(f"\n本页开头（截断）：\n{excerpt}")
    else:
        lines.append("\n本页内容：（空白或不可读）")

    lines.append(
        "\n注意：以上是本页的开头片段，不是全文。叶瑄只能根据这段内容和用户的讨论来回应，"
        "不要凭空声称知道本页后续内容或其他页的细节。"
    )
    lines.append("</page_context>")
    return "\n".join(lines)
