"""
core/dream/scene_label_loader.py — Per-world scene label loader (v2.3).

Each world package may optionally provide scene_labels.yaml mapping
scene_key (stable/sinking/boundary/neutral) → display text.
Returns empty dict when the file is absent or malformed; never raises.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_WORLDS_BASE = Path("characters/dream_worlds")

# Process-lifetime cache: world_id → {scene_key: str}
_scene_cache: dict[str, dict[str, str]] = {}

_FALLBACK: dict[str, str] = {
    "stable":   "稳定",
    "sinking":  "下沉",
    "boundary": "边界波动",
    "neutral":  "梦境中",
}


def load_scene_labels(world_id: str) -> dict[str, str]:
    """
    Load scene_labels.yaml for a world.

    Returns {scene_key: str} where scene_key is one of stable/sinking/boundary/neutral.
    Returns empty dict when the file is absent, malformed, or world_id is empty.
    """
    if not world_id:
        return {}
    if world_id in _scene_cache:
        return _scene_cache[world_id]
    path = _WORLDS_BASE / world_id / "scene_labels.yaml"
    result = _try_load_yaml(path, world_id)
    _scene_cache[world_id] = result
    return result


def resolve_scene_label(
    world_id: str,
    scene_key: str,
) -> str:
    """
    Resolve a scene_key to display text using world's scene_labels.yaml.
    Falls back to built-in defaults if the world file is missing or the key is absent.
    """
    world_labels = load_scene_labels(world_id)
    if scene_key in world_labels:
        return world_labels[scene_key]
    return _FALLBACK.get(scene_key, "梦境中")


def _try_load_yaml(path: Path, world_id: str) -> dict[str, str]:
    try:
        import yaml  # type: ignore

        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text)
        if not isinstance(raw, dict):
            return {}
        data = raw.get("labels", raw)
        if not isinstance(data, dict):
            return {}
        result: dict[str, str] = {}
        for key in ("stable", "sinking", "boundary", "neutral"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                result[key] = v.strip()
        if result:
            logger.debug(
                f"[scene_label_loader] loaded scene_labels for {world_id!r} ({len(result)} keys)"
            )
        return result
    except ModuleNotFoundError:
        logger.warning(
            "[scene_label_loader] pyyaml not installed; scene_labels.yaml cannot be loaded"
        )
        return {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.debug(f"[scene_label_loader] failed to load {path}: {e}")
        return {}
