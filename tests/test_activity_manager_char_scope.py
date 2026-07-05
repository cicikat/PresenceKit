"""
tests/test_activity_manager_char_scope.py — CC 任务 24 · 3

core/activity_manager.py 全局单状态 bug 修复回归测试：此前 _load_state()/_save_state()/
_load_pool() 调 get_paths().activity_state()/activity_pool() 时不传 char_id，全部落在默认
角色（yexuan）路径上，导致 /activity/current 的随机池动向与 active_character 无关。

覆盖：
1. _save_state(char_id=...) / _load_state(char_id=...) 按角色隔离，互不覆盖。
2. get_current(char_id=...) 为不同角色分别维护独立的切换状态。
3. _load_pool(char_id=...) 角色自己的 pool 不存在时 fallback 到默认角色池（不复制文件）。
4. get_prompt_fragment(char_id=...) 把 char_id 转发给 get_current()。
5. admin/routers/activity.py GET /activity/current 响应体带 char_id 字段，且随机池按该角色取。
"""

from __future__ import annotations

import json

import pytest

import core.activity_manager as am


# ── 1 & 2. _save_state / _load_state / get_current 按角色隔离 ────────────────

def test_save_and_load_state_isolated_per_char(sandbox, monkeypatch):
    monkeypatch.setattr(am, "_TRANSITION_CHARACTER_INNER", False)

    am._save_state({"current": "character_a 在写代码", "arc": "afternoon"}, char_id="character_a")
    am._save_state({"current": "character_b 在睡觉", "arc": "deep_night"}, char_id="character_b")

    state_a = am._load_state(char_id="character_a")
    state_b = am._load_state(char_id="character_b")

    assert state_a["current"] == "character_a 在写代码"
    assert state_b["current"] == "character_b 在睡觉"

    # 两个角色落盘路径不同
    path_a = sandbox.activity_state(char_id="character_a")
    path_b = sandbox.activity_state(char_id="character_b")
    assert path_a != path_b
    assert json.loads(path_a.read_text(encoding="utf-8"))["current"] == "character_a 在写代码"
    assert json.loads(path_b.read_text(encoding="utf-8"))["current"] == "character_b 在睡觉"


def test_load_state_missing_char_returns_empty(sandbox, monkeypatch):
    monkeypatch.setattr(am, "_TRANSITION_CHARACTER_INNER", False)
    am._save_state({"current": "只有 character_a 写过状态"}, char_id="character_a")

    assert am._load_state(char_id="character_b") == {}


def test_get_current_switches_independently_per_char(sandbox, monkeypatch):
    monkeypatch.setattr(am, "_TRANSITION_CHARACTER_INNER", False)
    monkeypatch.setattr(
        am, "_pick_activity",
        lambda arc, char_id="yexuan": {"text": f"{char_id} 的活动", "id": "x"},
    )

    state_a = am.get_current(char_id="character_a")
    state_b = am.get_current(char_id="character_b")

    assert state_a["current"] == "character_a 的活动"
    assert state_b["current"] == "character_b 的活动"

    # 第二次调用（未过期）应从各自磁盘状态直接读回，不重新切换
    monkeypatch.setattr(am, "should_switch", lambda char_id="yexuan": False)
    assert am.get_current(char_id="character_a")["current"] == "character_a 的活动"
    assert am.get_current(char_id="character_b")["current"] == "character_b 的活动"


# ── 3. _load_pool fallback ───────────────────────────────────────────────────

class _FakePaths:
    def __init__(self, pool_paths: dict[str, "object"]):
        self._pool_paths = pool_paths

    def activity_pool(self, *, char_id: str = "yexuan"):
        return self._pool_paths[char_id]


def test_load_pool_uses_own_file_when_present(tmp_path, monkeypatch):
    own_pool = tmp_path / "character_a_pool.yaml"
    own_pool.write_text("activities:\n  - id: a1\n    text: 在写诗\n    arcs: [afternoon]\n", encoding="utf-8")

    fake_paths = _FakePaths({"character_a": own_pool})
    monkeypatch.setattr(am, "get_paths", lambda: fake_paths)
    # 让 fallback-detection 的 own_pool.exists() 检查命中（模拟 content/characters/... 真实存在）
    monkeypatch.setattr(
        am.Path, "exists", lambda self: True if "character_a" in str(self) else False,
    )

    result = am._load_pool(char_id="character_a")
    assert result == [{"id": "a1", "text": "在写诗", "arcs": ["afternoon"]}]


def test_load_pool_fallback_logs_debug_when_own_pool_missing(tmp_path, monkeypatch, caplog):
    default_pool = tmp_path / "default_pool.yaml"
    default_pool.write_text("activities:\n  - id: d1\n    text: 在发呆\n    arcs: [afternoon]\n", encoding="utf-8")

    fake_paths = _FakePaths({"character_b": default_pool})
    monkeypatch.setattr(am, "get_paths", lambda: fake_paths)
    monkeypatch.setattr(am.Path, "exists", lambda self: False)  # 角色自己的池不存在

    import logging
    with caplog.at_level(logging.DEBUG, logger="core.activity_manager"):
        result = am._load_pool(char_id="character_b")

    assert result == [{"id": "d1", "text": "在发呆", "arcs": ["afternoon"]}]
    assert any("fallback" in r.message for r in caplog.records)


def test_load_pool_default_char_no_fallback_log(tmp_path, monkeypatch, caplog):
    """默认角色（yexuan）不应触发 fallback 判断分支。"""
    default_pool = tmp_path / "yexuan_pool.yaml"
    default_pool.write_text("activities:\n  - id: y1\n    text: 在看书\n    arcs: [afternoon]\n", encoding="utf-8")

    fake_paths = _FakePaths({"yexuan": default_pool})
    monkeypatch.setattr(am, "get_paths", lambda: fake_paths)

    import logging
    with caplog.at_level(logging.DEBUG, logger="core.activity_manager"):
        result = am._load_pool(char_id="yexuan")

    assert result == [{"id": "y1", "text": "在看书", "arcs": ["afternoon"]}]
    assert not any("fallback" in r.message for r in caplog.records)


# ── 4. get_prompt_fragment 透传 char_id ──────────────────────────────────────

def test_get_prompt_fragment_forwards_char_id(monkeypatch):
    captured = []

    def _fake_get_current(char_id="yexuan"):
        captured.append(char_id)
        return {"current": "在遛狗", "thinking_about": ""}

    monkeypatch.setattr(am, "get_current", _fake_get_current)
    text = am.get_prompt_fragment(char_id="character_c")

    assert text == "在遛狗"
    assert captured == ["character_c"]


# ── 5. admin/routers/activity.py 响应体带 char_id ────────────────────────────

@pytest.mark.asyncio
async def test_activity_current_endpoint_includes_char_id(sandbox, monkeypatch):
    p = sandbox.active_prompt_assets()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"active_character": "character_a"}), encoding="utf-8")

    import admin.routers.activity as _activity_router
    from core.asset_registry import get_registry

    monkeypatch.setattr(get_registry(), "resolve", lambda cid, kind: cid)

    captured_char_ids = []

    def _fake_get_current(char_id="yexuan"):
        captured_char_ids.append(char_id)
        return {"current": "character_a 在种花", "arc": "afternoon", "expected_until_ts": 0}

    monkeypatch.setattr(_activity_router.activity_manager, "get_current", _fake_get_current)
    monkeypatch.setattr(
        _activity_router, "_get_activity_text",
        lambda char_id: _activity_router.activity_manager.get_current(char_id=char_id)["current"],
    )

    result = await _activity_router.get_activity_state()

    assert result["char_id"] == "character_a"
    assert result["text"] == "character_a 在种花"
    assert captured_char_ids == ["character_a", "character_a"]
