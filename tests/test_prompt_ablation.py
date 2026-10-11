from tests.fixtures.public_assets import TEST_CHAR_ID
"""
tests/test_prompt_ablation.py — CC 任务 23 · B9

层级消融开关：只过滤注入，不短路检索。覆盖：

1. 关闭 5.5_lore → build() 输出无该层消息，debug_info["ablated_layers"] 含它。
2. 关闭 6c_episodic → episodic 与 fallback 消息均不出现（两者共用 _layer 名）。
3. set_state(["1_system_prompt"], ...) → ValueError；PUT 层面 422。
4. 开关文件写入损坏 JSON → get_state() 返回全启用，不 raise。
5. perception_block_disabled=True → 1_system_prompt 内容不含感知段。
6. 全开状态（默认文件缺失）→ 输出与改动前完全一致（回归保护）。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from admin.auth import TokenInfo
from admin.routers.settings_misc import router as settings_misc_router
from core.character_loader import Character


def _apply_build_stubs(monkeypatch):
    """Stub all filesystem-touching helpers so build() can run in tests."""
    import core.prompt_builder as _pb
    import core.presence as _pres
    import core.author_note_rotator as _anr
    import core.config_loader as _cl

    monkeypatch.setattr(_pb, "_load_jailbreak", lambda layer=None: "")
    monkeypatch.setattr(_pb, "_load_style_hint", lambda *, char_id="": "")
    monkeypatch.setattr(_pb, "_format_afterglow_soft_hint", lambda uid, char_id=TEST_CHAR_ID: "")
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


@pytest.fixture
def admin_client():
    app = FastAPI()
    app.include_router(settings_misc_router)
    fake_admin = TokenInfo(label="test-admin", scopes=frozenset({"admin"}))
    for route in settings_misc_router.routes:
        for dep in route.dependant.dependencies:
            if hasattr(dep.call, "_required_scopes"):
                app.dependency_overrides[dep.call] = lambda: fake_admin
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# 1. 关闭 5.5_lore
# ─────────────────────────────────────────────────────────────────────────────

def test_disable_lore_layer_removes_it_and_reports_ablated(sandbox, monkeypatch):
    _apply_build_stubs(monkeypatch)
    from core.prompt_ablation import set_state
    import core.prompt_builder as _pb

    set_state(["5.5_lore"], False)

    messages, debug_info = _pb.build(**_base_build_kwargs(lore_entries=["世界书条目内容"]))

    layers = [m.get("_layer") for m in messages]
    assert "5.5_lore" not in layers
    assert "5.5_lore" in debug_info["ablated_layers"]
    full_text = " ".join(m.get("content", "") for m in messages)
    assert "世界书条目内容" not in full_text


def test_lore_layer_present_when_not_disabled(sandbox, monkeypatch):
    _apply_build_stubs(monkeypatch)
    import core.prompt_builder as _pb

    messages, debug_info = _pb.build(**_base_build_kwargs(lore_entries=["世界书条目内容"]))

    layers = [m.get("_layer") for m in messages]
    assert "5.5_lore" in layers
    assert debug_info["ablated_layers"] == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. 关闭 6c_episodic → episodic 与 fallback 均不出现
# ─────────────────────────────────────────────────────────────────────────────

def test_disable_episodic_layer_removes_episodic_result(sandbox, monkeypatch):
    _apply_build_stubs(monkeypatch)
    from core.prompt_ablation import set_state
    import core.prompt_builder as _pb

    set_state(["6c_episodic"], False)

    messages, debug_info = _pb.build(
        **_base_build_kwargs(episodic_result="- 她记得一起看过日落")
    )

    layers = [m.get("_layer") for m in messages]
    assert "6c_episodic" not in layers
    assert "9.5_episodic_top" in layers  # 独立层，不受 6c_episodic 消融影响
    assert "6c_episodic" in debug_info["ablated_layers"]


def test_disable_episodic_layer_removes_fallback_too(sandbox, monkeypatch):
    """6c_episodic_fallback 的消息 _layer 写的也是 6c_episodic，关闭时随之一起消失（预期行为）。"""
    _apply_build_stubs(monkeypatch)
    from core.prompt_ablation import set_state
    import core.prompt_builder as _pb

    set_state(["6c_episodic"], False)

    messages, debug_info = _pb.build(
        **_base_build_kwargs(episodic_fallback_result="- 最近印象最深的一件事")
    )

    layers = [m.get("_layer") for m in messages]
    assert "6c_episodic" not in layers
    full_text = " ".join(m.get("content", "") for m in messages)
    assert "最近印象最深的一件事" not in full_text
    assert "6c_episodic" in debug_info["ablated_layers"]


# ─────────────────────────────────────────────────────────────────────────────
# 3. ALWAYS_ON 校验：set_state raise + PUT 422
# ─────────────────────────────────────────────────────────────────────────────

def test_set_state_rejects_always_on_layer(sandbox):
    from core.prompt_ablation import set_state

    with pytest.raises(ValueError):
        set_state(["1_system_prompt"], False)


def test_put_prompt_ablation_rejects_always_on_layer(sandbox, admin_client):
    resp = admin_client.put(
        "/prompt-ablation",
        json={"disabled_layers": ["1_system_prompt"], "perception_block_disabled": False},
    )
    assert resp.status_code == 422


def test_put_prompt_ablation_rejects_unknown_layer(sandbox, admin_client):
    resp = admin_client.put(
        "/prompt-ablation",
        json={"disabled_layers": ["not_a_real_layer"], "perception_block_disabled": False},
    )
    assert resp.status_code == 422


def test_put_prompt_ablation_accepts_known_layer(sandbox, admin_client):
    resp = admin_client.put(
        "/prompt-ablation",
        json={"disabled_layers": ["5.5_lore"], "perception_block_disabled": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["disabled_layers"] == ["5.5_lore"]

    get_resp = admin_client.get("/prompt-ablation")
    assert get_resp.status_code == 200
    get_body = get_resp.json()
    assert get_body["disabled_layers"] == ["5.5_lore"]
    assert "1_system_prompt" in get_body["always_on"]
    assert any(l["layer"] == "web_recall" for l in get_body["known_layers"])


# ─────────────────────────────────────────────────────────────────────────────
# 4. fail-open：损坏 JSON
# ─────────────────────────────────────────────────────────────────────────────

def test_get_state_fail_open_on_corrupt_json(sandbox):
    from core.prompt_ablation import get_state

    path = sandbox.prompt_layer_ablation()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json::", encoding="utf-8")

    state = get_state()  # 不应 raise
    assert state["disabled_layers"] == set()
    assert state["perception_block_disabled"] is False


def test_get_state_fail_open_on_missing_file(sandbox):
    from core.prompt_ablation import get_state

    assert not sandbox.prompt_layer_ablation().exists()
    state = get_state()
    assert state["disabled_layers"] == set()
    assert state["perception_block_disabled"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 5. perception_block 子开关
# ─────────────────────────────────────────────────────────────────────────────

def test_perception_block_disabled_strips_content(sandbox, monkeypatch):
    _apply_build_stubs(monkeypatch)
    from core.prompt_ablation import set_state
    import core.prompt_builder as _pb

    char = Character(name="Demo", system_prompt="人设正文。{perception_block}结束。")

    set_state([], True)
    messages, _ = _pb.build(
        **_base_build_kwargs(character=char, perception_block="她刚刚在敲键盘")
    )
    sys_msg = next(m for m in messages if m.get("_layer") == "1_system_prompt")
    assert "她刚刚在敲键盘" not in sys_msg["content"]

    set_state([], False)
    messages2, _ = _pb.build(
        **_base_build_kwargs(character=char, perception_block="她刚刚在敲键盘")
    )
    sys_msg2 = next(m for m in messages2 if m.get("_layer") == "1_system_prompt")
    assert "她刚刚在敲键盘" in sys_msg2["content"]


# ─────────────────────────────────────────────────────────────────────────────
# 6. 全开状态回归：默认文件缺失时输出与之前完全一致
# ─────────────────────────────────────────────────────────────────────────────

def test_all_enabled_by_default_matches_explicit_all_enabled(sandbox, monkeypatch):
    _apply_build_stubs(monkeypatch)
    from core.prompt_ablation import set_state
    import core.prompt_builder as _pb

    build_kwargs = _base_build_kwargs(
        lore_entries=["世界书条目"],
        episodic_result="- 一段记忆",
    )

    assert not sandbox.prompt_layer_ablation().exists()
    messages_default, debug_default = _pb.build(**build_kwargs)

    set_state([], False)
    messages_explicit, debug_explicit = _pb.build(**build_kwargs)

    assert messages_default == messages_explicit
    assert debug_default["ablated_layers"] == debug_explicit["ablated_layers"] == []
