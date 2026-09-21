"""Ticket 260 B: shared speech/music acoustic analysis fixtures.

Asserts values and quality degradation, not just field presence.
Filename suffix never selects the analyzer. Results never invent transcript text.
"""
from __future__ import annotations

import asyncio
import io
import time
import wave

import pytest

np = pytest.importorskip("numpy")

from core.audio_analysis import (
    analyze_audio,
    analyze_audio_bytes,
    deps_ready,
    in_flight_waiters,
)
from core.audio_music_contract import (
    ANALYSIS_VERSION,
    IMPRESSIONS,
    MAX_INPUT_BYTES,
    MELODY_STATUSES,
    MIN_VOICED_FRAMES_SUMMARY,
    PACE_UNKNOWN,
)


def _wav_bytes(samples, sr: int = 16000, *, channels: int = 1, width: int = 2) -> bytes:
    pcm = np.clip(np.asarray(samples, dtype=np.float64), -1.0, 1.0)
    if width == 2:
        frames = (pcm * 32767.0).astype("<i2").tobytes()
    elif width == 1:
        frames = ((pcm + 1.0) * 127.0).astype(np.uint8).tobytes()
    else:
        frames = pcm.astype("<f4").tobytes()
    if channels > 1:
        raise AssertionError("stereo helper not used")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(width)
        handle.setframerate(sr)
        handle.writeframes(frames)
    return buf.getvalue()


def _tone(freq_hz: float, duration_s: float = 1.0, sr: int = 16000, amp: float = 0.35):
    t = np.arange(int(sr * duration_s), dtype=np.float64) / sr
    return amp * np.sin(2.0 * np.pi * freq_hz * t)


def _chirp(f0: float, f1: float, duration_s: float = 1.5, sr: int = 16000, amp: float = 0.35):
    t = np.arange(int(sr * duration_s), dtype=np.float64) / sr
    phase = 2.0 * np.pi * (f0 * t + (f1 - f0) * t * t / (2.0 * duration_s))
    return amp * np.sin(phase)


def _assert_no_transcript(result: dict) -> None:
    assert "text" not in result
    assert result.get("transcript") in (None, "")
    assert result.get("stt_text") in (None, "")


def test_deps_ready_when_numpy_present():
    assert deps_ready() is True


def test_filename_suffix_never_selects_mode(sandbox):
    wav = _wav_bytes(_tone(180.0, 0.8))
    speech = analyze_audio_bytes(wav, mode="speech", filename="song.mp3", use_cache=False)
    music = analyze_audio_bytes(wav, mode="music", filename="voice.wav", use_cache=False)
    assert speech["mode"] == "speech"
    assert music["mode"] == "music"
    assert "pitch_curve" in speech
    assert "energy_curve" in music
    assert "impression" not in music
    _assert_no_transcript(speech)
    _assert_no_transcript(music)


def test_invalid_mode_and_empty_input_fail_without_text(sandbox):
    wav = _wav_bytes(_tone(180.0, 0.4))
    bad_mode = analyze_audio_bytes(wav, mode="auto", filename="clip.wav", use_cache=False)
    assert bad_mode["analysis_status"] == "failed"
    assert bad_mode["reason"] == "invalid_mode"
    _assert_no_transcript(bad_mode)
    empty = analyze_audio_bytes(b"", mode="speech", use_cache=False)
    assert empty["analysis_status"] == "failed"
    assert empty["reason"] == "empty"
    _assert_no_transcript(empty)


def test_silence_has_null_f0_never_zero_hz(sandbox):
    wav = _wav_bytes(np.zeros(16000, dtype=np.float64))
    result = analyze_audio_bytes(wav, mode="speech", use_cache=False)
    assert result["quality"] == "silent"
    assert result["analysis_status"] == "failed"
    assert result["impression"]["impression"] == "unclear"
    curve = result["pitch_curve"]
    assert curve
    assert all(row["f0_hz"] is None for row in curve)
    assert 0.0 not in {row["f0_hz"] for row in curve}
    assert result["pitch_summary"]["median_hz"] is None
    assert result["pace"]["status"] == PACE_UNKNOWN
    _assert_no_transcript(result)


def test_known_frequency_median_near_220hz(sandbox):
    wav = _wav_bytes(_tone(220.0, 1.2))
    result = analyze_audio_bytes(wav, mode="speech", use_cache=False)
    assert result["analysis_status"] == "ok"
    assert result["quality"] == "ok"
    median = result["pitch_summary"]["median_hz"]
    assert median is not None
    assert abs(median - 220.0) <= 12.0
    voiced = [row["f0_hz"] for row in result["pitch_curve"] if row["f0_hz"] is not None]
    assert len(voiced) >= MIN_VOICED_FRAMES_SUMMARY
    assert all(60.0 <= hz <= 400.0 for hz in voiced)
    assert result["energy"]["rms"] > 0.05
    assert result["energy"]["dbfs"] is not None
    assert result["energy"]["note"] == "device_gain_distance_compression"
    assert result["impression"]["impression"] in IMPRESSIONS
    assert result["impression"]["rule"] == ANALYSIS_VERSION
    _assert_no_transcript(result)


def test_rising_chirp_reports_rising_trend(sandbox):
    wav = _wav_bytes(_chirp(110.0, 280.0, duration_s=1.6))
    result = analyze_audio_bytes(wav, mode="speech", use_cache=False)
    assert result["pitch_summary"]["voiced_frames"] >= 30
    assert result["pitch_summary"]["trend"] == "rising"
    assert result["pitch_summary"]["median_hz"] is not None


def test_short_audio_is_insufficient(sandbox):
    wav = _wav_bytes(_tone(180.0, 0.08))
    result = analyze_audio_bytes(wav, mode="speech", use_cache=False)
    assert result["quality"] == "short"
    assert result["analysis_status"] == "failed"
    assert result["impression"]["impression"] == "unclear"
    assert result["pitch_summary"]["quality"] == "insufficient"


def test_clipping_is_quality_degraded(sandbox):
    samples = np.ones(16000, dtype=np.float64)
    wav = _wav_bytes(samples)
    result = analyze_audio_bytes(wav, mode="speech", use_cache=False)
    assert result["quality"] == "clipped"
    assert result["analysis_status"] in {"partial", "failed"}
    assert result["impression"]["impression"] == "unclear"


def test_noise_does_not_invent_stable_pitch(sandbox):
    rng = np.random.default_rng(7)
    wav = _wav_bytes(rng.uniform(-0.35, 0.35, 16000))
    result = analyze_audio_bytes(wav, mode="speech", use_cache=False)
    assert result["impression"]["impression"] == "unclear"
    assert all(row["f0_hz"] is None or 60.0 <= row["f0_hz"] <= 400.0 for row in result["pitch_curve"])
    if result["quality"] in {"noisy", "silent"}:
        assert result["analysis_status"] == "failed"
        assert result["pitch_summary"]["median_hz"] is None
    else:
        # White noise may yield scattered ACF peaks; they must not look like a
        # stable voiced summary or a confident impression.
        assert result["pitch_summary"]["quality"] in {"insufficient", "ok"}
        if result["pitch_summary"]["median_hz"] is not None:
            assert (result["pitch_summary"]["variation"] or 0) >= 0.08
        assert result["impression"]["quality"] in {"insufficient", "conflict"}


def test_bad_encoding_fails_without_transcript(sandbox):
    result = analyze_audio_bytes(b"ID3\x04not-a-wav", mode="speech", filename="clip.mp3", use_cache=False)
    assert result["analysis_status"] == "failed"
    assert result["reason"] in {"decode_error", "unsupported_wav"}
    assert result["pitch_curve"] == []
    _assert_no_transcript(result)
    music = analyze_audio_bytes(b"OggS????", mode="music", filename="song.ogg", use_cache=False)
    assert music["analysis_status"] == "failed"
    assert music["melody_status"] == "unavailable"
    _assert_no_transcript(music)


def test_oversize_input_is_rejected(sandbox):
    blob = b"RIFF" + b"\x00" * (MAX_INPUT_BYTES)
    result = analyze_audio_bytes(blob, mode="speech", use_cache=False)
    assert result["analysis_status"] == "failed"
    assert result["reason"] == "oversize"
    _assert_no_transcript(result)


def test_missing_numpy_is_unavailable(sandbox, monkeypatch):
    monkeypatch.setattr("core.audio_analysis.deps_ready", lambda: False)
    wav = _wav_bytes(_tone(180.0, 0.5))
    result = analyze_audio_bytes(wav, mode="speech", use_cache=False)
    assert result["analysis_status"] == "unavailable"
    assert result["reason"] == "missing_dependency"
    assert result["impression"]["impression"] == "unclear"
    _assert_no_transcript(result)


def test_music_polyphony_is_distribution_not_speech_f0(sandbox):
    t = np.arange(int(22050 * 2.0), dtype=np.float64) / 22050.0
    poly = 0.22 * (
        np.sin(2 * np.pi * 220 * t)
        + np.sin(2 * np.pi * 277 * t)
        + np.sin(2 * np.pi * 330 * t)
    )
    wav = _wav_bytes(poly, sr=22050)
    result = analyze_audio_bytes(wav, mode="music", filename="choir.wav", use_cache=False)
    assert result["mode"] == "music"
    assert result["analysis_status"] in {"ok", "partial"}
    assert result["melody_status"] in MELODY_STATUSES
    assert result["melody_status"] != "ok"
    assert "pitch_curve" not in result
    chroma = result["pitch_distribution"]["chroma"]
    assert len(chroma) == 12
    assert abs(sum(chroma) - 1.0) < 0.02
    assert result["energy_curve"]
    assert result["dynamics"]["range_db"] is not None
    assert "impression" not in result
    _assert_no_transcript(result)


def test_music_click_train_has_tempo_near_120bpm(sandbox):
    sr = 22050
    duration = 4.0
    samples = np.zeros(int(sr * duration), dtype=np.float64)
    interval = int(round(sr * 0.5))
    click = int(0.02 * sr)
    for start in range(0, samples.size, interval):
        samples[start:start + click] = 0.85
    wav = _wav_bytes(samples, sr=sr)
    result = analyze_audio_bytes(wav, mode="music", use_cache=False)
    assert result["quality"] == "ok"
    bpm = result["tempo"]["bpm"]
    assert bpm is not None
    assert 90.0 <= bpm <= 150.0
    assert result["onset_strength_mean"] > 0
    peaks = [row for row in result["energy_curve"] if row["rms"] > 0.1]
    assert len(peaks) >= 4


def test_long_music_is_partial_coverage(sandbox, monkeypatch):
    monkeypatch.setattr("core.audio_analysis.MUSIC_MAX_ANALYZE_DURATION_S", 0.8)
    wav = _wav_bytes(_tone(220.0, 3.0, sr=8000), sr=8000)
    result = analyze_audio_bytes(wav, mode="music", use_cache=False)
    assert result["analysis_status"] == "partial"
    assert result["coverage"]["ratio"] < 0.4
    assert result["coverage"]["end_s"] <= 0.85
    assert result["reason"] == "partial_coverage"


def test_timeout_is_not_cached(sandbox, monkeypatch):
    wav = _wav_bytes(_tone(180.0, 0.6))
    calls = {"n": 0}

    def slow(*args, **kwargs):
        calls["n"] += 1
        time.sleep(0.2)
        raise TimeoutError("analysis_timeout")

    monkeypatch.setattr("core.audio_analysis._decode_wav", slow)
    first = analyze_audio_bytes(wav, mode="speech", timeout_s=0.05, use_cache=True)
    assert first["analysis_status"] == "timeout"
    assert first.get("cache_hit") is False
    second = analyze_audio_bytes(wav, mode="speech", timeout_s=0.05, use_cache=True)
    assert second["analysis_status"] == "timeout"
    assert second.get("cache_hit") is not True
    assert calls["n"] == 2
    _assert_no_transcript(first)


def test_cache_key_is_content_mode_and_version(sandbox):
    wav = _wav_bytes(_tone(196.0, 0.7))
    first = analyze_audio_bytes(wav, mode="speech", use_cache=True)
    second = analyze_audio_bytes(wav, mode="speech", use_cache=True)
    assert first["analysis_status"] == "ok"
    assert second["cache_hit"] is True
    assert second["analysis_version"] == ANALYSIS_VERSION
    other_mode = analyze_audio_bytes(wav, mode="music", use_cache=True)
    assert other_mode.get("cache_hit") is not True
    assert other_mode["mode"] == "music"
    mutated = analyze_audio_bytes(wav + b"\x00", mode="speech", use_cache=True)
    assert mutated.get("cache_hit") is not True


def test_sync_timeout_returns_structured_failure(sandbox, monkeypatch):
    wav = _wav_bytes(_tone(180.0, 0.5))

    def late(*args, **kwargs):
        raise TimeoutError("analysis_timeout")

    monkeypatch.setattr("core.audio_analysis._pitch_track", late)
    result = analyze_audio_bytes(wav, mode="speech", timeout_s=0.01, use_cache=False)
    assert result["analysis_status"] == "timeout"
    assert result["quality"] == "insufficient"
    _assert_no_transcript(result)


@pytest.mark.asyncio
async def test_async_timeout_fail_open(sandbox, monkeypatch):
    wav = _wav_bytes(_tone(180.0, 0.5))

    def blocked(*args, **kwargs):
        time.sleep(1.0)
        return {"mode": "speech", "analysis_status": "ok"}

    monkeypatch.setattr("core.audio_analysis.analyze_audio_bytes", blocked)
    result = await analyze_audio(wav, mode="speech", timeout_s=0.05, use_cache=False)
    assert result["analysis_status"] == "timeout"
    _assert_no_transcript(result)


@pytest.mark.asyncio
async def test_analysis_concurrency_is_one(sandbox, monkeypatch):
    wav = _wav_bytes(_tone(180.0, 0.4))
    original = analyze_audio_bytes
    running = 0
    max_running = 0

    def wrapped(*args, **kwargs):
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        try:
            time.sleep(0.08)
            return original(*args, **kwargs)
        finally:
            running -= 1

    monkeypatch.setattr("core.audio_analysis.analyze_audio_bytes", wrapped)
    first, second = await asyncio.gather(
        analyze_audio(wav, mode="speech", use_cache=False),
        analyze_audio(wav, mode="speech", use_cache=False),
    )
    assert max_running == 1
    assert in_flight_waiters() == 0
    assert first["mode"] == "speech"
    assert second["mode"] == "speech"



