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
