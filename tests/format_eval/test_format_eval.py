from types import SimpleNamespace

import pytest

from tests.format_eval import engine
from tests.format_eval.scorer import aggregate_scores, score_reply

_CASES = engine.load_cases()


def test_all_fixed_cases_match_expectations():
    problems: list[str] = []
    for case in _CASES:
        result = engine.run_case(case)
        problems.extend(f"{case['id']}: {problem}" for problem in engine.check_expectations(case, result))
    assert not problems, "\n".join(problems)


def test_required_positive_negative_and_short_boundary_exist():
    scores = {case["id"]: engine.run_case(case) for case in _CASES}
    assert scores["long-single-paragraph"].strict_compliant is False
    assert scores["proper-multi-paragraph"].strict_compliant is True
    boundary = scores["short-exempt-boundary"]
    assert boundary.text_length == 8
    assert boundary.strict_compliant is False
    assert boundary.loose_compliant is True


def test_loose_matches_current_s4_single_newline_semantics():
    result = score_reply("长" * 41 + "\n" + "文本")
    assert result.strict_compliant is False
    assert result.loose_compliant is True


def test_corpus_metrics_are_discriminating():
    scores = [engine.run_case(case) for case in _CASES]
    strict = aggregate_scores(scores, mode="strict")
    loose = aggregate_scores(scores, mode="loose")
    assert 0 < strict.compliance_rate < 1
    assert strict.compliance_rate < loose.compliance_rate <= 1
    assert strict.average_paragraph_count > 0
    assert 0 < strict.unsegmented_ratio < 1


def test_online_rows_are_paired_and_report_fallback_rate():
    raw = [
        "第一句已经足够长，适合成为前半部分。第二句继续补充细节，也给兜底留下句末切点。",
        "第一段。\n\n第二段。",
        "短回复。",
    ]
    off, on = engine.build_online_rows(raw, min_len=10)
    assert [off.enforce, on.enforce] == ["off", "on"]
    assert off.metrics.compliance_rate == 1 / 3
    assert on.metrics.compliance_rate == 2 / 3
    assert off.fallback_trigger_ratio == 0
    assert on.fallback_trigger_count == 1
    assert on.fallback_trigger_ratio == 1 / 3


def test_online_generation_requires_explicit_character_id():
    try:
        engine.generate_online_replies_sync(n=1, char_id="")
    except ValueError as exc:
        assert "explicit char_id" in str(exc)
    else:
        raise AssertionError("missing char_id must fail loud")


@pytest.mark.asyncio
async def test_online_generation_uses_pipeline_run_llm(monkeypatch):
    import core.character_loader as character_loader
    import core.lore_engine as lore_module
    import core.pipeline as pipeline_module

    calls: list[tuple[list[dict], str | None]] = []
    character = SimpleNamespace(name="测试角色", description="评测角色", world_book=[])

    class FakeLoreEngine:
        def load(self):
            return None

        def load_entries(self, _entries):
            raise AssertionError("empty world_book must not load entries")

    class FakePipeline:
        generation = property(lambda self: self)
        def __init__(self, loaded_character, lore_engine, active_character_id=""):
            assert loaded_character is character
            assert isinstance(lore_engine, FakeLoreEngine)
            assert active_character_id == "format_eval_char"

        async def run_llm(self, messages, *, char_id=None):
            calls.append((messages, char_id))
            return f"reply-{len(calls)}"

    monkeypatch.setattr(character_loader, "load", lambda char_id: character)
    monkeypatch.setattr(lore_module, "LoreEngine", FakeLoreEngine)
    monkeypatch.setattr(pipeline_module, "Pipeline", FakePipeline)

    replies = await engine.generate_online_replies(
        n=3,
        char_id="format_eval_char",
        prompts=["prompt-a", "prompt-b"],
    )

    assert replies == ["reply-1", "reply-2", "reply-3"]
    assert [messages[-1]["content"] for messages, _ in calls] == [
        "prompt-a", "prompt-b", "prompt-a",
    ]
    assert all(char_id == "format_eval_char" for _, char_id in calls)
