"""Diary prose soft-wrap repair, independent of chat bubble splitting policy."""
import re

_STRUCTURE = re.compile(r"^(?: {4}|\t|\s{0,3}(?:#{1,6}\s|>\s?|[-+*]\s|\d+[.)]\s|\|))")


def normalize_diary_prose(text: str) -> str:
    """Join within-paragraph hard wraps; preserve blank paragraphs and Markdown blocks."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    prose = ""
    fence = ""
    def flush():
        nonlocal prose
        if prose:
            out.append(prose)
            prose = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            flush()
            marker = stripped[:3]
            fence = "" if fence == marker else marker
            out.append(line)
        elif fence or _STRUCTURE.match(line):
            flush()
            out.append(line)
        elif not stripped:
            flush()
            out.append("")
        elif prose:
            # CJK continuations join without invented spaces; Latin words keep a boundary.
            space = " " if prose[-1].isascii() and stripped[0].isascii() and stripped[0].isalnum() else ""
            prose += space + stripped
        else:
            prose = stripped
    flush()
    return "\n".join(out).strip()
