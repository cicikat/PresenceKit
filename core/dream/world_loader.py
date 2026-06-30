"""
core/dream/world_loader.py — Dream world package loader.

Each world package lives in characters/dream_worlds/{world_id}/ with:
  ruleset.md     — D2 world ruleset text (shown above D3, below D1)
  mes_example.md — D3 dream mes_example (独立于现实角色卡 mes_example)
  vocab.json     — proprietary terms for depth-defense vocab strip

Used exclusively by dream_prompt.py + distill_impression.py + dream_afterglow.py.
NEVER imported by core/pipeline.py or core/memory/fixation_pipeline.py (I2).

Lorebook:
  match_dream_lore() is a pure function — takes entries list + message + history,
  returns matched content strings. Dream lorebook data stored separately from
  the reality lorebook (characters/reality/lorebook.yaml).
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_WORLDS_BASE = Path("characters/dream_worlds")
_FALLBACK_WORLD = "reality_derived"
_DEFAULT_DIR = "_default"


def discover_worlds() -> list[str]:
    """Return sorted list of available world_ids by scanning characters/dream_worlds/."""
    try:
        return sorted(
            d.name
            for d in _WORLDS_BASE.iterdir()
            if d.is_dir() and not d.name.startswith("_")
        )
    except Exception:
        return [_FALLBACK_WORLD]


@dataclass
class WorldPackage:
    world_id: str
    ruleset: str
    mes_example: str
    vocab_terms: list[str] = field(default_factory=list)


def load_world(world_id: str) -> WorldPackage:
    """
    Load a world package from characters/dream_worlds/{world_id}/.
    Falls back to reality_derived if world directory does not exist.
    Falls back to _default/ for missing or empty ruleset, mes_example, vocab.
    """
    base = _WORLDS_BASE / world_id
    if not base.is_dir():
        logger.warning(
            f"[world_loader] unknown world_id={world_id!r}, "
            f"falling back to {_FALLBACK_WORLD}"
        )
        world_id = _FALLBACK_WORLD
        base = _WORLDS_BASE / world_id
    default_base = _WORLDS_BASE / _DEFAULT_DIR

    ruleset = _read_text_or_none(base / "ruleset.md")
    if ruleset is None:
        logger.info(
            f"[world_loader] fallback world_id={world_id!r} field=ruleset source=_default"
        )
        ruleset = _read_text(default_base / "ruleset.md")

    mes_example = _read_text_or_none(base / "mes_example.md")
    if mes_example is None:
        logger.info(
            f"[world_loader] fallback world_id={world_id!r} field=mes_example source=_default"
        )
        mes_example = _read_text(default_base / "mes_example.md")

    vocab_terms = _read_vocab_or_none(base / "vocab.json")
    if vocab_terms is None:
        logger.info(
            f"[world_loader] fallback world_id={world_id!r} field=vocab source=_default"
        )
        vocab_terms = _read_vocab(default_base / "vocab.json")

    return WorldPackage(
        world_id=world_id,
        ruleset=ruleset,
        mes_example=mes_example,
        vocab_terms=vocab_terms,
    )


def strip_vocab(text: str, world_id: str) -> str:
    """
    Remove world-specific proprietary terms from text.

    Second-layer depth defense —承重墙 (store isolation) is still the primary wall.
    Strips vocabulary terms loaded from the world package's vocab.json.
    """
    if not text:
        return text
    pkg = load_world(world_id)
    result = text
    for term in pkg.vocab_terms:
        if term:
            result = result.replace(term, "")
    return result


def match_dream_lore(
    entries: list[dict[str, Any]],
    user_message: str,
    recent_messages: list[dict[str, Any]] | None = None,
) -> list[str]:
    """
    Pure-function dream lorebook matcher.

    Accepts dream-specific entries (not the reality lorebook) and returns
    matched content strings. Same keyword-matching logic as LoreEngine.match()
    but as a stateless function — no reality state contamination.

    Entry format: {"keywords": [...], "content": "...", "regex": bool (optional)}
    """
    if not entries:
        return []

    scan_parts = [user_message]
    if recent_messages:
        for msg in recent_messages[-5:]:
            c = msg.get("content", "")
            if c:
                scan_parts.append(c)
    full_text = " ".join(scan_parts)
    full_text_lower = full_text.lower()

    matched: list[dict[str, Any]] = []
    seen: set[str] = set()

    for entry in entries:
        content = entry.get("content", "")
        if not content or content in seen:
            continue

        is_regex = entry.get("regex", False)
        hit = False

        for kw in entry.get("keywords", []):
            if is_regex:
                try:
                    if re.search(kw, full_text, re.IGNORECASE):
                        hit = True
                        break
                except re.error:
                    logger.warning(f"[world_loader] invalid regex: {kw!r}")
            else:
                if kw.lower() in full_text_lower:
                    hit = True
                    break

        if hit:
            seen.add(content)
            matched.append({"insertion_order": entry.get("insertion_order", 0), "content": content})

    matched.sort(key=lambda e: e["insertion_order"])
    return [e["content"] for e in matched]


def load_dream_lore_entries(world_id: str) -> list[dict[str, Any]]:
    """
    Load dream-specific lorebook entries for a world.
    Reads characters/dream_worlds/{world_id}/lorebook.yaml.
    Returns empty list when file missing or empty.
    """
    if not (_WORLDS_BASE / world_id).is_dir():
        world_id = _FALLBACK_WORLD
    path = _WORLDS_BASE / world_id / "lorebook.yaml"
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict)]
        return []
    except ModuleNotFoundError:
        # YAML not available — fall back to empty
        return []
    except Exception as e:
        logger.debug(f"[world_loader] lorebook load skipped {path}: {e}")
        return []


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.warning(f"[world_loader] cannot read {path}: {e}")
        return ""


def _read_text_or_none(path: Path) -> str | None:
    """Returns stripped text, or None if file missing/unreadable/empty after strip."""
    try:
        content = path.read_text(encoding="utf-8").strip()
        return content if content else None
    except Exception:
        return None


def _read_vocab(path: Path) -> list[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(t) for t in data if t]
        return []
    except Exception:
        return []


def _read_vocab_or_none(path: Path) -> list[str] | None:
    """Returns vocab list, or None if file missing or invalid (non-list JSON)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(t) for t in data if t]
        return None
    except Exception:
        return None
