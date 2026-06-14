"""
core/narrative_parser — Emerald Narrative Message Protocol parser.

Supports two output formats:
  XML      : <say>…</say>  <do>…</do>  <env>…</env>  <feel>…</feel>
  Markdown : plain text (say), *action* (do), _feel_ (feel), > env (env)

Format is auto-detected per reply: the XML path is taken when any known
open-tag is present; otherwise the Markdown path applies.  Both paths
return the same NarrativeParseResult shape and never raise (fallback:
single narration segment with the full reply).

Usage::

    from core.narrative_parser import parse_narrative_segments
    result = parse_narrative_segments(reply)
    # result["content"]  — marker-stripped plain text
    # result["segments"] — list of {"type": ..., "text": ...}

This module intentionally has zero imports from dreams/, impression_loader,
afterglow, dream_summary, DataPaths, or any reality/dream data path.
"""

import re
from typing import TypedDict

KNOWN_TAGS: frozenset[str] = frozenset({"say", "do", "env", "feel"})
# Inline style tags preserved in segment.text (desktop rich-render path only).
# All other paths (QQ / mobile / memory) strip them via strip_render_tags / _ALL_TAG_RE.
INLINE_STYLE_TAGS: frozenset[str] = frozenset({"hl", "big", "sm"})

# ── XML path ─────────────────────────────────────────────────────────────────
# Matches any XML-like open or close token: <word> or </word>
_TAG_TOKEN_RE = re.compile(r"<(/?)([\w]+)>")
# Strips all XML-like tag markers for building the clean content string
_ALL_TAG_RE = re.compile(r"</?[a-zA-Z]\w*>")
# Detects presence of at least one known XML open-tag → selects XML path
_HAS_XML_RE = re.compile(r"<(?:say|do|env|feel)>")

# ── Markdown path ─────────────────────────────────────────────────────────────
# *action*   single asterisk, no internal asterisks, whole line
_MD_DO_RE = re.compile(r"^\*([^*]+)\*$")
# _feel_     single underscore, no internal underscores, whole line
_MD_FEEL_RE = re.compile(r"^_([^_]+)_$")
# > env      blockquote prefix
_MD_ENV_RE = re.compile(r"^> (.+)$")


class NarrativeSegment(TypedDict):
    type: str   # "say" | "do" | "env" | "feel" | "narration"
    text: str


class NarrativeParseResult(TypedDict):
    content: str   # marker-stripped plain text
    segments: list  # list[NarrativeSegment]


def parse_narrative_segments(reply: str) -> NarrativeParseResult:
    """
    Parse *reply* into narrative segments.  Never raises; any internal error
    returns a safe fallback where the full reply is a single narration segment
    and content equals the original reply.
    """
    try:
        return _parse(reply)
    except Exception:
        return {
            "content": reply,
            "segments": [{"type": "narration", "text": reply}],
        }


# ─────────────────────────────────────────────────────────────────────────────

def _parse(reply: str) -> NarrativeParseResult:
    if _HAS_XML_RE.search(reply):
        return _parse_xml(reply)
    return _parse_markdown(reply)


def _parse_xml(reply: str) -> NarrativeParseResult:
    """Parse legacy XML-tag format.  Kept intact for backward compatibility."""
    # Phase 1: tokenise
    # Each token is ("text"|"open_known"|"close_known", value)
    # Unknown tags are kept as literal "text" tokens so content is never lost.
    tokens: list[tuple[str, str]] = []
    pos = 0
    for m in _TAG_TOKEN_RE.finditer(reply):
        start, end = m.span()
        if start > pos:
            tokens.append(("text", reply[pos:start]))
        is_close = m.group(1) == "/"
        tag = m.group(2).lower()
        if tag in KNOWN_TAGS:
            tokens.append(("close_known" if is_close else "open_known", tag))
        else:
            # Inline style tags are preserved in segment.text for desktop rendering.
            # Other unknown tags are silently dropped; text content between them
            # is still captured as regular text tokens, so no content is lost.
            if tag in INLINE_STYLE_TAGS:
                tokens.append(("text", m.group(0)))
        pos = end
    if pos < len(reply):
        tokens.append(("text", reply[pos:]))

    # Phase 2: build segments with a single-level tag stack
    segments: list[NarrativeSegment] = []
    current_tag: str | None = None
    buf: list[str] = []

    def _flush(seg_type: str) -> None:
        text = "".join(buf).strip()
        buf.clear()
        if text:
            segments.append({"type": seg_type, "text": text})

    for kind, value in tokens:
        if kind == "text":
            buf.append(value)
        elif kind == "open_known":
            if current_tag is None:
                _flush("narration")
                current_tag = value
            else:
                # Nested open tag inside an already-open known tag:
                # fold it in as literal text rather than trying to nest.
                buf.append(f"<{value}>")
        elif kind == "close_known":
            if current_tag == value:
                _flush(current_tag)
                current_tag = None
            else:
                # Orphaned or mismatched close tag: literal text, no content lost
                buf.append(f"</{value}>")

    # Flush remaining buffer.
    # If current_tag is set the tag was never closed; auto-close it here
    # (simpler and safer than downgrading to narration since the LLM intent
    # is clear even without the closing marker).
    _flush(current_tag if current_tag is not None else "narration")

    # Phase 3: build clean content string
    content = _ALL_TAG_RE.sub("", reply).strip()
    content = re.sub(r"\n{3,}", "\n\n", content)
    content = re.sub(r" {2,}", " ", content)

    return {"content": content, "segments": segments}


def _parse_markdown(reply: str) -> NarrativeParseResult:
    """
    Parse Markdown-format reply (triggered when no known XML tag detected).

    Line-level classification (single-line markers only):
      *text*  → do    (no internal asterisks; whole line)
      _text_  → feel  (no internal underscores; whole line)
      > text  → env
      other   → say   (plain speech; accumulated across consecutive lines)

    Empty lines flush the current say buffer without creating a segment.
    """
    segments: list[NarrativeSegment] = []
    say_lines: list[str] = []

    def _flush_say() -> None:
        text = "\n".join(say_lines).strip()
        say_lines.clear()
        if text:
            segments.append({"type": "say", "text": text})

    for line in reply.split("\n"):
        stripped = line.strip()

        if not stripped:
            _flush_say()
            continue

        m_do = _MD_DO_RE.match(stripped)
        m_feel = _MD_FEEL_RE.match(stripped)
        m_env = _MD_ENV_RE.match(stripped)

        if m_do:
            _flush_say()
            text = m_do.group(1).strip()
            if text:
                segments.append({"type": "do", "text": text})
        elif m_feel:
            _flush_say()
            text = m_feel.group(1).strip()
            if text:
                segments.append({"type": "feel", "text": text})
        elif m_env:
            _flush_say()
            text = m_env.group(1).strip()
            if text:
                segments.append({"type": "env", "text": text})
        else:
            say_lines.append(line)

    _flush_say()

    # Build clean content: strip ALL xml-like tags (including inline style tags)
    # so memory / QQ / mobile / stats paths receive plain text.
    _raw = "\n".join(s["text"] for s in segments)
    content = _ALL_TAG_RE.sub("", _raw).strip()
    content = re.sub(r"\n{3,}", "\n\n", content)
    content = re.sub(r" {2,}", " ", content)

    return {"content": content, "segments": segments}
