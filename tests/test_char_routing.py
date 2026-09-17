"""
tests/test_char_routing.py — Brief 30 · LLM 路由的 char 维度穿线

覆盖 cc-tasks/30-LLM路由char维度穿线.md §3 的 5 项测试：
1. char_id=None → 与现行为逐字节一致（活跃角色 override 生效）。
2. 显式 char_id=X（X 卡带 model_routing）且活跃角色是 Y → 解析用 X 的 profile。
3. X 卡无 presence_ext → 回落全局 active_routing（不是 Y 的 override）。
4. 日记多角色：白名单两个角色、卡路由不同 → 两次生成各用各的 preset。
5. 缓存：两个 preset 交替解析，client 实例各自稳定复用、互不串。
"""
from __future__ import annotations

import types
from dataclasses import dataclass, field

import pytest


_MP_CONFIG = {
    "active_routing": "default",
    "defaults": {},
    "presets": {
        "ds": {
            "provider_kind": "deepseek",
            "base_url": "",
            "api_key": "test-only-placeholder",
            "model": "ds-chat",
        },
        "claude": {
            "provider_kind": "anthropic_compat",
            "base_url": "",
            "api_key": "test-only-placeholder",
            "model": "claude",
        },
    },
    "routing_profiles": {
        "default": {"chat": "ds", "intent": "ds"},
        "claude-main": {"chat": "claude", "intent": "ds"},
    },
}


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


def _register_active_char(**presence_ext_kwargs):
    """注册"活跃角色"——char_id=None 路径读的是它（Brief 29 既有语义）。"""
    from core import pipeline_registry
    char = _FakeChar(presence_ext=presence_ext_kwargs)
    pipeline_registry.register(_FakePipeline(character=char))
    return char


def _patch_char_card(monkeypatch, cards: dict):
    """monkeypatch character_loader.load(char_id) → 对应角色卡（显式 char_id 路径读它）。

    未在映射里的 char_id → 抛 ValueError（模拟未注册/加载失败），走 fail-soft 回落。
    """
    def _fake_load(char_id):
        if char_id not in cards:
            raise ValueError(f"unknown char {char_id!r}")
        return _FakeChar(presence_ext=cards[char_id])

    monkeypatch.setattr("core.character_loader.load", _fake_load)


# ─────────────────────────────────────────────────────────────────────────────
# 1. char_id=None → 与现行为逐字节一致（活跃角色 override 生效）
# ─────────────────────────────────────────────────────────────────────────────

class TestCharIdNoneMatchesCurrentBehavior:
    def test_no_active_char_no_override(self, monkeypatch):
        import core.model_registry as mr
        monkeypatch.setattr(mr, "_get_preset_config", lambda: _MP_CONFIG)
        assert mr._resolve_preset_name("chat") == "ds"
        assert mr._resolve_preset_name("chat", char_id=None) == "ds"

    def test_active_char_override_still_applies_when_char_id_omitted_or_none(self, monkeypatch):
        import core.model_registry as mr
        monkeypatch.setattr(mr, "_get_preset_config", lambda: _MP_CONFIG)
        _register_active_char(model_routing="claude-main")

        # 不传 char_id、显式传 None：逐字节一致，都读活跃角色卡的 override
        assert mr._resolve_preset_name("chat") == "claude"
        assert mr._resolve_preset_name("chat", char_id=None) == "claude"


# ─────────────────────────────────────────────────────────────────────────────
# 2. 显式 char_id=X（带 model_routing）覆盖活跃角色 Y 的路由
# ─────────────────────────────────────────────────────────────────────────────

class TestExplicitCharIdUsesOwnCard:
    def test_explicit_char_routes_by_its_own_card_not_active_char(self, monkeypatch):
        import core.model_registry as mr
        monkeypatch.setattr(mr, "_get_preset_config", lambda: _MP_CONFIG)
        _patch_char_card(monkeypatch, {"X": {"model_routing": "claude-main"}})
        _register_active_char()  # 活跃角色 Y，无 override（走全局 default）

        assert mr._resolve_preset_name("chat", char_id="X") == "claude"

    def test_explicit_char_routing_wins_even_when_active_char_differs(self, monkeypatch):
        import core.model_registry as mr
        monkeypatch.setattr(mr, "_get_preset_config", lambda: _MP_CONFIG)
        _patch_char_card(monkeypatch, {"X": {"model_routing": "claude-main"}})
        _register_active_char(model_routing="default")  # 活跃角色 Y 显式指向 default

        assert mr._resolve_preset_name("chat", char_id="X") == "claude"


# ─────────────────────────────────────────────────────────────────────────────
# 3. X 卡无 presence_ext → 回落全局 active_routing（不是 Y 的 override）
# ─────────────────────────────────────────────────────────────────────────────

class TestExplicitCharFallsBackToGlobalNotActiveOverride:
    def test_char_without_model_routing_field_falls_back_to_global(self, monkeypatch):
        import core.model_registry as mr
        monkeypatch.setattr(mr, "_get_preset_config", lambda: _MP_CONFIG)
        _patch_char_card(monkeypatch, {"X": {}})  # X 卡存在但无 model_routing 字段
        _register_active_char(model_routing="claude-main")  # 活跃角色 Y 指向 claude

        # 显式传 X：X 无 override → 回落全局 active_routing="default"→"ds"，
        # 绝不能读到 Y 的 "claude-main" override（这是 §2.1 的核心行为边界）
        assert mr._resolve_preset_name("chat", char_id="X") == "ds"

    def test_char_load_failure_falls_back_to_global(self, monkeypatch):
        import core.model_registry as mr
        monkeypatch.setattr(mr, "_get_preset_config", lambda: _MP_CONFIG)
        _patch_char_card(monkeypatch, {})  # 任何 char_id 都会 raise（未注册/文件缺失）
        _register_active_char(model_routing="claude-main")

        assert mr._resolve_preset_name("chat", char_id="unregistered") == "ds"


# ─────────────────────────────────────────────────────────────────────────────
# 4. 日记多角色：白名单两个角色、卡路由不同 → 两次生成各用各的 preset
# ─────────────────────────────────────────────────────────────────────────────

def _make_fake_model_client(name: str, content: str):
    from core.model_registry import ModelClient

    async def fake_create(**kwargs):
        msg = types.SimpleNamespace(content=content)
        choice = types.SimpleNamespace(message=msg)
        return types.SimpleNamespace(choices=[choice])

    completions = types.SimpleNamespace(create=fake_create)
    chat_obj = types.SimpleNamespace(completions=completions)
    fake_client = types.SimpleNamespace(chat=chat_obj)

    return ModelClient(
        name=name,
        provider_kind="deepseek",
        model=f"{name}-model",
        tool_call_mode="function_calling",
        prompt_style="narrative",
        params={},
        client=fake_client,
    )


@pytest.mark.asyncio
async def test_diary_multi_char_each_generation_uses_own_preset(monkeypatch, sandbox):
    """白名单两个角色、卡路由不同 → 各自生成日记时 get_model_client 收到各自的 char_id
    （Brief 30 · §1 现存 bug 的修复验证：修复前两次调用都不带 char_id）。
    """
    from core.scheduler.triggers import time_based

    monkeypatch.setattr(
        "core.scheduler.triggers.time_based.get_char_name",
        lambda char_id: char_id,
    )
    monkeypatch.setattr(
        "core.memory.event_log.get_recent_days",
        lambda oid, days=1, **kw: "## 14:30\n**用户**：在干嘛\n**A**：想你了\n---\n",
    )
    monkeypatch.setattr("core.integrity_check.check_diary_facts", lambda text: [])

    _presets = {"char_a": "preset-a", "char_b": "preset-b"}
    seen: list[tuple[str, str | None]] = []

    def _fake_get_model_client(call_category, *, char_id=None):
        seen.append((call_category, char_id))
        preset = _presets.get(char_id, "preset-default")
        return _make_fake_model_client(preset, f"内容-{preset}")

    monkeypatch.setattr("core.llm_client.get_model_client", _fake_get_model_client)

    await time_based._generate_and_store_diary("owner1", "char_a")
    await time_based._generate_and_store_diary("owner1", "char_b")

    assert seen == [
        ("chat", "char_a"), ("chat", "char_a"),
        ("chat", "char_b"), ("chat", "char_b"),
    ]

    diary_a = sandbox.yexuan_inner_diary(char_id="char_a")
    diary_b = sandbox.yexuan_inner_diary(char_id="char_b")
    assert any(diary_a.iterdir())
    assert any(diary_b.iterdir())


# ─────────────────────────────────────────────────────────────────────────────
# 5. 缓存：两个 preset 交替解析，client 实例各自稳定复用、互不串
# ─────────────────────────────────────────────────────────────────────────────

class TestClientCacheStableAcrossChars:
    def test_alternating_chars_reuse_stable_clients_with_no_crosstalk(self, monkeypatch):
        import core.model_registry as mr
        monkeypatch.setattr(mr, "_get_preset_config", lambda: _MP_CONFIG)
        monkeypatch.setattr(mr, "_model_clients", {})
        _patch_char_card(monkeypatch, {
            "charA": {"model_routing": "claude-main"},
            "charB": {},  # 无 override → 全局 default → ds
        })

        mc_a1 = mr.get_model_client("chat", char_id="charA")
        mc_b1 = mr.get_model_client("chat", char_id="charB")
        mc_a2 = mr.get_model_client("chat", char_id="charA")
        mc_b2 = mr.get_model_client("chat", char_id="charB")

        assert mc_a1.name == "claude"
        assert mc_b1.name == "ds"
        assert mc_a1 is mc_a2, "同一 preset 应复用同一 ModelClient 实例"
        assert mc_b1 is mc_b2, "同一 preset 应复用同一 ModelClient 实例"
        assert mc_a1 is not mc_b1, "不同 preset 的 client 不得互相串用"


# ─────────────────────────────────────────────────────────────────────────────
# 6. specialized LLM：已有会话角色贯穿最终模型解析，中途切 active 不改归属
# ─────────────────────────────────────────────────────────────────────────────

def _spy_get_model_client(monkeypatch, *, switch_active=None):
    """Wrap get_model_client so tests can assert char_id actually reached resolution.

    If char_id exists at the call site but is dropped before model resolution,
    this spy never sees it and the test fails. Optional switch_active runs after
    the first resolution to simulate a mid-task active-character change.
    """
    import core.model_registry as mr

    seen: list[tuple[str, str | None]] = []
    original = mr.get_model_client

    def _fake(call_category, *, char_id=None, preset_name=None):
        seen.append((call_category, char_id))
        if switch_active is not None and len(seen) == 1:
            switch_active()
        return original(call_category, char_id=char_id, preset_name=preset_name)

    monkeypatch.setattr(mr, "get_model_client", _fake)
    return seen


@pytest.mark.asyncio
async def test_dream_solo_routes_owned_char_after_active_switch(monkeypatch, sandbox):
    """Dream solo 主 LLM 用 session 冻结角色；任务中途切 active C 仍解析原角色。"""
    from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_THIRD_CHAR_ID
    from core.dream.dream_state import DreamStatus, write_state
    from core.dream.dream_pipeline import dream_turn
    import core.model_registry as mr

    monkeypatch.setattr(mr, "_get_preset_config", lambda: _MP_CONFIG)
    _patch_char_card(monkeypatch, {
        TEST_CHAR_ID: {"model_routing": "claude-main"},
        TEST_THIRD_CHAR_ID: {"model_routing": "default"},
    })
    _register_active_char(model_routing="default")
    seen = _spy_get_model_client(
        monkeypatch,
        switch_active=lambda: _register_active_char(model_routing="default"),
    )

    write_state("owner-dream-e", {
        "user_id": "owner-dream-e",
        "status": DreamStatus.DREAM_ACTIVE.value,
        "dream_id": "dream-owned-a",
        "char_id": TEST_CHAR_ID,
        "frozen_world": "reality_derived",
        "lucid_mode": "non_lucid",
        "dream_mode": "sandbox",
        "context_snapshot": {
            "created_at": 0.0,
            "user_id": "owner-dream-e",
            "yexuan_awareness": "lucid_shared",
            "boundary": "dream_only",
            "entry_reason": "test",
            "memory_access": "none",
            "relationship_state": {},
            "recent_reality_context": "",
            "episodic_summary": "",
            "mid_term_context": "",
            "profile_impression": "",
        },
    })
    fake_pipeline = _FakePipeline(character=_FakeChar())
    from core import pipeline_registry
    pipeline_registry.register(fake_pipeline)

    async def _no_network_chat(messages, **kwargs):
        if "char_id" not in kwargs:
            raise AssertionError("char_id existed on the dream session but was not passed into llm_client.chat")
        assert kwargs["char_id"] == TEST_CHAR_ID
        assert kwargs.get("call_category") == "chat"
        mc = mr.get_model_client(kwargs.get("call_category") or "chat", char_id=kwargs["char_id"])
        assert mc.name == "claude"
        return "梦境回复"

    monkeypatch.setattr("core.llm_client.chat", _no_network_chat)
    result = await dream_turn("owner-dream-e", "梦境内容")
    assert result.get("reply") == "梦境回复"
    assert not result.get("error")
    assert seen, "char_id existed at the call site but was not passed into get_model_client"
    assert all(char_id == TEST_CHAR_ID for _cat, char_id in seen), seen
    assert TEST_THIRD_CHAR_ID not in {char_id for _cat, char_id in seen}

    mood_path = sandbox.mood_state(char_id=TEST_CHAR_ID)
    assert not mood_path.exists(), "Dream turn wrote reality mood_state"
    for label, dir_path in [
        ("episodic_memory", sandbox.episodic_memory()),
        ("history", sandbox.history()),
        ("mid_term", sandbox.mid_term()),
    ]:
        if dir_path.exists():
            files = list(dir_path.glob("*owner-dream-e*"))
            assert not files, f"Dream turn wrote Reality evidence in {label}: {files}"


@pytest.mark.asyncio
async def test_dream_invariants_observe_routes_owned_char(monkeypatch, sandbox):
    from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_THIRD_CHAR_ID
    from core.dream import invariants
    import core.model_registry as mr

    monkeypatch.setattr(mr, "_get_preset_config", lambda: _MP_CONFIG)
    _patch_char_card(monkeypatch, {TEST_CHAR_ID: {"model_routing": "claude-main"}})
    _register_active_char(model_routing="default")
    seen = _spy_get_model_client(monkeypatch)
    monkeypatch.setattr(invariants, "_archive_turns", lambda *_a, **_k: [
        {"role": "user", "content": "她靠近了"},
        {"role": "assistant", "content": "他先等对方说完"},
    ])
    monkeypatch.setattr(invariants, "merge", lambda *_a, **_k: None)

    async def _chat(messages, **kwargs):
        if "char_id" not in kwargs:
            raise AssertionError("char_id existed on observe() but was not passed into llm_client.chat")
        assert kwargs["char_id"] == TEST_CHAR_ID
        assert kwargs.get("call_category") == "summary"
        mc = mr.get_model_client("summary", char_id=kwargs["char_id"])
        assert mc.name == "claude"
        _register_active_char(model_routing="default")
        return '{"items":[]}'

    monkeypatch.setattr("core.llm_client.chat", _chat)
    await invariants.observe("u", "d1", world_id="cat", char_id=TEST_CHAR_ID)
    assert seen == [("summary", TEST_CHAR_ID)]
    assert TEST_THIRD_CHAR_ID not in {c for _cat, c in seen}


@pytest.mark.asyncio
async def test_perform_mapper_routes_owned_char_after_active_switch(monkeypatch):
    from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_THIRD_CHAR_ID
    from core.perform_mapper import enrich_say_segments
    from core.narrative_parser import build_say_segments
    import core.model_registry as mr

    monkeypatch.setattr(mr, "_get_preset_config", lambda: _MP_CONFIG)
    _patch_char_card(monkeypatch, {TEST_CHAR_ID: {"model_routing": "claude-main"}})
    _register_active_char(model_routing="default")
    seen = _spy_get_model_client(
        monkeypatch,
        switch_active=lambda: _register_active_char(model_routing="default"),
    )
    monkeypatch.setattr(
        "core.perform_mapper.get_config",
        lambda: {"performance_mapping": {"enabled": True, "provider": "llm", "llm_timeout_sec": 3.0}},
    )

    async def _chat(messages, **kwargs):
        if "char_id" not in kwargs:
            raise AssertionError("char_id existed on enrich_say_segments but was not passed into llm_client.chat")
        assert kwargs["char_id"] == TEST_CHAR_ID
        assert kwargs.get("call_category") == "perform"
        mc = mr.get_model_client("perform", char_id=kwargs["char_id"])
        assert mc.name == "claude"
        return "[{}]"

    monkeypatch.setattr("core.llm_client.chat", _chat)
    reply = "*她歪着头*\n才、才没有等你很久呢"
    _content, say_segs = build_say_segments(reply)
    await enrich_say_segments(reply, say_segs, char_id=TEST_CHAR_ID)
    assert seen == [("perform", TEST_CHAR_ID)]
    assert TEST_THIRD_CHAR_ID not in {c for _cat, c in seen}


@pytest.mark.asyncio
async def test_coplay_close_summary_routes_owned_char_after_active_switch(monkeypatch, sandbox):
    from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_THIRD_CHAR_ID
    from core.coplay.session_close import _summarize_session
    import core.model_registry as mr

    monkeypatch.setattr(mr, "_get_preset_config", lambda: _MP_CONFIG)
    _patch_char_card(monkeypatch, {TEST_CHAR_ID: {"model_routing": "claude-main"}})
    _register_active_char(model_routing="default")
    seen = _spy_get_model_client(
        monkeypatch,
        switch_active=lambda: _register_active_char(model_routing="default"),
    )

    async def _chat(messages, **kwargs):
        if "char_id" not in kwargs:
            raise AssertionError("char_id existed on _summarize_session but was not passed into llm_client.chat")
        assert kwargs["char_id"] == TEST_CHAR_ID
        assert kwargs.get("call_category") == "summary"
        mc = mr.get_model_client("summary", char_id=kwargs["char_id"])
        assert mc.name == "claude"
        return "探索了地图"

    monkeypatch.setattr("core.llm_client.chat", _chat)
    gist = await _summarize_session("黑暗之魂", ["打败了第一个boss"], char_id=TEST_CHAR_ID)
    assert gist == "探索了地图"
    assert seen == [("summary", TEST_CHAR_ID)]
    assert TEST_THIRD_CHAR_ID not in {c for _cat, c in seen}


@pytest.mark.asyncio
async def test_anti_collapse_prefix_retry_forwards_owned_char(monkeypatch):
    from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_THIRD_CHAR_ID
    from core.pipeline import Pipeline
    import core.model_registry as mr

    monkeypatch.setattr(mr, "_get_preset_config", lambda: _MP_CONFIG)
    _patch_char_card(monkeypatch, {TEST_CHAR_ID: {"model_routing": "claude-main"}})
    _register_active_char(model_routing="default")
    seen = _spy_get_model_client(monkeypatch)
    pipeline = Pipeline.__new__(Pipeline)
    messages = [
        {"role": "assistant", "content": "嗯。第一条", "_layer": "9_history"},
        {"role": "assistant", "content": "第二条", "_layer": "9_history", "_raw_content": "嗯。第二条"},
        {"role": "assistant", "content": "第三条", "_layer": "9_history", "_raw_content": "嗯。第三条"},
    ]

    async def _chat(retry_messages, **kwargs):
        if "char_id" not in kwargs:
            raise AssertionError("char_id existed on prefix retry but was not passed into llm_client.chat")
        assert kwargs["char_id"] == TEST_CHAR_ID
        mc = mr.get_model_client("chat", char_id=kwargs["char_id"])
        assert mc.name == "claude"
        _register_active_char(model_routing="default")
        return "换了个开头说话"

    monkeypatch.setattr("core.llm_client.chat", _chat)
    result = await pipeline._anti_collapse_prefix_retry(
        messages, "嗯。原始回复", char_id=TEST_CHAR_ID, is_proactive=False,
    )
    assert result == "换了个开头说话"
    assert seen == [("chat", TEST_CHAR_ID)]
    assert TEST_THIRD_CHAR_ID not in {c for _cat, c in seen}


@pytest.mark.asyncio
async def test_agentic_final_run_llm_forwards_owned_char(monkeypatch):
    from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_THIRD_CHAR_ID
    from core.pipeline import Pipeline
    from core.llm_client import ChatTurn

    captured: dict = {}

    async def _fake_chat_turn(messages, tools, **kwargs):
        assert kwargs.get("char_id") == TEST_CHAR_ID
        discovery = [
            (schema.get("function") or schema).get("name")
            for schema in tools
            if str((schema.get("function") or schema).get("name") or "").startswith("load_tools_")
        ]
        if discovery:
            calls = [{"id": name, "name": name, "arguments": {}} for name in discovery]
            return ChatTurn(
                content="",
                tool_calls=calls,
                assistant_message={"role": "assistant", "content": None},
            )
        return ChatTurn(
            content="工具已查完",
            tool_calls=[{"id": "t1", "name": "web_search", "arguments": {"query": "x"}}],
            assistant_message={"role": "assistant", "content": None, "tool_calls": [{
                "id": "t1", "type": "function",
                "function": {"name": "web_search", "arguments": "{\"query\":\"x\"}"},
            }]},
        )

    async def _fake_execute(*_a, **_kw):
        from core.tool_dispatcher import ToolExecutionOutcome
        return ToolExecutionOutcome(status="tool_executed", result="工具已执行：ok")

    async def _fake_run_llm(self, messages, *, char_id=None, is_proactive=False):
        captured["char_id"] = char_id
        captured["is_proactive"] = is_proactive
        if char_id is None:
            raise AssertionError("char_id existed on the agentic loop but was not passed into run_llm")
        return "收尾回复"

    monkeypatch.setattr("core.config_loader.get_config", lambda: {
        "tool_loop": {
            "max_steps": 5,
            "total_timeout_s": 90,
            "categories": ["info"],
            "exclude_tools": [],
            "nudge_hint": False,
        },
        "presets": {},
        "active_routing": "default",
        "routing_profiles": {"default": {"chat": "ds"}},
    })
    monkeypatch.setattr("core.model_registry._get_preset_config", lambda: {
        "presets": {},
        "active_routing": "default",
        "routing_profiles": {"default": {"chat": "ds"}},
    })
    monkeypatch.setattr("core.model_registry._resolve_preset_name", lambda *a, **k: "legacy")
    monkeypatch.setattr("core.character_loader.load", lambda _cid: _FakeChar())
    monkeypatch.setattr("core.character_name_provider.get_char_name", lambda *_a, **_k: "Companion")
    monkeypatch.setattr("core.self_management.policy.feature_enabled", lambda: False)
    from core.tool_dispatcher import _TOOL_REGISTRY
    monkeypatch.setitem(_TOOL_REGISTRY, "web_search", {"category": "info"})
    monkeypatch.setattr("core.tool_dispatcher.get_tools_schema", lambda categories=None, **kw: [
        {"type": "function", "function": {"name": "web_search", "description": "", "parameters": {"type": "object", "properties": {}}}},
    ])
    monkeypatch.setattr("core.llm_client.chat_turn", _fake_chat_turn)
    monkeypatch.setattr("core.tool_dispatcher.execute_structured", _fake_execute)
    monkeypatch.setattr(Pipeline, "run_llm", _fake_run_llm)

    pipeline = Pipeline.__new__(Pipeline)
    result = await pipeline.run_agentic_loop(
        [{"role": "user", "content": "查一下"}],
        uid="u1",
        char_id=TEST_CHAR_ID,
        session_state=object(),
    )
    assert result == "收尾回复"
    assert captured.get("char_id") == TEST_CHAR_ID
    assert TEST_THIRD_CHAR_ID != captured.get("char_id")


@pytest.mark.asyncio
async def test_pipeline_send_run_llm_uses_frozen_scope_char(monkeypatch):
    from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_THIRD_CHAR_ID
    import core.scheduler.loop as loop
    from core.memory.scope import MemoryScope

    captured: dict = {}

    class _FakePipelineSend:
        async def fetch_context(self, uid, content, *a, **kw):
            return {}

        def build_prompt(self, uid, content, context, **kw):
            captured["build_char"] = kw.get("char_id")
            return [{"role": "user", "content": content}], {}

        async def run_llm(self, messages, **kw):
            captured["run_char"] = kw.get("char_id")
            if kw.get("char_id") is None:
                raise AssertionError("frozen scope char_id was not passed into run_llm")
            return "ok"

        async def post_process_critical(self, *a, **kw):
            return {"turn_id": "t-test", "critical_written": True, "emotion": "neutral"}

        async def post_process_slow(self, *a, **kw):
            return {"emotion": "neutral"}

        def _current_reality_scope(self, uid):
            return MemoryScope.reality_scope(uid, TEST_CHAR_ID)

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "owner-e"}, "character": {"name": "Companion"}},
    )
    monkeypatch.setattr(loop, "_active_char_id_or_none", lambda: TEST_THIRD_CHAR_ID)
    monkeypatch.setattr("core.scheduler.triggers.birthday._is_birthday_period", lambda: False)
    from core import pipeline_registry
    pipeline_registry.register(_FakePipelineSend())

    import core.turn_sink as ts

    async def _fake_record(**kwargs):
        from core.turn_sink import TurnResult
        return TurnResult(turn_id="t-sched", written_to_memory=True, fanout_targets=[])

    monkeypatch.setattr(ts, "record_assistant_turn", _fake_record)
    monkeypatch.setattr("channels.desktop_ws.is_connected", lambda: False)
    monkeypatch.setattr("core.character_name_provider.get_char_name", lambda *_a, **_k: "Companion")

    from core.perceive_event import PerceiveResult, PerceiveStatus

    async def _accept(event):
        return PerceiveResult(status=PerceiveStatus.ACCEPTED, event_id="e1", dedupe_key="d1")

    monkeypatch.setattr("core.perceive_event.receive_perceive_event", _accept)

    result = await loop._pipeline_send(
        "主动开口", trigger_name="legacy_dream_guard_active", char_id=TEST_CHAR_ID,
    )
    assert result == "ok"
    assert captured.get("build_char") == TEST_CHAR_ID
    assert captured.get("run_char") == TEST_CHAR_ID


@pytest.mark.asyncio
async def test_coplay_vlm_fallback_passes_owned_session_char(monkeypatch):
    from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_THIRD_CHAR_ID
    from core.coplay.observer import vlm_fallback_summary

    captured: dict = {}
    monkeypatch.setattr(
        "core.coplay.observer.get_config",
        lambda: {"vision": {"enabled": True, "model": "test-vision"}},
    )

    async def _chat(messages, **kwargs):
        captured.update(kwargs)
        if "char_id" not in kwargs:
            raise AssertionError("owned coplay char_id was not passed into vision fallback chat")
        assert kwargs["char_id"] == TEST_CHAR_ID
        assert kwargs.get("call_category") == "vision"
        assert kwargs.get("use_vision") is True
        return "她正站在悬崖边"

    monkeypatch.setattr("core.llm_client.chat", _chat)
    result = await vlm_fallback_summary(b"not-a-real-png", char_id=TEST_CHAR_ID)
    assert result == "她正站在悬崖边"
    assert captured.get("char_id") == TEST_CHAR_ID
    assert captured.get("char_id") != TEST_THIRD_CHAR_ID


def test_coplay_commentator_uses_owned_session_char_not_live_active(monkeypatch, sandbox):
    from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_THIRD_CHAR_ID
    from core.coplay import commentator, observer, session
    from core.coplay.observer import GameMoment

    session.arm("owner1", char_id=TEST_CHAR_ID)
    session.enter_active("owner1", game_id="g1", game_name="Some Game", char_id=TEST_CHAR_ID)
    observer.push_moment("owner1", GameMoment(kind="idle", summary="停下来了"))

    fake_pl = types.SimpleNamespace(_active_character_id=TEST_THIRD_CHAR_ID)
    monkeypatch.setattr("core.pipeline_registry.get", lambda: fake_pl)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": "owner1"}},
    )
    proposal = commentator.propose_coplay_commentary()
    assert proposal is not None
    execute = proposal.execute
    assert execute.__closure__ is not None
    closed_chars = [
        cell.cell_contents for cell in execute.__closure__
        if isinstance(cell.cell_contents, str) and cell.cell_contents in {TEST_CHAR_ID, TEST_THIRD_CHAR_ID}
    ]
    assert TEST_CHAR_ID in closed_chars
    assert TEST_THIRD_CHAR_ID not in closed_chars
