"""
core/memory/user_hidden_state.py
================================
Phase 0 schema stub — User Hidden State System

=== FIELD ADMISSION TEST ===

  A field belongs in User Hidden State only if it describes the USER'S OWN
  psycho-physical constitution — something that remains meaningful regardless
  of which companion object is involved.

  Ask: "If the companion object changes, does this value reset to zero?"
    YES → it is relationship state. Do NOT put it here.
    NO  → it may belong here.

  Admitted:
    embodied_ease     — user's baseline ease/tension in body-intimate contexts.
                        A constitution-level set-point; regresses to center, not 0.

  Rejected (belong in a future relationship_state module):
    body_familiarity       — resets when the companion changes → relationship state.
    somatic_familiarity    — same reason → relationship state.

=== SECURITY BOUNDARIES (MUST READ BEFORE EXTENDING) ===

  Phase 0 scope:
    - Data structures, constants, function signatures, docstrings only.
    - No pipeline wiring.
    - No disk I/O.
    - No Dream writeback of any kind.
    - No WriteEnvelope stamp emitted here.
    - No direct memory / mood / profile / event_log writes.

  Write permissions:
    - DREAM_DIRECT_WRITABLE   = frozenset()   # Dream cannot directly mutate any field.
    - DIRECT_MEMORY_WRITABLE  = frozenset()   # This module does not write memory.
    - DIRECT_MOOD_WRITABLE    = frozenset()   # This module does not write mood.
    - DIRECT_PROFILE_WRITABLE = frozenset()   # This module does not write profile.
    - DIRECT_EVENT_LOG_WRITABLE = frozenset() # This module does not write event_log.

  Future persistence path:
    - All persistent writes must flow through the Reality-side integrator,
      which must obtain a WriteEnvelope with can_write_memory=True before
      calling any mutating function defined here.
    - Dream-derived update sources (DREAM_AFTERGLOW, DREAM_IMPRESSION,
      DREAM_BODY_EVENT) may only enter via the Reality-side integrator at
      Dream exit — never from within a live Dream turn.

  Sensor / Watch:
    - SENSOR_SIGNAL is defined as an UpdateSource for future extensibility.
    - In Phase 0 and until an explicit WriteEnvelope with can_write_memory=True
      is granted, sensor/watch raw signals must NOT affect long-term state.
    - Do not assume heart-rate, screen text, or activity data auto-enters
      persistent state.

  Render tags:
    - <say> / <thought> / <narration> are desktop render structures.
    - This module must not use render tags as evidence for hidden-state updates.
    - Only stripped plain-text or structured events should be used.

  QQ channel isolation:
    - DREAM_ACTIVE / DREAM_CLOSING: QQ owner messages are rejected upstream.
    - No logic in this module may depend on "QQ补消息 during Dream."

  data/chars retirement:
    - This module must not reference data/chars/{char_id}.
    - Future path access must use user_memory_root(...) — not wired in Phase 0.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

_log = logging.getLogger(__name__)

# ── A. UpdateSource ────────────────────────────────────────────────────────────


class UpdateSource(str, Enum):
    """Origin of a state-mutation event.

    SENSOR_SIGNAL is defined for future extensibility only.
    Phase 0 rule: sensor signals are untrusted by default and must not
    directly write long-term state without an explicit WriteEnvelope grant.
    """

    INIT = "init"
    """Initial value at construction — no external influence."""

    REALITY_BEHAVIOR = "reality_behavior"
    """Observed behavior during a Reality (non-Dream) turn."""

    DREAM_AFTERGLOW = "dream_afterglow"
    """Emotional residue computed after Dream exit.
    Must enter via Reality-side integrator only."""

    DREAM_IMPRESSION = "dream_impression"
    """Distilled impression extracted from Dream transcript.
    Must enter via Reality-side integrator only."""

    DREAM_BODY_EVENT = "dream_body_event"
    """Body-state event that occurred during Dream session.
    Must enter via Reality-side integrator only."""

    SENSOR_SIGNAL = "sensor_signal"
    """Raw sensor / watch signal (heart-rate, screen text, activity, etc.).

    UNTRUSTED by default.  Writing long-term state from this source
    requires an explicit WriteEnvelope with can_write_memory=True,
    granted by the Reality-side integrator — never auto-granted.
    """

    TIME_DECAY = "time_decay"
    """Passive decay applied by the scheduler tick."""

    CONSOLIDATION = "consolidation"
    """Baseline consolidation pass (Reality-side only)."""


# ── B. Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class ScalarState:
    """A single clamped scalar with provenance metadata.

    value is kept in [SCALAR_MIN, SCALAR_MAX] (0.0–100.0).
    last_updated is an ISO-8601 UTC string or None if never updated.
    """

    value: float = 0.0
    last_updated: Optional[str] = None
    last_update_source: UpdateSource = UpdateSource.INIT


@dataclass
class BodyMemoryEntry:
    """One conditioned body-cue → response association.

    weight is in [WEIGHT_MIN, WEIGHT_MAX] (0.0–1.0).
    Entries below MEMORY_EVICT_EPS are eligible for eviction.
    """

    cue: str = ""
    response_tag: str = ""
    weight: float = 0.0
    created_at: str = ""
    last_reinforced: str = ""


@dataclass
class SensitivityState:
    """Physical sensitivity expressed as two scalars.

    baseline — slow-moving population-level norm.
    current  — fast-moving session-level value that regresses toward baseline.
    """

    baseline: ScalarState = field(default_factory=ScalarState)
    current: ScalarState = field(default_factory=ScalarState)


@dataclass
class TouchNeedState:
    """Affective touch-need state.

    baseline — individual touch-appetite set-point.
    deficit  — accumulated unmet touch need; decays with time.
    """

    baseline: ScalarState = field(default_factory=ScalarState)
    deficit: ScalarState = field(default_factory=ScalarState)


@dataclass
class BodyMemory:
    """Collection of conditioned body-cue entries with a fixed capacity.

    When entries exceed max_entries, the lowest-weight entry is evicted.
    """

    entries: list[BodyMemoryEntry] = field(default_factory=list)
    max_entries: int = 32


@dataclass
class UserHiddenState:
    """Top-level container for all user hidden state.

    schema_version must be bumped on any breaking field change.
    last_decay_tick is an ISO-8601 UTC string of the most recent
    time-decay pass, or None if decay has never run.

    Immutability contract for Phase 0:
      No field in this object may be written directly by Dream turns,
      sensor signals, render-tag parsers, or QQ message handlers.
      All mutations must go through the Reality-side integrator with
      an explicit WriteEnvelope grant.
    """

    sensitivity: SensitivityState = field(default_factory=SensitivityState)
    touch_need: TouchNeedState = field(default_factory=TouchNeedState)
    embodied_ease: ScalarState = field(default_factory=ScalarState)
    body_memory: BodyMemory = field(default_factory=BodyMemory)
    last_decay_tick: Optional[str] = None
    schema_version: int = 1


# ── C. Constants ───────────────────────────────────────────────────────────────

# Scalar range
SCALAR_MIN: float = 0.0
SCALAR_MAX: float = 100.0
SCALAR_CENTER: float = 50.0

# Weight range for body-memory entries
WEIGHT_MIN: float = 0.0
WEIGHT_MAX: float = 1.0

# Half-life constants (days)
CURRENT_SENS_REGRESS_HL_DAYS: float = 5.0      # current sensitivity → baseline
SENS_BASELINE_CENTER_HL_DAYS: float = 180.0    # sensitivity baseline → center
TOUCH_DEFICIT_DECAY_HL_DAYS: float = 10.0      # touch deficit → 0
TOUCH_BASELINE_CENTER_HL_DAYS: float = 180.0   # touch baseline → center
EMBODIED_EASE_CENTER_HL_DAYS: float = 90.0     # embodied_ease → SCALAR_CENTER (constitution regression)
MEMORY_EXTINCTION_HL_DAYS: float = 45.0        # body-memory weight decay

# Learning / nudge limits
BASELINE_LEARN_RATE: float = 0.02              # fraction moved per event
MAX_NUDGE_PER_EVENT: float = 6.0               # max single-event delta on any scalar
DREAM_GATE_MIN: float = 0.2                    # minimum Dream-derived update gate
DREAM_GATE_MAX: float = 0.4                    # maximum Dream-derived update gate

# Afterglow TTL
AFTERGLOW_TTL_HOURS: float = 8.0
"""Maximum age (hours) for an afterglow residue to be considered valid.
Residues older than this are rejected by read_afterglow_residue() and
integrate_afterglow() alike."""

# Body memory capacity
BODY_MEMORY_MAX_ENTRIES: int = 32
MEMORY_EVICT_EPS: float = 0.05                 # weight threshold for eviction eligibility

# Write-permission frozensets (all empty — this module has no write authority)
DREAM_DIRECT_WRITABLE: frozenset[str] = frozenset()
"""No field may be written directly from a live Dream turn."""

DIRECT_MEMORY_WRITABLE: frozenset[str] = frozenset()
"""This module does not write to the memory subsystem."""

DIRECT_MOOD_WRITABLE: frozenset[str] = frozenset()
"""This module does not write to mood_state."""

DIRECT_PROFILE_WRITABLE: frozenset[str] = frozenset()
"""This module does not write to user_profile."""

DIRECT_EVENT_LOG_WRITABLE: frozenset[str] = frozenset()
"""This module does not write to event_log."""


# ── D. Default constructor ─────────────────────────────────────────────────────


def default_hidden_state() -> UserHiddenState:
    """Return a zero/center UserHiddenState with no provenance.

    Default values:
      sensitivity.baseline  = 50  (SCALAR_CENTER)
      sensitivity.current   = 50
      touch_need.baseline   = 50
      touch_need.deficit    = 0
      embodied_ease         = 50  (SCALAR_CENTER — constitution neutral)
      body_memory           = empty, max_entries=BODY_MEMORY_MAX_ENTRIES

    This function does not write memory, mood, profile, or event_log.
    It emits no WriteEnvelope stamp.
    """
    return UserHiddenState(
        sensitivity=SensitivityState(
            baseline=ScalarState(value=SCALAR_CENTER, last_update_source=UpdateSource.INIT),
            current=ScalarState(value=SCALAR_CENTER, last_update_source=UpdateSource.INIT),
        ),
        touch_need=TouchNeedState(
            baseline=ScalarState(value=SCALAR_CENTER, last_update_source=UpdateSource.INIT),
            deficit=ScalarState(value=0.0, last_update_source=UpdateSource.INIT),
        ),
        embodied_ease=ScalarState(value=SCALAR_CENTER, last_update_source=UpdateSource.INIT),
        body_memory=BodyMemory(entries=[], max_entries=BODY_MEMORY_MAX_ENTRIES),
        last_decay_tick=None,
        schema_version=1,
    )


# ── E. Primitive helpers ───────────────────────────────────────────────────────


def _clamp(value: float, lo: float = SCALAR_MIN, hi: float = SCALAR_MAX) -> float:
    """Return value clamped to [lo, hi].

    Pure function.  No state mutation.  No I/O.
    """
    return max(lo, min(hi, value))


def _half_life_factor(elapsed_days: float, half_life_days: float) -> float:
    """Return the fraction of original magnitude remaining after elapsed_days.

    Uses: factor = 0.5 ^ (elapsed_days / half_life_days)
    Returns 1.0 if half_life_days <= 0 (no decay).
    Returns 1.0 if elapsed_days < 0 (negative time is ignored).
    Pure function.  No state mutation.  No I/O.
    """
    if half_life_days <= 0.0 or elapsed_days <= 0.0:
        return 1.0
    return math.pow(0.5, elapsed_days / half_life_days)


def _regress(
    current: float,
    target: float,
    elapsed_days: float,
    half_life_days: float,
) -> float:
    """Move current toward target using exponential half-life decay.

    Returns current + (target - current) * (1 - half_life_factor).
    Pure function.  No state mutation.  No I/O.
    """
    factor = _half_life_factor(elapsed_days, half_life_days)
    return current + (target - current) * (1.0 - factor)


def _logistic_step(x: float, center: float = SCALAR_CENTER, steepness: float = 0.1) -> float:
    """Map x through a logistic sigmoid centered at `center`.

    Returns a value in (0, 1).
    Used for gate calculations (e.g., DREAM_GATE_MIN/MAX scaling).
    Pure function.  No state mutation.  No I/O.
    """
    return 1.0 / (1.0 + math.exp(-steepness * (x - center)))


# ── F. Update function stubs ───────────────────────────────────────────────────


def apply_time_decay(state: UserHiddenState, now: str) -> UserHiddenState:
    """Apply passive time-based decay to all scalar fields.

    Caller MUST hold a WriteEnvelope with can_write_memory=True before
    invoking this function and persisting the returned state.
    This function itself does not emit a WriteEnvelope stamp.
    It does not write memory, mood, profile, or event_log.

    Dream-derived sources must not call this directly;
    decay is applied by the Reality-side scheduler tick only.

    First-run (last_decay_tick is None): sets last_decay_tick = now, no value changes.
    Clock-rollback (elapsed_days < 0): clamped to 0, no decay applied.
    """
    if state.last_decay_tick is None:
        state.last_decay_tick = now
        return state

    try:
        elapsed_days = (
            datetime.fromisoformat(now) - datetime.fromisoformat(state.last_decay_tick)
        ).total_seconds() / 86400.0
    except (ValueError, TypeError):
        elapsed_days = 0.0

    if elapsed_days < 0.0:
        elapsed_days = 0.0

    # sensitivity.current → baseline
    state.sensitivity.current.value = _clamp(_regress(
        state.sensitivity.current.value,
        state.sensitivity.baseline.value,
        elapsed_days, CURRENT_SENS_REGRESS_HL_DAYS,
    ))
    state.sensitivity.current.last_updated = now
    state.sensitivity.current.last_update_source = UpdateSource.TIME_DECAY

    # sensitivity.baseline → SCALAR_CENTER
    state.sensitivity.baseline.value = _clamp(_regress(
        state.sensitivity.baseline.value,
        SCALAR_CENTER, elapsed_days, SENS_BASELINE_CENTER_HL_DAYS,
    ))
    state.sensitivity.baseline.last_updated = now
    state.sensitivity.baseline.last_update_source = UpdateSource.TIME_DECAY

    # touch_need.deficit → 0
    state.touch_need.deficit.value = _clamp(_regress(
        state.touch_need.deficit.value,
        0.0, elapsed_days, TOUCH_DEFICIT_DECAY_HL_DAYS,
    ))
    state.touch_need.deficit.last_updated = now
    state.touch_need.deficit.last_update_source = UpdateSource.TIME_DECAY

    # touch_need.baseline → SCALAR_CENTER
    state.touch_need.baseline.value = _clamp(_regress(
        state.touch_need.baseline.value,
        SCALAR_CENTER, elapsed_days, TOUCH_BASELINE_CENTER_HL_DAYS,
    ))
    state.touch_need.baseline.last_updated = now
    state.touch_need.baseline.last_update_source = UpdateSource.TIME_DECAY

    # embodied_ease → SCALAR_CENTER
    state.embodied_ease.value = _clamp(_regress(
        state.embodied_ease.value,
        SCALAR_CENTER, elapsed_days, EMBODIED_EASE_CENTER_HL_DAYS,
    ))
    state.embodied_ease.last_updated = now
    state.embodied_ease.last_update_source = UpdateSource.TIME_DECAY

    # body_memory weights → 0 (weights decay but entries are NOT evicted here)
    for entry in state.body_memory.entries:
        entry.weight = _clamp(
            _regress(entry.weight, 0.0, elapsed_days, MEMORY_EXTINCTION_HL_DAYS),
            lo=WEIGHT_MIN, hi=WEIGHT_MAX,
        )

    state.last_decay_tick = now
    return state


def nudge_current_sensitivity(
    state: UserHiddenState,
    delta: float,
    source: UpdateSource,
    now: str,
) -> UserHiddenState:
    """Nudge sensitivity.current by delta, clamped to scalar range.

    Caller MUST hold a WriteEnvelope with can_write_memory=True.
    This function does not emit a WriteEnvelope stamp.
    It does not write memory, mood, profile, or event_log.

    Dream-derived sources (DREAM_AFTERGLOW, DREAM_IMPRESSION,
    DREAM_BODY_EVENT) must only enter via the Reality-side integrator
    at Dream exit — never from a live Dream turn.

    SENSOR_SIGNAL source is accepted as an argument type but must NOT
    be passed unless the caller's WriteEnvelope explicitly grants
    can_write_memory=True for sensor paths.
    """
    if not isinstance(source, UpdateSource):
        raise TypeError(f"source must be UpdateSource, got {type(source).__name__}")
    state.sensitivity.current.value = _clamp(state.sensitivity.current.value + delta)
    state.sensitivity.current.last_updated = now
    state.sensitivity.current.last_update_source = source
    return state


def accrue_touch_deficit(
    state: UserHiddenState,
    elapsed_days: float,
    now: str,
) -> UserHiddenState:
    """Increase touch deficit based on elapsed time without touch contact.

    Caller MUST hold a WriteEnvelope with can_write_memory=True.
    This function does not emit a WriteEnvelope stamp.
    It does not write memory, mood, profile, or event_log.

    elapsed_days <= 0 → no-op (no stamp, no mutation).
    """
    if elapsed_days <= 0.0:
        return state
    accrual_per_day = SCALAR_MAX / TOUCH_DEFICIT_DECAY_HL_DAYS  # ~10 points/day
    delta = _clamp(accrual_per_day * elapsed_days, lo=0.0, hi=SCALAR_MAX)
    state.touch_need.deficit.value = _clamp(state.touch_need.deficit.value + delta)
    state.touch_need.deficit.last_updated = now
    state.touch_need.deficit.last_update_source = UpdateSource.REALITY_BEHAVIOR
    return state


def discharge_touch_deficit(
    state: UserHiddenState,
    amount: float,
    source: UpdateSource,
    now: str,
) -> UserHiddenState:
    """Reduce touch deficit by amount (positive amount means deficit decreases).

    Caller MUST hold a WriteEnvelope with can_write_memory=True.
    This function does not emit a WriteEnvelope stamp.
    It does not write memory, mood, profile, or event_log.
    """
    if not isinstance(source, UpdateSource):
        raise TypeError(f"source must be UpdateSource, got {type(source).__name__}")
    state.touch_need.deficit.value = _clamp(state.touch_need.deficit.value - amount)
    state.touch_need.deficit.last_updated = now
    state.touch_need.deficit.last_update_source = source
    return state


def nudge_embodied_ease(
    state: UserHiddenState,
    delta: float,
    source: UpdateSource,
    now: str,
) -> UserHiddenState:
    """Nudge embodied_ease by delta, clamped to scalar range.

    embodied_ease is the user's baseline ease/tension constitution in body-intimate
    contexts.  It regresses toward SCALAR_CENTER (50), not toward 0.

    What this field MEANS:
      "When body-intimate dimensions arise, how readily does this user relax
       at a constitutional level?"

    What this field does NOT mean:
      "How familiar is this user with their companion's body?"
      Relationship-specific somatic familiarity must NOT be written here.

    Call restrictions:
      - Only the Reality-side integrator may call this, after obtaining a
        WriteEnvelope with can_write_memory=True.
      - Dream turns must NOT call this directly.
      - Pure "familiarity with companion's body" exposure must NOT be written here.
      - Baseline / long-term updates must go through consolidation or an
        envelope-gated integrator, not ad-hoc nudges.

    This function does not emit a WriteEnvelope stamp.
    It does not write memory, mood, profile, or event_log.

    Dream-derived sources (DREAM_AFTERGLOW, DREAM_IMPRESSION, DREAM_BODY_EVENT)
    must only enter via the Reality-side integrator at Dream exit.
    """
    if not isinstance(source, UpdateSource):
        raise TypeError(f"source must be UpdateSource, got {type(source).__name__}")
    delta = _clamp(delta, lo=-MAX_NUDGE_PER_EVENT, hi=MAX_NUDGE_PER_EVENT)
    state.embodied_ease.value = _clamp(state.embodied_ease.value + delta)
    state.embodied_ease.last_updated = now
    state.embodied_ease.last_update_source = source
    return state


def reinforce_body_memory(
    state: UserHiddenState,
    cue: str,
    response_tag: str,
    strength: float,
    source: UpdateSource,
    now: str,
) -> UserHiddenState:
    """Upsert a body-memory entry and reinforce its weight.

    If cue already exists, updates weight and last_reinforced.
    If cue is new and body_memory is full, evicts the lowest-weight entry
    that is below MEMORY_EVICT_EPS; if none qualifies, the new entry is dropped.

    Phase 1 requirement:
      Caller MUST hold a WriteEnvelope with can_write_memory=True.
      This function does not emit a WriteEnvelope stamp.
      It does not write memory, mood, profile, or event_log.

    Dream-derived sources must only enter via Reality-side integrator at exit.
    SENSOR_SIGNAL must not be passed without explicit WriteEnvelope grant.

    Long-term layer: valid write path is integrate_body_cue* (Reality-side, Phase 3+).
    """
    if not isinstance(source, UpdateSource):
        raise TypeError(f"source must be UpdateSource, got {type(source).__name__}")

    cue_norm = cue.strip().lower()
    if not cue_norm:
        return state  # empty cue → no-op, no error

    strength = _clamp(strength, lo=WEIGHT_MIN, hi=WEIGHT_MAX)

    existing = next(
        (e for e in state.body_memory.entries if e.cue.strip().lower() == cue_norm),
        None,
    )

    if existing is not None:
        old_w = existing.weight
        existing.weight = _clamp(
            old_w + strength * (WEIGHT_MAX - old_w), lo=WEIGHT_MIN, hi=WEIGHT_MAX
        )
        existing.last_reinforced = now
        existing.response_tag = response_tag
        return state

    new_entry = BodyMemoryEntry(
        cue=cue_norm,
        response_tag=response_tag,
        weight=strength,
        created_at=now,
        last_reinforced=now,
    )

    if len(state.body_memory.entries) < state.body_memory.max_entries:
        state.body_memory.entries.append(new_entry)
        return state

    # Capacity full: evict weakest entry below MEMORY_EVICT_EPS
    evict_candidates = [e for e in state.body_memory.entries if e.weight < MEMORY_EVICT_EPS]
    if not evict_candidates:
        _log.debug(
            "[body_memory] capacity full, no evictable entry — new cue '%s' dropped", cue_norm
        )
        return state

    weakest = min(evict_candidates, key=lambda e: e.weight)
    state.body_memory.entries.remove(weakest)
    state.body_memory.entries.append(new_entry)
    return state


def consolidate_baselines(
    state: UserHiddenState,
    now: str,
) -> UserHiddenState:
    """Nudge sensitivity and touch baselines toward SCALAR_CENTER.

    Intended for infrequent consolidation runs (weekly/monthly).

    Caller MUST hold a WriteEnvelope with can_write_memory=True.
    This function does not emit a WriteEnvelope stamp.
    It does not write memory, mood, profile, or event_log.
    Must not be triggered from within a Dream turn.

    Nudges baselines toward SCALAR_CENTER by BASELINE_LEARN_RATE per call.
    Does not touch sensitivity.current, deficit, embodied_ease, or body_memory.
    """
    sens_b = state.sensitivity.baseline
    sens_b.value = _clamp(sens_b.value + BASELINE_LEARN_RATE * (SCALAR_CENTER - sens_b.value))
    sens_b.last_updated = now
    sens_b.last_update_source = UpdateSource.CONSOLIDATION

    tn_b = state.touch_need.baseline
    tn_b.value = _clamp(tn_b.value + BASELINE_LEARN_RATE * (SCALAR_CENTER - tn_b.value))
    tn_b.last_updated = now
    tn_b.last_update_source = UpdateSource.CONSOLIDATION

    return state


def to_dict(state: UserHiddenState) -> dict[str, Any]:
    """Serialize UserHiddenState to a JSON-compatible dict.

    Does NOT write to disk.  Does NOT write memory, mood, profile, or event_log.
    Caller is responsible for persistence via WriteEnvelope-gated path.
    """
    def _scalar(s: ScalarState) -> dict[str, Any]:
        return {
            "value": s.value,
            "last_updated": s.last_updated,
            "last_update_source": s.last_update_source.value,
        }

    def _entry(e: BodyMemoryEntry) -> dict[str, Any]:
        return {
            "cue": e.cue,
            "response_tag": e.response_tag,
            "weight": e.weight,
            "created_at": e.created_at,
            "last_reinforced": e.last_reinforced,
        }

    return {
        "schema_version": state.schema_version,
        "last_decay_tick": state.last_decay_tick,
        "sensitivity": {
            "baseline": _scalar(state.sensitivity.baseline),
            "current": _scalar(state.sensitivity.current),
        },
        "touch_need": {
            "baseline": _scalar(state.touch_need.baseline),
            "deficit": _scalar(state.touch_need.deficit),
        },
        "embodied_ease": _scalar(state.embodied_ease),
        "body_memory": {
            "entries": [_entry(e) for e in state.body_memory.entries],
            "max_entries": state.body_memory.max_entries,
        },
    }


def from_dict(data: dict[str, Any]) -> UserHiddenState:
    """Deserialize a dict (from to_dict) back to UserHiddenState.

    Does NOT read from disk.  Does NOT write memory, mood, profile, or event_log.
    Unknown keys are ignored; missing keys fall back to default_hidden_state values.

    schema_version missing  → logs warning, proceeds with lenient deserialization.
    schema_version mismatch → logs warning, returns default_hidden_state().
    """
    if not isinstance(data, dict):
        _log.warning("[user_hidden_state] from_dict: expected dict, got %r — returning default", type(data).__name__)
        return default_hidden_state()

    defaults = default_hidden_state()

    if "schema_version" not in data:
        _log.warning("[user_hidden_state] from_dict: schema_version key missing — proceeding with lenient deserialization")
    else:
        sv = data["schema_version"]
        if sv != defaults.schema_version:
            _log.warning(
                "[user_hidden_state] from_dict: schema_version mismatch (got %r, expected %r) — returning default",
                sv, defaults.schema_version,
            )
            return default_hidden_state()

    def _scalar(raw: Any, dflt: ScalarState) -> ScalarState:
        if not isinstance(raw, dict):
            return dflt
        try:
            source_str = raw.get("last_update_source", dflt.last_update_source.value)
            try:
                source = UpdateSource(source_str)
            except ValueError:
                source = dflt.last_update_source
            return ScalarState(
                value=float(raw.get("value", dflt.value)),
                last_updated=raw.get("last_updated", dflt.last_updated),
                last_update_source=source,
            )
        except Exception:
            return dflt

    def _entry(raw: Any) -> Optional[BodyMemoryEntry]:
        if not isinstance(raw, dict):
            return None
        try:
            return BodyMemoryEntry(
                cue=str(raw.get("cue", "")),
                response_tag=str(raw.get("response_tag", "")),
                weight=float(raw.get("weight", 0.0)),
                created_at=str(raw.get("created_at", "")),
                last_reinforced=str(raw.get("last_reinforced", "")),
            )
        except Exception:
            return None

    sens_raw = data.get("sensitivity")
    if not isinstance(sens_raw, dict):
        sens_raw = {}
    sensitivity = SensitivityState(
        baseline=_scalar(sens_raw.get("baseline"), defaults.sensitivity.baseline),
        current=_scalar(sens_raw.get("current"), defaults.sensitivity.current),
    )

    tn_raw = data.get("touch_need")
    if not isinstance(tn_raw, dict):
        tn_raw = {}
    touch_need = TouchNeedState(
        baseline=_scalar(tn_raw.get("baseline"), defaults.touch_need.baseline),
        deficit=_scalar(tn_raw.get("deficit"), defaults.touch_need.deficit),
    )

    embodied_ease = _scalar(data.get("embodied_ease"), defaults.embodied_ease)

    bm_raw = data.get("body_memory")
    if isinstance(bm_raw, dict):
        entries_raw = bm_raw.get("entries", [])
        entries = [e for e in (_entry(r) for r in (entries_raw if isinstance(entries_raw, list) else [])) if e is not None]
        try:
            max_entries = int(bm_raw.get("max_entries", defaults.body_memory.max_entries))
        except (TypeError, ValueError):
            max_entries = defaults.body_memory.max_entries
    else:
        entries = []
        max_entries = defaults.body_memory.max_entries

    sv_out = data.get("schema_version", defaults.schema_version)
    try:
        sv_out = int(sv_out)
    except (TypeError, ValueError):
        sv_out = defaults.schema_version

    return UserHiddenState(
        sensitivity=sensitivity,
        touch_need=touch_need,
        embodied_ease=embodied_ease,
        body_memory=BodyMemory(entries=entries, max_entries=max_entries),
        last_decay_tick=data.get("last_decay_tick", defaults.last_decay_tick),
        schema_version=sv_out,
    )


# ── G. Input event dataclasses ─────────────────────────────────────────────────


@dataclass
class DreamBodyStateEvent:
    """Body-state snapshot captured during a Dream session.

    All fields are raw Dream-internal measurements.
    This dataclass must NOT be consumed directly by any write path.
    It must pass through the Reality-side integrator at Dream exit,
    which is responsible for gating with WriteEnvelope.

    No direct write authority — DREAM_DIRECT_WRITABLE = frozenset().
    """

    heat: float = 0.0
    sensitivity: float = 0.0
    tension: float = 0.0
    arousal: float = 0.0
    duration_min: float = 0.0


@dataclass
class AfterglowResidueInput:
    """Emotional afterglow residue computed after Dream session ends.

    Must enter hidden-state update pipeline only via Reality-side integrator.
    Must not be applied during an active or closing Dream turn.
    """

    emotional_tags: list[str] = field(default_factory=list)
    tone: str = ""
    age_hours: float = 0.0


@dataclass
class ImpressionInput:
    """Distilled impression from a Dream transcript.

    Must enter hidden-state update pipeline only via Reality-side integrator.
    impression_text is stripped plain-text — render tags must be removed upstream.
    """

    impression_text: str = ""
    emotional_tags: list[str] = field(default_factory=list)
    weight: float = 0.0


@dataclass
class SensorSignalInput:
    """Raw sensor / watch signal.

    UNTRUSTED BY DEFAULT.
    This dataclass defines structure only.
    It is NOT wired to any write path in Phase 0.

    Phase 1+ rule: consuming code must hold a WriteEnvelope with
    can_write_memory=True (sensor-granted) before passing this to any
    mutating function.  sensor/watch raw signals may NEVER auto-enter
    long-term state without explicit envelope approval.
    """

    signal_type: str = ""
    value: float = 0.0
    confidence: float = 0.0
    age_seconds: float = 0.0


@dataclass
class IntegratorInput:
    """Aggregated input bundle for the Reality-side integrator.

    The Reality-side integrator is the only authorised entry point for
    turning these inputs into hidden-state mutations.  It is responsible
    for validating and stamping a WriteEnvelope before calling any
    mutating function in this module.

    All Dream-derived fields (body_event, afterglow, impression) must
    only be populated at Dream exit — never during an active Dream turn.
    sensor_signal must be treated as untrusted unless the integrator's
    WriteEnvelope explicitly grants can_write_memory=True.
    reality_signals is a free-form dict for future Reality-turn data.
    now is an ISO-8601 UTC timestamp string.
    """

    body_event: Optional[DreamBodyStateEvent] = None
    afterglow: Optional[AfterglowResidueInput] = None
    impression: Optional[ImpressionInput] = None
    sensor_signal: Optional[SensorSignalInput] = None
    reality_signals: dict[str, Any] = field(default_factory=dict)
    now: str = ""


# ── H. Reader / projection ────────────────────────────────────────────────────

# Bucket thresholds (shared by to_dream_snapshot helpers)
_SNAPSHOT_BUCKET_LOW: float = 35.0
_SNAPSHOT_BUCKET_HIGH: float = 65.0
_SNAPSHOT_TOP_CUES: int = 5


def read_afterglow_residue(uid: str, now: str) -> Optional[AfterglowResidueInput]:
    """Return the most recent afterglow residue for uid if within TTL, else None.

    Phase 5 implementation.  Reads from disk via the store (lazy import to
    keep schema module import-order safe).  Applies TTL check (AFTERGLOW_TTL_HOURS).

    Contract:
      - Read-only.  Does NOT write memory, mood, profile, or event_log.
      - Does NOT emit a WriteEnvelope stamp.
      - If the stored residue is absent, corrupt, or expired → returns None.
      - age_hours on the returned object reflects elapsed time since creation.
      - Must not be called from within a live Dream turn.

    TTL window: 0 ~ AFTERGLOW_TTL_HOURS (8 h).  Residues older than TTL
    are silently discarded (returns None).
    """
    from core.memory.user_hidden_state_store import _load_afterglow_raw as _load  # lazy

    raw = _load(uid)
    if raw is None:
        return None

    created_at = raw.get("created_at")
    if not created_at:
        _log.debug("[afterglow] uid=%s: no created_at in stored residue — discarding", uid)
        return None

    try:
        age_hours = (
            datetime.fromisoformat(now) - datetime.fromisoformat(created_at)
        ).total_seconds() / 3600.0
    except (ValueError, TypeError) as exc:
        _log.warning("[afterglow] uid=%s: cannot parse timestamps (%s) — discarding", uid, exc)
        return None

    if age_hours > AFTERGLOW_TTL_HOURS:
        _log.debug(
            "[afterglow] uid=%s: TTL expired (%.2fh > %.0fh) — returning None",
            uid, age_hours, AFTERGLOW_TTL_HOURS,
        )
        return None

    try:
        return AfterglowResidueInput(
            emotional_tags=list(raw.get("emotional_tags", [])),
            tone=str(raw.get("tone", "")),
            age_hours=max(0.0, age_hours),
        )
    except Exception as exc:
        _log.warning("[afterglow] uid=%s: failed to build AfterglowResidueInput: %s", uid, exc)
        return None


def to_dream_snapshot(state: UserHiddenState, now: str) -> dict[str, Any]:
    """Return a coarse-grained bucket snapshot suitable for Dream context injection.

    Contract:
      - Returns low-resolution buckets only (no precise scalar values exposed).
      - Does NOT modify state.
      - Does NOT connect to build_snapshot.
      - Does NOT write memory, mood, profile, or event_log.
      - Does NOT emit a WriteEnvelope stamp.

    SECURITY — fail-closed write-lock:
      DREAM_DIRECT_WRITABLE = frozenset() — no hidden-state field may be written
      from within a Dream turn.  This function is a READ-ONLY projection; it
      carries no WriteEnvelope authority.  All mutations must flow through the
      Reality-side integrator with can_write_memory=True.

      If an unexpected error occurs, a neutral mid/neutral snapshot is returned
      rather than raising, so Dream prompts degrade gracefully.

    Projection rules (Phase 2):
      中期层 → bucket strings only (no raw values):
        sensitivity.current  → "sensitivity"    low / mid / high
        touch_need.deficit   → "touch_appetite" low / mid / high
      长期层 → coarse label only (no raw numbers, no baseline values):
        embodied_ease        → "embodied_ease"  guarded / neutral / easy
        body_memory.entries  → "memory_cues"    top-N cue strings by weight
                                                (weights are NOT included)

    Return shape::

        {
            "sensitivity":     "low" | "mid" | "high",
            "touch_appetite":  "low" | "mid" | "high",
            "embodied_ease":   "guarded" | "neutral" | "easy",
            "memory_cues":     [str, ...],   # top cue strings by weight
        }

    Bucket thresholds:
      sensitivity / touch_appetite:
        low   < 35
        mid   35 – 65  (inclusive both ends)
        high  > 65
      embodied_ease (user's constitutional ease in body-intimate contexts):
        guarded  < 35   — tends toward tension / wariness
        neutral  35 – 65
        easy     > 65   — tends toward relaxed openness
    """
    # Fail-closed: any unexpected error returns a neutral mid/neutral snapshot
    # so the Dream LLM call is never blocked by state-projection errors.
    _NEUTRAL: dict[str, Any] = {
        "sensitivity": "mid",
        "touch_appetite": "mid",
        "embodied_ease": "neutral",
        "memory_cues": [],
    }

    def _lmh(v: float) -> str:
        if v < _SNAPSHOT_BUCKET_LOW:
            return "low"
        return "high" if v > _SNAPSHOT_BUCKET_HIGH else "mid"

    def _ease(v: float) -> str:
        if v < _SNAPSHOT_BUCKET_LOW:
            return "guarded"
        return "easy" if v > _SNAPSHOT_BUCKET_HIGH else "neutral"

    try:
        # 中期层 → buckets (raw values intentionally excluded from output)
        sensitivity_bucket = _lmh(state.sensitivity.current.value)
        appetite_bucket = _lmh(state.touch_need.deficit.value)

        # 长期层 → coarse label only (no raw numbers in output)
        ease_label = _ease(state.embodied_ease.value)

        # 长期层 → cue strings only, sorted by weight descending (no weights)
        top_cues = [
            e.cue
            for e in sorted(
                state.body_memory.entries,
                key=lambda e: e.weight,
                reverse=True,
            )[:_SNAPSHOT_TOP_CUES]
            if e.cue
        ]
    except Exception:
        _log.exception("[to_dream_snapshot] unexpected error — returning neutral snapshot")
        return dict(_NEUTRAL)

    return {
        "sensitivity": sensitivity_bucket,
        "touch_appetite": appetite_bucket,
        "embodied_ease": ease_label,
        "memory_cues": top_cues,
    }
