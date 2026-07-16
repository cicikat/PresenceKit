"""
Impression loader — the only reader of data/dreams/{char_id}/impressions/{uid}.json.

Provides formatted text for reality prompt layer 6g_dream_impression.

Injection strategy: force the latest exited dream for a precise number of
reality turns, then retrieve only impressions relevant to the current topic.
Framing: explicit <梦境印象> XML tag with non-reality note; renders plot + vivid_lines
when present (D2 细粒度 schema), falls back to impression_text-only for legacy entries.
"""

import logging
import re
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

_MAX_INJECT = 3
_PLOT_TOKEN_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]+")
_COMMON_BIGRAMS = {"我们", "你们", "他们", "这个", "那个", "还是", "只是", "已经", "好像", "什么", "怎么"}


def _tag_note(char_name: str) -> str:
    name = char_name or "角色"
    return f"以下是{name}做过的梦，不是现实发生的事；可以像记得一个梦一样自然提起，但绝不可当作真实经历复述为事实"


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


def _relevance_score(imp: dict, user_text: str, tags: set[str]) -> int:
    query = user_text.casefold()
    query_tags = {str(tag).strip().casefold() for tag in tags if str(tag).strip()}
    score = 0
    for raw_tag in imp.get("emotional_tags") or []:
        tag = str(raw_tag).strip().casefold()
        if tag and (tag in query or tag in query_tags):
            score += 4

    plot = str(imp.get("plot") or "").casefold()
    if plot and query:
        query_chunks = _PLOT_TOKEN_RE.findall(query)
        bigrams = {
            chunk[index:index + 2]
            for chunk in query_chunks
            for index in range(max(0, len(chunk) - 1))
        } - _COMMON_BIGRAMS
        score += sum(1 for token in bigrams if token in plot)
    return score


def load_impression_text(
    uid: str,
    *,
    char_id: str = DEFAULT_CHAR_ID,
    char_name: str = "",
    forced_rounds_left: int | None = None,
    latest_dream_id: str = "",
    user_text: str = "",
    tags: set[str] | None = None,
    recall_enabled: bool = True,
) -> str:
    """
    Return formatted impression block for 6g injection.
    Empty string when no active impressions exist.
    """
    try:
        from core.dream.impression_store import get_active_impressions

        active = get_active_impressions(uid, char_id=char_id)
        if not active:
            return ""

        if forced_rounds_left is None:
            selected = active[:_MAX_INJECT]  # compatibility for direct legacy callers
        elif forced_rounds_left > 0:
            selected = [
                imp for imp in active if imp.get("dream_id") == latest_dream_id
            ][:1]
        elif recall_enabled:
            scored = [
                (_relevance_score(imp, user_text, tags or set()), imp)
                for imp in active
            ]
            selected = [
                imp for score, imp in sorted(
                    scored,
                    key=lambda item: (item[0], item[1].get("ts", 0.0)),
                    reverse=True,
                )
                if score > 0
            ][:_MAX_INJECT]
        else:
            selected = []

        rendered: list[str] = []
        for imp in selected:
            text = _render_entry(imp)
            if text:
                rendered.append(text)

        if not rendered:
            return ""

        body = "\n\n".join(rendered)
        return f'<梦境印象 note="{_tag_note(char_name)}">\n{body}\n</梦境印象>'
    except Exception as e:
        logger.warning(f"[impression_loader] uid={uid}: {e}")
        return ""


def has_active_impressions(uid: str, *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    """D2 隔离用：本轮是否存在活跃梦境印象（fail-closed 返回 False）。"""
    try:
        from core.dream.impression_store import get_active_impressions

        return bool(get_active_impressions(uid, char_id=char_id))
    except Exception:
        return False
