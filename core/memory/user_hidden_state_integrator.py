"""
core/memory/user_hidden_state_integrator.py
==========================================
Phase 1 MVP + Phase 2 save wiring

Entry points (pure, in-memory):
  integrate_event(event_type, hidden_state, write_envelope, now)
  integrate_impression(impression, hidden_state, write_envelope, now)

Entry points (Phase 2 — load → integrate → save):
  integrate_event_and_save(uid, event_type, write_envelope, now)
  integrate_impression_and_save(uid, impression, write_envelope, now)

Writable fields (中期层 only):
  - touch_need.deficit
  - sensitivity.current

Protected fields (长期层 — zero writes, always):
  - sensitivity.baseline
  - touch_need.baseline
  - embodied_ease
  - body_memory

Fail-closed contract:
  All mutations require write_envelope.can_write_memory == True.
  If the envelope gate is closed, the state is returned unchanged and
  IntegratorResult.rejected_reasons is populated.
  The _and_save variants only persist when the envelope is open AND the
  result is accepted; rejected calls never touch disk.

Not implemented (Phase 3+):
  - consolidate / baseline promotion
  - body_memory reinforcement
  - embodied_ease updates
  - afterglow processing
  - dream_body_event processing
  - sensor integration
  - build_snapshot
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

from core.memory.user_hidden_state import (
    AFTERGLOW_TTL_HOURS,
    DREAM_GATE_MAX,
    DREAM_GATE_MIN,
    MAX_NUDGE_PER_EVENT,
    AfterglowResidueInput,
    ImpressionInput,
    UpdateSource,
    UserHiddenState,
    _clamp,
    discharge_touch_deficit,
    nudge_current_sensitivity,
    nudge_embodied_ease,
    reinforce_body_memory,
)
from core.memory.user_hidden_state_store import load_hidden_state, save_hidden_state
from core.write_envelope import SourceType, WriteEnvelope

logger = logging.getLogger(__name__)

# ── Module-level event deltas ─────────────────────────────────────────────────

DEFICIT_DISCHARGE_AMOUNT: float = 8.0
"""Points removed from touch_need.deficit per comfort / companionship event."""

DEFICIT_ACCRUE_AMOUNT: float = 4.0
"""Points added to touch_need.deficit per no-interaction event."""

IMPRESSION_MAX_NUDGE: float = 3.0
"""Max sensitivity.current increase per impression (before MAX_NUDGE_PER_EVENT cap)."""

# Long-term field names guarded against accidental writes from integrate_event /
# integrate_impression.  The only permitted write path for body_memory in Phase 3
# is integrate_body_cue* (Reality-side, explicitly opened below).
_LONG_TERM_FIELDS: frozenset[str] = frozenset({
    "sensitivity.baseline",
    "touch_need.baseline",
    "embodied_ease",
    "body_memory",
})


def _assert_not_long_term(field_name: str) -> None:
    """Raise RuntimeError if field_name is a long-term layer field.

    Call this before constructing FieldDelta in integrate_event /
    integrate_impression to prevent accidental long-term layer writes.
    integrate_body_cue* does NOT call this guard — that path explicitly
    writes body_memory as its declared purpose.
    """
    if field_name in _LONG_TERM_FIELDS:
        raise RuntimeError(
            f"integrator attempted to write long-term field '{field_name}' — "
            "must go through consolidation/decay scheduler path only"
        )


# ── A. RealityEventType ───────────────────────────────────────────────────────


class RealityEventType(str, Enum):
    SEEK_COMPANIONSHIP = "seek_companionship"
    """User actively seeks companionship — discharges touch deficit."""

    NO_INTERACTION = "no_interaction"
    """Long period without interaction — accrues touch deficit."""

    RECEIVED_COMFORT = "received_comfort"
    """User was soothed / comforted — discharges touch deficit."""


# ── B. Audit types ────────────────────────────────────────────────────────────


@dataclass
class FieldDelta:
    """Audit record for a single field mutation."""

    field: str
    old_value: float
    new_value: float
    source: str


@dataclass
class IntegratorResult:
    """Audit record returned by every integrator call.

    touched_fields — list of mutations that were applied.
    rejected_reasons — list of reasons mutations were blocked.
    source — originating event or source identifier.
    timestamp — ISO-8601 UTC string passed in as `now`.
    """

    touched_fields: list[FieldDelta] = field(default_factory=list)
    rejected_reasons: list[str] = field(default_factory=list)
    source: str = ""
    timestamp: str = ""

    @property
    def accepted(self) -> bool:
        return bool(self.touched_fields)

    @property
    def rejected(self) -> bool:
        return bool(self.rejected_reasons) and not self.touched_fields


# ── C. integrate_event ────────────────────────────────────────────────────────


def integrate_event(
    event_type: RealityEventType,
    hidden_state: UserHiddenState,
    write_envelope: WriteEnvelope,
    now: str,
) -> tuple[UserHiddenState, IntegratorResult]:
    """Apply a Reality event to the 中期层 fields of hidden_state.

    Rules:
      SEEK_COMPANIONSHIP  → touch_need.deficit discharge (−DEFICIT_DISCHARGE_AMOUNT)
      RECEIVED_COMFORT    → touch_need.deficit discharge (−DEFICIT_DISCHARGE_AMOUNT)
      NO_INTERACTION      → touch_need.deficit accrue   (+DEFICIT_ACCRUE_AMOUNT)

    All mutations require write_envelope.can_write_memory == True.
    Long-term fields (sensitivity.baseline, touch_need.baseline,
    embodied_ease, body_memory) are never touched.

    Returns:
        (updated_state, IntegratorResult)
        On rejection, state is returned unchanged.
    """
    if not isinstance(event_type, RealityEventType):
        raise TypeError(
            f"integrate_event: event_type must be RealityEventType, got {type(event_type).__name__}"
        )
    result = IntegratorResult(source=event_type.value, timestamp=now)

    if not write_envelope.can_write_memory:
        reason = f"write_envelope.can_write_memory=False [event={event_type.value}]"
        result.rejected_reasons.append(reason)
        logger.warning("integrator rejected: %s", reason)
        return hidden_state, result

    deficit = hidden_state.touch_need.deficit

    if event_type in (RealityEventType.SEEK_COMPANIONSHIP, RealityEventType.RECEIVED_COMFORT):
        old_val = deficit.value
        discharge_touch_deficit(hidden_state, DEFICIT_DISCHARGE_AMOUNT, UpdateSource.REALITY_BEHAVIOR, now)
        new_val = deficit.value
        result.touched_fields.append(FieldDelta(
            field="touch_need.deficit",
            old_value=old_val,
            new_value=new_val,
            source=event_type.value,
        ))
        logger.info(
            "integrator: touch_need.deficit %.2f → %.2f [source=%s]",
            old_val, new_val, event_type.value,
        )

    elif event_type == RealityEventType.NO_INTERACTION:
        old_val = deficit.value
        new_val = _clamp(old_val + DEFICIT_ACCRUE_AMOUNT)
        hidden_state.touch_need.deficit.value = new_val
        hidden_state.touch_need.deficit.last_updated = now
        hidden_state.touch_need.deficit.last_update_source = UpdateSource.REALITY_BEHAVIOR
        result.touched_fields.append(FieldDelta(
            field="touch_need.deficit",
            old_value=old_val,
            new_value=new_val,
            source=event_type.value,
        ))
        logger.info(
            "integrator: touch_need.deficit %.2f → %.2f [source=%s]",
            old_val, new_val, event_type.value,
        )

    return hidden_state, result


# ── D. integrate_impression ───────────────────────────────────────────────────


def integrate_impression(
    impression: ImpressionInput,
    hidden_state: UserHiddenState,
    write_envelope: WriteEnvelope,
    now: str,
) -> tuple[UserHiddenState, IntegratorResult]:
    """Apply a Dream-derived impression to sensitivity.current (increase only).

    Gate rules:
      1. write_envelope.can_write_memory must be True.
      2. impression.weight must be in [DREAM_GATE_MIN, DREAM_GATE_MAX].
         Values outside this range are rejected.
      3. Delta is always positive (increases only).
      4. Delta is capped at min(IMPRESSION_MAX_NUDGE, MAX_NUDGE_PER_EVENT).

    Long-term fields are never touched by this function.

    Returns:
        (updated_state, IntegratorResult)
        On rejection, state is returned unchanged.
    """
    if not isinstance(impression, ImpressionInput):
        raise TypeError(
            f"integrate_impression: impression must be ImpressionInput, got {type(impression).__name__}"
        )
    result = IntegratorResult(source=UpdateSource.DREAM_IMPRESSION.value, timestamp=now)

    if not write_envelope.can_write_memory:
        reason = "write_envelope.can_write_memory=False [impression]"
        result.rejected_reasons.append(reason)
        logger.warning("integrator rejected impression: %s", reason)
        return hidden_state, result

    weight = impression.weight
    if weight < DREAM_GATE_MIN or weight > DREAM_GATE_MAX:
        reason = (
            f"impression.weight={weight:.3f} outside gate "
            f"[{DREAM_GATE_MIN}, {DREAM_GATE_MAX}]"
        )
        result.rejected_reasons.append(reason)
        logger.warning("integrator rejected impression: %s", reason)
        return hidden_state, result

    gate_span = DREAM_GATE_MAX - DREAM_GATE_MIN
    ratio = (weight - DREAM_GATE_MIN) / gate_span if gate_span > 0 else 1.0
    delta = min(ratio * IMPRESSION_MAX_NUDGE, MAX_NUDGE_PER_EVENT)

    old_val = hidden_state.sensitivity.current.value
    nudge_current_sensitivity(hidden_state, delta, UpdateSource.DREAM_IMPRESSION, now)
    new_val = hidden_state.sensitivity.current.value
    result.touched_fields.append(FieldDelta(
        field="sensitivity.current",
        old_value=old_val,
        new_value=new_val,
        source=UpdateSource.DREAM_IMPRESSION.value,
    ))
    logger.info(
        "integrator: sensitivity.current %.2f → %.2f [weight=%.3f]",
        old_val, new_val, weight,
    )

    return hidden_state, result


# ── E. Disk-wired entry points (Phase 2) ─────────────────────────────────────


def integrate_event_and_save(
    uid: str | int,
    event_type: RealityEventType,
    write_envelope: WriteEnvelope,
    now: str,
) -> tuple[UserHiddenState, IntegratorResult]:
    """Load hidden state, apply a Reality event, and persist if permitted.

    Steps:
      1. Load UserHiddenState from store (or default if absent/corrupt).
      2. Call integrate_event() — 中期层 mutation only.
      3. If write_envelope.can_write_memory AND result.accepted: atomic save.
         Otherwise: state is returned unchanged, nothing is written to disk.

    Long-term fields (sensitivity.baseline, touch_need.baseline, embodied_ease,
    body_memory) are never touched — guaranteed by integrate_event().

    Fail-closed: a rejected envelope or any I/O error leaves disk untouched.
    The atomic write guarantee comes from safe_write_json inside save_hidden_state.

    Returns:
        (state_after_mutation, IntegratorResult)
    """
    if not isinstance(uid, (str, int)):
        raise TypeError(f"uid must be str or int, got {type(uid).__name__}")
    state = load_hidden_state(uid)
    state, result = integrate_event(event_type, state, write_envelope, now)
    if write_envelope.can_write_memory and result.accepted:
        ok = save_hidden_state(uid, state)
        if not ok:
            logger.error(
                "integrate_event_and_save: save failed [uid=%s event=%s]",
                uid, event_type.value,
            )
    return state, result


def integrate_impression_and_save(
    uid: str | int,
    impression: ImpressionInput,
    write_envelope: WriteEnvelope,
    now: str,
) -> tuple[UserHiddenState, IntegratorResult]:
    """Load hidden state, apply a Dream-derived impression, and persist if permitted.

    Steps:
      1. Load UserHiddenState from store (or default if absent/corrupt).
      2. Call integrate_impression() — sensitivity.current mutation only.
      3. If write_envelope.can_write_memory AND result.accepted: atomic save.
         Otherwise: state is returned unchanged, nothing is written to disk.

    Long-term fields are never touched — guaranteed by integrate_impression().
    Dream-derived inputs must only be supplied at Dream exit by the Reality-side
    caller; never from within an active Dream turn.

    Fail-closed: rejected envelope, out-of-gate weight, or I/O error → no write.
    The atomic write guarantee comes from safe_write_json inside save_hidden_state.

    Returns:
        (state_after_mutation, IntegratorResult)
    """
    if not isinstance(uid, (str, int)):
        raise TypeError(f"uid must be str or int, got {type(uid).__name__}")
    state = load_hidden_state(uid)
    state, result = integrate_impression(impression, state, write_envelope, now)
    if write_envelope.can_write_memory and result.accepted:
        ok = save_hidden_state(uid, state)
        if not ok:
            logger.error(
                "integrate_impression_and_save: save failed [uid=%s]", uid,
            )
    return state, result


# ── F. Phase 3 — body_memory long-term layer (Reality-side only) ──────────────


def integrate_body_cue(
    cue: str,
    response_tag: str,
    strength: float,
    hidden_state: UserHiddenState,
    write_envelope: WriteEnvelope,
    now: str,
) -> tuple[UserHiddenState, IntegratorResult]:
    """Reinforce a body-memory entry (long-term layer) from a Reality-turn cue.

    Rules:
      - write_envelope.can_write_memory must be True.
      - source is fixed to REALITY_BEHAVIOR (Phase 3).
      - Empty / whitespace-only cue → silent no-op (accepted=False, no rejection reason).
      - Delegates weight management to reinforce_body_memory.

    Dream turns must NOT call this function directly.
    No WriteEnvelope is automatically emitted by this function.

    Returns:
        (updated_state, IntegratorResult)
    """
    result = IntegratorResult(source=UpdateSource.REALITY_BEHAVIOR.value, timestamp=now)

    if not write_envelope.can_write_memory:
        reason = "write_envelope.can_write_memory=False [body_cue]"
        result.rejected_reasons.append(reason)
        logger.warning("integrator rejected body_cue: %s", reason)
        return hidden_state, result

    cue_norm = cue.strip().lower()
    if not cue_norm:
        return hidden_state, result  # silent no-op, accepted stays False

    old_weight = next(
        (e.weight for e in hidden_state.body_memory.entries
         if e.cue.strip().lower() == cue_norm),
        None,
    )

    reinforce_body_memory(
        hidden_state, cue, response_tag, strength, UpdateSource.REALITY_BEHAVIOR, now
    )

    new_weight = next(
        (e.weight for e in hidden_state.body_memory.entries
         if e.cue.strip().lower() == cue_norm),
        None,
    )

    if new_weight is not None:
        result.touched_fields.append(FieldDelta(
            field="body_memory",
            old_value=old_weight if old_weight is not None else 0.0,
            new_value=new_weight,
            source=UpdateSource.REALITY_BEHAVIOR.value,
        ))
        logger.info(
            "integrator: body_memory cue='%s' %.3f → %.3f",
            cue_norm,
            old_weight if old_weight is not None else 0.0,
            new_weight,
        )

    return hidden_state, result


# ── G. Phase 5 — Afterglow integration (Dream exit → Reality-side) ────────────

_POSITIVE_TONES: frozenset[str] = frozenset({"comfort", "calm", "warm", "safe", "trusted"})
"""Tones that produce a gentle positive nudge to sensitivity.current."""

_EASE_TONES: frozenset[str] = frozenset({"comfort", "safe", "trusted"})
"""Subset of positive tones that also nudge embodied_ease upward."""

_NEGATIVE_TONES: frozenset[str] = frozenset({"fear", "stress", "threat"})
"""Tones that produce a gentle negative nudge to sensitivity.current."""

AFTERGLOW_SENS_NUDGE_POSITIVE: float = 1.5
"""Delta applied to sensitivity.current for positive-tone afterglow (0.25x typical event)."""

AFTERGLOW_SENS_NUDGE_NEGATIVE: float = -1.5
"""Delta applied to sensitivity.current for negative-tone afterglow (0.25x typical event)."""

AFTERGLOW_EASE_NUDGE: float = 0.8
"""Delta applied to embodied_ease for comfort/safe/trusted afterglow (very weak)."""


def integrate_afterglow(
    afterglow: AfterglowResidueInput,
    hidden_state: UserHiddenState,
    write_envelope: WriteEnvelope,
    now: str,
) -> tuple[UserHiddenState, IntegratorResult]:
    """Apply Dream afterglow residue to sensitivity.current and embodied_ease.

    Afterglow is emotional residue — not fact, not long-term memory.
    It affects only the two fast-moving fields within AFTERGLOW_TTL_HOURS (8 h).

    Gate rules (all must pass; fail-closed):
      1. write_envelope.can_write_memory must be True.
      2. write_envelope.source must be SourceType.DREAM_AFTERGLOW.
      3. afterglow.age_hours must be <= AFTERGLOW_TTL_HOURS.

    Allowed mutations:
      - sensitivity.current  — small increase (positive tone) or decrease (negative tone).
      - embodied_ease        — very small increase (comfort/safe/trusted only).

    Prohibited mutations (enforced; these fields are never touched):
      - sensitivity.baseline
      - touch_need.baseline
      - touch_need.deficit
      - body_memory

    Influence magnitude:
      AFTERGLOW_SENS_NUDGE_*  = ±1.5  (≈ 0.25× a typical Reality event)
      AFTERGLOW_EASE_NUDGE    = +0.8  (extremely weak)

    Returns:
        (updated_state, IntegratorResult)
        On any rejection, state is returned unchanged.
    """
    if not isinstance(afterglow, AfterglowResidueInput):
        raise TypeError(
            f"integrate_afterglow: afterglow must be AfterglowResidueInput, "
            f"got {type(afterglow).__name__}"
        )

    result = IntegratorResult(source=UpdateSource.DREAM_AFTERGLOW.value, timestamp=now)

    # Gate 1 — envelope memory write
    if not write_envelope.can_write_memory:
        reason = "write_envelope.can_write_memory=False [afterglow]"
        result.rejected_reasons.append(reason)
        logger.warning("integrator rejected afterglow: %s", reason)
        return hidden_state, result

    # Gate 2 — source must be DREAM_AFTERGLOW
    if write_envelope.source != SourceType.DREAM_AFTERGLOW:
        reason = (
            f"write_envelope.source={write_envelope.source.value!r} "
            f"!= 'dream_afterglow' [afterglow]"
        )
        result.rejected_reasons.append(reason)
        logger.warning("integrator rejected afterglow: %s", reason)
        return hidden_state, result

    # Gate 3 — TTL
    if afterglow.age_hours > AFTERGLOW_TTL_HOURS:
        reason = (
            f"afterglow.age_hours={afterglow.age_hours:.2f}h "
            f"> TTL {AFTERGLOW_TTL_HOURS:.0f}h"
        )
        result.rejected_reasons.append(reason)
        logger.debug("integrator rejected afterglow (TTL): %s", reason)
        return hidden_state, result

    # Classify tone
    tone = afterglow.tone.lower().strip()
    all_tags: list[str] = [t.lower().strip() for t in afterglow.emotional_tags]
    if tone:
        all_tags.append(tone)

    has_positive = tone in _POSITIVE_TONES or any(t in _POSITIVE_TONES for t in all_tags)
    has_negative = tone in _NEGATIVE_TONES or any(t in _NEGATIVE_TONES for t in all_tags)
    has_ease = tone in _EASE_TONES or any(t in _EASE_TONES for t in all_tags)

    # sensitivity.current nudge
    if has_positive and not has_negative:
        sens_delta = AFTERGLOW_SENS_NUDGE_POSITIVE
    elif has_negative and not has_positive:
        sens_delta = AFTERGLOW_SENS_NUDGE_NEGATIVE
    else:
        sens_delta = 0.0  # ambiguous or neutral → no nudge

    if sens_delta != 0.0:
        old_sens = hidden_state.sensitivity.current.value
        nudge_current_sensitivity(hidden_state, sens_delta, UpdateSource.DREAM_AFTERGLOW, now)
        new_sens = hidden_state.sensitivity.current.value
        result.touched_fields.append(FieldDelta(
            field="sensitivity.current",
            old_value=old_sens,
            new_value=new_sens,
            source=UpdateSource.DREAM_AFTERGLOW.value,
        ))
        logger.info(
            "integrator: afterglow sensitivity.current %.2f → %.2f [tone=%r age=%.1fh]",
            old_sens, new_sens, tone, afterglow.age_hours,
        )

    # embodied_ease nudge — only for ease-qualifying tones, no negative overriding
    if has_ease and not has_negative:
        old_ease = hidden_state.embodied_ease.value
        nudge_embodied_ease(hidden_state, AFTERGLOW_EASE_NUDGE, UpdateSource.DREAM_AFTERGLOW, now)
        new_ease = hidden_state.embodied_ease.value
        result.touched_fields.append(FieldDelta(
            field="embodied_ease",
            old_value=old_ease,
            new_value=new_ease,
            source=UpdateSource.DREAM_AFTERGLOW.value,
        ))
        logger.info(
            "integrator: afterglow embodied_ease %.2f → %.2f [tone=%r age=%.1fh]",
            old_ease, new_ease, tone, afterglow.age_hours,
        )

    return hidden_state, result


def integrate_afterglow_and_save(
    uid: str | int,
    afterglow: AfterglowResidueInput,
    write_envelope: WriteEnvelope,
    now: str,
) -> tuple[UserHiddenState, IntegratorResult]:
    """Load hidden state, apply afterglow residue, and persist if permitted.

    Steps:
      1. Load UserHiddenState from store (or default if absent/corrupt).
      2. Call integrate_afterglow() — applies afterglow gates and mutations.
      3. If write_envelope.can_write_memory AND result.accepted: atomic save.
         Otherwise: state is returned unchanged, nothing is written to disk.

    Dream-derived afterglow must only be supplied at Dream exit by the
    Reality-side caller — never from within an active Dream turn.

    Fail-closed: closed envelope, wrong source, expired TTL, or I/O error → no write.

    Returns:
        (state_after_mutation, IntegratorResult)
    """
    if not isinstance(uid, (str, int)):
        raise TypeError(f"uid must be str or int, got {type(uid).__name__}")
    state = load_hidden_state(uid)
    state, result = integrate_afterglow(afterglow, state, write_envelope, now)
    if write_envelope.can_write_memory and result.accepted:
        ok = save_hidden_state(uid, state)
        if not ok:
            logger.error(
                "integrate_afterglow_and_save: save failed [uid=%s tone=%s]",
                uid, afterglow.tone,
            )
    return state, result


def integrate_body_cue_and_save(
    uid: str | int,
    cue: str,
    response_tag: str,
    strength: float,
    write_envelope: WriteEnvelope,
    now: str,
) -> tuple[UserHiddenState, IntegratorResult]:
    """Load hidden state, reinforce a body-memory cue, and persist if permitted.

    Steps:
      1. Load UserHiddenState from store (or default if absent/corrupt).
      2. Call integrate_body_cue() — body_memory long-term layer only.
      3. If write_envelope.can_write_memory AND result.accepted: atomic save.
         Otherwise: nothing is written to disk.

    Fail-closed: closed envelope, empty cue, or I/O error → no disk write.

    Returns:
        (state_after_mutation, IntegratorResult)
    """
    if not isinstance(uid, (str, int)):
        raise TypeError(f"uid must be str or int, got {type(uid).__name__}")
    state = load_hidden_state(uid)
    state, result = integrate_body_cue(cue, response_tag, strength, state, write_envelope, now)
    if write_envelope.can_write_memory and result.accepted:
        ok = save_hidden_state(uid, state)
        if not ok:
            logger.error(
                "integrate_body_cue_and_save: save failed [uid=%s cue=%s]", uid, cue
            )
    return state, result
