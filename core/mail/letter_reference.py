"""
Helpers for injecting style samples and knowledge snippets into letter context.

Both helpers are fail-soft: missing directory / no files → return empty string.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path

logger = logging.getLogger(__name__)

_SAMPLE_EXTENSION = ".txt"
_KNOWLEDGE_EXTENSION = ".md"


def sample_style(
    char_id: str,
    *,
    n: int = 2,
    exclude_names: list[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Return up to *n* random sample-letter texts and their filenames.

    Files recently used (exclude_names) are avoided when enough alternatives exist.
    Returns (texts, names); both lists may be empty if the dir is absent or empty.
    """
    from core.sandbox import get_paths

    samples_dir: Path = get_paths().letter_samples_dir(char_id=char_id)
    if not samples_dir.is_dir():
        return [], []

    all_files = sorted(samples_dir.glob(f"*{_SAMPLE_EXTENSION}"))
    if not all_files:
        return [], []

    exclude = set(exclude_names or [])
    candidates = [f for f in all_files if f.name not in exclude]
    if len(candidates) < n and len(all_files) >= n:
        candidates = all_files  # fall back to all when exclusion is too restrictive

    chosen = random.sample(candidates, min(n, len(candidates)))
    texts, names = [], []
    for f in chosen:
        try:
            texts.append(f.read_text(encoding="utf-8").strip())
            names.append(f.name)
        except Exception:
            pass
    return texts, names


def sample_reference(char_id: str) -> str:
    """Return a random knowledge snippet (one paragraph), or empty string."""
    from core.sandbox import get_paths

    knowledge_dir: Path = get_paths().letter_knowledge_dir(char_id=char_id)
    if not knowledge_dir.is_dir():
        return ""

    all_files = sorted(knowledge_dir.glob(f"*{_KNOWLEDGE_EXTENSION}"))
    if not all_files:
        return ""

    chosen = random.choice(all_files)
    try:
        content = chosen.read_text(encoding="utf-8").strip()
    except Exception:
        return ""

    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    if not paragraphs:
        return ""
    snippet = random.choice(paragraphs)
    return snippet[:200]


def load_sent_letters(uid: str, char_id: str, *, limit: int = 3) -> list[str]:
    """Return the texts of the most recent *limit* sent letters (oldest-first order)."""
    from core.sandbox import get_paths

    path = get_paths().sent_letters(uid, char_id=char_id)
    try:
        records: list[dict] = json.loads(path.read_text(encoding="utf-8"))
        return [str(r.get("text") or "") for r in records[-limit:] if r.get("text")]
    except Exception:
        return []


def append_sent_letter(uid: str, char_id: str, text: str) -> None:
    """Append one sent-letter record (text + timestamp) to the archive."""
    import time

    from core.sandbox import get_paths
    from core.safe_write import safe_write_json

    path = get_paths().sent_letters(uid, char_id=char_id)
    try:
        records: list[dict] = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        records = []

    records.append({"text": text, "ts": time.time()})
    records = records[-50:]  # keep at most 50
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        safe_write_json(path, records)
    except Exception as exc:
        logger.warning("[letter_reference] failed to archive sent letter: %s", exc)
