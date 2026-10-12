import time

from core.memory import mid_term


def test_history_turns_are_excluded_without_dropping_unselected_gaps(monkeypatch):
    now = time.time()
    monkeypatch.setattr(mid_term, "load", lambda *a, **kw: [
        {"ts": now, "occurred_at": now - 30, "source_turn_id": "shown", "summary": "derived overlap"},
        {"ts": now, "occurred_at": now - 20, "source_turn_id": "gap", "summary": "unselected gap"},
        {"ts": now, "occurred_at": now - 10, "source_turn_id": "latest", "summary": "recent overlap"},
    ])
    text = mid_term.format_for_prompt("fixture-user", history=[
        {"_turn_id": "shown", "content": "original wording"},
        {"_turn_id": "latest", "content": "other original"},
    ])
    assert "derived overlap" not in text and "recent overlap" not in text
    assert "unselected gap" in text


def test_actual_occurrence_order_dedup_and_complete_lines(monkeypatch):
    now = time.time()
    monkeypatch.setattr(mid_term, "load", lambda *a, **kw: [
        {"ts": now - 60, "occurred_at": now - 10, "summary": "new event"},
        {"ts": now, "occurred_at": now - 86400, "summary": "old\nevent"},
        {"ts": now - 1, "summary": "legacy exact"},
        {"ts": now - 2, "summary": "new event"},
    ])
    text = mid_term.format_for_prompt("fixture-user", history=[{"content": "prefix legacy exact suffix"}])
    assert text.index("old event") < text.index("new event")
    assert text.count("new event") == 1
    assert "legacy exact" not in text
    assert time.strftime("%Y-%m-%d %H:%M", time.localtime(now - 86400)) in text
    assert len(text.splitlines()) == 2
    assert "刚才" not in text


def test_bounded_projection_and_invalidated_summaries(monkeypatch):
    now = time.time()
    events = [{"ts": now - i, "summary": f"entry {i} " + "x" * 160} for i in range(20)]
    events += [{"ts": now, "summary": "invalid", "invalidated": True},
               {"ts": now, "summary": "oversized " + "z" * 2000}]
    monkeypatch.setattr(mid_term, "load", lambda *a, **kw: events)
    text = mid_term.format_for_prompt("fixture-user")
    assert len(text) <= 1200 and len(text.splitlines()) <= 8
    assert "invalid" not in text and "oversized" not in text
    assert "entry 0 " in text
    assert all(line.endswith("x" * 160) for line in text.splitlines())


def test_all_history_overlap_yields_empty_projection(monkeypatch):
    monkeypatch.setattr(mid_term, "load", lambda *a, **kw: [
        {"ts": time.time(), "source_turn_id": "existing", "summary": "summary"}])
    assert mid_term.format_for_prompt("fixture-user", history=[{"_turn_id": "existing"}]) == ""
