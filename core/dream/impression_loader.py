"""
Impression loader — the only reader of data/dreams/{char_id}/impressions/{uid}.json.

Provides formatted text for reality prompt layer 6g_dream_impression.

Injection strategy: ambient (newest-first, up to _MAX_INJECT unexpired entries).
No relevance retrieve — see FUTURE F1.
Framing: explicit <梦境印象> XML tag with non-reality note; renders plot + vivid_lines
when present (D2 细粒度 schema), falls back to impression_text-only for legacy entries.
"""

import logging

logger = logging.getLogger(__name__)

_MAX_INJECT = 3

_TAG_NOTE = "以下是叶瑄做过的梦，不是现实发生的事；可以像记得一个梦一样自然提起，但绝不可当作真实经历复述为事实"


def _render_entry(imp: dict) -> str:
    """Render a single impression entry into human-readable text."""
    parts: list[str] = []

    plot = (imp.get("plot") or "").strip()
    vivid_lines = [v.strip() for v in (imp.get("vivid_lines") or []) if str(v).strip()]
    impression_text = (imp.get("impression_text") or "").strip()

    if plot:
        parts.append(plot)
    if vivid_lines:
        parts.append("——" + "；".join(f'"{v}"' for v in vivid_lines))
    if impression_text:
        # Always include the first-person overview as the final line
        parts.append(impression_text)
    elif not parts:
        return ""

    return "\n".join(parts)


def load_impression_text(uid: str, *, char_id: str = "yexuan") -> str:
    """
    Return formatted impression block for 6g injection.
    Empty string when no active impressions exist.
    """
    try:
        from core.dream.impression_store import get_active_impressions

        active = get_active_impressions(uid, char_id=char_id)
        if not active:
            return ""

        rendered: list[str] = []
        for imp in active[:_MAX_INJECT]:
            text = _render_entry(imp)
            if text:
                rendered.append(text)

        if not rendered:
            return ""

        body = "\n\n".join(rendered)
        return f'<梦境印象 note="{_TAG_NOTE}">\n{body}\n</梦境印象>'
    except Exception as e:
        logger.warning(f"[impression_loader] uid={uid}: {e}")
        return ""
