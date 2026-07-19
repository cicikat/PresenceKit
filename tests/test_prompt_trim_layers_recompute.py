"""
tests/test_prompt_trim_layers_recompute.py — Brief 102 · P3

裁剪后 layers_activated 重算：验证 build() 强制裁剪触发后，layers_activated
只反映最终 messages 里实际存在的层，被裁掉的层改为出现在新增字段
layers_before_trim 里；未触发裁剪时两者相等。
"""

import pytest
from core.character_loader import Character


def _apply_build_stubs(monkeypatch):
    """Stub all filesystem-touching helpers so build() can run in tests."""
    import core.prompt_builder as _pb
    import core.presence as _pres
    import core.author_note_rotator as _anr
    import core.config_loader as _cl

    monkeypatch.setattr(_pb, "_load_jailbreak", lambda layer=None: "")
    monkeypatch.setattr(_pb, "_load_style_hint", lambda *, char_id="": "")
    monkeypatch.setattr(_pb, "_load_activity_snapshot", lambda *, char_id="": "")
    monkeypatch.setattr(_pb, "_format_afterglow_soft_hint", lambda uid, char_id="yexuan": "")
    monkeypatch.setattr(_pres, "get_last_seen_text", lambda uid: "")
    monkeypatch.setattr(_anr, "get_current_note", lambda paths=None, char_id=None: "")
    monkeypatch.setattr(_cl, "get_config", lambda: {"chat": {}})


def _base_build_kwargs(**overrides):
    kwargs = dict(
        character=Character(name="DemoUser"),
        user_id="u1",
        user_message="你好",
        history=[{"role": "user", "content": "hi", "_layer": "9_history"}],
        relation={"role": "friend"},
        profile={},
        group_context=[],
    )
    kwargs.update(overrides)
    return kwargs


def test_trimmed_layer_absent_from_activated_but_present_before_trim(sandbox, monkeypatch):
    _apply_build_stubs(monkeypatch)
    import core.prompt_builder as _pb

    # 超 20k 场景：唯一可裁层（5.5_lore, drop_priority=80）单独撑爆预算，
    # 裁掉它足以回落到 target 以下。
    messages, debug_info = _pb.build(
        **_base_build_kwargs(lore_entries=["A" * 25000])
    )

    layers = [m.get("_layer") for m in messages]
    assert "5.5_lore" not in layers
    assert "5.5_lore" not in debug_info["layers_activated"]
    assert "5.5_lore" in debug_info["layers_before_trim"]
    assert "5.5_lore" in debug_info["removed_layers"]


def test_layers_activated_matches_final_messages(sandbox, monkeypatch):
    _apply_build_stubs(monkeypatch)
    import core.prompt_builder as _pb

    messages, debug_info = _pb.build(
        **_base_build_kwargs(lore_entries=["A" * 25000])
    )

    assert debug_info["layers_activated"] == [m.get("_layer", "unknown") for m in messages]


def test_no_trim_layers_activated_equals_before_trim(sandbox, monkeypatch):
    _apply_build_stubs(monkeypatch)
    import core.prompt_builder as _pb

    messages, debug_info = _pb.build(
        **_base_build_kwargs(lore_entries=["世界书条目内容"])
    )

    assert debug_info["removed_layers"] == []
    assert debug_info["layers_activated"] == debug_info["layers_before_trim"]
    assert "5.5_lore" in debug_info["layers_activated"]
