import time
from core.memory.prompt_sources import evidence_hint
from core.memory import mid_term, episodic_memory


def test_real_sources_are_bounded_and_not_guessed():
    assert evidence_hint([]) == ""
    hint = evidence_hint(["ev1", "ev1", "ev2", "ev3", "ev4"])
    assert "ev1" in hint and "ev3" in hint and "ev4" not in hint
    assert "原始证据event_id=" in hint
    assert "summary123" not in hint


def test_midterm_and_episodic_keep_actual_evidence(monkeypatch):
    monkeypatch.setattr(mid_term, "load", lambda *a, **k: [{"ts": time.time(),
        "mid_id": "derived-id", "summary": "remembered event", "source_event_ids": ["source1"]}])
    text = mid_term.format_for_prompt("fixture-user")
    assert "source1" in text and "derived-id" not in text
    text = episodic_memory.format_for_prompt([{"id": "episode-id", "timestamp": time.time(),
        "summary": "episode", "source_event_ids": ["source2"]}], char_name="Companion")
    assert "source2" in text and "episode-id" not in text


def test_legacy_turn_resolves_only_existing_scoped_ledger_ids(monkeypatch):
    from core.memory import event_store
    seen = []
    def lookup(scope, turn_id):
        seen.append((scope.uid, scope.character_id, turn_id))
        return ["ledger-event"]
    monkeypatch.setattr(event_store, "event_ids_for_turn", lookup)
    assert "ledger-event" in evidence_hint(uid="user", char_id="character", turn_id="turn")
    assert seen == [("user", "character", "turn")]
    monkeypatch.setattr(event_store, "event_ids_for_turn", lambda *a: [])
    assert evidence_hint(uid="user", char_id="character", turn_id="turn") == ""


def test_materials_expose_queryable_ids_without_transport_hashes():
    from core.context_continuity import _material_summary
    doc = _material_summary("upload", {"document_id": "doc_fixture", "filename": "notes",
        "summary": "excerpt", "sha256": "private-hash"})
    assert "document_id=doc_fixture" in doc and "read_document" in doc
    assert "private-hash" not in doc
    life = _material_summary("life", {"id": "record_fixture", "title": "record", "note": "detail"})
    assert "record_id=record_fixture" in life and "read_life_records" in life


def test_top_episodic_keeps_source_id_and_30k_pruning_boundary(sandbox, monkeypatch):
    from tests.test_prompt_ablation import _apply_build_stubs, _base_build_kwargs
    from core.prompt_builder import build
    _apply_build_stubs(monkeypatch)
    episodic = episodic_memory.format_for_prompt([{"timestamp": time.time(), "summary": "memory",
        "source_event_ids": ["exact-source"]}], char_name="Companion")
    messages, debug = build(**_base_build_kwargs(episodic_result=episodic, episodic_top_bucket="long",
        lore_entries=["x" * 25000]))
    top = next(m for m in messages if m["_layer"] == "9.5_episodic_top")
    assert "exact-source" in top["content"]
    assert debug["char_estimate"] > 20000 and not debug["removed_layers"]
    _, debug = build(**_base_build_kwargs(lore_entries=["x" * 35000]))
    assert "5.5_lore" in debug["removed_layers"] and debug["char_estimate"] <= 28000
