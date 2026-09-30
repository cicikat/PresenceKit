"""Ticket 260 A: frozen audio/music schema, budget, scope, counts and host inventory."""
from pathlib import Path

from core.audio_music_contract import (
    ADAPTER_EVENTS,
    ANALYSIS_MODES,
    ANALYSIS_VERSION,
    ANALYSIS_VERSION_V0,
    AUDIO_ACCESS,
    AUTONOMY_SIGNAL_SOURCE,
    COMMAND_OUTCOMES,
    CONFIG_ROOT,
    CONTRACT_VERSION,
    CONTRACT_VERSION_V0,
    COUNT_FIELDS,
    DELETION_AUTHORIZED,
    DELETION_CANDIDATES,
    DEFAULT_SWITCHES,
    FEATURE_SWITCHES,
    HOST_INVENTORY,
    IMPRESSIONS,
    IMPRESSIONS_V0,
    IMPRESSIONS_V1_ADDED,
    INDEPENDENT_DESKTOP_ACTIONS,
    LEGAL_TRANSITIONS,
    LISTEN_THRESHOLD_VERSION,
    MAX_CONCURRENT_ANALYSIS,
    MAX_INPUT_BYTES,
    MELODY_STATUSES,
    MIN_VOICED_FRAMES_SUMMARY,
    OPTIONAL_ADAPTER_ACTIONS,
    PACE_UNKNOWN,
    PLAYER_ADAPTER_VERSION,
    PLAYBACK_STATES,
    PLANNED_TOOLS,
    PROMPT_LAYER,
    REQUIRED_ADAPTER_ACTIONS,
    SCOPE_PLAN,
    SPEECH_ANALYSIS_TIMEOUT_S,
    UNVOICED_F0,
    conservative_impression,
    conservative_impression_v0,
    is_legal_transition,
    listen_threshold_seconds,
    pitch_point,
    pitch_summary,
    should_count_listen,
    stt_configured_is_not_analysis_available,
)
from core.data_paths import DataPaths
from core.data_registry import REGISTRY, RETENTION_POLICY


def test_contract_versions_and_modes():
    # v1 (video-call work order E) is an additive contract change; v0 constants stay.
    assert CONTRACT_VERSION == "audio-music-perception.v1"
    assert ANALYSIS_VERSION == "audio-analysis.v1"
    assert CONTRACT_VERSION_V0 == "audio-music-perception.v0"
    assert ANALYSIS_VERSION_V0 == "audio-analysis.v0"
    assert LISTEN_THRESHOLD_VERSION == "listen-threshold.v0"
    assert PLAYER_ADAPTER_VERSION == "player-adapter.v0"
    assert ANALYSIS_MODES == {"speech", "music"}
    assert IMPRESSIONS_V0 == {"calm", "tired", "bright", "tense", "unclear"}
    assert IMPRESSIONS_V1_ADDED == {"unsteady", "breathy", "low_toned"}
    assert IMPRESSIONS == IMPRESSIONS_V0 | IMPRESSIONS_V1_ADDED  # nothing removed or renamed
    # Deliberately unsupported: no baseline / formant / jitter features exist.
    assert not IMPRESSIONS & {"crying", "pinched", "tight", "sad"}
    assert PROMPT_LAYER == "3.8_audio_impression"
    assert CONFIG_ROOT == "audio_music"
    assert FEATURE_SWITCHES == (
        "speech_analysis", "music_analysis", "music_control", "music_autonomy",
    )
    assert DEFAULT_SWITCHES == {name: False for name in FEATURE_SWITCHES}


def test_unvoiced_pitch_is_null_not_zero():
    assert UNVOICED_F0 is None
    silent = pitch_point(0.12, 0.0, 0.0)
    assert silent["f0_hz"] is None
    trusted = pitch_point(0.24, 180.0, 0.8)
    assert trusted["f0_hz"] == 180.0
    out_of_band = pitch_point(0.36, 12.0, 0.9)
    assert out_of_band["f0_hz"] is None


def test_pitch_summary_requires_voiced_frames_and_units():
    empty = pitch_summary([pitch_point(i * 0.01, None, 0.0) for i in range(20)])
    assert empty["quality"] == "insufficient"
    assert empty["median_hz"] is None
    voiced = [
        pitch_point(i * 0.01, 160.0 + i, 0.7) for i in range(MIN_VOICED_FRAMES_SUMMARY)
    ]
    summary = pitch_summary(voiced)
    assert summary["quality"] == "ok"
    assert summary["median_hz"] is not None
    assert summary["units"]["median"] == "Hz"
    assert summary["algorithm"] == ANALYSIS_VERSION
    assert summary["trend"] == PACE_UNKNOWN


def test_impression_is_conservative_and_ignores_provider_override():
    tired = conservative_impression(
        quality="ok", median_hz=120.0, variation=0.05, pace=1.6,
        energy_dbfs=-28.0, provider_tone="bright",
    )
    # v1: slow + low + not-quiet is the weaker, literal "low_toned"; "tired" needs quiet too.
    assert tired["impression"] == "low_toned"
    assert tired["provider_tone_hint"] == "bright"
    quiet = conservative_impression(
        quality="ok", median_hz=120.0, variation=0.05, pace=1.6, energy_dbfs=-40.0,
    )
    assert quiet["impression"] == "tired"
    # The frozen v0 mapping is untouched and still answers with v0 semantics.
    v0 = conservative_impression_v0(
        quality="ok", median_hz=120.0, variation=0.05, pace=1.6, energy_dbfs=-28.0,
    )
    assert v0["impression"] == "tired" and v0["rule"] == ANALYSIS_VERSION_V0
    pitch_only = conservative_impression(
        quality="ok", median_hz=120.0, variation=0.05, pace=3.5,
        energy_dbfs=-20.0,
    )
    assert pitch_only["impression"] == "unclear"
    failed = conservative_impression(
        quality="failed", median_hz=180.0, variation=0.04, pace=3.0,
        energy_dbfs=-20.0, provider_tone="calm",
    )
    assert failed["impression"] == "unclear"
    assert failed["provider_tone_hint"] == "calm"


def test_listen_count_threshold_and_idempotence():
    assert listen_threshold_seconds(None) == 30.0
    assert listen_threshold_seconds(0) == 30.0
    assert listen_threshold_seconds(40) == 20.0
    assert listen_threshold_seconds(120) == 30.0
    assert should_count_listen(
        accumulated_play_s=20.0, duration_s=40.0, already_counted=False,
    )
    assert not should_count_listen(
        accumulated_play_s=19.9, duration_s=40.0, already_counted=False,
    )
    assert not should_count_listen(
        accumulated_play_s=40.0, duration_s=40.0, already_counted=True,
    )
    assert COUNT_FIELDS == ("started_count", "listen_count", "completed_count")


def test_playback_state_machine_and_adapter_envelope():
    assert "playing" in PLAYBACK_STATES
    assert is_legal_transition("playing", "paused")
    assert is_legal_transition("playing", "ended")
    assert is_legal_transition("paused", "playing")
    assert not is_legal_transition("idle", "playing")
    assert not is_legal_transition("ended", "paused")
    for src, dst in LEGAL_TRANSITIONS:
        assert src in PLAYBACK_STATES and dst in PLAYBACK_STATES
    assert REQUIRED_ADAPTER_ACTIONS >= {
        "capabilities", "get_state", "play", "pause", "resume", "stop", "set_queue", "next",
    }
    assert "seek" in OPTIONAL_ADAPTER_ACTIONS
    assert ADAPTER_EVENTS >= {"started", "changed", "paused", "resumed", "finished", "stopped", "error", "progress"}
    assert COMMAND_OUTCOMES == {"accepted", "rejected", "confirmed", "failed", "outcome_unknown"}
    assert "local_playable_backend_unread" in AUDIO_ACCESS
    assert "unknown" in MELODY_STATUSES


def test_host_inventory_forbids_mock_and_keeps_old_actions():
    assert HOST_INVENTORY["reusable_first_party_player"] is False
    assert HOST_INVENTORY["standalone_player_demo_started"] is False
    assert HOST_INVENTORY["netease_or_media_key_is_adapter"] is False
    assert HOST_INVENTORY["tts_playback_queue_is_music_host"] is False
    assert HOST_INVENTORY["phone_player_in_v0"] is False
    assert HOST_INVENTORY["mock_adapter_closes_e"] is False
    assert HOST_INVENTORY["first_host"] == "minimal_first_party_desktop_player"
    assert INDEPENDENT_DESKTOP_ACTIONS == {"media_play_pause", "play_netease"}
    assert DELETION_AUTHORIZED is False
    assert "provider_tone_as_final_impression" in DELETION_CANDIDATES


def test_scopes_reuse_existing_tokens_and_keep_switches_off():
    assert SCOPE_PLAN["settings"] == "admin"
    assert SCOPE_PLAN["analysis_and_adapter_metadata"] == "state.read"
    assert SCOPE_PLAN["listening_history"] == "memory.read"
    assert SCOPE_PLAN["character_track_notes"] == "memory.read"
    assert SCOPE_PLAN["transcribe_and_receipts"] == "chat"
    assert SCOPE_PLAN["player_host_transport"] == "ws.desktop"
    assert SCOPE_PLAN["new_scope"] is None
    assert stt_configured_is_not_analysis_available(
        stt_effective=True, speech_analysis_enabled=False, analysis_deps_ready=True,
    )
    assert MAX_CONCURRENT_ANALYSIS == 1
    assert SPEECH_ANALYSIS_TIMEOUT_S <= 3.0
    assert MAX_INPUT_BYTES == 25 * 1024 * 1024
    assert AUTONOMY_SIGNAL_SOURCE == "music_playback"
    assert PLANNED_TOOLS[-1] == "choose_next_track"


def test_sandbox_listening_paths_and_taxonomy():
    paths = DataPaths(mode="test", test_session_id="audio_music_a")
    root = paths.listening_root("owner")
    assert root.as_posix().endswith("runtime/listening/owner")
    assert paths.music_library_db("owner").name == "library.sqlite3"
    assert paths.listening_session("owner").name == "session.json"
    assert paths.listening_history_db("owner").name == "history.sqlite3"
    assert paths.listening_stats_db("owner").name == "stats.sqlite3"
    assert paths.music_audio_blob_dir("owner").name == "audio"
    notes = paths.character_track_notes("owner", char_id="fixture_character")
    assert "characters/fixture_character/listening/owner" in notes.as_posix().replace("\\", "/")
    cache = paths.audio_analysis_cache_dir()
    assert cache.as_posix().endswith("cache/audio_analysis")
    for name in (
        "listening_root", "music_library_db", "listening_session",
        "listening_history_db", "listening_stats_db", "music_audio_blob_dir",
        "character_track_notes", "audio_analysis_cache_dir",
    ):
        assert name in REGISTRY
    assert REGISTRY["listening_history_db"].durability == "canonical"
    assert REGISTRY["listening_stats_db"].durability == "derived"
    assert REGISTRY["character_track_notes"].domain == "character_inner"
    assert REGISTRY["character_track_notes"].scope == "per_char_user"
    assert "listening_history_db" in RETENTION_POLICY
    assert "audio_analysis_cache_dir" in RETENTION_POLICY


def test_sandbox_paths_stay_under_test_prefix():
    paths = DataPaths(mode="test", test_session_id="audio_music_a")
    target = paths.listening_root("owner")
    assert "test_sandbox/audio_music_a" in target.as_posix().replace("\\", "/")
    assert target.resolve().is_relative_to(paths.root_dir().resolve())


def _v1(**kw):
    base = dict(quality="ok", median_hz=180.0, variation=0.12, pace=3.0, energy_dbfs=-25.0)
    base.update(kw)
    return conservative_impression(**base)


def test_unsteady_needs_wide_variation_and_a_non_linear_contour():
    assert _v1(variation=0.30, linear_r2=0.2)["impression"] == "unsteady"
    assert _v1(variation=0.29, linear_r2=0.2)["impression"] != "unsteady"      # just under
    assert _v1(variation=0.40, linear_r2=0.5)["impression"] != "unsteady"      # a steady ramp, not a wobble
    assert _v1(variation=0.40, linear_r2=None)["impression"] != "unsteady"     # feature missing: no label
    assert _v1(variation=0.40, linear_r2=0.49)["impression"] == "unsteady"


def test_breathy_needs_high_frequency_share_over_threshold():
    assert _v1(hf_ratio=0.16)["impression"] == "breathy"
    assert _v1(hf_ratio=0.15)["impression"] != "breathy"                        # boundary is exclusive
    assert _v1(hf_ratio=0.04)["impression"] != "breathy"
    assert _v1(hf_ratio=None)["impression"] != "breathy"                        # budget skipped: no label


def test_low_toned_is_absolute_and_never_claims_a_baseline():
    quiet_enough = _v1(median_hz=120.0, variation=0.05, pace=1.6, energy_dbfs=-28.0)
    assert quiet_enough["impression"] == "low_toned"
    assert _v1(median_hz=150.0, variation=0.05, pace=1.6)["impression"] != "low_toned"  # not low enough
    assert _v1(median_hz=120.0, variation=0.05, pace=3.5)["impression"] != "low_toned"  # not slow
    import core.audio_perception as perception
    gloss = perception.TONE_GLOSS["low_toned"]
    assert "比平时" not in gloss and "没有个人基线" in gloss


def test_conflicting_criteria_fall_back_to_unclear_never_a_list():
    both = _v1(variation=0.35, linear_r2=0.1, hf_ratio=0.25)
    assert both["impression"] == "unclear" and both["quality"] == "conflict"
    mixed = _v1(median_hz=120.0, variation=0.05, pace=1.6, hf_ratio=0.25)  # low_toned + breathy
    assert mixed["impression"] == "unclear" and mixed["quality"] == "conflict"
    assert isinstance(both["impression"], str)


def test_failed_quality_still_wins_over_new_features():
    failed = conservative_impression(
        quality="failed", median_hz=180.0, variation=0.4, pace=3.0, energy_dbfs=-20.0,
        linear_r2=0.0, hf_ratio=0.5, provider_tone="breathy",
    )
    assert failed["impression"] == "unclear"
    assert failed["provider_tone_hint"] == "breathy"   # hint recorded, never promoted


def test_a_specific_voice_quality_finding_replaces_calm_but_not_substantive_labels():
    # Steady, moderate voice (v0 "calm") with breathy noise: the specific label wins.
    calm = _v1(variation=0.05, pace=3.0, energy_dbfs=-25.0)
    assert calm["impression"] == "calm"
    assert _v1(variation=0.05, pace=3.0, energy_dbfs=-25.0, hf_ratio=0.25)["impression"] == "breathy"
    # A substantive v0 label still conflicts with a new one -> unclear, not a list.
    tense = _v1(variation=0.30, pace=5.0, energy_dbfs=-10.0, hf_ratio=0.25)
    assert tense["impression"] == "unclear" and tense["quality"] == "conflict"
