"""Internal `{false}` / `{true: intent}` control markers (Brief 120 / 261-D).

parse_tail_brace extracts a relay intent when the model actually asked for a
follow-up tool. strip_control_markers / ControlMarkerStreamFilter only clean
display text — they never execute tools or replay calls.

A protocol marker counts only as a tail marker: after the closed `}` (or, if
unclosed, through end of string) there may be whitespace, but no other text.
Ordinary braces and a literal `{false}` / `{true: ...}` in the middle of a
reply are left intact.
"""

from __future__ import annotations

import re

_TAIL_BRACE_RE = re.compile(r"\{\s*(true|false)\s*[:：]?", re.IGNORECASE)
_TRUE = "true"
_FALSE = "false"


def _is_tail_match(text: str, match: re.Match[str]) -> bool:
    rest = text[match.end():]
    if match.group(1).lower() == "false":
        rest_body = rest.lstrip()
        if rest_body.startswith("}"):
            rest_body = rest_body[1:]
        return not rest_body.strip()
    close = rest.find("}")
    if close == -1:
        return True
    return not rest[close + 1:].strip()


def _last_tail_match(text: str) -> re.Match[str] | None:
    last = None
    for match in _TAIL_BRACE_RE.finditer(text):
        last = match
    if last is None or not _is_tail_match(text, last):
        return None
    return last


def parse_tail_brace(text: str) -> tuple[str, str | None]:
    """Lenient parse of a trailing ``{true: intent}`` / ``{false}`` marker.

    Not a strict JSON check: any ``{true`` (case / fullwidth colon allowed)
    counts as "call", cut at the next ``}``, or to end of string if unclosed.
    Only a tail marker is consumed. Returns (display_text, intent_text);
    intent_text is set only for {true...}.
    """
    if not text:
        return text, None
    match = _last_tail_match(text)
    if not match:
        return text, None
    display_text = text[: match.start()].rstrip()
    if match.group(1).lower() == "false":
        return display_text, None
    rest = text[match.end():]
    close = rest.find("}")
    intent = (rest[:close] if close != -1 else rest).strip()
    return display_text, (intent or None)


def strip_control_markers(text: str) -> str:
    """Display-only cleanup. Never treats a marker as a new tool request."""
    display, _intent = parse_tail_brace(text)
    return display


def _suffix_is_open_marker_prefix(suffix: str) -> bool:
    """True when suffix is `{` plus a still-incomplete true/false keyword."""
    if not suffix.startswith("{"):
        return False
    i = 1
    while i < len(suffix) and suffix[i].isspace():
        i += 1
    if i >= len(suffix):
        return True
    rest = suffix[i:].lower()
    return _TRUE.startswith(rest) or _FALSE.startswith(rest)


def _hold_index(buf: str) -> int | None:
    """Index from which buf must stay buffered (possible tail marker / spaces)."""
    match = _last_tail_match(buf)
    if match is not None:
        idx = match.start()
        while idx > 0 and buf[idx - 1].isspace():
            idx -= 1
        return idx
    idx = buf.rfind("{")
    if idx != -1 and _suffix_is_open_marker_prefix(buf[idx:]):
        while idx > 0 and buf[idx - 1].isspace():
            idx -= 1
        return idx
    i = len(buf)
    while i > 0 and buf[i - 1].isspace():
        i -= 1
    if i < len(buf):
        return i
    return None


class ControlMarkerStreamFilter:
    """Hold back protocol-marker prefixes so they never flash in a stream delta.

    Ordinary braces and mid-text `{false}` examples pass through once more
    non-whitespace text proves they are not the tail. An interrupted stream
    drops an incomplete `{fal` / `{tru` prefix instead of flushing it.
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, piece: str) -> str:
        if not piece:
            return ""
        self._buf += piece
        hold = _hold_index(self._buf)
        if hold is None:
            out, self._buf = self._buf, ""
            return out
        out, self._buf = self._buf[:hold], self._buf[hold:]
        return out

    def finish(self) -> str:
        buf = self._buf
        self._buf = ""
        if not buf:
            return ""
        display, _intent = parse_tail_brace(buf)
        idx = display.rfind("{")
        if idx != -1 and _suffix_is_open_marker_prefix(display[idx:]):
            return display[:idx].rstrip()
        return display
