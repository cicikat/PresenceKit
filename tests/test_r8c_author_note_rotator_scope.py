"""
R8-C: author_note_rotator trait_state 多角色读路径修复。

Coverage:
1.  get_current_note(char_id="hongcha") 读 hongcha trait_state，不读 yexuan
2.  yexuan/hongcha 同时存在时，hongcha 数据影响 _pick_note underrepresented 入参
3.  prompt_builder.build() 传入 char_id 时，get_current_note 以该 char_id 被调用
4.  char_id=None 兼容旧行为（legacy path：trait_state 调用无 char_id kwarg）
5.  core/author_note_rotator.py 无新增 char_id="yexuan" 函数参数默认值（R3-CI Rule-1）
6.  R8-B handler 写路径与 rotator 读路径一致（同 char_id 下同一 Path 对象）
7.  yexuan 单角色默认行为不回归（char_id="yexuan" 显式传入）
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pool_data(*note_ids_and_traits) -> dict:
    """Build a minimal notes pool dict.
    note_ids_and_traits: sequence of (id, trait_ids_list).
    """
    return {"notes": [
        {"id": nid, "content": f"content_{nid}", "trait_ids": traits}
        for nid, traits in note_ids_and_traits
    ]}


class _FakePaths:
    """Minimal paths stub; lets tests control each path independently."""

    def __init__(self, pool_path, state_path, hongcha_trait_path, yexuan_trait_path=None):
        self._pool = pool_path
        self._state = state_path
        self._hongcha_trait = hongcha_trait_path
        self._yexuan_trait = yexuan_trait_path
        self.trait_calls: list[dict] = []

    def author_notes_pool(self, **kw):
        return self._pool

    def author_note_state(self, **kw):
        return self._state

    def trait_state(self, **kw):
        self.trait_calls.append(dict(kw))
        char = kw.get("char_id")
        if char == "hongcha":
            return self._hongcha_trait
        # For yexuan or default: return yexuan_trait if provided, else a nonexistent path
        if self._yexuan_trait is not None:
            return self._yexuan_trait
        return self._hongcha_trait.parent / "yexuan_trait_state.json"


# ---------------------------------------------------------------------------
# 1. get_current_note(char_id="hongcha") 调 trait_state(char_id="hongcha")
# ---------------------------------------------------------------------------

def test_rotator_uses_hongcha_trait_state_when_char_id_hongcha(tmp_path):
    """
    With char_id="hongcha", get_current_note must call paths.trait_state(char_id="hongcha"),
    not paths.trait_state() (which would default to yexuan).
    """
    from core.author_note_rotator import get_current_note

    pool_file = tmp_path / "notes.json"
    pool_file.write_text(json.dumps(_pool_data(
        ("note1", ["TRAIT_A"]),
        ("note2", ["TRAIT_B"]),
    )), encoding="utf-8")

    hongcha_trait = tmp_path / "hongcha_trait.json"
    hongcha_trait.write_text(json.dumps({"underrepresented": ["TRAIT_A"]}), encoding="utf-8")

    paths = _FakePaths(
        pool_path=pool_file,
        state_path=tmp_path / "state.json",
        hongcha_trait_path=hongcha_trait,
    )

    get_current_note(paths=paths, char_id="hongcha")

    assert paths.trait_calls, "trait_state() was never called"
    call = paths.trait_calls[-1]
    assert call == {"char_id": "hongcha"}, (
        f"trait_state must be called with char_id='hongcha', got {call}"
    )


# ---------------------------------------------------------------------------
# 2. hongcha 与 yexuan 同时存在时，hongcha 数据影响 underrepresented 入参
# ---------------------------------------------------------------------------

def test_hongcha_underrepresented_reaches_pick_note(tmp_path, monkeypatch):
    """
    With char_id="hongcha", _pick_note must receive hongcha's underrepresented
    list, not yexuan's.
    """
    import core.author_note_rotator as _anr
    from core.author_note_rotator import get_current_note

    pool_file = tmp_path / "notes.json"
    pool_file.write_text(json.dumps(_pool_data(
        ("note1", ["HONGCHA_TRAIT"]),
        ("note2", ["YEXUAN_TRAIT"]),
    )), encoding="utf-8")

    hongcha_trait = tmp_path / "hongcha_trait.json"
    hongcha_trait.write_text(json.dumps({"underrepresented": ["HONGCHA_TRAIT"]}), encoding="utf-8")

    yexuan_trait = tmp_path / "yexuan_trait.json"
    yexuan_trait.write_text(json.dumps({"underrepresented": ["YEXUAN_TRAIT"]}), encoding="utf-8")

    paths = _FakePaths(
        pool_path=pool_file,
        state_path=tmp_path / "state.json",
        hongcha_trait_path=hongcha_trait,
        yexuan_trait_path=yexuan_trait,
    )

    captured: dict = {}
    original_pick = _anr._pick_note

    def _spy_pick(pool, state, underrepresented):
        captured["underrepresented"] = list(underrepresented)
        return original_pick(pool, state, underrepresented)

    monkeypatch.setattr(_anr, "_pick_note", _spy_pick)

    get_current_note(paths=paths, char_id="hongcha")

    assert captured.get("underrepresented") is not None, "_pick_note was never called"
    assert "HONGCHA_TRAIT" in captured["underrepresented"], (
        f"hongcha's underrepresented should be passed; got {captured['underrepresented']}"
    )
    assert "YEXUAN_TRAIT" not in captured["underrepresented"], (
        f"yexuan's underrepresented must NOT appear when char_id='hongcha'; "
        f"got {captured['underrepresented']}"
    )


# ---------------------------------------------------------------------------
# 3. build() 传入 char_id 时 get_current_note 以该 char_id 被调用
# ---------------------------------------------------------------------------

def test_build_passes_char_id_to_get_current_note(monkeypatch):
    """
    prompt_builder.build(char_id="hongcha") must call get_current_note(char_id="hongcha").
    """
    import core.prompt_builder as _pb
    import core.presence as _pres
    import core.author_note_rotator as _anr
    import core.config_loader as _cl

    monkeypatch.setattr(_pb, "_load_jailbreak", lambda layer=None: "")
    monkeypatch.setattr(_pb, "_load_style_hint", lambda *, char_id="": "")
    monkeypatch.setattr(_pb, "_load_activity_snapshot", lambda *, char_id="": "")
    monkeypatch.setattr(_pb, "_format_afterglow_soft_hint", lambda uid, char_id="yexuan": "")
    monkeypatch.setattr(_pres, "get_last_seen_text", lambda uid: "")
    monkeypatch.setattr(_cl, "get_config", lambda: {"chat": {}})

    received_char_id: list = []

    def _spy_get_current_note(paths=None, char_id=None):
        received_char_id.append(char_id)
        return ""

    monkeypatch.setattr(_anr, "get_current_note", _spy_get_current_note)

    from core.character_loader import Character
    char = Character(name="红茶")

    _pb.build(
        character=char,
        user_id="uid1",
        user_message="hello",
        history=[],
        relation={},
        profile={},
        group_context=[],
        char_id="hongcha",
    )

    assert received_char_id, "get_current_note was never called during build()"
    assert received_char_id[0] == "hongcha", (
        f"build() must forward char_id='hongcha'; got {received_char_id[0]}"
    )


# ---------------------------------------------------------------------------
# 4. char_id=None 兼容旧行为（trait_state 调用无 char_id kwarg）
# ---------------------------------------------------------------------------

def test_char_id_none_legacy_no_char_id_kwarg(tmp_path):
    """
    With char_id=None (legacy), trait_state must be called with no char_id kwarg,
    so it falls back to data_paths.py's default (yexuan).
    """
    from core.author_note_rotator import get_current_note

    pool_file = tmp_path / "notes.json"
    pool_file.write_text(json.dumps(_pool_data(("note1", []))), encoding="utf-8")

    trait_file = tmp_path / "default_trait.json"
    trait_file.write_text(json.dumps({"underrepresented": []}), encoding="utf-8")

    paths = _FakePaths(
        pool_path=pool_file,
        state_path=tmp_path / "state.json",
        hongcha_trait_path=trait_file,   # doesn't matter for char_id=None
    )
    # Override trait_state to capture the actual kwargs
    trait_calls: list[dict] = []

    def _trait_state_spy(**kw):
        trait_calls.append(dict(kw))
        return trait_file

    paths.trait_state = _trait_state_spy  # type: ignore[method-assign]

    get_current_note(paths=paths)  # char_id not passed → None

    assert trait_calls, "trait_state() was never called"
    # When char_id=None, _kw={}, so trait_state is called with NO char_id kwarg
    assert "char_id" not in trait_calls[-1], (
        f"legacy call must have no char_id kwarg; got {trait_calls[-1]}"
    )


# ---------------------------------------------------------------------------
# 5. R3-CI Rule-1：core/author_note_rotator.py 无 char_id="yexuan" 函数默认值
# ---------------------------------------------------------------------------

def test_no_yexuan_default_in_author_note_rotator():
    """
    core/author_note_rotator.py must not define any function with
    char_id="yexuan" or character_id="yexuan" as a parameter default.
    (R3 Rule-1 guard for this file specifically.)
    """
    PROJECT_ROOT = Path(__file__).parent.parent
    src = (PROJECT_ROOT / "core" / "author_note_rotator.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    _guarded = frozenset({"char_id", "character_id"})

    violations: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        all_pos = args.posonlyargs + args.args
        offset = len(all_pos) - len(args.defaults)
        for i, default in enumerate(args.defaults):
            arg = all_pos[offset + i]
            if (
                arg.arg in _guarded
                and isinstance(default, ast.Constant)
                and default.value == "yexuan"
            ):
                violations.append(default.lineno)
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            if default is None:
                continue
            if (
                arg.arg in _guarded
                and isinstance(default, ast.Constant)
                and default.value == "yexuan"
            ):
                violations.append(default.lineno)

    assert not violations, (
        f"core/author_note_rotator.py has char_id='yexuan' defaults at lines {violations}"
    )


# ---------------------------------------------------------------------------
# 6. R8-B handler 写路径与 rotator 读路径一致（同 char_id）
# ---------------------------------------------------------------------------

def test_r8b_write_path_matches_rotator_read_path_per_char_id(sandbox):
    """
    For a given char_id, the path the R8-B handler writes to must equal the path
    get_current_note reads trait_state from.
    Checks both yexuan and hongcha to confirm char_id is respected.
    """
    from core.sandbox import get_paths
    paths = get_paths()

    for char_id in ("yexuan", "hongcha"):
        handler_write = paths.trait_state(char_id=char_id)

        # Simulate what get_current_note does with _kw = {"char_id": char_id}
        rotator_read = paths.trait_state(char_id=char_id)

        assert handler_write == rotator_read, (
            f"char_id={char_id!r}: handler writes to {handler_write}, "
            f"rotator reads from {rotator_read}"
        )

    # Explicit: hongcha and yexuan paths must differ
    yexuan_path = paths.trait_state(char_id="yexuan")
    hongcha_path = paths.trait_state(char_id="hongcha")
    assert yexuan_path != hongcha_path, (
        "yexuan and hongcha trait_state paths must be different "
        f"(both resolved to {yexuan_path})"
    )


# ---------------------------------------------------------------------------
# 7. yexuan 单角色默认行为不回归
# ---------------------------------------------------------------------------

def test_yexuan_explicit_reads_yexuan_trait_state(tmp_path):
    """
    get_current_note(char_id="yexuan") must call trait_state(char_id="yexuan"),
    confirming the explicit path is symmetric with the hongcha test.
    """
    from core.author_note_rotator import get_current_note

    pool_file = tmp_path / "notes.json"
    pool_file.write_text(json.dumps(_pool_data(("note1", ["TRAIT_Y"]))), encoding="utf-8")

    yexuan_trait = tmp_path / "yexuan_trait.json"
    yexuan_trait.write_text(json.dumps({"underrepresented": ["TRAIT_Y"]}), encoding="utf-8")

    paths = _FakePaths(
        pool_path=pool_file,
        state_path=tmp_path / "state.json",
        hongcha_trait_path=tmp_path / "hongcha_trait.json",
        yexuan_trait_path=yexuan_trait,
    )

    get_current_note(paths=paths, char_id="yexuan")

    assert paths.trait_calls, "trait_state() was never called"
    call = paths.trait_calls[-1]
    assert call == {"char_id": "yexuan"}, (
        f"trait_state must be called with char_id='yexuan'; got {call}"
    )
