"""
Dream HUD v2.1 — World Layer 修正矩阵补全.

Design invariants:
- Pure computation functions (ema_update, anchor_charge, compute_scene_intrusiveness,
  anchor_repeat_ratio, compute_symbolic_pressure, compute_symbolic_tag_pressure,
  derive_hud_v1) have no I/O side effects beyond module-level caches.
- I/O is confined to load_hud_state / save_hud_state / delete_hud_state.
- Never reads mood_state, user_identity, or any reality store.
- Never modifies dream_state directly; caller owns state writes.
- HUD v0 API surface (field names, 0-100 integer range) is preserved exactly.
- symbolic_pressure and tag_pressure are internal; never output to API or UI.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.dream.hud_label_loader import load_hud_labels
from core.dream.scene_label_loader import resolve_scene_label
from core.dream.symbolic_loader import load_symbolic_profile
from core.safe_write import safe_write_json
from core.sandbox import get_paths, safe_user_id

logger = logging.getLogger(__name__)

# ── EMA alphas ────────────────────────────────────────────────────────────────
# fast: emotion_tension (α=0.6)
# mid:  boundary_intrusion, intimacy_tendency (α=0.5)
# slow: obsession, dream_stability, dream_depth (α=0.35)
_ALPHA: dict[str, float] = {
    "emotion_tension":     0.6,
    "boundary_intrusion":  0.5,
    "intimacy_tendency":   0.5,
    "obsession":           0.35,
    "dream_stability":     0.35,
    "dream_depth":         0.35,
}

# ── HUD state keys persisted across turns ─────────────────────────────────────
_HUD_KEYS = (
    "emotion_tension",
    "boundary_intrusion",
    "intimacy_tendency",
    "obsession",
    "dream_stability",
    "dream_depth",
)

# ── World correction matrix (v2.1) ────────────────────────────────────────────
# Each entry: field → {world_id: (multiplier, offset)}
# Applied before EMA; after anchor_charge injection, before tag_pressure.
# reality_derived / custom / unknown: no entry → identity transform.
# cat intimacy_tendency: handled by cat_attach formula in derive_hud_v1.
# abo physiological_arousal: conditional +10 handled in derive_hud_v1.
_WORLD_MULT: dict[str, dict[str, tuple[float, float]]] = {
    "emotion_tension": {
        "abo":        (1.10, 0.0),
        "vampire":    (1.20, 0.0),
        "cat":        (0.80, 0.0),
        "flower_bud": (0.90, 0.0),
    },
    "boundary_intrusion": {
        "abo":        (1.15, 0.0),
        "vampire":    (1.40, 5.0),
        "cat":        (0.60, 0.0),
        "flower_bud": (0.70, 0.0),
    },
    "intimacy_tendency": {
        "abo":        (1.30, 0.0),
        "vampire":    (1.10, 0.0),
        "flower_bud": (1.00, 0.0),
        # cat: replaced by cat_attach formula (attachment, not desire)
    },
    "obsession": {
        "abo":        (1.10, 0.0),
        "vampire":    (1.45, 5.0),
        "cat":        (0.90, 0.0),
        "flower_bud": (1.15, 0.0),
    },
    "dream_stability": {
        "abo":        (0.95, 0.0),
        "vampire":    (0.85, 0.0),
        "cat":        (1.10, 0.0),
        "flower_bud": (1.20, 0.0),
    },
    "dream_depth": {
        "abo":        (1.05, 0.0),
        "vampire":    (1.10, 0.0),
        "cat":        (1.00, 0.0),
        "flower_bud": (1.35, 0.0),
    },
    "physiological_arousal": {
        "abo":        (1.45, 10.0),  # +10 conditional on heat/tension in derive_hud_v1
        "vampire":    (1.15,  0.0),
        "cat":        (0.40,  0.0),
        "flower_bud": (0.85,  0.0),
    },
}

# ── Scene intrusiveness keyword table ─────────────────────────────────────────
_SCENE_HIGH_INTRUSION = {"压", "困", "缚", "锁", "抓", "逼近", "包围", "侵入", "贴近", "控制"}
_SCENE_MID_INTRUSION  = {"靠近", "注视", "等待", "跟随", "停留"}
_SCENE_SOFT           = {"暖", "柔软", "安静", "花", "阳光", "飘"}

# ── Dominant emotion_label axis map ───────────────────────────────────────────
# tiers are checked in order; last tier catches the remainder (inf threshold)
_DOMINANT_LABEL_MAP: dict[str, list[tuple[float, str]]] = {
    "emotion_tension":    [(40.0, "平静"),   (70.0, "克制"),   (float("inf"), "绷紧")],
    "boundary_intrusion": [(40.0, "警觉"),   (70.0, "不安"),   (float("inf"), "被迫靠近")],
    "intimacy_tendency":  [(40.0, "疏离"),   (70.0, "想靠近"), (float("inf"), "强烈依恋")],
    "obsession":          [(40.0, "挂念"),   (70.0, "放不下"), (float("inf"), "戒不掉的执念")],
}


def _resolve_emotion_label(world_id: str, dominant_axis: str, dominant_val: float) -> str:
    """
    Resolve emotion_label for the given world, axis, and value.

    Priority:
      1. World's hud_labels.yaml → axis + band entry
      2. Built-in _DOMINANT_LABEL_MAP fallback
    """
    band = "low" if dominant_val < 40.0 else ("mid" if dominant_val < 70.0 else "high")
    world_labels = load_hud_labels(world_id)
    axis_labels = world_labels.get(dominant_axis, {})
    if band in axis_labels:
        return axis_labels[band]
    for _thresh, _lbl in _DOMINANT_LABEL_MAP[dominant_axis]:
        if dominant_val < _thresh:
            return _lbl
    return _DOMINANT_LABEL_MAP[dominant_axis][-1][1]

# ── anchor_history rolling window size ───────────────────────────────────────
_ANCHOR_HISTORY_WINDOW = 5

# ── Supported symbolic tags (v2.0) ───────────────────────────────────────────
# Other tags in symbolic_profile.yaml are silently ignored.
_SUPPORTED_TAGS: frozenset[str] = frozenset(
    {"obsession", "intrusion", "intimacy", "depth", "stability"}
)


def _clean_anchors(raw: Any) -> list[str]:
    """Filter to non-empty strings; silently drops non-string and blank values."""
    if not isinstance(raw, list):
        return []
    return [a for a in raw if isinstance(a, str) and a.strip()]


# ── Pure computation ──────────────────────────────────────────────────────────

def ema_update(field: str, raw: float, old: float) -> float:
    """Apply EMA for the given HUD field. Returns the smoothed value (float)."""
    alpha = _ALPHA.get(field, 0.5)
    return alpha * raw + (1.0 - alpha) * old


def anchor_charge(anchors: list[str], world_id: str = "") -> float:
    """
    Compute anchor_charge in [0, 1] from a list of symbolic anchor strings.

    anchor_charge = clamp(Σ weight(anchor) / 5, 0, 1)
    Weights are sourced from the world's symbolic_profile.yaml when available,
    falling back to anchor_weights.json. Unknown anchors use the "default" weight.
    """
    if not anchors:
        return 0.0
    profile = load_symbolic_profile(world_id)
    default_w = profile.get("default", {}).get("weight", 0.5)
    total = sum(profile.get(a, {}).get("weight", default_w) for a in anchors)
    return min(1.0, max(0.0, total / 5.0))


def compute_symbolic_pressure(anchors: list[str], arr: float, world_id: str) -> float:
    """
    Internal metric: symbolic_pressure ∈ [0, 1].

    symbolic_pressure = clamp(Σ(weight for anchors) + arr × 2, 0, 5) / 5

    Not output to API or UI. Drives overall dream_depth (+×20) and
    dream_stability (+×10). Directional tag effects are handled separately
    by compute_symbolic_tag_pressure.
    """
    profile = load_symbolic_profile(world_id)
    default_w = profile.get("default", {}).get("weight", 0.5)
    weight_sum = sum(profile.get(a, {}).get("weight", default_w) for a in anchors)
    return min(1.0, max(0.0, (weight_sum + arr * 2.0) / 5.0))


def compute_symbolic_tag_pressure(
    anchors: list[str], world_id: str
) -> dict[str, float]:
    """
    Compute per-tag directional pressure from current symbolic anchors.

    For each supported tag, accumulates the weight of every anchor that carries
    that tag, then normalises: tag_pressure[tag] = clamp(Σweight / 3, 0, 1).
    Denominator 3 means 3 strong symbols saturate a tag axis.

    Supported tags: obsession, intrusion, intimacy, depth, stability.
    All other tags in symbolic_profile.yaml are silently ignored.
    If symbolic_profile.yaml is absent the fallback (anchor_weights.json) has
    no tags, so every pressure returns 0.0 — no errors raised.

    Not output to API or UI. Applied in derive_hud_v1 after world multipliers,
    before EMA/clamp.
    """
    result: dict[str, float] = dict.fromkeys(_SUPPORTED_TAGS, 0.0)
    if not anchors:
        return result

    profile = load_symbolic_profile(world_id)
    for anchor in anchors:
        entry = profile.get(anchor)
        if entry is None:
            continue
        w = entry.get("weight", 0.5)
        if not isinstance(w, (int, float)):
            w = 0.5
        tags = entry.get("tags", [])
        if not isinstance(tags, list):
            continue
        for tag in tags:
            if tag in _SUPPORTED_TAGS:
                result[tag] += float(w)

    for tag in _SUPPORTED_TAGS:
        result[tag] = min(1.0, max(0.0, result[tag] / 3.0))

    return result


def compute_scene_intrusiveness(scene_state: str | None) -> float:
    """
    Classify scene_state into an intrusion score [0.0, 1.0].

    Returns 0.0 when no scene is present; 0.4 when scene text matches no keyword.
    Pure — no I/O.
    """
    if not scene_state:
        return 0.0
    for kw in _SCENE_HIGH_INTRUSION:
        if kw in scene_state:
            return 0.8
    for kw in _SCENE_MID_INTRUSION:
        if kw in scene_state:
            return 0.6
    for kw in _SCENE_SOFT:
        if kw in scene_state:
            return 0.2
    return 0.4  # scene present but no keyword matched


def anchor_repeat_ratio(anchor_history: list[list[str]]) -> float:
    """
    Compute how often anchors repeat across the stored history window.

    repeat_ratio = clamp(1 - distinct/total, 0, 1)
    Returns 0.0 when history is empty or contains no anchors.
    Pure — no I/O.
    """
    flat = [a for window in anchor_history for a in window]
    if not flat:
        return 0.0
    total = len(flat)
    distinct = len(set(flat))
    return min(1.0, max(0.0, 1.0 - distinct / total))


def derive_hud_v1(
    state: dict[str, Any],
    settings: dict[str, Any],
    body,                    # BodyState instance
    prev_smooth: dict[str, Any],
) -> tuple[dict[str, Any], dict]:
    """
    Compute HUD v2.1 derived fields.

    Returns:
        smooth: dict of smoothed float values (0–100) for all 6 HUD keys
                plus updated anchor_history (list[list[str]])
        hud:    display dict (int 0-100) with emotion_label + scene_label appended

    symbolic_pressure and tag_pressure are computed internally; not in either return value.
    """
    heat = body.heat
    sensitivity = body.sensitivity

    raw_tension = float(state.get("emotional_tension", 0.0))
    # v0 base values (0–100 float)
    emotion_tension_v0  = raw_tension * 100.0
    obsession_v0        = emotion_tension_v0 * 0.7 + min(len(list(state.get("symbolic_anchors") or [])) * 10, 40) * 0.3
    dream_depth_v0      = (heat + sensitivity + 10.0) / 3.0

    boundary_factor_map = {
        "vague": 10, "body_perceptible": 20, "numbers_visible": 35, "threshold_break": 35,
    }
    boundary_factor = boundary_factor_map.get(settings.get("boundary_level", "body_perceptible"), 20)

    world = state.get("frozen_world") or settings.get("world_layer", "reality_derived")

    # scene_intrusiveness: drives boundary_v0 up and stability_v0 down
    si = compute_scene_intrusiveness(state.get("scene_state"))

    # v0 base for boundary_intrusion / intimacy_tendency
    intimacy_v0  = (heat + sensitivity + emotion_tension_v0) / 3.0
    boundary_v0  = heat * 0.4 + emotion_tension_v0 * 0.4 + boundary_factor + si * 15.0
    stability_v0 = 100.0 - emotion_tension_v0 * 0.4 - boundary_v0 * 0.2 - si * 10.0

    # anchor_history → ARR (computed first so symbolic_pressure can use it)
    prev_history: list[list[str]] = prev_smooth.get("anchor_history") or []
    if not isinstance(prev_history, list):
        prev_history = []
    current_anchors = _clean_anchors(state.get("symbolic_anchors"))
    updated_history = (prev_history + [current_anchors])[-_ANCHOR_HISTORY_WINDOW:]
    arr = anchor_repeat_ratio(updated_history)

    # World-aware symbolic weights (v1.3: sourced from symbolic_profile.yaml)
    ac = anchor_charge(current_anchors, world)
    sp = compute_symbolic_pressure(current_anchors, arr, world)

    # anchor_charge injection
    raw_emotion_tension = emotion_tension_v0 + ac * 10.0
    raw_obsession       = obsession_v0       + ac * 25.0
    raw_dream_depth     = dream_depth_v0     + ac * 20.0  # dream_depth_v1 (before world mult)
    raw_boundary        = boundary_v0
    raw_intimacy        = intimacy_v0
    # symbolic_pressure stability gain (max +10, added before world mult)
    raw_stability       = stability_v0       + sp * 10.0

    # world correction matrix (applied before EMA on raw values)
    def _wmult(field: str, value: float) -> float:
        entry = _WORLD_MULT.get(field, {}).get(world)
        if entry is None:
            return value
        mult, offset = entry
        return value * mult + offset

    raw_emotion_tension = _wmult("emotion_tension",    raw_emotion_tension)
    raw_boundary        = _wmult("boundary_intrusion", raw_boundary)
    raw_obsession       = _wmult("obsession",          raw_obsession)
    raw_stability       = _wmult("dream_stability",    raw_stability)
    raw_dream_depth     = _wmult("dream_depth",        raw_dream_depth)

    if world == "cat":
        # cat intimacy = attachment closeness, not desire; use cat-adjusted emotion_tension
        raw_intimacy = (
            raw_emotion_tension * 0.4
            + sensitivity * 0.3
            + ac * 100.0 * 0.3
        )
    else:
        raw_intimacy = _wmult("intimacy_tendency", raw_intimacy)

    # obsession_v1: repeat ratio boost applied after world mult (preserving v1.2 behavior)
    raw_obsession = raw_obsession + arr * 30.0

    # symbolic_pressure dream_depth boost: dream_depth_raw = dream_depth_v1 + sp × 20
    raw_dream_depth = raw_dream_depth + sp * 20.0

    # tag_pressure directional corrections (v2.0) — applied after world mult, before EMA/clamp
    tag_press = compute_symbolic_tag_pressure(current_anchors, world)
    raw_obsession   = raw_obsession   + tag_press["obsession"]  * 20.0
    raw_boundary    = raw_boundary    + tag_press["intrusion"]   * 15.0
    raw_intimacy    = raw_intimacy    + tag_press["intimacy"]    * 15.0
    raw_dream_depth = raw_dream_depth + tag_press["depth"]       * 15.0
    raw_stability   = raw_stability   + tag_press["stability"]   * 10.0

    def _clamp(v: float) -> float:
        return min(100.0, max(0.0, v))

    raws = {
        "emotion_tension":    _clamp(raw_emotion_tension),
        "boundary_intrusion": _clamp(raw_boundary),
        "intimacy_tendency":  _clamp(raw_intimacy),
        "obsession":          _clamp(raw_obsession),
        "dream_stability":    _clamp(raw_stability),
        "dream_depth":        _clamp(raw_dream_depth),
    }

    smooth: dict[str, Any] = {}
    for key in _HUD_KEYS:
        old = prev_smooth.get(key, raws[key])  # first turn: seed from raw
        smooth[key] = _clamp(ema_update(key, raws[key], old))
    smooth["anchor_history"] = updated_history

    # Build integer display dict
    # Dominant axis: whichever of the 4 tracked axes scores highest drives the label.
    _axes = {
        "emotion_tension":    smooth["emotion_tension"],
        "boundary_intrusion": smooth["boundary_intrusion"],
        "intimacy_tendency":  smooth["intimacy_tendency"],
        "obsession":          smooth["obsession"],
    }
    dominant_axis = max(_axes, key=_axes.__getitem__)
    dominant_val  = _axes[dominant_axis]
    emotion_label = _resolve_emotion_label(world, dominant_axis, dominant_val)

    scene_state = state.get("scene_state")
    bi = smooth["boundary_intrusion"]
    dd = smooth["dream_depth"]
    ds = smooth["dream_stability"]
    if scene_state:
        scene_label = scene_state
    else:
        if ds > 70:
            _scene_key = "stable"
        elif dd > 70:
            _scene_key = "sinking"
        elif bi > 60:
            _scene_key = "boundary"
        else:
            _scene_key = "neutral"
        scene_label = resolve_scene_label(world, _scene_key)

    _phys_entry = _WORLD_MULT.get("physiological_arousal", {}).get(world)
    if _phys_entry is not None:
        _pm, _po = _phys_entry
        _phys_raw = heat * _pm + _po
        if world == "abo" and heat > 70.0 and smooth["emotion_tension"] > 60.0:
            _phys_raw += 10.0
    else:
        _phys_raw = heat
    physiological_arousal = round(_clamp(_phys_raw))

    hud = {
        "emotion_label":       emotion_label,
        "scene_label":         scene_label,
        "emotion_tension":     round(smooth["emotion_tension"]),
        "boundary_intrusion":  round(smooth["boundary_intrusion"]),
        "intimacy_tendency":   round(smooth["intimacy_tendency"]),
        "obsession":           round(smooth["obsession"]),
        "dream_stability":     round(smooth["dream_stability"]),
        "dream_depth":         round(smooth["dream_depth"]),
        "physiological_arousal": physiological_arousal,
    }
    return smooth, hud


# ── HUD state I/O ─────────────────────────────────────────────────────────────

def load_hud_state(user_id: str | int) -> dict[str, Any]:
    """Load persisted HUD state. Returns float fields + anchor_history."""
    path = get_paths().dream_hud_state_path(user_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            result: dict[str, Any] = {k: float(v) for k, v in data.items() if k in _HUD_KEYS}
            ah = data.get("anchor_history", [])
            if isinstance(ah, list):
                result["anchor_history"] = [
                    _clean_anchors(window)
                    for window in ah if isinstance(window, list)
                ]
            else:
                result["anchor_history"] = []
            return result
    except Exception as e:
        logger.warning(f"[dream_hud] load_hud_state failed uid={user_id}: {e}")
    return {}


def save_hud_state(user_id: str | int, smooth: dict[str, Any]) -> None:
    """Persist smooth values + anchor_history to dream_hud_state.json (dream-local, not reality)."""
    path = get_paths().dream_hud_state_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {k: smooth[k] for k in _HUD_KEYS if k in smooth}
    if "anchor_history" in smooth:
        payload["anchor_history"] = smooth["anchor_history"]
    safe_write_json(path, payload)


def delete_hud_state(user_id: str | int) -> None:
    """Remove dream_hud_state.json at dream close."""
    path = get_paths().dream_hud_state_path(user_id)
    try:
        if path.exists():
            path.unlink()
    except Exception as e:
        logger.warning(f"[dream_hud] delete_hud_state failed uid={user_id}: {e}")
