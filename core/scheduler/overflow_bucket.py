"""Compute the read-only signals used by the overflow proactive trigger."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time

logger = logging.getLogger(__name__)

OVERFLOW_THRESHOLD = 1.6
OVERFLOW_JITTER = 0.15

W_TIME_GAP = 0.6
W_EPISODIC = 0.5
W_HIDDEN_NEED = 0.4
W_GARDEN = 0.3
W_MOOD = 0.4

_SIGNAL_WEIGHTS = {
    "time_gap": W_TIME_GAP,
    "episodic": W_EPISODIC,
    "hidden_need": W_HIDDEN_NEED,
    "garden": W_GARDEN,
    "mood": W_MOOD,
}


@dataclass
class OverflowSignals:
    time_gap_score: float = 0.0
    episodic_score: float = 0.0
    hidden_need_score: float = 0.0
    garden_score: float = 0.0
    mood_score: float = 0.0
    top_signal: str = ""
    top_signal_detail: str = ""
    # S5b 观测：hidden_need 门控前后值与各字段置信度（不影响 bucket_score）
    hidden_need_raw: float = 0.0
    hidden_need_gated: float = 0.0
    hidden_need_confidence: dict = field(default_factory=dict)

    def bucket_score(self) -> float:
        return (
            self.time_gap_score * W_TIME_GAP
            + self.episodic_score * W_EPISODIC
            + self.hidden_need_score * W_HIDDEN_NEED
            + self.garden_score * W_GARDEN
            + self.mood_score * W_MOOD
        )

    def is_overflow(self, *, jitter: float = 0.0) -> bool:
        threshold = OVERFLOW_THRESHOLD * (1.0 + jitter)
        return self.bucket_score() >= threshold


def _clamp_score(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def _pick_top_signal(sig: OverflowSignals, details: dict[str, str]) -> None:
    scores = {
        "time_gap": sig.time_gap_score,
        "episodic": sig.episodic_score,
        "hidden_need": sig.hidden_need_score,
        "garden": sig.garden_score,
        "mood": sig.mood_score,
    }
    signal, score = max(
        scores.items(),
        key=lambda item: item[1] * _SIGNAL_WEIGHTS[item[0]],
    )
    if score <= 0:
        return
    sig.top_signal = signal
    sig.top_signal_detail = details.get(signal, "")


def compute_signals(uid: str, *, char_id: str) -> OverflowSignals:
    """Read each signal independently; a failed source contributes zero."""
    sig = OverflowSignals()
    details: dict[str, str] = {}
    now = time.time()

    try:
        from core.memory import short_term

        history = short_term.load(uid, char_id=char_id)
        timestamps = [
            float(message.get("timestamp", 0))
            for message in history
            if isinstance(message, dict) and message.get("timestamp")
        ]
        if timestamps:
            gap_hours = max(0.0, (now - max(timestamps)) / 3600.0)
            sig.time_gap_score = _clamp_score((gap_hours - 6.0) / 18.0)
            details["time_gap"] = f"已经有约{max(1, round(gap_hours))}小时没有聊过了"
    except Exception as exc:
        logger.debug("[overflow_bucket] time_gap failed: %s", exc)

    try:
        from core.memory.episodic_memory import _load_memories

        candidates = []
        for episode in _load_memories(uid, char_id=char_id):
            strength = float(episode.get("strength", 0))
            last_retrieved = episode.get("last_retrieved")
            reference_ts = float(last_retrieved or episode.get("timestamp") or now)
            if strength > 0.7 and now - reference_ts > 3 * 86400:
                candidates.append((strength, episode))
        if candidates:
            strength, best = max(candidates, key=lambda item: item[0])
            sig.episodic_score = _clamp_score(strength)
            detail = best.get("narrative_summary") or best.get("summary") or ""
            details["episodic"] = str(detail)[:60]
    except Exception as exc:
        logger.debug("[overflow_bucket] episodic_pull failed: %s", exc)

    try:
        from core.memory.user_hidden_state_store import load_hidden_state

        state = load_hidden_state(uid, char_id=char_id)
        sensitivity_excess = (
            float(state.sensitivity.current.value)
            - float(state.sensitivity.baseline.value)
            - 15.0
        )
        touch_excess = float(state.touch_need.deficit.value) - 65.0
        from core.memory.user_hidden_state import confidence_gating_settings, scalar_view

        conf_s = scalar_view(state, "sensitivity.current")["confidence"]
        conf_t = scalar_view(state, "touch_need.deficit")["confidence"]
        _gate_on, _gate_min = confidence_gating_settings()
        raw_s = _clamp_score(sensitivity_excess / 35.0)
        raw_t = _clamp_score(touch_excess / 35.0)
        gated_s = raw_s * conf_s if conf_s >= _gate_min else 0.0
        gated_t = raw_t * conf_t if conf_t >= _gate_min else 0.0
        sig.hidden_need_raw = _clamp_score(max(sensitivity_excess, touch_excess) / 35.0)
        sig.hidden_need_gated = _clamp_score(max(gated_s, gated_t))
        sig.hidden_need_confidence = {
            "sensitivity.current": conf_s,
            "touch_need.deficit": conf_t,
        }
        sig.hidden_need_score = sig.hidden_need_gated if _gate_on else sig.hidden_need_raw
        if sig.hidden_need_score > 0:
            details["hidden_need"] = "有一点想靠近她、确认她此刻好不好"
    except Exception as exc:
        logger.debug("[overflow_bucket] hidden_need failed: %s", exc)

    try:
        from core.garden.manager import get_shareable_event

        event = get_shareable_event(char_id=char_id)
        if event:
            sig.garden_score = 0.8
            details["garden"] = str(event)[:80]
    except Exception as exc:
        logger.debug("[overflow_bucket] garden_event failed: %s", exc)

    try:
        from core.memory.mood_state import get_intensity

        intensity = float(get_intensity(char_id=char_id))
        if intensity > 0.75:
            sig.mood_score = _clamp_score((intensity - 0.75) / 0.25)
            details["mood"] = "此刻的情绪很满，想找她说句话"
    except Exception as exc:
        logger.debug("[overflow_bucket] mood_overflow failed: %s", exc)

    _pick_top_signal(sig, details)
    return sig
