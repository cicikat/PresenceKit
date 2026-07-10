"""
多角色路径铺线验证测试（S4 + S5 + S6）

当前布局（全部 v1）：
- CHARACTER_INNER v1: runtime/characters/{char_id}/inner/...
- REALITY v1:        chars/{char_id}/{artifact}  (DataPaths 直接函数)
                     runtime/memory/{char_id}/{uid}/{artifact}  (path_resolver 模块函数)
- DREAM v1:          runtime/dreams/{char_id}/{...}
"""

from datetime import datetime
from pathlib import Path

import pytest

from core.sandbox import DataPaths, _LAYOUT_CHARACTER_INNER, _LAYOUT_DREAM, _LAYOUT_REALITY


@pytest.fixture
def dp(tmp_path):
    """使用 tmp_path 作为沙盒根，隔离文件系统。"""
    paths = DataPaths(mode="test", test_session_id="multichar_unit")
    paths._base = tmp_path
    return paths


# ── 确认布局开关状态（S5+S6 已全部翻至 v1）──────────────────────────────────────

def test_layout_switches():
    assert _LAYOUT_CHARACTER_INNER == "v1"   # S5 已翻转
    assert _LAYOUT_REALITY == "v1"           # S6 已翻转
    assert _LAYOUT_DREAM == "v1"             # dream 已翻转


# ── character_inner v1：char_id=yexuan 落在 runtime/characters/{char_id}/inner/ ─

_NO_DEFAULT = {"observations", "activity_snapshot", "mood_state"}

@pytest.mark.parametrize("method,expected_parts", [
    ("mood_state",        ("runtime", "characters", "yexuan", "inner", "mood_state.json")),
    ("activity_state",    ("runtime", "characters", "yexuan", "inner", "activity_state.json")),
    ("trait_state",       ("runtime", "characters", "yexuan", "inner", "trait_state.json")),
    ("author_note_state", ("runtime", "characters", "yexuan", "inner", "author_note_state.json")),
    ("observations",      ("runtime", "characters", "yexuan", "inner", "observations.jsonl")),
    ("yexuan_inner_diary",("runtime", "characters", "yexuan", "inner", "diary")),
    ("presence",          ("runtime", "characters", "yexuan", "inner", "presence.json")),
    ("activity_snapshot", ("runtime", "characters", "yexuan", "inner", "activity_snapshot.json")),
])
def test_character_inner_v1_paths(dp, tmp_path, method, expected_parts):
    fn = getattr(dp, method)
    assert fn(char_id="yexuan") == tmp_path.joinpath(*expected_parts)
    # methods with no default char_id cannot be called without arg
    if method not in _NO_DEFAULT:
        assert fn() == fn(char_id="yexuan")


@pytest.mark.parametrize("method,expected_parts", [
    ("pet_file",         ("runtime", "characters", "yexuan", "pet.json")),
    ("garden",           ("runtime", "characters", "yexuan", "garden")),
])
def test_character_inner_v1_top_paths(dp, tmp_path, method, expected_parts):
    fn = getattr(dp, method)
    assert fn(char_id="yexuan") == tmp_path.joinpath(*expected_parts)
    assert fn() == fn(char_id="yexuan")


# ── character_inner authored 静态路径（不走沙盒 _p，绝对路径略去 base）─────────

def test_activity_pool_v1(dp):
    # content/characters/yexuan/activity_pool.yaml now exists — new path wins
    assert dp.activity_pool(char_id="yexuan") == Path("content/characters/yexuan/activity_pool.yaml")
    assert dp.activity_pool() == dp.activity_pool(char_id="yexuan")


def test_author_notes_pool_legacy(dp):
    # v1 fallback path (no new content/characters/yexuan file exists in test)
    result = dp.author_notes_pool(char_id="yexuan")
    assert "yexuan_author_notes.json" in str(result)
    assert dp.author_notes_pool() == dp.author_notes_pool(char_id="yexuan")


def test_yexuan_traits_legacy(dp):
    assert dp.yexuan_traits(char_id="yexuan") == Path("data/yexuan_traits.yaml")
    assert dp.yexuan_traits() == dp.yexuan_traits(char_id="yexuan")


# character_growth 路径解析测试已删除（Brief 50 · 工单C.3）：character_growth
# 是 Brief 35 已删除模块的 legacy/dead artifact；DataPaths.character_growth()
# 本身仍有调用方（scripts/migrate_data_v1.py 一次性迁移脚本），故兼容分支保留，
# 只删测试。行为覆盖见 core/data_paths.py:character_growth() 的说明注释。


# ── reality per_char_user 目录：v1 路径落在 chars/{char_id}/{artifact} ──────────

@pytest.mark.parametrize("method,expected_rel", [
    ("history",           ("chars", "yexuan", "history")),
    ("mid_term",          ("chars", "yexuan", "mid_term")),
    ("episodic_memory",   ("chars", "yexuan", "episodic_memory")),
    ("memory_index",      ("chars", "yexuan", "memory_index")),
    ("profiles",          ("chars", "yexuan", "profiles")),
    ("reminders",         ("chars", "yexuan", "reminders")),
    ("diary_context",     ("chars", "yexuan", "diary_context")),
    ("event_log",         ("chars", "yexuan", "event_log")),
    ("fixation_state_dir",("chars", "yexuan", "fixation_state")),
    ("user_identity_dir", ("chars", "yexuan", "user_identity")),
])
def test_reality_v1_paths(dp, tmp_path, method, expected_rel):
    fn = getattr(dp, method)
    assert fn(char_id="yexuan") == tmp_path.joinpath(*expected_rel)
    assert fn() == fn(char_id="yexuan")


# ── dream 路径：v1 路径落在 runtime/dreams/{char_id}/... ─────────────────────

@pytest.mark.parametrize("method,expected_parts", [
    ("dreams_tmp_dir",        ("runtime", "dreams", "yexuan", "tmp")),
    ("dreams_archive_dir",    ("runtime", "dreams", "yexuan", "archive")),
    ("dreams_summaries_dir",  ("runtime", "dreams", "yexuan", "summaries")),
    ("dreams_impressions_dir",("runtime", "dreams", "yexuan", "impressions")),
])
def test_dream_dir_v1_paths(dp, tmp_path, method, expected_parts):
    fn = getattr(dp, method)
    assert fn(char_id="yexuan") == tmp_path.joinpath(*expected_parts)
    assert fn() == fn(char_id="yexuan")


def test_dream_state_path_v1(dp, tmp_path):
    assert dp.dream_state_path("123456", char_id="yexuan") == (
        tmp_path / "runtime" / "dreams" / "yexuan" / "state" / "123456" / "dream_state.json"
    )
    assert dp.dream_state_path("123456") == dp.dream_state_path("123456", char_id="yexuan")


def test_dream_settings_path_v1(dp, tmp_path):
    assert dp.dream_settings_path("123456", char_id="yexuan") == (
        tmp_path / "runtime" / "dreams" / "yexuan" / "settings" / "123456.json"
    )
    assert dp.dream_settings_path("123456") == dp.dream_settings_path("123456", char_id="yexuan")


# ── 内存模块路径助手：S6 v1 路径穿透到 path_resolver ────────────────────────────

def test_short_term_history_path_v1(dp, tmp_path, monkeypatch):
    import core.sandbox as _sb
    monkeypatch.setattr(_sb, "_instance", dp)

    from core.memory import short_term
    p = short_term._history_path("1234567890", char_id="yexuan")
    # S6 v1: runtime/memory/{char_id}/{uid}/history.json
    assert p == tmp_path / "runtime" / "memory" / "yexuan" / "1234567890" / "history.json"
    assert short_term._history_path("1234567890") == p


def test_mid_term_file_v1(dp, tmp_path, monkeypatch):
    import core.sandbox as _sb
    monkeypatch.setattr(_sb, "_instance", dp)

    from core.memory import mid_term
    p = mid_term._read_file("1234567890", char_id="yexuan")
    # S6 v1: runtime/memory/{char_id}/{uid}/mid_term.json
    assert p == tmp_path / "runtime" / "memory" / "yexuan" / "1234567890" / "mid_term.json"
    assert mid_term._read_file("1234567890") == p


def test_episodic_mem_file_v1(dp, tmp_path, monkeypatch):
    import core.sandbox as _sb
    monkeypatch.setattr(_sb, "_instance", dp)

    from core.memory import episodic_memory
    p = episodic_memory._mem_read_file("1234567890", char_id="yexuan")
    # S6 v1: runtime/memory/{char_id}/{uid}/episodic.json
    assert p == tmp_path / "runtime" / "memory" / "yexuan" / "1234567890" / "episodic.json"
    assert episodic_memory._mem_read_file("1234567890") == p


def test_fixation_state_file_v1(dp, tmp_path, monkeypatch):
    import core.sandbox as _sb
    monkeypatch.setattr(_sb, "_instance", dp)

    from core.memory import fixation_pipeline
    p = fixation_pipeline._state_read_file("1234567890", char_id="yexuan")
    # S6 v1: runtime/memory/{char_id}/{uid}/fixation_state.json
    assert p == tmp_path / "runtime" / "memory" / "yexuan" / "1234567890" / "fixation_state.json"
    assert fixation_pipeline._state_read_file("1234567890") == p


def test_event_log_day_file_v1(dp, tmp_path, monkeypatch):
    import core.sandbox as _sb
    monkeypatch.setattr(_sb, "_instance", dp)

    from core.memory import event_log
    d = datetime(2026, 5, 29)
    # Use _day_file_write which always targets v1 path (no fallback)
    p = event_log._day_file_write("1234567890", d, char_id="yexuan")
    # S6 v1: runtime/memory/{char_id}/{uid}/event_log/2026-05-29.md
    assert p == (
        tmp_path / "runtime" / "memory" / "yexuan" / "1234567890" / "event_log" / "2026-05-29.md"
    )
    assert event_log._day_file_write("1234567890", d) == p
