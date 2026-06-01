"""
core/dream/hud_label_loader.py — Per-world HUD emotion label loader (v2.2).

Each world package may optionally provide hud_labels.yaml mapping
dominant_axis + band (low/mid/high) → emotion_label text.
Returns empty dict when the file is absent or malformed; never raises.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_WORLDS_BASE = Path("characters/dream_worlds")

# Process-lifetime cache: world_id → {axis: {"low": str, "mid": str, "high": str}}
_label_cache: dict[str, dict[str, dict[str, str]]] = {}


def load_hud_labels(world_id: str) -> dict[str, dict[str, str]]:
    """
    Load hud_labels.yaml for a world.

    Returns {axis: {"low": str, "mid": str, "high": str}}.
    Returns empty dict when the file is absent, malformed, or world_id is empty.
    """
    if not world_id:
        return {}
    if world_id in _label_cache:
        return _label_cache[world_id]
    path = _WORLDS_BASE / world_id / "hud_labels.yaml"
    result = _try_load_yaml(path, world_id)
    _label_cache[world_id] = result
    return result


def _try_load_yaml(path: Path, world_id: str) -> dict[str, dict[str, str]]:
    try:
        import yaml  # type: ignore

        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text)
        if not isinstance(raw, dict):
            return {}
        data = raw.get("labels", raw)
        if not isinstance(data, dict):
            return {}
        result: dict[str, dict[str, str]] = {}
        for axis, bands in data.items():
            if not isinstance(bands, dict):
                continue
            parsed: dict[str, str] = {}
            for band in ("low", "mid", "high"):
                v = bands.get(band)
                if isinstance(v, str) and v.strip():
                    parsed[band] = v.strip()
            if parsed:
                result[str(axis)] = parsed
        if result:
            logger.debug(
                f"[hud_label_loader] loaded hud_labels for {world_id!r} ({len(result)} axes)"
            )
        return result
    except ModuleNotFoundError:
        logger.warning(
            "[hud_label_loader] pyyaml not installed; hud_labels.yaml cannot be loaded"
        )
        return {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.debug(f"[hud_label_loader] failed to load {path}: {e}")
        return {}
