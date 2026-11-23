from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.fixtures.public_assets import TEST_CHAR_ID


def _scope(uid: str):
    from core.memory.scope import MemoryScope

    return MemoryScope.reality_scope(uid, TEST_CHAR_ID)


def _event(event_id: str, *, occurred_at: float, source: str = "user_chat", **extra):
    value = {
        "event_id": event_id,
        "turn_id": event_id,
        "seq": 0,
        "occurred_at": occurred_at,
        "realm": "reality",
        "kind": "user_message",
        "actor": "user",
        "channel": "qq",
        "stream": "qq:owner",
        "source": source,
    }
    value.update(extra)
    return value


def test_late_event_replaces_only_split_adjacency_and_does_not_scan_scope(sandbox):
    from core.memory import event_store

    scope = _scope("repair-edge")
    event_store._reset_observability_for_tests()
    assert event_store.append_event(scope, _event("a", occurred_at=1)).ok
    assert event_store.append_event(scope, _event("c", occurred_at=3)).ok
    before = event_store.observability_snapshot()["hot_path"]
    assert event_store.append_event(scope, _event("b", occurred_at=2)).ok

    path = event_store.resolve_path(scope, "event_store")
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT from_event_id, to_event_id, relation_type FROM event_edges WHERE relation_type IN ('previous', 'next')"
        ).fetchall()
    assert set(rows) == {
        ("a", "b", "next"), ("b", "a", "previous"),
        ("b", "c", "next"), ("c", "b", "previous"),
    }
    after = event_store.observability_snapshot()["hot_path"]
    assert after["stream_queries"] - before["stream_queries"] == 2
    assert after["turn_queries"] - before["turn_queries"] == 1


@pytest.mark.asyncio
async def test_qq_adapter_forwards_raw_text_and_safe_media_refs(sandbox, monkeypatch):
    import main
    from core.memory.scope import MemoryScope
    from core.output import text_output

    monkeypatch.setattr(text_output, "send", AsyncMock())
    pipeline = MagicMock()
    pipeline.effects.capture_critical = AsyncMock(return_value={"turn_id": "t", "critical_written": True})
    pipeline.effects.run_deferred_effects = AsyncMock()
    monkeypatch.setattr(main, "_pipeline", pipeline)

    await main._qq_reality_reply_adapter(
        ["reply"], "repair-qq", "media prompt text", "repair-qq", False,
        frozen_scope=MemoryScope.reality_scope("repair-qq", TEST_CHAR_ID),
        raw_user_text="owner caption",
        media_refs=[{"kind": "image", "filename": "safe.png", "sha256": "a" * 64}],
    )

    kwargs = pipeline.effects.capture_critical.await_args.kwargs
    assert kwargs["raw_user_text"] == "owner caption"
    assert kwargs["media_refs"] == [{"kind": "image", "filename": "safe.png", "sha256": "a" * 64}]


@pytest.mark.asyncio
async def test_qq_media_processing_returns_hash_without_url_or_path(monkeypatch, tmp_path):
    from core import media_processor

    image_bytes = b"\x89PNG\r\n\x1a\nfixture"
    monkeypatch.setattr(media_processor, "download_bytes", AsyncMock(return_value=image_bytes))
    monkeypatch.setattr(media_processor, "ingest_image_bytes", AsyncMock(return_value=["image description"]))
    description, image_ref = await media_processor.process_image_with_evidence(
        "https://example.invalid/private.png?token=secret"
    )
    assert description == "image description"
    assert image_ref == {
        "kind": "image", "filename": "private.png", "availability": "available",
        "sha256": "bd54b02fae14b6b9ed73887ded339b8ef846fbcba0d4e5f9d95470ac23ade242",
    }

    file_bytes = b"owner document"
    monkeypatch.setattr(media_processor, "download_bytes", AsyncMock(return_value=file_bytes))
    monkeypatch.setattr(
        media_processor, "ingest_file_bytes", AsyncMock(return_value=("extracted text", Path(tmp_path) / "private.txt")),
    )
    text, file_ref = await media_processor.process_file_with_evidence({
        "name": "private.txt", "url": "https://example.invalid/file?token=secret",
    })
    assert text == "extracted text"
    assert file_ref["kind"] == "file"
    assert file_ref["filename"] == "private.txt"
    assert len(file_ref["sha256"]) == 64
    assert "url" not in file_ref and "path" not in file_ref


def test_storyline_nodes_expand_only_selected_material_sources(sandbox):
    from core.memory import storyline
    from core.scheduler.triggers.storyline_weekly import _apply_ops

    uid = "repair-lineage"
    _apply_ops(uid, TEST_CHAR_ID, [
        {"op": "open_arc", "title": "topic A", "tags": []},
        {"op": "append_node", "arc_title": "topic A", "summary": "A", "ts": 1,
         "span": [1, 1], "source_material_ids": ["m001"]},
    ], material_sources={"m001": ["a:user"], "m002": ["b:user"]})
    node = storyline.load(uid, char_id=TEST_CHAR_ID)["arcs"][0]["nodes"][0]
    assert node["source_ids"] == ["a:user"]

    with pytest.raises(ValueError, match="invalid_material_ids"):
        _apply_ops(uid, TEST_CHAR_ID, [
            {"op": "append_node", "arc_title": "topic A", "summary": "bad", "ts": 2,
             "span": [2, 2], "source_material_ids": ["m404"]},
        ], material_sources={"m001": ["a:user"]})
    assert len(storyline.load(uid, char_id=TEST_CHAR_ID)["arcs"][0]["nodes"]) == 1


@pytest.mark.asyncio
async def test_role_tools_hide_isolated_sources_and_admin_can_select_them(sandbox):
    from core.memory import event_query, event_store
    from core.tools.event_tools import search_events_wrapper

    scope = _scope("repair-source")
    assert event_store.append_event(scope, _event("ordinary", occurred_at=1)).ok
    assert event_store.append_event(scope, _event("external", occurred_at=2, source="web")).ok

    default = event_query.search(
        scope, text="", actor="", kind="", source="", occurred_after=None,
        occurred_before=None, cursor="", limit=10,
    )
    assert [item["event_id"] for item in default["items"]] == ["ordinary"]
    source_metrics = event_store.observability_snapshot()["source_policy"]
    assert source_metrics["policy_filtered_query_count"] >= 1
    assert source_metrics["rejected"] == 0
    forensic = event_query.search(
        scope, text="", actor="", kind="", source="web", occurred_after=None,
        occurred_before=None, cursor="", limit=10,
    )
    assert [item["event_id"] for item in forensic["items"]] == ["external"]
    denied = json.loads((await search_events_wrapper(
        scope.uid, source="web", char_id=TEST_CHAR_ID,
    )).safe_summary)
    assert denied["reason"] == "source_not_available"

    path = event_store.resolve_path(scope, "event_store")
    with sqlite3.connect(path) as connection:
        edges = connection.execute(
            "SELECT COUNT(*) FROM event_edges WHERE relation_type IN ('previous', 'next')"
        ).fetchone()[0]
    assert edges == 0
