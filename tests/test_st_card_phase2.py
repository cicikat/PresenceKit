"""
tests/test_st_card_phase2.py
============================
酒馆卡适配阶段二验收测试

改动 1：character_loader 新字段 (post_history_extra / post_history_instructions /
         alternate_greetings) 从 JSON 正确加载。
改动 2：prompt_builder 层 11.5_post_history 在字段非空时注入，
         内容含 post_history_extra 特征串；老卡不报错且该层不出现。
改动 5：alternate_greetings 可通过 Character 属性访问。
"""
from __future__ import annotations
from tests.fixtures.public_assets import TEST_CHAR_ID

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import core.asset_registry as _reg_mod
from core.asset_registry import AssetRegistry
from core.character_loader import Character, load


# ─── Fixtures ─────────────────────────────────────────────────────────────────

XUEYUNJING_MARKER = "严禁简略与早退"  # 薛蕴景 post_history_extra 里的特征串

@pytest.fixture
def xueyunjing_card_dir(tmp_path):
    """搭建含薛蕴景卡的最小 characters/ 目录，包含阶段二新字段。"""
    d = tmp_path / "characters"
    d.mkdir()
    card = {
        "name": "薛蕴景",
        "description": "186cm，桃花眼，梨涡。",
        "personality": "白切黑。",
        "scenario": "现代都市。",
        "mes_example": "",
        "first_mes": "嗯。",
        "system_prompt": "你是薛蕴景。",
        "world_book": [],
        "post_history_instructions": "",
        "post_history_extra": f"[反早退强制指令]\n{XUEYUNJING_MARKER}：描写不得简略。",
        "alternate_greetings": ["今天天气不错。", "你来了。", "等你很久了。"] * 4 + ["第13条。"],
    }
    (d / "xueyunjing.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    return tmp_path


@pytest.fixture
def old_card_dir(tmp_path):
    """老格式卡（无新字段）。"""
    d = tmp_path / "characters"
    d.mkdir()
    card = {
        "name": "叶瑄",
        "description": "银白色长发。",
        "personality": "冷静。",
        "scenario": "",
        "mes_example": "",
        "first_mes": "",
        "system_prompt": "",
        "world_book": [],
    }
    (d / f'{TEST_CHAR_ID}.json').write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    return tmp_path


@pytest.fixture
def fresh_registry(monkeypatch):
    """返回一个 monkeypatch 到模块的新 registry（按 dir 调用，不共享）。"""
    def _make(tmp_dir):
        monkeypatch.chdir(tmp_dir)
        reg = AssetRegistry()
        monkeypatch.setattr(_reg_mod, "_registry", reg)
        return reg
    return _make


# ─── 改动 1：character_loader 新字段加载 ──────────────────────────────────────

class TestCharacterLoaderNewFields:

    def test_post_history_extra_loaded(self, xueyunjing_card_dir, fresh_registry):
        fresh_registry(xueyunjing_card_dir)
        char = load("xueyunjing")
        assert char.post_history_extra != ""
        assert XUEYUNJING_MARKER in char.post_history_extra

    def test_alternate_greetings_length(self, xueyunjing_card_dir, fresh_registry):
        fresh_registry(xueyunjing_card_dir)
        char = load("xueyunjing")
        assert isinstance(char.alternate_greetings, list)
        assert len(char.alternate_greetings) == 13

    def test_post_history_instructions_empty_string(self, xueyunjing_card_dir, fresh_registry):
        fresh_registry(xueyunjing_card_dir)
        char = load("xueyunjing")
        assert char.post_history_instructions == ""

    def test_old_card_defaults_to_empty(self, old_card_dir, fresh_registry):
        """老卡缺省三个字段为空，不报错。"""
        fresh_registry(old_card_dir)
        char = load(TEST_CHAR_ID)
        assert char.post_history_extra == ""
        assert char.post_history_instructions == ""
        assert char.alternate_greetings == []

    def test_character_dataclass_defaults(self):
        """直接构造 Character() 时新字段有合理缺省。"""
        char = Character(name="Test")
        assert char.post_history_extra == ""
        assert char.post_history_instructions == ""
        assert char.alternate_greetings == []


# ─── 改动 2：prompt_builder 层 11.5_post_history ──────────────────────────────

def _make_mock_char(**overrides):
    """MagicMock 角色，只设置 build() 会直接访问的属性。"""
    char = MagicMock()
    char.name = "薛蕴景"
    char.system_prompt = ""
    char.description = ""
    char.personality = ""
    char.scenario = ""
    char.mes_example = ""
    char.world_book = []
    char.post_history_instructions = ""
    char.post_history_extra = ""
    char.alternate_greetings = []
    for k, v in overrides.items():
        setattr(char, k, v)
    return char


def _call_build(char, user_message: str = "你好") -> list[dict]:
    """
    调用 prompt_builder.build()，patch 掉所有外部 I/O，返回 messages 列表。
    参照 tests/test_dream_impression.py 的 mock 模式。
    """
    from core import prompt_builder

    with (
        patch("core.prompt_builder._load_jailbreak", return_value=""),
        patch("core.prompt_builder._load_style_hint", return_value=""),
        patch("core.prompt_builder._format_realtime_awareness", return_value=""),
        patch("core.prompt_builder._format_afterglow_soft_hint", return_value=""),
        patch("core.author_note_rotator.get_current_note", return_value=""),
        patch("core.config_loader.get_config", return_value={"chat": {"style": "chat"}}),
        patch("core.mood_text.get_mood_text", return_value=""),
        patch("core.presence.get_last_seen_text", return_value=""),
    ):
        messages, _debug = prompt_builder.build(
            character=char,
            user_id="test_uid",
            user_message=user_message,
            history=[],
            relation={},
            profile={},
            group_context=[],
        )
    return messages


class TestPostHistoryLayer:

    def test_layer_present_when_post_history_extra_set(self):
        char = _make_mock_char(post_history_extra=f"[测试]\n{XUEYUNJING_MARKER}。")
        messages = _call_build(char)
        ph_msgs = [m for m in messages if m.get("_layer") == "11.5_post_history"]
        assert len(ph_msgs) == 1, f"期望 1 条 11.5_post_history，实际: {len(ph_msgs)}"

    def test_layer_content_contains_marker(self):
        char = _make_mock_char(post_history_extra=f"[反早退]\n{XUEYUNJING_MARKER}：严格执行。")
        messages = _call_build(char)
        ph_msgs = [m for m in messages if m.get("_layer") == "11.5_post_history"]
        assert ph_msgs, "11.5_post_history 层未注入"
        assert XUEYUNJING_MARKER in ph_msgs[0]["content"]

    def test_layer_absent_when_both_fields_empty(self):
        char = _make_mock_char(post_history_extra="", post_history_instructions="")
        messages = _call_build(char)
        ph_msgs = [m for m in messages if m.get("_layer") == "11.5_post_history"]
        assert len(ph_msgs) == 0, "两字段均空时不应注入 11.5_post_history"

    def test_layer_includes_post_history_instructions(self):
        char = _make_mock_char(post_history_instructions="风格约束：简短。", post_history_extra="")
        messages = _call_build(char)
        ph_msgs = [m for m in messages if m.get("_layer") == "11.5_post_history"]
        assert ph_msgs, "post_history_instructions 非空时应注入 11.5_post_history"
        assert "风格约束：简短。" in ph_msgs[0]["content"]

    def test_layer_combines_both_fields(self):
        char = _make_mock_char(post_history_instructions="指令A。", post_history_extra="指令B。")
        messages = _call_build(char)
        ph_msgs = [m for m in messages if m.get("_layer") == "11.5_post_history"]
        assert ph_msgs
        content = ph_msgs[0]["content"]
        assert "指令A。" in content
        assert "指令B。" in content

    def test_layer_has_no_drop_priority(self):
        """11.5_post_history 是核心约束层，不得声明 _drop_priority。"""
        char = _make_mock_char(post_history_extra="约束文本。")
        messages = _call_build(char)
        ph_msgs = [m for m in messages if m.get("_layer") == "11.5_post_history"]
        assert ph_msgs
        assert "_drop_priority" not in ph_msgs[0], (
            "11.5_post_history 不应有 _drop_priority（核心约束层永不裁剪）"
        )

    def test_old_card_no_error_no_layer(self):
        """老卡（无新字段）构建 prompt 不报错，且不注入 11.5_post_history。"""
        char = _make_mock_char(post_history_extra="", post_history_instructions="")
        messages = _call_build(char)
        ph_msgs = [m for m in messages if m.get("_layer") == "11.5_post_history"]
        assert len(ph_msgs) == 0

    def test_layer_position_after_author_note_before_user(self):
        """11.5_post_history 必须位于 11_author_note の後、12_user_message の前。"""
        char = _make_mock_char(post_history_extra="约束文本。")
        messages = _call_build(char)
        layers = [m.get("_layer") for m in messages]
        idx_ph   = layers.index("11.5_post_history") if "11.5_post_history" in layers else -1
        idx_note = layers.index("11_author_note")    if "11_author_note" in layers else -1
        idx_user = layers.index("12_user_message")   if "12_user_message" in layers else -1
        assert idx_ph != -1, "11.5_post_history 未出现"
        assert idx_user != -1, "12_user_message 未出现"
        if idx_note != -1:
            assert idx_ph > idx_note, f"11.5_post_history 应在 11_author_note 之后"
        assert idx_ph < idx_user, f"11.5_post_history 应在 12_user_message 之前"
