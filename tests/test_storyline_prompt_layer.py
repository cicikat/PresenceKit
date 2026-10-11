"""
tests/test_storyline_prompt_layer.py — Brief 80 §4 storyline tagged 召回层验收

Covers:
1. tag 命中 active/dormant 弧线 → 注入该条，priority=65，_provenance mode=tagged
2. 多条弧线命中时只注入交集最大的一条
3. closed 弧线不参与召回
4. 无 tag 命中 → 不注入
5. backchannel 低信息轮 → 不注入（即便 tag 命中）
"""
from unittest.mock import MagicMock, patch


def _character():
    char = MagicMock()
    char.name = "Companion"
    char.system_prompt = ""
    char.description = ""
    char.personality = ""
    char.scenario = ""
    char.mes_example = ""
    char.jailbreak_entries = []
    return char


def _arc(title, tags, nodes=None, status="active"):
    return {
        "arc_id": f"arc_{title}",
        "title": title,
        "status": status,
        "tags": tags,
        "nodes": nodes or [{"node_id": "n1", "ts": 0.0, "span": [0.0, 0.0], "summary": f"{title}的进展", "source_ids": []}],
        "created_at": 0.0,
        "updated_at": 0.0,
    }


def _build(tags: set[str], arcs: list[dict], user_message: str = "你好"):
    from core import prompt_builder

    with (
        patch("core.prompt_builder._load_jailbreak", return_value=""),
        patch("core.prompt_builder._load_style_hint", return_value=""),
        patch("core.presence.get_last_seen_text", return_value=""),
        patch("core.author_note_rotator.get_current_note", return_value=""),
        patch("core.config_loader.get_config", return_value={"chat": {"style": "roleplay"}}),
        patch("core.mood_text.get_mood_text", return_value=""),
        patch("core.memory.storyline.list_recallable_arcs", return_value=arcs),
    ):
        messages, _ = prompt_builder.build(
            character=_character(), user_id="storyline-layer", user_message=user_message,
            history=[], relation={"role": "朋友"}, profile={}, group_context=[], tags=tags,
        )
    return messages


# ── 1. tag 命中 → 注入，priority=65 ───────────────────────────────────────────

def test_tag_hit_injects_matching_arc():
    arcs = [_arc("职业转型", ["topic.learning"])]
    messages = _build({"topic.learning"}, arcs)
    layer = next(m for m in messages if m.get("_layer") == "6h_storyline")
    assert "职业转型" in layer["content"]
    assert "职业转型的进展" in layer["content"]
    assert layer["_drop_priority"] == 65
    assert layer["_provenance"]["mode"] == "tagged"
    assert layer["_provenance"]["matched_tags"] == ["topic.learning"]


# ── 2. 多条命中 → 只注入交集最大的一条 ────────────────────────────────────────

def test_only_best_overlap_arc_is_injected():
    arcs = [
        _arc("弱相关弧线", ["topic.learning"]),
        _arc("强相关弧线", ["topic.learning", "topic.writing"]),
    ]
    messages = _build({"topic.learning", "topic.writing"}, arcs)
    injected = [m for m in messages if m.get("_layer") == "6h_storyline"]
    assert len(injected) == 1
    assert "强相关弧线" in injected[0]["content"]


# ── 3. closed 弧线不参与召回（list_recallable_arcs 契约：build() 信任其只返回 active/dormant）──

def test_no_recallable_arcs_skips_injection():
    """closed 弧线被 list_recallable_arcs 过滤在先（见 test_storyline.py），
    build() 收到空列表时不应注入任何 storyline 层。"""
    messages = _build({"topic.learning"}, arcs=[])
    assert all(m.get("_layer") != "6h_storyline" for m in messages)


# ── 4. 无 tag 命中 → 不注入 ───────────────────────────────────────────────────

def test_no_tag_overlap_skips_injection():
    arcs = [_arc("无关弧线", ["topic.music"])]
    messages = _build({"topic.learning"}, arcs)
    assert all(m.get("_layer") != "6h_storyline" for m in messages)


# ── 5. backchannel 低信息轮不注入 ─────────────────────────────────────────────

def test_backchannel_message_skips_injection_even_with_tag_hit():
    arcs = [_arc("职业转型", ["topic.learning"])]
    messages = _build({"topic.learning"}, arcs, user_message="嗯嗯")
    assert all(m.get("_layer") != "6h_storyline" for m in messages)
