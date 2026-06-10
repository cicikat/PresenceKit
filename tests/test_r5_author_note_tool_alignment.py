"""
tests/test_r5_author_note_tool_alignment.py
===========================================
Fable R5: Author's Note 工具能力对齐检查

规则：
  1. 当 tool_result=None 时，层11 Author's Note 不得含未 grounded 的工具完成表述。
  2. 当 tool_result 来自 read_diary 时，层11 可以含工具已提供提示，
     但不得再次要求调用工具。
  3. 所有 prompt 中提到的工具名必须来自 _TOOL_REGISTRY。
  4. format_tool_capability_note() 仅返回 registry 内的工具。
  5. 静态扫描：prompt_builder.py 不含硬编码"必须调用 read_diary"。
  6. 回归：read_diary 工具在 registry 内且可执行路径完整。
"""
from __future__ import annotations

import importlib
import pathlib
import re
from unittest.mock import MagicMock

import pytest

ROOT = pathlib.Path(__file__).parent.parent


def _fresh_tool_dispatcher():
    """Return a freshly-reloaded tool_dispatcher module.

    Some tests in the suite directly assign _td._TOOL_REGISTRY = {} without
    using monkeypatch, leaving the registry empty for subsequent tests.
    Reload gives us the canonical populated registry regardless of run order.
    """
    import core.tool_dispatcher as _td
    return importlib.reload(_td)


def _read_src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Build helper — stub all filesystem-touching helpers
# ─────────────────────────────────────────────────────────────────────────────

def _apply_build_stubs(monkeypatch):
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


def _get_author_note_text(messages: list[dict]) -> str:
    """从 build() 返回的 messages 中提取 layer 11 Author's Note 的内容。"""
    for m in messages:
        if m.get("_layer") == "11_author_note":
            return m.get("content", "")
    return ""


def _build_minimal(monkeypatch, *, tool_result=None, user_message="你好"):
    """用最小参数调用 build()，返回 (messages, meta)。"""
    _apply_build_stubs(monkeypatch)

    import core.prompt_builder as _pb
    from core.character_loader import Character

    char = Character(name="叶瑄")
    messages, meta = _pb.build(
        character=char,
        user_id="u_test",
        user_message=user_message,
        history=[],
        relation={"role": "friend"},
        profile={},
        group_context=[],
        tool_result=tool_result,
        char_id="yexuan",
    )
    return messages, meta


# ─────────────────────────────────────────────────────────────────────────────
# 1. 静态扫描：prompt_builder.py 不含"必须调用 read_diary"
# ─────────────────────────────────────────────────────────────────────────────

def test_no_hardcoded_must_call_read_diary():
    """prompt_builder.py 不应包含硬编码的'必须调用 read_diary'指令。"""
    src = _read_src("core/prompt_builder.py")
    assert "必须调用 read_diary" not in src
    assert "必须调用read_diary" not in src


def test_no_hardcoded_already_read_diary_unconditional():
    """prompt_builder.py 中不应有无条件的'你已经读完日记'表述。"""
    src = _read_src("core/prompt_builder.py")
    assert "你已经读完日记" not in src
    assert "你已经读过日记" not in src


def test_old_forced_tool_rule_removed():
    """旧的无条件'【强制工具规则】...必须优先依据工具结果回答'已被移除。"""
    src = _read_src("core/prompt_builder.py")
    assert "【强制工具规则】" not in src, (
        "旧的无条件强制工具规则仍存在于 prompt_builder.py，应替换为条件版本"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. tool_result=None 时，Author's Note 不含未 grounded 工具完成表述
# ─────────────────────────────────────────────────────────────────────────────

_FORBIDDEN_PATTERNS_NO_TOOL = [
    "必须调用 read_diary",
    "必须调用read_diary",
    "你已经读完日记",
    "你已经读过日记",
    "如上游工具层已提供时间/日记结果，必须优先",
]


@pytest.mark.parametrize("forbidden", _FORBIDDEN_PATTERNS_NO_TOOL)
def test_author_note_no_tool_result_forbids_pattern(monkeypatch, forbidden):
    """tool_result=None 时 Author's Note 不含 forbidden 字符串。"""
    messages, _ = _build_minimal(monkeypatch, tool_result=None)
    note = _get_author_note_text(messages)
    assert forbidden not in note, (
        f"Author's Note 在 tool_result=None 时含有禁止短语: {forbidden!r}\n"
        f"Author's Note 内容:\n{note}"
    )


def test_author_note_no_tool_result_has_no_tool_guard(monkeypatch):
    """tool_result=None 时 Author's Note 须包含'无工具'相关说明。"""
    messages, _ = _build_minimal(monkeypatch, tool_result=None)
    note = _get_author_note_text(messages)
    assert "无工具" in note or "没有任何工具" in note or "没有工具" in note, (
        "tool_result=None 时 Author's Note 应明确说明本轮无工具结果，"
        f"实际内容:\n{note}"
    )


def test_author_note_no_tool_result_has_no_fabrication_guard(monkeypatch):
    """tool_result=None 时 Author's Note 须含禁止编造日记内容的说明。"""
    messages, _ = _build_minimal(monkeypatch, tool_result=None)
    note = _get_author_note_text(messages)
    assert "禁止编造日记" in note, (
        "tool_result=None 时 Author's Note 应包含'禁止编造日记内容'，"
        f"实际内容:\n{note}"
    )


def test_author_note_no_tool_result_no_tool_result_layer(monkeypatch):
    """tool_result=None 时 prompt 不应包含层10 tool_result 层。"""
    messages, _ = _build_minimal(monkeypatch, tool_result=None)
    layers = [m.get("_layer") for m in messages]
    assert "10_tool_result" not in layers, (
        f"tool_result=None 时不应注入层10，实际 layers={layers}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. tool_result 有值（模拟 read_diary 结果）时的规则
# ─────────────────────────────────────────────────────────────────────────────

_DIARY_TOOL_RESULT = "她4月10日的日记内容：\n今天很开心，做了好吃的饭。"


def test_author_note_with_tool_result_no_re_call_instruction(monkeypatch):
    """tool_result 已提供时，Author's Note 不得再要求调用工具。"""
    messages, _ = _build_minimal(monkeypatch, tool_result=_DIARY_TOOL_RESULT)
    note = _get_author_note_text(messages)
    # 不应出现"请调用"/"必须调用"/"去调用"等要求再次调用的语言
    assert "请调用" not in note
    assert "必须调用" not in note
    assert "去调用" not in note


def test_author_note_with_tool_result_has_provided_hint(monkeypatch):
    """tool_result 已提供时，Author's Note 须说明工具结果本轮已注入。"""
    messages, _ = _build_minimal(monkeypatch, tool_result=_DIARY_TOOL_RESULT)
    note = _get_author_note_text(messages)
    # 必须包含说明工具结果已提供的字样
    has_provided = (
        "工具结果已提供" in note
        or "已注入工具" in note
        or "层10已注入" in note
        or "工具执行结果" in note
    )
    assert has_provided, (
        "tool_result 非 None 时 Author's Note 应说明工具结果已在本轮提供，"
        f"实际内容:\n{note}"
    )


def test_layer10_present_when_tool_result_provided(monkeypatch):
    """tool_result 非 None 时，prompt 应包含层10 tool_result 层。"""
    messages, _ = _build_minimal(monkeypatch, tool_result=_DIARY_TOOL_RESULT)
    layers = [m.get("_layer") for m in messages]
    assert "10_tool_result" in layers, (
        f"tool_result 非 None 时应存在层10，实际 layers={layers}"
    )


def test_layer10_not_present_when_no_tool_result(monkeypatch):
    """tool_result=None 时，prompt 不应包含层10 tool_result 层（回归）。"""
    messages, _ = _build_minimal(monkeypatch, tool_result=None)
    layers = [m.get("_layer") for m in messages]
    assert "10_tool_result" not in layers


def test_tool_result_content_in_layer10(monkeypatch):
    """层10内容应包含 tool_result 原始文本（经 frame_tool_result 包装）。"""
    messages, _ = _build_minimal(monkeypatch, tool_result=_DIARY_TOOL_RESULT)
    layer10 = next(
        (m["content"] for m in messages if m.get("_layer") == "10_tool_result"),
        None,
    )
    assert layer10 is not None
    assert "今天很开心" in layer10, (
        f"层10内容中未找到 tool_result 中的日记文字，layer10={layer10!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. 工具名来自 _TOOL_REGISTRY — format_tool_capability_note
# ─────────────────────────────────────────────────────────────────────────────

def test_format_tool_capability_note_returns_registry_names():
    """format_tool_capability_note() 返回的工具名全在 _TOOL_REGISTRY 中。"""
    _td = _fresh_tool_dispatcher()

    note = _td.format_tool_capability_note()
    if not note:
        return  # 空注册表也是合法的
    assert note.startswith("可用工具："), f"格式不符合预期: {note!r}"
    names_part = note[len("可用工具："):]
    returned_names = [n.strip() for n in names_part.split("、") if n.strip()]
    for name in returned_names:
        assert name in _td._TOOL_REGISTRY, (
            f"format_tool_capability_note() 返回了不在 registry 中的工具名: {name!r}"
        )


def test_format_tool_capability_note_category_filter():
    """categories=['info'] 时，返回的工具名都属于 info 分类。"""
    _td = _fresh_tool_dispatcher()

    note = _td.format_tool_capability_note(categories=["info"])
    if not note:
        return
    names_part = note[len("可用工具："):]
    returned_names = [n.strip() for n in names_part.split("、") if n.strip()]
    for name in returned_names:
        assert _td._TOOL_REGISTRY.get(name, {}).get("category") == "info", (
            f"format_tool_capability_note(categories=['info']) 返回了非 info 分类工具: {name!r}"
        )


def test_read_diary_in_registry():
    """read_diary 工具必须在 _TOOL_REGISTRY 中（回归：不能被误删）。"""
    _td = _fresh_tool_dispatcher()
    assert "read_diary" in _td._TOOL_REGISTRY, "read_diary 工具已从 registry 中消失"


def test_format_tool_capability_note_includes_read_diary():
    """read_diary 属于 info 分类，format_tool_capability_note(categories=['info']) 应包含它。"""
    _td = _fresh_tool_dispatcher()
    note = _td.format_tool_capability_note(categories=["info"])
    assert "read_diary" in note, (
        f"format_tool_capability_note(categories=['info']) 未包含 read_diary，返回: {note!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. Author's Note 中若列出工具名，必须来自 registry
# ─────────────────────────────────────────────────────────────────────────────

def test_author_note_no_phantom_tool_names(monkeypatch):
    """Author's Note 中的工具名（read_*/search_*/get_* 形式）均在 registry 中。"""
    _td = _fresh_tool_dispatcher()

    messages, _ = _build_minimal(monkeypatch, tool_result=None)
    note = _get_author_note_text(messages)

    # 检测形如 read_diary / search_diary / get_time 等的工具名模式
    tool_name_re = re.compile(r'\b(read|search|get|add|water|play|desktop|device|exit)_\w+\b')
    full_names = tool_name_re.findall(note)
    if full_names:
        for name in full_names:
            assert any(name in k for k in _td._TOOL_REGISTRY), (
                f"Author's Note 中出现了疑似工具名 {name!r}，但不在 registry 中"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 6. 回归：read_diary 执行路径完整
# ─────────────────────────────────────────────────────────────────────────────

def test_read_diary_tool_function_registered():
    """read_diary 工具有 func 字段且可调用。"""
    _td = _fresh_tool_dispatcher()
    spec = _td._TOOL_REGISTRY.get("read_diary")
    assert spec is not None, "read_diary 不在 registry 中"
    assert callable(spec.get("func")), "read_diary 的 func 不可调用"


def test_read_diary_tool_has_required_fields():
    """read_diary 工具有 description / keywords / examples 字段。"""
    _td = _fresh_tool_dispatcher()
    spec = _td._TOOL_REGISTRY["read_diary"]
    assert spec.get("description"), "read_diary 缺少 description"
    assert spec.get("keywords"), "read_diary 缺少 keywords"
    assert spec.get("examples"), "read_diary 缺少 examples"


def test_read_diary_category_is_info():
    """read_diary 应属于 info 分类（非 memory），确保探针正确触发。"""
    _td = _fresh_tool_dispatcher()
    spec = _td._TOOL_REGISTRY["read_diary"]
    assert spec.get("category") == "info", (
        f"read_diary category 应为 'info'，实际 {spec.get('category')!r}"
    )


def test_tool_result_none_no_diary_content_leak(monkeypatch):
    """tool_result=None 时，即使 user_message 提到日记，层11也不应声称读过日记。"""
    messages, _ = _build_minimal(
        monkeypatch,
        tool_result=None,
        user_message="你帮我看一下我今天写的日记",
    )
    note = _get_author_note_text(messages)

    # 不应出现任何暗示模型已读日记的固定断言
    forbidden = [
        "你已经读完日记",
        "你已经读过日记",
        "你刚刚读完",
    ]
    for phrase in forbidden:
        assert phrase not in note, (
            f"即使 user_message 提到日记，tool_result=None 时 Author's Note "
            f"不应含: {phrase!r}\n实际内容:\n{note}"
        )
