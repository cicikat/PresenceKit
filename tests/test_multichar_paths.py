"""
多角色路径铺线验证测试（S4 + S5）

S4: 断言 char_id="yexuan" + layout=legacy 产出的路径与改前旧路径逐字节一致。
S5: _LAYOUT_CHARACTER_INNER 已翻至 v1；断言 v1 路径落在 characters/{char_id}/inner/ 下。
"""

from pathlib import Path

import pytest

from core.sandbox import DataPaths, _LAYOUT_CHARACTER_INNER, _LAYOUT_DREAM, _LAYOUT_REALITY


@pytest.fixture
def dp(tmp_path):
    """使用 tmp_path 作为沙盒根，隔离文件系统。"""
    paths = DataPaths(mode="test", test_session_id="multichar_unit")
    paths._base = tmp_path
    return paths


# ── 确认布局开关状态（S5 后 CHARACTER_INNER = v1）──────────────────────────────

def test_layout_switches():
    assert _LAYOUT_CHARACTER_INNER == "v1"   # S5 已翻转
    assert _LAYOUT_REALITY == "legacy"
    assert _LAYOUT_DREAM == "legacy"


# ── character_inner v1：char_id=yexuan 落在 characters/{char_id}/inner/ ─────────

@pytest.mark.parametrize("method,expected_parts", [
    ("mood_state",        ("characters", "yexuan", "inner", "mood_state.json")),
    ("activity_state",    ("characters", "yexuan", "inner", "activity_state.json")),
    ("trait_state",       ("characters", "yexuan", "inner", "trait_state.json")),
    ("author_note_state", ("characters", "yexuan", "inner", "author_note_state.json")),
    ("observations",      ("characters", "yexuan", "inner", "observations.jsonl")),
    ("yexuan_inner_diary",("characters", "yexuan", "inner", "diary")),
    ("presence",          ("characters", "yexuan", "inner", "presence.json")),
    ("activity_snapshot", ("characters", "yexuan", "inner", "activity_snapshot.json")),
])
def test_character_inner_v1_paths(dp, tmp_path, method, expected_parts):
    fn = getattr(dp, method)
    assert fn(char_id="yexuan") == tmp_path.joinpath(*expected_parts)
    # default arg == explicit "yexuan"
    assert fn() == fn(char_id="yexuan")


@pytest.mark.parametrize("method,expected_parts", [
    ("pet_file",         ("characters", "yexuan", "pet.json")),
    ("garden",           ("characters", "yexuan", "garden")),
    ("character_growth", ("characters", "yexuan", "character_growth")),
])
def test_character_inner_v1_top_paths(dp, tmp_path, method, expected_parts):
    fn = getattr(dp, method)
    assert fn(char_id="yexuan") == tmp_path.joinpath(*expected_parts)
    assert fn() == fn(char_id="yexuan")


# ── character_inner authored 静态路径（不走沙盒 _p，绝对路径略去 base）─────────

def test_activity_pool_legacy(dp):
    assert dp.activity_pool(char_id="yexuan") == Path("data/yexuan_inner/activity_pool.yaml")
    assert dp.activity_pool() == dp.activity_pool(char_id="yexuan")


def test_author_notes_pool_legacy(dp):
    assert dp.author_notes_pool(char_id="yexuan") == Path("characters/yexuan_author_notes.json")
    assert dp.author_notes_pool() == dp.author_notes_pool(char_id="yexuan")


def test_yexuan_traits_legacy(dp):
    assert dp.yexuan_traits(char_id="yexuan") == Path("data/yexuan_traits.yaml")
    assert dp.yexuan_traits() == dp.yexuan_traits(char_id="yexuan")


# ── character_growth（character_inner per_char_user）─────────────────────────
# S5 后路径随 _LAYOUT_CHARACTER_INNER=v1 迁至 characters/{char_id}/character_growth

def test_character_growth_v1(dp, tmp_path):
    assert dp.character_growth(char_id="yexuan") == tmp_path / "characters" / "yexuan" / "character_growth"
    assert dp.character_growth() == dp.character_growth(char_id="yexuan")


# ── reality per_char_user 目录：char_id=yexuan + legacy == 旧路径 ──────────────

@pytest.mark.parametrize("method,expected_rel", [
    ("history",           "history"),
    ("mid_term",          "mid_term"),
    ("episodic_memory",   "episodic_memory"),
    ("memory_index",      "memory_index"),
    ("profiles",          "profiles"),
    ("reminders",         "reminders"),
    ("diary_context",     "diary_context"),
    ("event_log",         "event_log"),
    ("fixation_state_dir","fixation_state"),
    ("user_identity_dir", "user_identity"),
])
def test_reality_legacy_paths(dp, tmp_path, method, expected_rel):
    fn = getattr(dp, method)
    assert fn(char_id="yexuan") == tmp_path / expected_rel
    assert fn() == fn(char_id="yexuan")


# ── dream 路径：char_id=yexuan + legacy == 旧路径 ─────────────────────────────

@pytest.mark.parametrize("method,expected_parts", [
    ("dreams_tmp_dir",        ("dreams", "tmp")),
    ("dreams_archive_dir",    ("dreams", "archive")),
    ("dreams_summaries_dir",  ("dreams", "summaries")),
    ("dreams_impressions_dir",("dreams", "impressions")),
])
def test_dream_dir_legacy_paths(dp, tmp_path, method, expected_parts):
    fn = getattr(dp, method)
    assert fn(char_id="yexuan") == tmp_path.joinpath(*expected_parts)
    assert fn() == fn(char_id="yexuan")


def test_dream_state_path_legacy(dp, tmp_path):
    assert dp.dream_state_path("123456", char_id="yexuan") == (
        tmp_path / "dreams" / "state" / "123456" / "dream_state.json"
    )
    assert dp.dream_state_path("123456") == dp.dream_state_path("123456", char_id="yexuan")


def test_dream_settings_path_legacy(dp, tmp_path):
    assert dp.dream_settings_path("123456", char_id="yexuan") == (
        tmp_path / "dreams" / "settings" / "123456.json"
    )
    assert dp.dream_settings_path("123456") == dp.dream_settings_path("123456", char_id="yexuan")


# ── 内存模块路径助手：char_id 穿透到 DataPaths ────────────────────────────────

def test_short_term_history_path_legacy(dp, tmp_path, monkeypatch):
    import core.sandbox as _sb
    monkeypatch.setattr(_sb, "_instance", dp)

    from core.memory import short_term
    p = short_term._history_path("1234567890", char_id="yexuan")
    assert p == tmp_path / "history" / "1234567890.json"
    assert short_term._history_path("1234567890") == p


def test_mid_term_file_legacy(dp, tmp_path, monkeypatch):
    import core.sandbox as _sb
    monkeypatch.setattr(_sb, "_instance", dp)

    from core.memory import mid_term
    p = mid_term._file("1234567890", char_id="yexuan")
    assert p == tmp_path / "mid_term" / "1234567890.json"
    assert mid_term._file("1234567890") == p


def test_episodic_mem_file_legacy(dp, tmp_path, monkeypatch):
    import core.sandbox as _sb
    monkeypatch.setattr(_sb, "_instance", dp)

    from core.memory import episodic_memory
    p = episodic_memory._mem_file("1234567890", char_id="yexuan")
    assert p == tmp_path / "episodic_memory" / "1234567890.json"
    assert episodic_memory._mem_file("1234567890") == p


def test_fixation_state_file_legacy(dp, tmp_path, monkeypatch):
    import core.sandbox as _sb
    monkeypatch.setattr(_sb, "_instance", dp)

    from core.memory import fixation_pipeline
    p = fixation_pipeline._state_file("1234567890", char_id="yexuan")
    assert p == tmp_path / "fixation_state" / "1234567890.json"
    assert fixation_pipeline._state_file("1234567890") == p


def test_event_log_day_file_legacy(dp, tmp_path, monkeypatch):
    import core.sandbox as _sb
    monkeypatch.setattr(_sb, "_instance", dp)

    from datetime import datetime
    from core.memory import event_log
    d = datetime(2026, 5, 29)
    p = event_log._day_file("1234567890", d, char_id="yexuan")
    assert p == tmp_path / "event_log" / "1234567890" / "2026-05-29.md"
    assert event_log._day_file("1234567890", d) == p
