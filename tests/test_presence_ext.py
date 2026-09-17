"""
tests/test_presence_ext.py — Brief 29 · "本我"模式：角色卡 presence_ext 四个 per-char 兼容钩子

覆盖 cc-tasks/29-本我模式-角色卡扩展-MCP接入.md §7 的回归项：
1. presence_ext 缺失 → 四个钩子全走默认，现有角色回归零变化。
2. 注入合并：卡关某层 → build 产物无该层；ALWAYS_ON 层不受影响。
3. routing：卡指向存在的 profile → 命中；不存在 → 回落 + warning。
4. proactive=off：gating 全拒发言 proposal；legacy_tick_should_send 同步让路，force 不受影响。
6. 暴露面：卡 tool_categories 覆盖 run_agentic_loop 的工具类别选择。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# 1. character_loader: presence_ext 解析（fail-soft）
# ─────────────────────────────────────────────────────────────────────────────

class TestCharacterLoaderPresenceExt:
    def test_missing_presence_ext_defaults_to_empty_dict(self, tmp_path, monkeypatch):
        import json
        d = tmp_path / "characters"
        d.mkdir()
        (d / "plain.json").write_text(json.dumps({"name": "Plain"}), encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        from core.asset_registry import AssetRegistry
        import core.asset_registry as _reg_mod
        monkeypatch.setattr(_reg_mod, "_registry", AssetRegistry())

        from core.character_loader import load
        char = load("plain")
        assert char.presence_ext == {}

    def test_presence_ext_parsed(self, tmp_path, monkeypatch):
        import json
        d = tmp_path / "characters"
        d.mkdir()
        (d / "benwo.json").write_text(json.dumps({
            "name": "本我",
            "presence_ext": {
                "disabled_layers": ["0_jailbreak"],
                "model_routing": "claude-main",
                "tool_categories": ["info", "mcp"],
                "proactive": "off",
            },
        }), encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        from core.asset_registry import AssetRegistry
        import core.asset_registry as _reg_mod
        monkeypatch.setattr(_reg_mod, "_registry", AssetRegistry())

        from core.character_loader import load
        char = load("benwo")
        assert char.presence_ext["disabled_layers"] == ["0_jailbreak"]
        assert char.presence_ext["model_routing"] == "claude-main"
        assert char.presence_ext["tool_categories"] == ["info", "mcp"]
        assert char.presence_ext["proactive"] == "off"

    def test_non_dict_presence_ext_ignored(self, tmp_path, monkeypatch):
        import json
        d = tmp_path / "characters"
        d.mkdir()
        (d / "bad.json").write_text(json.dumps({"name": "Bad", "presence_ext": "oops"}), encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        from core.asset_registry import AssetRegistry
        import core.asset_registry as _reg_mod
        monkeypatch.setattr(_reg_mod, "_registry", AssetRegistry())

        from core.character_loader import load
        char = load("bad")
        assert char.presence_ext == {}


# ─────────────────────────────────────────────────────────────────────────────
# helpers: fake pipeline registration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _FakeChar:
    name: str = "Fake"
    gender: str = "neutral"
    presence_ext: dict = field(default_factory=dict)


@dataclass
class _FakePipeline:
    character: object


@pytest.fixture(autouse=True)
def _clear_pipeline_registry():
    from core import pipeline_registry
    pipeline_registry.register(None)
    yield
    pipeline_registry.register(None)


def _register_fake_char(**presence_ext_kwargs):
    from core import pipeline_registry
    char = _FakeChar(presence_ext=presence_ext_kwargs)
    pipeline_registry.register(_FakePipeline(character=char))
    return char


# ─────────────────────────────────────────────────────────────────────────────
# 4 (proactive helper). character_loader.is_proactive_disabled
# ─────────────────────────────────────────────────────────────────────────────

class TestIsProactiveDisabled:
    def test_no_pipeline_registered_is_false(self):
        from core.character_loader import is_proactive_disabled
        assert is_proactive_disabled() is False

    def test_default_full_is_false(self):
        _register_fake_char(proactive="full")
        from core.character_loader import is_proactive_disabled
        assert is_proactive_disabled() is False

    def test_off_is_true(self):
        _register_fake_char(proactive="off")
        from core.character_loader import is_proactive_disabled
        assert is_proactive_disabled() is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. prompt_ablation: 全局 ∪ 活跃角色卡 disabled_layers
# ─────────────────────────────────────────────────────────────────────────────

class TestPromptAblationMerge:
    def test_no_global_file_uses_char_layers_only(self, monkeypatch):
        import core.prompt_ablation as ab
        monkeypatch.setattr(ab, "_read_raw", lambda: None)
        _register_fake_char(disabled_layers=["2_jailbreak"])

        state = ab.get_state()
        assert state["disabled_layers"] == {"2_jailbreak"}

    def test_union_of_global_and_char(self, monkeypatch):
        import core.prompt_ablation as ab
        monkeypatch.setattr(ab, "_read_raw", lambda: {
            "disabled_layers": ["0_jailbreak"],
            "perception_block_disabled": False,
        })
        _register_fake_char(disabled_layers=["2_jailbreak", "11_jailbreak"])

        state = ab.get_state()
        assert state["disabled_layers"] == {"0_jailbreak", "2_jailbreak", "11_jailbreak"}

    def test_no_active_char_falls_back_to_global_only(self, monkeypatch):
        import core.prompt_ablation as ab
        monkeypatch.setattr(ab, "_read_raw", lambda: {
            "disabled_layers": ["0_jailbreak"],
            "perception_block_disabled": False,
        })
        state = ab.get_state()
        assert state["disabled_layers"] == {"0_jailbreak"}

    def test_always_on_still_protected_by_consumer(self, monkeypatch):
        """ALWAYS_ON 保护在消费点（prompt_builder.build）判断，get_state 本身不过滤——
        角色卡把 1_system_prompt 塞进 disabled_layers 也会原样出现在返回集合里，
        由调用方按 `not in ALWAYS_ON` 二次把关（与全局开关文件同语义）。"""
        import core.prompt_ablation as ab
        monkeypatch.setattr(ab, "_read_raw", lambda: None)
        _register_fake_char(disabled_layers=["1_system_prompt"])

        state = ab.get_state()
        assert "1_system_prompt" in state["disabled_layers"]
        from core.prompt_ablation import ALWAYS_ON
        assert "1_system_prompt" in ALWAYS_ON


# ─────────────────────────────────────────────────────────────────────────────
# 3. model_registry: per-char model_routing 覆盖
# ─────────────────────────────────────────────────────────────────────────────

_MP_CONFIG = {
    "active_routing": "default",
    "defaults": {},
    "presets": {
        "ds": {"provider_kind": "deepseek", "base_url": "", "api_key": "", "model": "ds-chat"},
        "claude": {"provider_kind": "anthropic_compat", "base_url": "", "api_key": "", "model": "claude"},
    },
    "routing_profiles": {
        "default": {"chat": "ds", "intent": "ds"},
        "claude-main": {"chat": "claude", "intent": "ds"},
    },
}


class TestModelRegistryRoutingOverride:
    def test_no_char_override_uses_active_routing(self, monkeypatch):
        import core.model_registry as mr
        monkeypatch.setattr(mr, "_get_preset_config", lambda: _MP_CONFIG)
        assert mr._resolve_preset_name("chat") == "ds"

    def test_char_override_existing_profile_wins(self, monkeypatch):
        import core.model_registry as mr
        monkeypatch.setattr(mr, "_get_preset_config", lambda: _MP_CONFIG)
        _register_fake_char(model_routing="claude-main")
        assert mr._resolve_preset_name("chat") == "claude"
        # 杂活类别也随 profile 走（brief 3.2 明确的预期行为）
        assert mr._resolve_preset_name("intent") == "ds"

    def test_char_override_missing_profile_falls_back(self, monkeypatch, caplog):
        import core.model_registry as mr
        monkeypatch.setattr(mr, "_get_preset_config", lambda: _MP_CONFIG)
        _register_fake_char(model_routing="does-not-exist")
        assert mr._resolve_preset_name("chat") == "ds"


# ─────────────────────────────────────────────────────────────────────────────
# 4. scheduler proactive=off 闸门
# ─────────────────────────────────────────────────────────────────────────────

def _make_proposal(trigger_name: str, urgency: float = 0.5):
    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState
    return TriggerProposal(
        trigger_name=trigger_name,
        urgency=urgency,
        topic_source="test",
        requires_state=[TriggerState.QUIET],
    )


def _patch_decide_env(monkeypatch, *, user_active: bool = False, dnd_active: bool = False):
    import core.scheduler.loop as _loop
    import core.scheduler.triggers.dnd as _dnd
    from core.scheduler.state_machine import TriggerState

    monkeypatch.setattr(_loop, "_user_active_recently", lambda *a, **kw: user_active)
    monkeypatch.setattr(_dnd, "is_dnd", lambda uid: dnd_active)
    monkeypatch.setattr("core.scheduler.gating.get_current_state", lambda uid: TriggerState.QUIET)
    monkeypatch.setattr("core.scheduler.gating.is_trigger_ready", lambda name: True)


class TestGatingProactiveGate:
    def test_proactive_off_rejects_all_proposals(self, monkeypatch):
        from core.scheduler.gating import _decide
        _patch_decide_env(monkeypatch)
        _register_fake_char(proactive="off")

        picked, reason, _candidates = _decide("u1", [_make_proposal("morning_greeting")])
        assert picked is None
        assert reason == "proactive_off"

    def test_proactive_full_unaffected(self, monkeypatch):
        from core.scheduler.gating import _decide
        _patch_decide_env(monkeypatch)
        _register_fake_char(proactive="full")

        picked, reason, _candidates = _decide("u1", [_make_proposal("morning_greeting")])
        assert picked is not None
        assert picked.trigger_name == "morning_greeting"

    def test_empty_proposals_not_affected_by_proactive_check(self, monkeypatch):
        """proposals 为空时走原有 no_candidates 分支，不因为空列表触碰角色加载。"""
        from core.scheduler.gating import _decide
        _patch_decide_env(monkeypatch)
        _register_fake_char(proactive="off")

        picked, reason, _candidates = _decide("u1", [])
        assert picked is None
        assert reason == "no_candidates"


class TestLegacyTickShouldSend:
    def test_force_bypasses_proactive_off(self, monkeypatch):
        from core.scheduler.execution import legacy_tick_should_send
        _register_fake_char(proactive="off")
        assert legacy_tick_should_send(force=True) is True

    def test_non_force_live_mode_always_false(self, monkeypatch):
        import core.scheduler.execution as ex
        monkeypatch.setattr(ex, "EXECUTE_MODE", "live")
        _register_fake_char(proactive="off")
        assert legacy_tick_should_send_result(ex) is False

    def test_non_force_non_live_respects_proactive_off(self, monkeypatch):
        import core.scheduler.execution as ex
        monkeypatch.setattr(ex, "EXECUTE_MODE", "test")
        _register_fake_char(proactive="off")
        assert ex.legacy_tick_should_send(force=False) is False

    def test_non_force_non_live_full_proactive_sends(self, monkeypatch):
        import core.scheduler.execution as ex
        monkeypatch.setattr(ex, "EXECUTE_MODE", "test")
        _register_fake_char(proactive="full")
        assert ex.legacy_tick_should_send(force=False) is True


def legacy_tick_should_send_result(ex_module):
    return ex_module.legacy_tick_should_send(force=False)


# ─────────────────────────────────────────────────────────────────────────────
# 6. run_agentic_loop: per-char tool_categories 覆盖
# ─────────────────────────────────────────────────────────────────────────────

class TestRunAgenticLoopToolCategories:
    def _make_pipeline(self, character=None):
        from core.pipeline import Pipeline
        pl = Pipeline.__new__(Pipeline)
        pl.character = character
        return pl

    @pytest.fixture(autouse=True)
    def _patch_char_name(self, monkeypatch):
        monkeypatch.setattr("core.character_name_provider.get_char_name", lambda char_id=None: "小星")

    def test_char_tool_categories_path_c_override_global(self, monkeypatch):
        from core.llm_client import ChatTurn

        cfg = {"tool_loop": {"max_steps": 5, "total_timeout_s": 90,
                              "categories": ["info", "desktop", "memory"], "exclude_tools": []}}
        monkeypatch.setattr("core.config_loader.get_config", lambda: cfg)

        seen_categories = []

        def _fake_schema(categories=None):
            seen_categories.append(categories)
            return []
        monkeypatch.setattr("core.tool_dispatcher.get_tools_schema", _fake_schema)

        async def _fake_chat_turn(messages, tools, **kw):
            return ChatTurn(content="done", tool_calls=[], assistant_message={})
        monkeypatch.setattr("core.llm_client.chat_turn", _fake_chat_turn)

        async def _fake_retry(loop_msgs, text, **_kw):
            return text
        character = _FakeChar(presence_ext={"tool_categories_path_c": ["info", "mcp"]})
        monkeypatch.setattr("core.character_loader.load", lambda _char_id: character)
        pl = self._make_pipeline(character=character)
        monkeypatch.setattr(pl, "_anti_collapse_prefix_retry", _fake_retry)

        import asyncio

        class _Sess:
            status = "idle"

        result = asyncio.get_event_loop().run_until_complete(
            pl.run_agentic_loop([{"role": "user", "content": "hi"}], uid="u1", char_id="c1", session_state=_Sess())
        )
        assert result == "done"
        assert seen_categories == [["info", "mcp"]]

    def test_no_char_override_uses_global_categories(self, monkeypatch):
        from core.llm_client import ChatTurn

        cfg = {"tool_loop": {"max_steps": 5, "total_timeout_s": 90,
                              "categories": ["info", "desktop", "memory"], "exclude_tools": []}}
        monkeypatch.setattr("core.config_loader.get_config", lambda: cfg)

        seen_categories = []

        def _fake_schema(categories=None):
            seen_categories.append(categories)
            return []
        monkeypatch.setattr("core.tool_dispatcher.get_tools_schema", _fake_schema)

        async def _fake_chat_turn(messages, tools, **kw):
            return ChatTurn(content="done", tool_calls=[], assistant_message={})
        monkeypatch.setattr("core.llm_client.chat_turn", _fake_chat_turn)

        async def _fake_retry(loop_msgs, text, **_kw):
            return text
        pl = self._make_pipeline(character=_FakeChar(presence_ext={}))
        monkeypatch.setattr(pl, "_anti_collapse_prefix_retry", _fake_retry)

        import asyncio

        class _Sess:
            status = "idle"

        result = asyncio.get_event_loop().run_until_complete(
            pl.run_agentic_loop([{"role": "user", "content": "hi"}], uid="u1", char_id="c1", session_state=_Sess())
        )
        assert result == "done"
        assert seen_categories == [["info", "desktop", "memory"]]

    def test_nudge_hint_injected_before_last_message(self, monkeypatch):
        from core.llm_client import ChatTurn

        cfg = {"tool_loop": {"max_steps": 5, "total_timeout_s": 90,
                              "categories": ["info"], "exclude_tools": [], "nudge_hint": True}}
        monkeypatch.setattr("core.config_loader.get_config", lambda: cfg)
        monkeypatch.setattr("core.tool_dispatcher.get_tools_schema", lambda categories=None: [])

        seen_messages = []

        async def _fake_chat_turn(messages, tools, **kw):
            seen_messages.append([dict(m) for m in messages])
            return ChatTurn(content="done", tool_calls=[], assistant_message={})
        monkeypatch.setattr("core.llm_client.chat_turn", _fake_chat_turn)

        async def _fake_retry(loop_msgs, text, **_kw):
            return text
        pl = self._make_pipeline(character=_FakeChar(presence_ext={}))
        monkeypatch.setattr(pl, "_anti_collapse_prefix_retry", _fake_retry)

        import asyncio

        class _Sess:
            status = "idle"

        asyncio.get_event_loop().run_until_complete(
            pl.run_agentic_loop([{"role": "user", "content": "hi"}], uid="u1", char_id="c1", session_state=_Sess())
        )
        msgs = seen_messages[0]
        assert msgs[-1]["role"] == "user"
        assert msgs[-2]["_layer"] == "11.5_tool_nudge"

    def test_nudge_hint_disabled_by_config(self, monkeypatch):
        from core.llm_client import ChatTurn

        cfg = {"tool_loop": {"max_steps": 5, "total_timeout_s": 90,
                              "categories": ["info"], "exclude_tools": [], "nudge_hint": False}}
        monkeypatch.setattr("core.config_loader.get_config", lambda: cfg)
        monkeypatch.setattr("core.tool_dispatcher.get_tools_schema", lambda categories=None: [])

        seen_messages = []

        async def _fake_chat_turn(messages, tools, **kw):
            seen_messages.append([dict(m) for m in messages])
            return ChatTurn(content="done", tool_calls=[], assistant_message={})
        monkeypatch.setattr("core.llm_client.chat_turn", _fake_chat_turn)

        async def _fake_retry(loop_msgs, text, **_kw):
            return text
        pl = self._make_pipeline(character=_FakeChar(presence_ext={}))
        monkeypatch.setattr(pl, "_anti_collapse_prefix_retry", _fake_retry)

        import asyncio

        class _Sess:
            status = "idle"

        asyncio.get_event_loop().run_until_complete(
            pl.run_agentic_loop([{"role": "user", "content": "hi"}], uid="u1", char_id="c1", session_state=_Sess())
        )
        msgs = seen_messages[0]
        assert all(m.get("_layer") != "11.5_tool_nudge" for m in msgs)
