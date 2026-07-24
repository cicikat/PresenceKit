"""
core/dream/symbolic_loader.py — World symbolic profile loader (HUD v1.3).

Each world package may provide symbolic_profile.yaml with per-symbol weights.
Falls back to the global anchor_weights.json when symbolic_profile.yaml is absent.

Tags are parsed and stored but not used in computation (reserved for v2).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.sandbox import get_paths

logger = logging.getLogger(__name__)

def _worlds_base() -> Path:
    """Resolve fresh on every call instead of freezing at import time (see world_loader.py)."""
    return get_paths().dream_worlds_dir()


def _anchor_weights_path() -> Path:
    return _worlds_base() / "anchor_weights.json"


# Per-world profile cache: world_id → {symbol: {"weight": float, "tags": list[str]}}
_profile_cache: dict[str, dict[str, dict]] = {}
# Global fallback cache (anchor_weights.json → profile format)
_fallback_cache: dict[str, dict] | None = None


def load_symbolic_profile(world_id: str) -> dict[str, dict]:
    """
    Load symbolic profile for a world.

    Returns {symbol: {"weight": float, "tags": list[str]}}.
    Source priority:
      1. characters/dream_worlds/{world_id}/symbolic_profile.yaml
      2. characters/dream_worlds/anchor_weights.json  (global fallback)

    Cache is module-level (process lifetime, cleared on process restart).
    """
    if not world_id:
        return _load_fallback()

    if world_id in _profile_cache:
        return _profile_cache[world_id]

    path = _worlds_base() / world_id / "symbolic_profile.yaml"
    profile = _try_load_yaml(path, world_id)
    if profile is not None:
        logger.debug(
            f"[symbolic_loader] loaded symbolic_profile for {world_id!r} ({len(profile)} symbols)"
        )
        _profile_cache[world_id] = profile
        return profile

    logger.debug(
        f"[symbolic_loader] no symbolic_profile.yaml for {world_id!r}, "
        "falling back to anchor_weights.json"
    )
    fallback = _load_fallback()
    _profile_cache[world_id] = fallback
    return fallback


def _try_load_yaml(path: Path, world_id: str) -> dict[str, dict] | None:
    try:
        import yaml  # type: ignore

        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text)
        if not isinstance(raw, dict):
            return None
        # Support both nested {symbolic_profile: {...}} and flat {symbol: {...}}
        data = raw.get("symbolic_profile", raw)
        if not isinstance(data, dict):
            return None
        result: dict[str, dict] = {}
        for symbol, entry in data.items():
            sym = str(symbol)
            if isinstance(entry, (int, float)):
                result[sym] = {"weight": float(entry), "tags": []}
            elif isinstance(entry, dict):
                result[sym] = {
                    "weight": float(entry.get("weight", 0.5)),
                    "tags": [str(t) for t in entry.get("tags", [])],
                }
        return result if result else None
    except ModuleNotFoundError:
        logger.warning(
            "[symbolic_loader] pyyaml not installed; symbolic_profile.yaml cannot be loaded"
        )
        return None
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.debug(f"[symbolic_loader] failed to load {path}: {e}")
        return None


def _load_fallback() -> dict[str, dict]:
    global _fallback_cache
    if _fallback_cache is not None:
        return _fallback_cache
    try:
        data = json.loads(_anchor_weights_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _fallback_cache = {
                str(k): {"weight": float(v), "tags": []}
                for k, v in data.items()
            }
            return _fallback_cache
    except Exception as e:
        logger.warning(f"[symbolic_loader] anchor_weights.json fallback failed: {e}")
    _fallback_cache = {"default": {"weight": 0.5, "tags": []}}
    return _fallback_cache
