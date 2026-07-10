"""
Activity companion output cleaning — visible-side scrub for chess/gomoku/reading
companion chat (Brief 43 §A).

Lighter than core.reality_output_scrubber: does not drop whole action-word lines
(companion replies are short conversational lines and would be over-killed by that
heuristic), only strips bracket action descriptions and whole-line markdown
narration markers. Reuses reality_output_scrubber's whole-line regex constants for
*-do / _feel_ / > env markers (see that module for the canonical patterns) instead
of duplicating them.

Must run before the reply is written to transcript — the transcript is next-turn
context, so unscrubbed action text would self-reinforce.
"""
from __future__ import annotations

import re

from core.reality_output_scrubber import _MD_DO_RE, _MD_ENV_RE, _MD_FEEL_RE

_FALLBACK_TRUNCATE = 80

# Inline or whole-line bracket action descriptions — unlike reality_output_scrubber
# (whole-line only), activity companion replies mix action asides into short
# sentences, so bracket removal here is per-occurrence, not whole-line-only.
_CJK_BRACKET_RE = re.compile(r"（[^（）\n]*）")
_EN_BRACKET_RE = re.compile(r"\([^()\n]*\)")


def strip_action_descriptions(text: str) -> str:
    """Remove action/stage-direction markup from an activity companion reply.

    Removes inline and whole-line （…）/(…) bracket asides, and whole-line
    *do*, _feel_, > env markdown narration lines. Cross-line brackets are not
    handled (matching is per line only).

    Returns the cleaned text, stripped. If cleaning empties the text entirely,
    returns the first 80 chars of the original (pre-clean) text instead — a
    reply must never be swallowed down to an empty string.
    """
    if not text:
        return text

    kept: list[str] = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            kept.append(line)
            continue
        if _MD_DO_RE.match(s) or _MD_FEEL_RE.match(s) or _MD_ENV_RE.match(s):
            continue
        line = _CJK_BRACKET_RE.sub("", line)
        line = _EN_BRACKET_RE.sub("", line)
        kept.append(line)

    cleaned = "\n".join(kept).strip()
    if cleaned:
        return cleaned
    return text.strip()[:_FALLBACK_TRUNCATE]
