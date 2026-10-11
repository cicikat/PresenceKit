import pytest
from core.activity import records, store, transcript


def test_scoped_readonly_and_revision(sandbox):
    value = store.create_session("owner", "companion", "chess", {"move_history": [{"move": "e4"}]})
    ref = f"activity:chess:{value.session_id}:moves"
    result = records.read("owner", "companion", ref)
    assert result["items"] == [{"move": "e4"}]
    with pytest.raises(LookupError):
        records.read("other", "companion", ref)
    store.update_state("companion", "owner", "chess", value.session_id, {"move_history": [{"move": "d4"}]})
    with pytest.raises(ValueError):
        records.read("owner", "companion", ref, expected_revision=result["revision"])
    with pytest.raises(ValueError):
        records.read("owner", "companion", f"activity:dream_seed:{value.session_id}:state")


def test_long_original_and_concurrent_sessions(sandbox):
    first = store.create_session("owner", "companion", "chess")
    store.create_session("owner", "companion", "gomoku")
    text = "a" * 15000 + "TAIL"
    transcript.append_entry("companion", "owner", "chess", first.session_id,
                            {"type": "user_chat", "text": text, "ts": "2026-10-11"})
    ref = f"activity:chess:{first.session_id}:entry:0"
    assert records.read("owner", "companion", ref, text_offset=15000)["items"][0]["text"] == "TAIL"
    assert records.list_sessions("owner", "companion", active_only=True)["total"] == 2


def test_late_state_cannot_replace_current_board(sandbox):
    value = store.create_session("owner", "companion", "chess")
    stale = store.load_session("companion", "owner", "chess", value.session_id)
    store.update_state("companion", "owner", "chess", value.session_id, {"fen": "new"})
    stale.state = {"fen": "old"}
    with pytest.raises(store.ActivityPersistenceError):
        store.save_session(stale)
    assert store.load_session("companion", "owner", "chess", value.session_id).state["fen"] == "new"


def test_withdrawal_blocks_local_recall_and_invalidates_projection(sandbox, monkeypatch):
    import asyncio
    import json
    from core.activity import activity_summary
    value = store.create_session("owner", "companion", "chess")
    transcript.append_entry("companion", "owner", "chess", value.session_id,
                            {"type": "user_chat", "text": "private phrase"})
    root = transcript._path("companion", "owner", "chess", value.session_id).parent
    receipts = root / "dialogue_receipts"
    receipts.mkdir()
    (receipts / "request.json").write_text(json.dumps({"status": "completed", "turn_id": "turn",
        "entry_start": 0, "entry_end": 1}), encoding="utf-8")
    state = {"redaction_state": "memory_cleaned"}
    monkeypatch.setattr("core.memory.event_query.get_event", lambda *args, **kwargs: state)
    asyncio.run(activity_summary.generate_and_reflow("owner", "companion", "chess", value.session_id))
    state["redaction_state"] = "tombstoned"
    ref = f"activity:chess:{value.session_id}"
    assert records.read("owner", "companion", ref + ":entry:0")["items"][0]["text"] == ""
    assert records.read("owner", "companion", ref + ":summary")["status"] == "pending"
    assert transcript.load_recent("companion", "owner", "chess", value.session_id) == []
    assert "private phrase" in (root / "transcript.jsonl").read_text(encoding="utf-8")
    assert records.search("owner", "companion", keyword="private")["total"] == 0


def test_search_across_scoped_sessions(sandbox):
    for kind in ("chess", "gomoku"):
        value = store.create_session("owner", "companion", kind)
        transcript.append_entry("companion", "owner", kind, value.session_id,
                                {"type": "user_chat", "text": "needle", "ts": "2026-10-11"})
    assert records.search("owner", "companion", keyword="needle", limit=1)["total"] == 2
    assert records.search("other", "companion", keyword="needle")["total"] == 0
