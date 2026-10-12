from core.prompt_sections import arrange_sections
from tests.test_prompt_ablation import _apply_build_stubs, _base_build_kwargs


def _msg(layer, content, **meta):
    return dict(role="system", _layer=layer, content=content, **meta)


def test_sections_are_contiguous_and_keep_leaf_contracts():
    receipt = {"id": "receipt"}
    source = [_msg("1_system_prompt", "rules"), _msg("3.7_sensor", "battery"),
              _msg("6c_episodic", "past", _drop_priority=70),
              _msg("9_history", "history"),
              _msg("10_tool_result", "screen result", _continuity_receipt=receipt),
              _msg("3.9_screen_awareness", "activity"),
              _msg("6g_dream_impression", "dream"),
              dict(role="user", _layer="12_user_message", content="now")]
    result = arrange_sections(source)
    assert [m["_layer"] for m in result] == [
        "1_system_prompt", "3.7_sensor", "3.9_screen_awareness", "6c_episodic",
        "10_tool_result", "6g_dream_impression", "9_history", "12_user_message"]
    text = "\n".join(m["content"] for m in result)
    assert text.index("【/真实数据】") < text.index("screen result")
    assert text.count("【真实数据】") == text.count("【/真实数据】") == 1
    assert next(m for m in result if m["_layer"] == "10_tool_result")["_continuity_receipt"] is receipt
    assert source[1]["content"] == "battery"
    assert next(m for m in result if m["_layer"] == "6c_episodic")["_drop_priority"] == 70


def test_removed_endpoints_rebuild_closed_sections_and_no_empty_groups():
    source = [_msg("3.7_sensor", "battery"), _msg("3.9_screen_awareness", "activity")]
    result = arrange_sections(source[1:])
    assert result[0]["content"].startswith("【真实数据】")
    assert result[0]["content"].endswith("【/真实数据】")
    assert arrange_sections([]) == []


def test_native_tool_protocol_and_unknown_layers_are_preserved():
    source = [dict(role="assistant", content="", tool_calls=[{"id": "call1"}]),
              dict(role="tool", content="result", tool_call_id="call1", _layer="10_tool_result"),
              _msg("unknown", "other")]
    assert arrange_sections(source) == source


def test_builder_pruning_ablation_and_final_budget(sandbox, monkeypatch):
    _apply_build_stubs(monkeypatch)
    from core.prompt_builder import build
    from core.prompt_ablation import set_state
    set_state(["6g_dream_impression"], False)
    result, debug = build(**_base_build_kwargs(
        event_search_result="past" * 7000, episodic_result="retained memory",
        dream_impression_text="dream", tool_result="screen summary"))
    text = "\n".join(m["content"] for m in result)
    assert "6b_event_search" in debug["removed_layers"]
    assert "6g_dream_impression" in debug["ablated_layers"]
    assert text.count("【现实回忆】") == text.count("【/现实回忆】") == 1
    assert "【主观余韵】" not in text
    assert "<<<TOOL_DATA_START>>>" in text
    assert debug["char_estimate"] == sum(len(m["content"]) for m in result)
    assert all(m["_estimated_tokens"] == round(len(m["content"]) / 1.7, 1) for m in result)
    assert debug["char_estimate"] <= 18000


def test_legacy_perception_slot_is_separate_and_can_be_disabled(sandbox, monkeypatch):
    _apply_build_stubs(monkeypatch)
    from core.prompt_builder import build
    from core.character_loader import Character
    from core.prompt_ablation import set_state
    kwargs = _base_build_kwargs(character=Character(name="Demo", system_prompt="rules {perception_block}"),
                                perception_block="pending action failed")
    result, _ = build(**kwargs)
    system = next(m for m in result if m["_layer"] == "1_system_prompt")
    pending = next(m for m in result if m["_layer"] == "1.4_perception")
    assert "pending action failed" not in system["content"]
    assert "{perception_block}" not in system["content"]
    assert "【工具结果】" in pending["content"]
    set_state([], True)
    disabled, _ = build(**kwargs)
    assert not any(m["_layer"] == "1.4_perception" for m in disabled)


def test_truth_boundary_precedes_identity_and_author_note_ends_with_tool_emphasis(sandbox, monkeypatch):
    _apply_build_stubs(monkeypatch)
    from core.prompt_builder import build
    result, _ = build(**_base_build_kwargs())
    layers = [m["_layer"] for m in result]
    assert layers.index("1.5_fact_boundary") < layers.index("1_system_prompt")
    note = next(m["content"] for m in result if m["_layer"] == "11_author_note")
    assert note.splitlines()[-1].startswith("【使用工具强调】")
    assert note.endswith("当遇到冲突时，优先信任用户，其次调用工具。")
