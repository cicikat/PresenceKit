"""Frozen Audio + Music Perception Foundation v0 contract (ticket 260 A).

This module is the machine-checkable freeze for schema, units, budgets,
scopes, playback state, listen-count semantics, adapter envelope and host
inventory. Later stages implement runtime against these constants; importing
this file must not decode audio, start a player, or touch production data.
"""
from __future__ import annotations

from typing import Any


# v1 (video-call work order E) adds impression labels; nothing from v0 is removed.
# The v0 constants and ``conservative_impression_v0`` stay so older receipts, traces
# and cached results remain explainable (DELETION_AUTHORIZED is still False).
CONTRACT_VERSION_V0 = "audio-music-perception.v0"
ANALYSIS_VERSION_V0 = "audio-analysis.v0"
CONTRACT_VERSION = "audio-music-perception.v1"
ANALYSIS_VERSION = "audio-analysis.v1"
LISTEN_THRESHOLD_VERSION = "listen-threshold.v0"
PLAYER_ADAPTER_VERSION = "player-adapter.v0"
PROMPT_LAYER = "3.8_audio_impression"

ANALYSIS_MODES = frozenset({"speech", "music"})
IMPRESSIONS_V0 = frozenset({"calm", "tired", "bright", "tense", "unclear"})
# unsteady = pitch swings widely and a straight line explains little of it (not a simple
# rise/fall); breathy = an unusually large share of voiced-frame power above 2.5 kHz;
# low_toned = low absolute pitch AND slow pace at normal loudness (no personal baseline
# exists, so it never means "lower than usual").
# Deliberately NOT here: crying voice, pinched/tight voice — see docs/audio-perception.md.
IMPRESSIONS_V1_ADDED = frozenset({"unsteady", "breathy", "low_toned"})
IMPRESSIONS = IMPRESSIONS_V0 | IMPRESSIONS_V1_ADDED
PROVIDER_TONE_HINTS = IMPRESSIONS

# v1 thresholds. They were calibrated on synthetic signals only (harmonic source + noise
# leaning high, HNR 40..3 dB): clean voices gave hf_ratio <= 0.04, HNR <= 6 dB gave
# >= 0.15.  Real recordings have NOT been checked yet; prefer no label over a wrong one.
UNSTEADY_VARIATION = 0.30
UNSTEADY_MAX_LINEAR_R2 = 0.5
BREATHY_HF_RATIO_MIN = 0.15
HF_BAND_LOW_HZ = 2500.0
BREATHINESS_MIN_BUDGET_S = 0.6

PITCH_UNITS = {
    "time": "seconds",
    "f0": "Hz",
    "voicing": "confidence_0_1_or_null",
}
PITCH_F0_MIN_HZ = 60.0
PITCH_F0_MAX_HZ = 400.0
PITCH_FRAME_MS = 25
PITCH_HOP_MS = 10
MIN_VOICED_FRAMES_SUMMARY = 10
MIN_VOICED_FRAMES_TREND = 30
TREND_RISING_HZ_S = 8.0
UNVOICED_F0 = None  # never coerce missing F0 to 0 Hz

ENERGY_UNITS = {"rms": "linear_fs", "level": "dBFS"}
PACE_METHOD = "voiced_onset_rate"
PACE_UNIT = "onsets_per_second"
PACE_UNKNOWN = "unknown"

LISTEN_MIN_SECONDS = 30.0
LISTEN_DURATION_FRACTION = 0.5
LISTEN_UNKNOWN_DURATION_SECONDS = 30.0

MAX_INPUT_BYTES = 25 * 1024 * 1024
SPEECH_MAX_DURATION_S = 120.0
MUSIC_MAX_ANALYZE_DURATION_S = 180.0
TARGET_SPEECH_SAMPLE_RATE = 16000
TARGET_MUSIC_SAMPLE_RATE = 22050
MAX_DECODE_MEMORY_BYTES = 64 * 1024 * 1024
SPEECH_ANALYSIS_TIMEOUT_S = 3.0
MUSIC_ANALYSIS_TIMEOUT_S = 8.0
MAX_CONCURRENT_ANALYSIS = 1

RECEIPT_TTL_S = 300
RECEIPT_CAPACITY = 128

LIBRARY_TRACK_CAP = 500
AUDIO_BLOB_BUDGET_BYTES = 2 * 1024 * 1024 * 1024
NOTE_MAX_CHARS = 4000
HISTORY_OCCURRENCE_CAP = 2000
COMMAND_RESULT_CACHE_CAP = 256
HOST_EVENT_RING_CAP = 200
ANALYSIS_CACHE_MAX_AGE_DAYS = 30
ANALYSIS_CACHE_MAX_BYTES = 512 * 1024 * 1024

FEATURE_SWITCHES = (
    "speech_analysis",
    "music_analysis",
    "music_control",
    "music_autonomy",
)
CONFIG_ROOT = "audio_music"
DEFAULT_SWITCHES = {name: False for name in FEATURE_SWITCHES}

PLAYBACK_STATES = frozenset({
    "idle", "loading", "playing", "paused", "stopped", "ended", "error",
})
LEGAL_TRANSITIONS = frozenset({
    ("idle", "loading"),
    ("loading", "playing"),
    ("loading", "error"),
    ("loading", "stopped"),
    ("playing", "paused"),
    ("playing", "ended"),
    ("playing", "stopped"),
    ("playing", "error"),
    ("playing", "loading"),
    ("paused", "playing"),
    ("paused", "stopped"),
    ("paused", "loading"),
    ("paused", "error"),
    ("ended", "loading"),
    ("ended", "stopped"),
    ("ended", "idle"),
    ("stopped", "loading"),
    ("stopped", "idle"),
    ("error", "loading"),
    ("error", "stopped"),
    ("error", "idle"),
})

COMMAND_OUTCOMES = frozenset({
    "accepted", "rejected", "confirmed", "failed", "outcome_unknown",
})
ADAPTER_EVENTS = frozenset({
    "started", "changed", "paused", "resumed", "finished", "stopped",
    "error", "progress",
})
REQUIRED_ADAPTER_ACTIONS = frozenset({
    "capabilities", "get_state", "play", "pause", "resume", "stop",
    "set_queue", "next",
})
OPTIONAL_ADAPTER_ACTIONS = frozenset({"seek"})
AUDIO_ACCESS = frozenset({
    "backend_readable",
    "local_playable_backend_unread",
    "unavailable",
})

ANALYSIS_STATUSES = frozenset({
    "ok", "partial", "unavailable", "failed", "timeout",
})
MELODY_STATUSES = frozenset({
    "unknown", "distribution_only", "unavailable",
})
COUNT_FIELDS = ("started_count", "listen_count", "completed_count")
TERMINATION_REASONS = frozenset({
    "finished", "stopped", "changed", "removed", "error", "disconnected",
    "host_expired", "shutdown",
})

PLANNED_TOOLS = (
    "get_listening_state",
    "get_listening_queue",
    "get_listening_history",
    "get_track_note",
    "write_track_note",
    "choose_next_track",
)

AUTONOMY_SIGNAL_SOURCE = "music_playback"
FEEDBACK_LOOP_COOLDOWN_S = 600
SESSION_TALK_BUDGET = 2
PROGRESS_SIGNAL_MIN_INTERVAL_S = 1.0

# Existing independent desktop actions. Ticket 260 does not migrate or delete them.
INDEPENDENT_DESKTOP_ACTIONS = frozenset({"media_play_pause", "play_netease"})
DELETION_CANDIDATES = (
    "provider_tone_as_final_impression",
    "play_netease",
    "media_play_pause",
)
DELETION_AUTHORIZED = False

HOST_INVENTORY = {
    "reusable_first_party_player": False,
    "standalone_player_demo_started": False,
    "netease_or_media_key_is_adapter": False,
    "tts_playback_queue_is_music_host": False,
    "phone_player_in_v0": False,
    "first_host": "minimal_first_party_desktop_player",
    "mock_adapter_closes_e": False,
}

SCOPE_PLAN = {
    "settings": "admin",
    "analysis_and_adapter_metadata": "state.read",
    "listening_history": "memory.read",
    "character_track_notes": "memory.read",
    "transcribe_and_receipts": "chat",
    "player_host_transport": "ws.desktop",
    "new_scope": None,
}

LOCK_NAMES = {
    "listening": "listening:{uid}",
    "track_notes": "track_notes:{char_id}:{uid}",
    "analysis": "audio_analysis",
}

QUALITY_MARKS = frozenset({
    "ok", "insufficient", "clipped", "noisy", "short", "silent", "conflict",
})


def listen_threshold_seconds(duration_s: float | None) -> float:
    """Seconds of actual play required before listen_count increments once."""
    if duration_s is None or duration_s <= 0:
        return LISTEN_UNKNOWN_DURATION_SECONDS
    return min(LISTEN_MIN_SECONDS, LISTEN_DURATION_FRACTION * float(duration_s))


def should_count_listen(
    *,
    accumulated_play_s: float,
    duration_s: float | None,
    already_counted: bool,
) -> bool:
    if already_counted:
        return False
    return float(accumulated_play_s) + 1e-9 >= listen_threshold_seconds(duration_s)


def is_legal_transition(previous: str, nxt: str) -> bool:
    return previous in PLAYBACK_STATES and nxt in PLAYBACK_STATES and (
        previous == nxt or (previous, nxt) in LEGAL_TRANSITIONS
    )


def pitch_point(time_s: float, f0_hz: float | None, voicing: float | None) -> dict[str, Any]:
    """Bounded pitch sample. Unvoiced or untrusted F0 stays null, never 0 Hz."""
    if f0_hz is not None:
        if voicing is None or voicing <= 0:
            f0_hz = None
        elif not (PITCH_F0_MIN_HZ <= float(f0_hz) <= PITCH_F0_MAX_HZ):
            f0_hz = None
    return {
        "t": float(time_s),
        "f0_hz": None if f0_hz is None else float(f0_hz),
        "voicing": None if voicing is None else float(voicing),
    }


def pitch_summary(points: list[dict[str, Any]]) -> dict[str, Any]:
    """Median / range / variation / trend over voiced frames only."""
    voiced = [
        row for row in points
        if row.get("f0_hz") is not None and row.get("voicing") not in (None, 0)
    ]
    if len(voiced) < MIN_VOICED_FRAMES_SUMMARY:
        return {
            "median_hz": None,
            "range_hz": None,
            "variation": None,
            "trend": PACE_UNKNOWN,
            "voiced_frames": len(voiced),
            "quality": "insufficient",
            "algorithm": ANALYSIS_VERSION,
        }
    values = sorted(float(row["f0_hz"]) for row in voiced)
    n = len(values)
    median = values[n // 2] if n % 2 else 0.5 * (values[n // 2 - 1] + values[n // 2])
    lo = values[max(0, int(0.05 * (n - 1)))]
    hi = values[min(n - 1, int(0.95 * (n - 1)))]
    q1 = values[max(0, int(0.25 * (n - 1)))]
    q3 = values[min(n - 1, int(0.75 * (n - 1)))]
    variation = None if median <= 0 else (q3 - q1) / median
    trend = PACE_UNKNOWN
    if len(voiced) >= MIN_VOICED_FRAMES_TREND:
        t0 = float(voiced[0]["t"])
        t1 = float(voiced[-1]["t"])
        span = t1 - t0
        if span > 0:
            slope = (float(voiced[-1]["f0_hz"]) - float(voiced[0]["f0_hz"])) / span
            if slope > TREND_RISING_HZ_S:
                trend = "rising"
            elif slope < -TREND_RISING_HZ_S:
                trend = "falling"
            else:
                trend = "stable"
    return {
        "median_hz": median,
        "range_hz": hi - lo,
        "variation": variation,
        "trend": trend,
        "voiced_frames": len(voiced),
        "quality": "ok",
        "algorithm": ANALYSIS_VERSION,
        "units": {"median": "Hz", "range": "Hz", "variation": "iqr_over_median", "trend": "Hz_per_s_class"},
    }


def conservative_impression(
    *,
    quality: str,
    median_hz: float | None,
    variation: float | None,
    pace: float | None | str,
    energy_dbfs: float | None,
    provider_tone: str | None = None,
    linear_r2: float | None = None,
    hf_ratio: float | None = None,
) -> dict[str, Any]:
    """v1 mapping: the v0 label plus the new ones, never several at once.

    Exactly one label is returned. Any disagreement between criteria falls back to
    ``unclear`` with quality ``conflict``, as in v0.  Provider tone never overrides
    failure.  Missing new features (``linear_r2`` / ``hf_ratio`` None, e.g. the
    feature budget was skipped) simply mean the new labels cannot fire.
    """
    base = conservative_impression_v0(
        quality=quality, median_hz=median_hz, variation=variation, pace=pace,
        energy_dbfs=energy_dbfs, provider_tone=provider_tone,
    )
    if quality != "ok" or median_hz is None:
        return {**base, "rule": ANALYSIS_VERSION}
    labels = set() if base["impression"] == "unclear" else {base["impression"]}
    low_energy = energy_dbfs is not None and energy_dbfs < -35
    if "tired" in labels and not low_energy:
        # v0 called any slow, low voice "tired"; v1 keeps that for quiet voices only.
        labels.discard("tired")
        labels.add("low_toned")
    if (variation is not None and variation >= UNSTEADY_VARIATION
            and linear_r2 is not None and linear_r2 < UNSTEADY_MAX_LINEAR_R2):
        labels.add("unsteady")
    if hf_ratio is not None and hf_ratio > BREATHY_HF_RATIO_MIN:
        labels.add("breathy")
    if "calm" in labels and labels & IMPRESSIONS_V1_ADDED:
        # ``calm`` only means "nothing salient was found"; a specific voice-quality
        # finding is more informative and does not contradict it.  Substantive labels
        # (tired / bright / tense / ...) still conflict with each other -> unclear.
        labels.discard("calm")
    label = next(iter(labels)) if len(labels) == 1 else "unclear"
    return {
        "impression": label,
        "quality": "ok" if label != "unclear" else "conflict",
        "rule": ANALYSIS_VERSION,
        "provider_tone_hint": _hint(provider_tone),
    }


def conservative_impression_v0(
    *,
    quality: str,
    median_hz: float | None,
    variation: float | None,
    pace: float | None | str,
    energy_dbfs: float | None,
    provider_tone: str | None = None,
) -> dict[str, Any]:
    """The frozen v0 mapping (five labels). Kept verbatim; v1 builds on it."""
    if quality != "ok" or median_hz is None:
        return {
            "impression": "unclear",
            "quality": quality if quality != "ok" else "insufficient",
            "rule": ANALYSIS_VERSION_V0,
            "provider_tone_hint": _hint(provider_tone),
        }
    pace_value = pace if isinstance(pace, (int, float)) else None
    high_var = variation is not None and variation >= 0.18
    low_var = variation is not None and variation <= 0.08
    slow = pace_value is not None and pace_value < 2.2
    fast = pace_value is not None and pace_value > 4.5
    low_pitch = median_hz < 140
    high_pitch = median_hz > 210
    low_energy = energy_dbfs is not None and energy_dbfs < -35
    high_energy = energy_dbfs is not None and energy_dbfs > -16
    label = "unclear"
    if low_pitch and slow and not high_energy:
        label = "tired"
    elif high_pitch and high_energy and not slow:
        label = "bright"
    elif (high_var or fast) and high_energy:
        label = "tense"
    elif low_var and not fast and not high_energy and not low_energy:
        label = "calm"
    if low_pitch and not slow:
        label = "unclear"
    if high_pitch and not high_energy and label == "bright":
        label = "unclear"
    return {
        "impression": label,
        "quality": "ok" if label != "unclear" else "conflict",
        "rule": ANALYSIS_VERSION_V0,
        "provider_tone_hint": _hint(provider_tone),
    }


def _hint(provider_tone: str | None) -> str | None:
    if isinstance(provider_tone, str) and provider_tone in PROVIDER_TONE_HINTS:
        return provider_tone
    return None


def stt_configured_is_not_analysis_available(
    *,
    stt_effective: bool,
    speech_analysis_enabled: bool,
    analysis_deps_ready: bool,
) -> bool:
    return not (stt_effective and speech_analysis_enabled and analysis_deps_ready)
