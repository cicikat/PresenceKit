import pytest

from tests.identity_eval import engine

_CASES = engine.load_cases()


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case["id"])
def test_identity_continuity_case(case, identity_case_env, monkeypatch):
    result = engine.run_case(case, monkeypatch, char_ids=identity_case_env)
    assert not (problems := engine.check_expectations(case, result)), "\n".join(problems)


def test_at_least_eight_cases_cover_required_families():
    ids = {case["id"] for case in _CASES}
    assert len(ids) >= 8
    assert {"cont-01", "cont-02", "evo-01", "evo-02", "abs-01", "abs-02", "abs-03", "reg-01"} <= ids


def test_negative_assertion_is_discriminating(identity_case_env, monkeypatch):
    case = next(case for case in _CASES if case["id"] == "abs-01")
    mutant = {**case, "sessions": [*case["sessions"], {"identity": {
        "topic_preference": {"text": "虚构标记-热爱攀岩", "confidence": 0.9, "evidence_count": 10}
    }}]}
    result = engine.run_case(mutant, monkeypatch, char_ids=identity_case_env)
    assert any("identity_absent failed" in problem for problem in engine.check_expectations(mutant, result))
