"""Offline P2 comparison: python -m pytest -q -s tests/round2_p2_benchmark.py."""

from contextlib import ExitStack
from math import ceil
from time import perf_counter, time

from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID
from tests.test_memory_event_baseline import _baseline
from tests.test_prompt_ablation import _apply_build_stubs, _base_build_kwargs
from tests.test_vector_store import _CHAR, _FAKE_VEC, _UID, _patch_env


def report(name, samples):
    ordered = sorted(samples)
    print(f"{name} n={len(ordered)} p50_ms={ordered[ceil(len(ordered)*.50)-1]:.3f} "
          f"p95_ms={ordered[ceil(len(ordered)*.95)-1]:.3f}")


async def test_context_and_event_search_baseline(sandbox, monkeypatch):
    from core.character_loader import load
    from core.lore_engine import LoreEngine
    from core.memory import embedding, event_log
    from core.memory.fixation_pipeline import capture_turn
    from core.memory.scope import MemoryScope
    from core.pipeline import Pipeline
    from core.write_envelope import stamp_user_chat

    async def offline(_texts):
        raise RuntimeError("offline fixture")

    monkeypatch.setattr(embedding, "embed", offline)
    uid = _baseline()["owner_uid"]
    query = "placeholder project"
    reply = "baseline assistant reply about that placeholder project"
    peer = "peer character private placeholder memory"
    capture_turn(uid, query, reply, turn_id=f"{uid}_1700000000001", char_id=TEST_CHAR_ID, envelope=stamp_user_chat())
    capture_turn(uid, query, peer, turn_id=f"{uid}_1700000000002", char_id=TEST_PEER_CHAR_ID, envelope=stamp_user_chat())
    pipeline = Pipeline(load(TEST_CHAR_ID), LoreEngine(), active_character_id=TEST_CHAR_ID)
    scope = MemoryScope.reality_scope(uid, TEST_CHAR_ID)
    event_elapsed, context_elapsed = [], []
    for repeat in range(33):
        t = perf_counter()
        events, _ = await event_log.search(uid, query, char_id=TEST_CHAR_ID, return_trace=True)
        duration_event = (perf_counter() - t) * 1_000
        t = perf_counter()
        context = await pipeline.fetch_context(user_id=uid, content=query, frozen_scope=scope)
        duration_context = (perf_counter() - t) * 1_000
        assert reply in events and peer not in events
        assert reply in str(context["history"]) and peer not in str(context)
        if repeat >= 3:
            event_elapsed.append(duration_event)
            context_elapsed.append(duration_context)
    report("event_log.search", event_elapsed)
    report("Pipeline.fetch_context", context_elapsed)


async def test_vector_baseline(tmp_path):
    from core.memory.vector_store import query, upsert

    elapsed = []
    with ExitStack() as stack:
        for patch in _patch_env(tmp_path):
            stack.enter_context(patch)
        await upsert(_UID, _CHAR, "episodic", "ep_001", time(), "one fixture row")
        for repeat in range(33):
            t = perf_counter()
            rows = query(_UID, _CHAR, _FAKE_VEC, k=1)
            duration = (perf_counter() - t) * 1_000
            assert rows and rows[0][0] == "ep_001"
            if repeat >= 3:
                elapsed.append(duration)
    report("vector_store.query", elapsed)


def test_prompt_baseline(sandbox, monkeypatch):
    from core.prompt_builder import build

    _apply_build_stubs(monkeypatch)
    for label, event_text in (("ordinary", "past record"), ("over_budget", "past" * 10000)):
        elapsed = []
        for repeat in range(33):
            kwargs = _base_build_kwargs(event_search_result=event_text, episodic_result="retained memory",
                                        tool_result="screen summary")
            t = perf_counter()
            messages, debug = build(**kwargs)
            duration = (perf_counter() - t) * 1_000
            assert messages[-1]["_layer"] == "12_user_message"
            assert ("6b_event_search" in debug["removed_layers"]) == (label == "over_budget")
            if repeat >= 3:
                elapsed.append(duration)
        report(f"prompt_builder.build.{label}", elapsed)
        print(f"{label} chars={debug['char_estimate']} est_tokens={debug['estimated_tokens']}")
