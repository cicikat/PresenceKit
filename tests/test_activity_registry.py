"""
tests/test_activity_registry.py

Activity Registry P0-Lite — 静态结构断言。

覆盖：
 1.  registry 包含 reading / gomoku / chess / dream_seed
 2.  id 唯一
 3.  route_prefix 唯一
 4.  frontend_key 唯一
 5.  tauri_command_prefix 唯一
 6.  所有 activity: writes_short_term == False
 7.  所有 activity: writes_hidden_state == False
 8.  所有 activity: writes_event_log == False
 9.  gomoku summary_threshold == 12
10.  reading / chess summary_threshold is None
11.  gomoku has_companion_chat == True
12.  reading / chess has_companion_chat == False
13.  docs_path 对应文件存在
14.  list_enabled_activities 返回三个且均 enabled
15.  get_activity_meta 未知 id 返回 None
16.  gomoku summary_threshold 与引擎常量 SUMMARY_THRESHOLD 一致
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.activity.registry import (
    ACTIVITY_REGISTRY,
    ActivityMeta,
    MemoryPolicy,
    get_activity_meta,
    list_enabled_activities,
)

_PROJECT_ROOT = Path(__file__).parent.parent


# ── Registry completeness ──────────────────────────────────────────────────────

def test_registry_contains_reading():
    assert get_activity_meta("reading") is not None


def test_registry_contains_gomoku():
    assert get_activity_meta("gomoku") is not None


def test_registry_contains_chess():
    assert get_activity_meta("chess") is not None


def test_registry_contains_dream_seed():
    assert get_activity_meta("dream_seed") is not None


# ── Uniqueness constraints ─────────────────────────────────────────────────────

def test_ids_unique():
    ids = [m.id for m in ACTIVITY_REGISTRY]
    assert len(ids) == len(set(ids)), f"Duplicate ids: {ids}"


def test_route_prefixes_unique():
    prefixes = [m.route_prefix for m in ACTIVITY_REGISTRY]
    assert len(prefixes) == len(set(prefixes)), f"Duplicate route_prefix: {prefixes}"


def test_frontend_keys_unique():
    keys = [m.frontend_key for m in ACTIVITY_REGISTRY]
    assert len(keys) == len(set(keys)), f"Duplicate frontend_key: {keys}"


def test_tauri_command_prefixes_unique():
    prefixes = [m.tauri_command_prefix for m in ACTIVITY_REGISTRY]
    assert len(prefixes) == len(set(prefixes)), f"Duplicate tauri_command_prefix: {prefixes}"


def test_tauri_commands_unique_across_registry():
    all_cmds: list[str] = []
    for m in ACTIVITY_REGISTRY:
        all_cmds.extend(m.tauri_commands)
    assert len(all_cmds) == len(set(all_cmds)), f"Duplicate tauri commands: {all_cmds}"


# ── Memory policy: all activities must forbid main memory writes ───────────────

@pytest.mark.parametrize("activity_id", ["reading", "gomoku", "chess", "dream_seed"])
def test_writes_short_term_false(activity_id):
    meta = get_activity_meta(activity_id)
    assert meta.memory_policy.writes_short_term is False, (
        f"{activity_id}: writes_short_term must be False"
    )


@pytest.mark.parametrize("activity_id", ["reading", "gomoku", "chess", "dream_seed"])
def test_writes_hidden_state_false(activity_id):
    meta = get_activity_meta(activity_id)
    assert meta.memory_policy.writes_hidden_state is False, (
        f"{activity_id}: writes_hidden_state must be False"
    )


@pytest.mark.parametrize("activity_id", ["reading", "gomoku", "chess", "dream_seed"])
def test_writes_event_log_false(activity_id):
    meta = get_activity_meta(activity_id)
    assert meta.memory_policy.writes_event_log is False, (
        f"{activity_id}: writes_event_log must be False"
    )


# ── Per-activity summary_threshold ────────────────────────────────────────────

def test_gomoku_summary_threshold_12():
    meta = get_activity_meta("gomoku")
    assert meta.memory_policy.summary_threshold == 12


def test_reading_summary_threshold_none():
    meta = get_activity_meta("reading")
    assert meta.memory_policy.summary_threshold is None


def test_chess_summary_threshold_none():
    meta = get_activity_meta("chess")
    assert meta.memory_policy.summary_threshold is None


def test_dream_seed_summary_threshold_six():
    meta = get_activity_meta("dream_seed")
    assert meta.memory_policy.summary_threshold == 6


# ── Registry matches engine constant ──────────────────────────────────────────

def test_gomoku_summary_threshold_matches_engine_constant():
    from core.activity.gomoku import SUMMARY_THRESHOLD
    meta = get_activity_meta("gomoku")
    assert meta.memory_policy.summary_threshold == SUMMARY_THRESHOLD, (
        f"Registry declares {meta.memory_policy.summary_threshold}, "
        f"engine has SUMMARY_THRESHOLD={SUMMARY_THRESHOLD}"
    )


# ── Per-activity companion chat ────────────────────────────────────────────────

def test_gomoku_has_companion_chat():
    assert get_activity_meta("gomoku").has_companion_chat is True


def test_reading_no_companion_chat():
    assert get_activity_meta("reading").has_companion_chat is False


def test_chess_no_companion_chat():
    assert get_activity_meta("chess").has_companion_chat is False


def test_dream_seed_has_companion_chat():
    assert get_activity_meta("dream_seed").has_companion_chat is True


# ── Docs files exist ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("activity_id", ["reading", "gomoku", "chess", "dream_seed"])
def test_docs_path_exists(activity_id):
    meta = get_activity_meta(activity_id)
    path = _PROJECT_ROOT / meta.docs_path
    assert path.exists(), f"docs file not found: {path}"


# ── list_enabled_activities ────────────────────────────────────────────────────

def test_list_enabled_activities_returns_all():
    enabled = list_enabled_activities()
    ids = {m.id for m in enabled}
    assert ids == {"reading", "gomoku", "chess", "dream_seed"}


def test_list_enabled_activities_all_have_enabled_true():
    for m in list_enabled_activities():
        assert m.enabled is True, f"{m.id}.enabled must be True"


def test_list_enabled_activities_order_stable():
    enabled = list_enabled_activities()
    assert [m.id for m in enabled] == ["reading", "gomoku", "chess", "dream_seed"]


# ── get_activity_meta ──────────────────────────────────────────────────────────

def test_get_activity_meta_unknown_returns_none():
    assert get_activity_meta("unknown_xyz") is None


def test_get_activity_meta_empty_string_returns_none():
    assert get_activity_meta("") is None


@pytest.mark.parametrize("activity_id,expected_label", [
    ("reading", "一起看书"),
    ("gomoku", "五子棋"),
    ("chess", "国际象棋"),
    ("dream_seed", "梦境预构"),
])
def test_get_activity_meta_correct_label(activity_id, expected_label):
    meta = get_activity_meta(activity_id)
    assert meta.label == expected_label


# ── kind field ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("activity_id", ["reading", "gomoku", "chess", "dream_seed"])
def test_kind_is_activity(activity_id):
    meta = get_activity_meta(activity_id)
    assert meta.kind == "activity"


# ── Tauri command prefix consistency ──────────────────────────────────────────

@pytest.mark.parametrize("activity_id", ["reading", "gomoku", "chess", "dream_seed"])
def test_tauri_commands_start_with_prefix(activity_id):
    meta = get_activity_meta(activity_id)
    for cmd in meta.tauri_commands:
        assert cmd.startswith(meta.tauri_command_prefix), (
            f"{activity_id}: command {cmd!r} does not start with prefix {meta.tauri_command_prefix!r}"
        )


# ── route_prefix structure ────────────────────────────────────────────────────

@pytest.mark.parametrize("activity_id", ["reading", "gomoku", "chess", "dream_seed"])
def test_route_prefix_starts_with_activity(activity_id):
    meta = get_activity_meta(activity_id)
    assert meta.route_prefix.startswith("/activity/"), (
        f"{activity_id}: route_prefix {meta.route_prefix!r} must start with /activity/"
    )


@pytest.mark.parametrize("activity_id", ["reading", "gomoku", "chess", "dream_seed"])
def test_route_prefix_contains_activity_id(activity_id):
    meta = get_activity_meta(activity_id)
    assert f"/{activity_id}" in meta.route_prefix


# ── frontend_key matches id ───────────────────────────────────────────────────

@pytest.mark.parametrize("activity_id", ["reading", "gomoku", "chess", "dream_seed"])
def test_frontend_key_matches_id(activity_id):
    meta = get_activity_meta(activity_id)
    assert meta.frontend_key == meta.id


# ── MemoryPolicy defaults are safe ────────────────────────────────────────────

def test_memory_policy_default_forbids_all_writes():
    policy = MemoryPolicy()
    assert policy.writes_short_term is False
    assert policy.writes_hidden_state is False
    assert policy.writes_event_log is False


def test_memory_policy_default_transcript_activity_local():
    assert MemoryPolicy().transcript == "activity_local"


def test_memory_policy_default_summary_threshold_none():
    assert MemoryPolicy().summary_threshold is None


def test_memory_policy_default_main_memory_none():
    assert MemoryPolicy().main_memory == "none"
