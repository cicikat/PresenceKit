"""Deterministic multi-session identity continuity evaluator (Brief 71).

Cases seed only existing memory write points, optionally execute the real
``consolidate_to_identity`` fixation path with an offline LLM response, then
exercise ``Pipeline.fetch_context`` and ``Pipeline.build_prompt``.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import yaml

from tests.memeval import engine as memeval_engine

CASES_DIR = Path(__file__).parent / "cases"


def load_cases() -> list[dict]:
    cases = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        case = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        case.setdefault("id", path.stem)
        assert case["id"] == path.stem
        assert isinstance(case.get("sessions"), list)
        assert isinstance(case.get("query"), str)
        cases.append(case)
    return cases


new_test_char_id = memeval_engine.new_test_char_id
install_test_character = memeval_engine.install_test_character
remove_test_character = memeval_engine.remove_test_character


def _dimension(raw: dict) -> dict:
    return {
        "text": raw["text"],
        "confidence": float(raw.get("confidence", 0.8)),
        "evidence_count": int(raw.get("evidence_count", 10)),
        "last_updated": float(raw.get("last_updated", time.time())),
        "counter_evidence_count": int(raw.get("counter_evidence_count", 0)),
        "last_conflict_at": float(raw.get("last_conflict_at", 0.0)),
    }


async def _apply_session(uid: str, char_id: str, session: dict) -> None:
    from core.memory import user_identity
    from core.memory.fixation_pipeline import consolidate_to_identity

    identity_seed = session.get("identity") or {}
    if identity_seed:
        current = await user_identity.load(uid, char_id=char_id)
        current.update({key: _dimension(value) for key, value in identity_seed.items()})
        assert await user_identity.save(uid, current, char_id=char_id)

    if session.get("episodic"):
        memeval_engine.seed_episodic(uid, char_id, session["episodic"])
    if session.get("mid_term"):
        memeval_engine.seed_mid_term(uid, char_id, session["mid_term"])

    fixation = session.get("fixation")
    if fixation:
        memeval_engine.seed_episodic(uid, char_id, fixation.get("episodic", []))
        llm = MagicMock()
        llm.chat = AsyncMock(return_value=json.dumps(fixation["response"], ensure_ascii=False))
        assert await consolidate_to_identity(uid, llm, char_id=char_id)


@dataclass
class CaseResult:
    case_id: str
    identity_text: str
    identity_state: dict
    layers_activated: list[str]
    prompt_identity_text: str
    ctx: dict = field(repr=False, default_factory=dict)


async def _run_case_async(case: dict, monkeypatch, char_ids: dict[str, str]) -> CaseResult:
    uid = case.get("uid", f"identity_eval_{case['id']}")
    memeval_engine.apply_ambient_stubs(monkeypatch)
    memeval_engine.apply_event_log_seed(monkeypatch, [])

    for session in case["sessions"]:
        bucket = session.get("char", "primary")
        assert bucket in char_ids, f"unknown char bucket: {bucket}"
        await _apply_session(uid, char_ids[bucket], session)

    primary = char_ids["primary"]
    pipeline = memeval_engine.make_pipeline(primary)
    from core.memory import user_identity
    from core.memory.scope import MemoryScope

    ctx = await pipeline.fetch_context(
        user_id=uid,
        content=case["query"],
        frozen_scope=MemoryScope.reality_scope(uid, primary),
    )
    messages, debug = pipeline.build_prompt(
        user_id=uid, content=case["query"], context=ctx, char_id=primary,
    )
    prompt_identity = "\n".join(
        str(message.get("content", ""))
        for message in messages
        if message.get("_layer") == "6a_user_identity"
    )
    return CaseResult(
        case_id=case["id"],
        identity_text=ctx.get("user_identity_text", "") or "",
        identity_state=await user_identity.load(uid, char_id=primary),
        layers_activated=debug.get("layers_activated", []),
        prompt_identity_text=prompt_identity,
        ctx=ctx,
    )


def run_case(case: dict, monkeypatch, *, char_ids: dict[str, str]) -> CaseResult:
    return asyncio.run(_run_case_async(case, monkeypatch, char_ids))


def check_expectations(case: dict, result: CaseResult) -> list[str]:
    expect = case.get("expect") or {}
    problems: list[str] = []
    for text in expect.get("identity_contains", []):
        if text not in result.identity_text or text not in result.prompt_identity_text:
            problems.append(f"identity_contains failed: {text!r}")
    for text in expect.get("identity_absent", []):
        if text in result.identity_text or text in result.prompt_identity_text:
            problems.append(f"identity_absent failed: {text!r}")

    expected_state = expect.get("dimension_state") or {}
    for key, fields in expected_state.items():
        actual = result.identity_state.get(key)
        if actual is None:
            problems.append(f"dimension_state missing: {key}")
            continue
        for field_name, expected in fields.items():
            if actual.get(field_name) != expected:
                problems.append(
                    f"dimension_state {key}.{field_name}: expected {expected!r}, got {actual.get(field_name)!r}"
                )

    should_have_layer = bool(result.identity_text)
    has_layer = "6a_user_identity" in result.layers_activated
    if should_have_layer != has_layer:
        problems.append(f"6a layer mismatch: identity={should_have_layer}, layer={has_layer}")
    return problems
