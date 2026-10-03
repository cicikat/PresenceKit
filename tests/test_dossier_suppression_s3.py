"""工单 S3：dossier 抑制模式（global / overlap）与注入开关。"""
from __future__ import annotations

import asyncio

import pytest

from tests.fixtures.public_assets import TEST_CHAR_ID
from tests.test_fetch_context_lowinfo_gate import (  # noqa: F401  (fixtures)
    _apply_base_stubs, _make_pipeline, _write_active, chars_tree, registry,
)


def _ep(eid, event_ids, text):
    return {"id": eid, "content": text, "timestamp": 1.0, "source_event_ids": event_ids,
            "_bucket": "recent", "strength": 0.5}


def _setup(monkeypatch, sandbox, registry, cfg):
    import core.config_loader as _cl
    import core.memory.dossiers as _dos
    import core.memory.episodic_memory as _ep_mod
    pipeline = _make_pipeline(TEST_CHAR_ID, registry)
    _write_active(sandbox, TEST_CHAR_ID)
    _apply_base_stubs(monkeypatch)
    real = _cl.get_config
    monkeypatch.setattr(_cl, "get_config", lambda *a, **kw: {**dict(real(*a, **kw) or {}), **cfg})
    monkeypatch.setattr(_dos, "build_recall_context", lambda *a, **kw: {
        "text": "[coffee] understanding", "dossier_ids": ["d1"], "truncated": False,
        "unreviewed": False, "event_ids": ["ev-1"]})
    episodes = [_ep("a", ["ev-1"], "overlap-episode"), _ep("b", ["ev-9"], "kept-episode")]
    monkeypatch.setattr(_ep_mod, "retrieve_mixed", lambda *a, **kw: (list(episodes), []))
    monkeypatch.setattr(_ep_mod, "format_for_prompt",
                        lambda items, **kw: "|".join(i["content"] for i in items))
    import core.pipeline as _pl
    monkeypatch.setattr(_pl, "format_for_prompt", _ep_mod.format_for_prompt, raising=False)
    return pipeline


def _run(pipeline):
    return asyncio.run(pipeline.fetch_context(user_id="u1", content="tell me about coffee please"))


def test_global_mode_clears_all(monkeypatch, sandbox, registry, chars_tree):
    ctx = _run(_setup(monkeypatch, sandbox, registry, {"memory_dossiers": {"suppression": "global"}}))
    assert ctx["memory_dossier_context"]
    assert ctx["episodic_result"] == ""


def test_overlap_mode_keeps_non_overlapping(monkeypatch, sandbox, registry, chars_tree):
    ctx = _run(_setup(monkeypatch, sandbox, registry, {"memory_dossiers": {"suppression": "overlap"}}))
    assert ctx["memory_dossier_context"]
    assert ctx["episodic_result"] == "kept-episode"


def test_prompt_injection_false_disables_layer_and_suppression(monkeypatch, sandbox, registry, chars_tree):
    ctx = _run(_setup(monkeypatch, sandbox, registry,
                      {"memory_dossiers": {"prompt_injection": False, "suppression": "global"}}))
    assert ctx["memory_dossier_context"] == ""
    assert ctx["episodic_result"] == "overlap-episode|kept-episode"
