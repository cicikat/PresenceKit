"""Contract tests for the observation-only cross-world invariant store."""
import asyncio
from unittest.mock import AsyncMock, patch

def test_forbidden_abstract_words_are_dropped():
    from core.dream.invariants import valid_items
    result = valid_items({"items": [{"situation": "\u88ab\u8bef\u89e3\u65f6", "response": "\u4ed6\u5148\u7b49\u5bf9\u65b9\u8bf4\u660e"}, {"situation": "\u6c38\u8fdc\u5931\u7ea6\u65f6", "response": "\u4ed6\u6c89\u9ed8"}]})
    assert result == [{"situation": "\u88ab\u8bef\u89e3\u65f6", "response": "\u4ed6\u5148\u7b49\u5bf9\u65b9\u8bf4\u660e"}]

def test_merge_creates_and_then_converges_across_worlds():
    from core.dream import invariants
    stored = []
    def fake_load(*_args, **_kwargs): return stored
    def fake_save(_uid, entries, **_kwargs): stored[:] = entries; return True
    with patch.object(invariants, "load", fake_load), patch.object(invariants, "save", fake_save), patch.object(invariants, "_relation", AsyncMock(return_value="same")):
        asyncio.run(invariants.merge("u", {"situation": "s", "response": "r"}, dream_id="d1", world_id="cat"))
        asyncio.run(invariants.merge("u", {"situation": "s", "response": "r"}, dream_id="d2", world_id="vampire"))
    assert stored[0]["count"] == 2
    assert stored[0]["worlds_seen"] == ["cat", "vampire"]

def test_merge_appends_contradiction_to_high_convergence_entry():
    from core.dream import invariants
    stored = [{"situation": "s", "response": "wait", "count": 3, "worlds_seen": ["cat", "vampire"], "contradicted_by": []}]
    with patch.object(invariants, "load", return_value=stored), patch.object(invariants, "save", return_value=True), patch.object(invariants, "_relation", AsyncMock(return_value="contradicts")):
        asyncio.run(invariants.merge("u", {"situation": "s", "response": "ask"}, dream_id="d3", world_id="abo"))
    assert stored[0]["contradicted_by"] == [{"dream_id": "d3", "summary": "ask"}]

def test_conversation_prompt_modules_cannot_read_invariants_store():
    from pathlib import Path
    for name in ("core/prompt_builder.py", "core/dream/dream_prompt.py"):
        source = Path(name).read_text(encoding="utf-8")
        assert "dreams_invariants" not in source
        assert "core.dream.invariants" not in source
    assert "core.dream.invariants" in Path("admin/routers/dream.py").read_text(encoding="utf-8")
