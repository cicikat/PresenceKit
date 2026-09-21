"""Shared speech/music acoustic analysis for ticket 260 B.

Decode, framing, quality and budgets are shared. Callers must pass an explicit
``speech`` or ``music`` mode; the filename suffix never selects the analyzer.
v0 decodes PCM WAV via the stdlib ``wave`` module and already-locked numpy.
mp3/ogg/flac and other encodings return ``decode_error``; missing numpy,
budget failures and timeouts return a structured result and never invent
transcript text. Prompt injection and the speech receipt chain remain stage C.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import time
import wave
from typing import Any

from core.audio_music_contract import (
    ANALYSIS_MODES,
    ANALYSIS_VERSION,
    MAX_CONCURRENT_ANALYSIS,
    MAX_DECODE_MEMORY_BYTES,
    MAX_INPUT_BYTES,
    MIN_VOICED_FRAMES_SUMMARY,
    MUSIC_ANALYSIS_TIMEOUT_S,
    MUSIC_MAX_ANALYZE_DURATION_S,
    PACE_METHOD,
    PACE_UNIT,
    PACE_UNKNOWN,
    PITCH_F0_MAX_HZ,
    PITCH_F0_MIN_HZ,
    PITCH_FRAME_MS,
    PITCH_HOP_MS,
    SPEECH_ANALYSIS_TIMEOUT_S,
    SPEECH_MAX_DURATION_S,
    TARGET_MUSIC_SAMPLE_RATE,
    TARGET_SPEECH_SAMPLE_RATE,
    conservative_impression,
    pitch_point,
    pitch_summary,
)

logger = logging.getLogger(__name__)

_ANALYSIS_LOCK = asyncio.Lock()
_WAITERS = 0


def deps_ready() -> bool:
    try:
        import numpy  # noqa: F401
    except Exception:
        return False
    return True


def _numpy():
    import numpy as np
    return np


def _empty_result(mode: str, status: str, *, quality: str, reason: str, elapsed_ms: float = 0.0) -> dict[str, Any]:
    base = {
        "mode": mode if mode in ANALYSIS_MODES else "speech",
        "analysis_status": status,
        "analysis_version": ANALYSIS_VERSION,
        "quality": quality,
        "reason": reason,
        "elapsed_ms": elapsed_ms,
        "coverage": {"start_s": 0.0, "end_s": 0.0, "ratio": 0.0},
    }
    if base["mode"] == "speech":
        base.update({
            "pitch_curve": [],
            "pitch_summary": pitch_summary([]),
            "energy": {"rms": 0.0, "dbfs": None, "note": "device_gain_distance_compression"},
            "pace": {"value": None, "unit": PACE_UNIT, "method": PACE_METHOD, "status": PACE_UNKNOWN},
            "voiced_ratio": {"value": 0.0, "voiced_frames": 0, "total_frames": 0, "duration_s": 0.0},
            "impression": conservative_impression(
                quality=quality, median_hz=None, variation=None, pace=None, energy_dbfs=None,
            ),
        })
    else:
        base.update({
            "melody_status": "unavailable",
            "energy_curve": [],
            "dynamics": {"range_db": None, "crest": None},
            "onset_strength_mean": None,
            "tempo": {"bpm": None, "confidence": 0.0, "candidates": [], "method": "onset_acf"},
            "pitch_distribution": {"chroma": [0.0] * 12, "method": "stft_chroma"},
        })
    return base


def _content_key(data: bytes, mode: str) -> str:
    digest = hashlib.sha256(data).hexdigest()
    return f"{digest[:40]}-{mode}-{ANALYSIS_VERSION}"


def _cache_path(key: str):
    from core.sandbox import get_paths
    return get_paths().audio_analysis_cache_dir() / f"{key}.json"


def _read_cache(key: str) -> dict[str, Any] | None:
    path = _cache_path(key)
    try:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("analysis_version") != ANALYSIS_VERSION:
        return None
    return payload


def _write_cache(key: str, payload: dict[str, Any]) -> None:
    if payload.get("analysis_status") == "timeout":
        return
    path = _cache_path(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8", newline="\n")
        tmp.replace(path)
    except Exception:
        logger.debug("[audio_analysis] cache write skipped", exc_info=True)


def _decode_wav(data: bytes, *, target_rate: int, max_duration_s: float, deadline: float) -> tuple[Any, int, float, float]:
    """Return (mono float32, sr, source_duration_s, analyzed_duration_s)."""
    _check_deadline(deadline)
    np = _numpy()
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            src_rate = handle.getframerate()
            nframes = handle.getnframes()
            comptype = handle.getcomptype()
            if channels < 1 or src_rate < 1000 or width not in {1, 2, 4} or comptype not in {"NONE", "not compressed"}:
                raise ValueError("unsupported_wav")
            source_duration = nframes / float(src_rate)
            keep_frames = min(nframes, int(math.ceil(max_duration_s * src_rate)))
            if keep_frames * channels * width > MAX_DECODE_MEMORY_BYTES:
                raise ValueError("memory_exceeded")
            raw = handle.readframes(keep_frames)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("decode_error") from exc
    _check_deadline(deadline)
    if width == 2:
        pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 1:
        pcm = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        pcm = np.frombuffer(raw, dtype="<f4").copy()
    if channels > 1:
        usable = (pcm.size // channels) * channels
        pcm = pcm[:usable].reshape(-1, channels).mean(axis=1)
    if pcm.nbytes > MAX_DECODE_MEMORY_BYTES:
        raise ValueError("memory_exceeded")
    samples = _resample(pcm, src_rate, target_rate)
    if samples.nbytes > MAX_DECODE_MEMORY_BYTES:
        raise ValueError("memory_exceeded")
    analyzed = len(samples) / float(target_rate)
    return samples, target_rate, source_duration, analyzed


def _resample(samples: Any, src_rate: int, dst_rate: int) -> Any:
    np = _numpy()
    if src_rate == dst_rate or samples.size == 0:
        return samples.astype(np.float32, copy=False)
    n = max(1, int(round(samples.size * dst_rate / src_rate)))
    src_t = np.linspace(0.0, 1.0, samples.size, endpoint=False)
    dst_t = np.linspace(0.0, 1.0, n, endpoint=False)
    return np.interp(dst_t, src_t, samples.astype(np.float32)).astype(np.float32)


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("analysis_timeout")


def _rms(samples: Any) -> float:
    np = _numpy()
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))


def _dbfs(rms: float) -> float | None:
    if rms <= 0:
        return None
    return 20.0 * math.log10(max(rms, 1e-12))


def _frame_signal(samples: Any, sr: int, frame_ms: int, hop_ms: int) -> Any:
    np = _numpy()
    frame = max(1, int(sr * frame_ms / 1000))
    hop = max(1, int(sr * hop_ms / 1000))
    if samples.size < frame:
        return np.empty((0, frame), dtype=np.float32)
    starts = np.arange(0, samples.size - frame + 1, hop)
    return np.stack([samples[i:i + frame] for i in starts]).astype(np.float32)


def _pitch_track(samples: Any, sr: int, deadline: float) -> list[dict[str, Any]]:
    np = _numpy()
    frames = _frame_signal(samples, sr, PITCH_FRAME_MS, PITCH_HOP_MS)
    hop = max(1, int(sr * PITCH_HOP_MS / 1000))
    window = np.hanning(frames.shape[1]).astype(np.float32) if frames.size else None
    min_lag = max(2, int(sr / PITCH_F0_MAX_HZ))
    max_lag = min(frames.shape[1] - 1 if frames.size else 2, int(sr / PITCH_F0_MIN_HZ))
    points: list[dict[str, Any]] = []
    for index, frame in enumerate(frames):
        if index % 32 == 0:
            _check_deadline(deadline)
        centered = frame - float(frame.mean())
        windowed = centered * window
        corr = np.correlate(windowed, windowed, mode="full")
        mid = corr.size // 2
        segment = corr[mid + min_lag:mid + max_lag + 1]
        energy = float(corr[mid]) if corr.size else 0.0
        if segment.size == 0 or energy <= 1e-12:
            points.append(pitch_point(index * hop / sr, None, 0.0))
            continue
        lag = int(segment.argmax()) + min_lag
        peak = float(segment.max())
        voicing = max(0.0, min(1.0, peak / energy))
        f0 = sr / lag if voicing >= 0.35 else None
        points.append(pitch_point(index * hop / sr, f0, voicing))
    return points


def _pace(points: list[dict[str, Any]], duration_s: float) -> dict[str, Any]:
    voiced = [row for row in points if row.get("f0_hz") is not None]
    if len(voiced) < MIN_VOICED_FRAMES_SUMMARY or duration_s <= 0:
        return {"value": None, "unit": PACE_UNIT, "method": PACE_METHOD, "status": PACE_UNKNOWN}
    onsets = 0
    prev = False
    for row in points:
        voiced_now = row.get("f0_hz") is not None and (row.get("voicing") or 0) >= 0.45
        if voiced_now and not prev:
            onsets += 1
        prev = voiced_now
    return {
        "value": onsets / duration_s,
        "unit": PACE_UNIT,
        "method": PACE_METHOD,
        "status": "ok",
    }


def _quality(samples: Any, duration_s: float, voiced_frames: int, total_frames: int) -> str:
    np = _numpy()
    if duration_s < 0.25 or total_frames < MIN_VOICED_FRAMES_SUMMARY:
        return "short"
    rms = _rms(samples)
    if rms < 0.004:
        return "silent"
    clip_frac = float(np.mean(np.abs(samples) >= 0.98))
    if clip_frac > 0.02:
        return "clipped"
    if voiced_frames == 0:
        return "noisy" if rms > 0.02 else "silent"
    return "ok"


def _speech_result(samples: Any, sr: int, source_duration: float, analyzed: float, deadline: float) -> dict[str, Any]:
    points = _pitch_track(samples, sr, deadline)
    summary = pitch_summary(points)
    voiced_frames = int(summary["voiced_frames"])
    total_frames = len(points)
    quality = _quality(samples, analyzed, voiced_frames, total_frames)
    if quality != "ok":
        summary = {**summary, "quality": "insufficient" if quality != "clipped" else "insufficient"}
        if quality in {"silent", "short", "noisy"}:
            summary.update(median_hz=None, range_hz=None, variation=None, trend=PACE_UNKNOWN)
    rms = _rms(samples)
    dbfs = _dbfs(rms)
    pace = _pace(points, analyzed)
    impression = conservative_impression(
        quality="ok" if quality == "ok" else quality,
        median_hz=summary.get("median_hz"),
        variation=summary.get("variation"),
        pace=pace.get("value"),
        energy_dbfs=dbfs,
    )
    ratio = 0.0 if source_duration <= 0 else min(1.0, analyzed / source_duration)
    status = "ok" if quality == "ok" else "partial" if quality == "clipped" and voiced_frames else "failed"
    if quality in {"silent", "short", "noisy"}:
        status = "failed"
        impression = conservative_impression(
            quality="insufficient", median_hz=None, variation=None, pace=None, energy_dbfs=dbfs,
        )
    return {
        "mode": "speech",
        "analysis_status": status,
        "analysis_version": ANALYSIS_VERSION,
        "quality": quality,
        "reason": quality if status != "ok" else "",
        "duration_s": source_duration,
        "sample_rate": sr,
        "coverage": {"start_s": 0.0, "end_s": analyzed, "ratio": ratio},
        "pitch_curve": points,
        "pitch_summary": summary,
        "energy": {
            "rms": rms,
            "dbfs": dbfs,
            "note": "device_gain_distance_compression",
        },
        "pace": pace,
        "voiced_ratio": {
            "value": 0.0 if total_frames == 0 else voiced_frames / total_frames,
            "voiced_frames": voiced_frames,
            "total_frames": total_frames,
            "duration_s": analyzed,
        },
        "impression": impression,
    }


def _stft_mags(samples: Any, sr: int, n_fft: int, hop: int, deadline: float) -> Any:
    np = _numpy()
    if samples.size < n_fft:
        return np.empty((0, n_fft // 2 + 1), dtype=np.float32)
    window = np.hanning(n_fft).astype(np.float32)
    starts = np.arange(0, samples.size - n_fft + 1, hop)
    rows = []
    for index, start in enumerate(starts):
        if index % 16 == 0:
            _check_deadline(deadline)
        frame = samples[start:start + n_fft] * window
        spec = np.fft.rfft(frame, n=n_fft)
        rows.append(np.abs(spec).astype(np.float32))
    return np.stack(rows)


def _chroma(mags: Any, sr: int, n_fft: int) -> list[float]:
    np = _numpy()
    bins = np.zeros(12, dtype=np.float64)
    if mags.size == 0:
        return [0.0] * 12
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    mean = mags.mean(axis=0)
    for index, freq in enumerate(freqs):
        if freq < 50 or freq > 5000:
            continue
        midi = 69 + 12 * math.log2(freq / 440.0)
        bins[int(round(midi)) % 12] += float(mean[index])
    total = float(bins.sum())
    if total <= 0:
        return [0.0] * 12
    return [float(x / total) for x in bins]


def _tempo(onset: Any, hop_s: float) -> dict[str, Any]:
    np = _numpy()
    if onset.size < 8:
        return {"bpm": None, "confidence": 0.0, "candidates": [], "method": "onset_acf"}
    centered = onset - float(onset.mean())
    corr = np.correlate(centered, centered, mode="full")
    mid = corr.size // 2
    acf = corr[mid:]
    min_lag = max(1, int(round(60.0 / 200.0 / hop_s)))
    max_lag = min(acf.size - 1, int(round(60.0 / 40.0 / hop_s)))
    if max_lag <= min_lag:
        return {"bpm": None, "confidence": 0.0, "candidates": [], "method": "onset_acf"}
    region = acf[min_lag:max_lag + 1]
    lag = int(region.argmax()) + min_lag
    peak = float(region.max())
    energy = float(acf[0]) if acf.size else 0.0
    confidence = 0.0 if energy <= 0 else max(0.0, min(1.0, peak / energy))
    bpm = 60.0 / (lag * hop_s)
    candidates = [{"bpm": bpm, "confidence": confidence}]
    return {"bpm": bpm if confidence >= 0.15 else None, "confidence": confidence, "candidates": candidates, "method": "onset_acf"}


def _music_result(samples: Any, sr: int, source_duration: float, analyzed: float, deadline: float) -> dict[str, Any]:
    np = _numpy()
    hop = max(1, int(sr * 0.05))
    n_fft = 1024
    frames = _frame_signal(samples, sr, 50, 50)
    energy_curve = []
    for index, frame in enumerate(frames):
        rms = _rms(frame)
        energy_curve.append({"t": index * 0.05, "rms": rms, "dbfs": _dbfs(rms)})
    levels = [row["dbfs"] for row in energy_curve if row["dbfs"] is not None]
    range_db = (max(levels) - min(levels)) if levels else None
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    rms = _rms(samples)
    crest = None if rms <= 0 else peak / rms
    mags = _stft_mags(samples, sr, n_fft, hop, deadline)
    if mags.shape[0] >= 2:
        flux = np.maximum(mags[1:] - mags[:-1], 0).sum(axis=1)
        onset_mean = float(flux.mean()) if flux.size else 0.0
        tempo = _tempo(flux.astype(np.float32), hop / sr)
    else:
        onset_mean = 0.0
        tempo = {"bpm": None, "confidence": 0.0, "candidates": [], "method": "onset_acf"}
    chroma = _chroma(mags, sr, n_fft)
    chroma_peak = max(chroma) if chroma else 0.0
    # Polyphony and mixed spectra stay distribution-only; a concentrated bin is
    # still not a reliable sung melody, so the status is unknown — never a
    # speech-style F0 curve pretending to be the track's melody.
    melody = "distribution_only" if chroma_peak < 0.45 else "unknown"
    quality = _quality(samples, analyzed, 1 if rms > 0.004 else 0, max(1, len(energy_curve)))
    ratio = 0.0 if source_duration <= 0 else min(1.0, analyzed / source_duration)
    status = "ok"
    if quality in {"silent", "short"}:
        status = "failed"
        melody = "unavailable"
    elif ratio < 0.999:
        status = "partial"
    return {
        "mode": "music",
        "analysis_status": status,
        "analysis_version": ANALYSIS_VERSION,
        "quality": quality,
        "reason": "" if status == "ok" else ("partial_coverage" if status == "partial" else quality),
        "duration_s": source_duration,
        "sample_rate": sr,
        "coverage": {"start_s": 0.0, "end_s": analyzed, "ratio": ratio},
        "melody_status": melody,
        "energy_curve": energy_curve,
        "dynamics": {"range_db": range_db, "crest": crest},
        "onset_strength_mean": onset_mean,
        "tempo": tempo,
        "pitch_distribution": {"chroma": chroma, "method": "stft_chroma"},
    }


def analyze_audio_bytes(
    data: bytes,
    *,
    mode: str,
    filename: str = "audio.wav",
    timeout_s: float | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Synchronous analyzer. ``filename`` is metadata only and never selects mode."""
    started = time.monotonic()
    mode = str(mode or "")
    if mode not in ANALYSIS_MODES:
        result = _empty_result(mode, "failed", quality="insufficient", reason="invalid_mode")
        result["elapsed_ms"] = (time.monotonic() - started) * 1000
        return result
    if not isinstance(data, (bytes, bytearray)) or not data:
        result = _empty_result(mode, "failed", quality="insufficient", reason="empty")
        result["elapsed_ms"] = (time.monotonic() - started) * 1000
        return result
    if len(data) > MAX_INPUT_BYTES:
        result = _empty_result(mode, "failed", quality="insufficient", reason="oversize")
        result["elapsed_ms"] = (time.monotonic() - started) * 1000
        return result
    if not deps_ready():
        result = _empty_result(mode, "unavailable", quality="insufficient", reason="missing_dependency")
        result["elapsed_ms"] = (time.monotonic() - started) * 1000
        return result
    timeout = SPEECH_ANALYSIS_TIMEOUT_S if mode == "speech" else MUSIC_ANALYSIS_TIMEOUT_S
    if timeout_s is not None:
        timeout = float(timeout_s)
    deadline = started + timeout
    key = _content_key(bytes(data), mode)
    if use_cache:
        cached = _read_cache(key)
        if cached is not None:
            cached = dict(cached)
            cached["elapsed_ms"] = (time.monotonic() - started) * 1000
            cached["cache_hit"] = True
            return cached
    try:
        target = TARGET_SPEECH_SAMPLE_RATE if mode == "speech" else TARGET_MUSIC_SAMPLE_RATE
        max_dur = SPEECH_MAX_DURATION_S if mode == "speech" else MUSIC_MAX_ANALYZE_DURATION_S
        samples, sr, source_duration, analyzed = _decode_wav(
            bytes(data), target_rate=target, max_duration_s=max_dur, deadline=deadline,
        )
        if mode == "speech":
            result = _speech_result(samples, sr, source_duration, analyzed, deadline)
        else:
            result = _music_result(samples, sr, source_duration, analyzed, deadline)
    except TimeoutError:
        result = _empty_result(mode, "timeout", quality="insufficient", reason="timeout")
    except ValueError as exc:
        reason = str(exc) or "decode_error"
        result = _empty_result(mode, "failed" if reason != "missing_dependency" else "unavailable",
                               quality="insufficient", reason=reason)
    except Exception:
        logger.debug("[audio_analysis] unexpected failure", exc_info=True)
        result = _empty_result(mode, "failed", quality="insufficient", reason="internal")
    result["elapsed_ms"] = (time.monotonic() - started) * 1000
    result["cache_hit"] = False
    result["source_filename"] = str(filename or "")
    if use_cache:
        _write_cache(key, {k: v for k, v in result.items() if k != "elapsed_ms"})
    return result


async def analyze_audio(
    data: bytes,
    *,
    mode: str,
    filename: str = "audio.wav",
    timeout_s: float | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Bounded async entry: one in-flight analysis, hard timeout, fail-open."""
    global _WAITERS
    timeout = SPEECH_ANALYSIS_TIMEOUT_S if mode == "speech" else MUSIC_ANALYSIS_TIMEOUT_S
    if timeout_s is not None:
        timeout = float(timeout_s)
    _WAITERS += 1
    try:
        async with _ANALYSIS_LOCK:
            if MAX_CONCURRENT_ANALYSIS != 1:
                raise RuntimeError("analysis concurrency contract is 1")
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        analyze_audio_bytes, data, mode=mode, filename=filename,
                        timeout_s=timeout, use_cache=use_cache,
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                result = _empty_result(mode if mode in ANALYSIS_MODES else "speech", "timeout",
                                       quality="insufficient", reason="timeout")
                result["elapsed_ms"] = timeout * 1000
                return result
    finally:
        _WAITERS -= 1


def in_flight_waiters() -> int:
    return _WAITERS
