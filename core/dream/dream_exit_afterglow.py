"""
Dream exit afterglow wiring — Phase 6.

Reality-side only.  Called from _generate_summary_bg() AFTER generate_summary()
completes and writes the summary record to disk.

Contract:
  - Must not be called from within a live Dream turn.
  - Must not directly write hidden_state — all writes flow through
    integrate_afterglow_and_save() with stamp_dream_afterglow().
  - Failure is warning-only: wire_afterglow_from_summary() never raises,
    never blocks Dream exit.
  - Afterglow only affects: sensitivity.current, embodied_ease.
  - Prohibited mutations: sensitivity.baseline, touch_need.*, body_memory.

Tone derivation from summary record:
  hard_exit OR hurt_reluctance narrative → "stress"   (negative nudge)
  gentle_residue + summary_weight ≥ 0.7  → "comfort"  (positive + ease nudge)
  gentle_residue + summary_weight < 0.7  → "calm"     (positive nudge only)
  fallback / empty summary               → "neutral"   (zero numeric effect)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def wire_afterglow_from_summary(uid: str, dream_id: str, exit_type: str) -> None:
    """Save afterglow residue and integrate into hidden state at Dream exit.

    Called at Dream exit (Reality-side), AFTER generate_summary() has written
    the summary file to disk.  Always completes without raising — any internal
    failure is logged at WARNING level and silently swallowed.

    Steps:
      1. Load summary from dreams/summaries/dream_{dream_id}.summary.json.
      2. Derive tone from afterglow field + exit_type.
      3. Build AfterglowResidueInput (age_hours=0.0 — freshly generated).
      4. save_afterglow_residue() — atomic write, overwrites previous residue.
      5. integrate_afterglow_and_save() with stamp_dream_afterglow().
    """
    try:
        _do_wire(uid, dream_id, exit_type)
    except Exception as exc:
        logger.warning(
            "[dream_exit_afterglow] wire_afterglow_from_summary failed "
            "uid=%s dream_id=%s: %s",
            uid, dream_id, exc,
        )


def _do_wire(uid: str, dream_id: str, exit_type: str) -> None:
    from core.memory.user_hidden_state import AfterglowResidueInput
    from core.memory.user_hidden_state_store import save_afterglow_residue
    from core.memory.user_hidden_state_integrator import integrate_afterglow_and_save
    from core.write_envelope import stamp_dream_afterglow
    from core.sandbox import get_paths

    # 1. Load summary record written by generate_summary()
    summaries_dir: Path = get_paths().dreams_summaries_dir()
    summary_path = summaries_dir / f"dream_{dream_id}.summary.json"
    summary_data = _load_summary(summary_path)

    # 2. Derive tone and emotional_tags from summary (conservative fallback: neutral)
    emotional_tags, tone = _extract_tone_and_tags(summary_data, exit_type)

    # 3. Build residue — age_hours=0.0 since it is freshly generated
    residue = AfterglowResidueInput(
        emotional_tags=emotional_tags,
        tone=tone,
        age_hours=0.0,
    )

    now_str = datetime.now(timezone.utc).isoformat()

    # 4. Persist residue (atomic write, overwrites previous; TTL checked at read time)
    try:
        ok = save_afterglow_residue(uid, residue, created_at=now_str)
        if not ok:
            logger.warning(
                "[dream_exit_afterglow] save_afterglow_residue returned False uid=%s", uid
            )
    except Exception as exc:
        logger.warning(
            "[dream_exit_afterglow] save_afterglow_residue raised uid=%s: %s", uid, exc
        )

    # 5. Integrate into hidden state via Reality-side integrator only
    try:
        envelope = stamp_dream_afterglow()
        _, result = integrate_afterglow_and_save(
            uid,
            residue,
            write_envelope=envelope,
            now=now_str,
        )
        if result.accepted:
            logger.info(
                "[dream_exit_afterglow] integrated uid=%s tone=%r fields=%s",
                uid, tone, [f.field for f in result.touched_fields],
            )
        elif result.rejected:
            logger.debug(
                "[dream_exit_afterglow] rejected uid=%s reasons=%s",
                uid, result.rejected_reasons,
            )
    except Exception as exc:
        logger.warning(
            "[dream_exit_afterglow] integrate_afterglow_and_save raised uid=%s: %s", uid, exc
        )


def _load_summary(path: Path) -> dict[str, Any]:
    """Load summary record from disk.  Returns {} on any error."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _extract_tone_and_tags(
    summary: dict[str, Any],
    exit_type: str,
) -> tuple[list[str], str]:
    """Derive (emotional_tags, tone) from summary record and exit_type.

    Tone vocabulary must match integrator frozensets:
      Positive (sensitivity up):  "comfort", "calm", "warm", "safe", "trusted"
      Ease-qualifying (also ease): "comfort", "safe", "trusted"
      Negative (sensitivity down): "fear", "stress", "threat"
      Neutral (zero effect):       "neutral"

    Rules (in priority order):
      hard_exit OR hurt_reluctance  →  "stress"
      gentle_residue + weight ≥ 0.7 →  "comfort"  (nudges ease too)
      gentle_residue                →  "calm"
      fallback                      →  "neutral"
    """
    if not summary:
        return [], "neutral"

    emotional_tags: list[str] = list(summary.get("emotional_tags") or [])
    afterglow_type: str = summary.get("afterglow", "")
    summary_weight: float = float(summary.get("summary_weight") or 0.0)

    if exit_type == "hard_exit" or afterglow_type == "hurt_reluctance":
        return emotional_tags, "stress"

    if afterglow_type == "gentle_residue":
        if summary_weight >= 0.7:
            return emotional_tags, "comfort"
        return emotional_tags, "calm"

    return emotional_tags, "neutral"
