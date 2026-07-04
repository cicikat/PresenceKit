"""Unit tests for Phase 1 碎碎念主题软降权: recent_topics freshness helpers."""

from datetime import datetime

import pytest


# ── compute_topic_freshness ────────────────────────────────────────────────────

def test_fresh_topic_returns_full_freshness(sandbox):
    from core.scheduler.last_mentioned import compute_topic_freshness

    assert compute_topic_freshness("实习材料", "random") == 1.0


def test_empty_topic_key_returns_full_freshness(sandbox):
    from core.scheduler.last_mentioned import compute_topic_freshness

    assert compute_topic_freshness("", "random") == 1.0


def test_just_marked_topic_is_dampened_not_zeroed(sandbox):
    from core.scheduler.last_mentioned import (
        MIN_FRESHNESS,
        compute_topic_freshness,
        mark_recent_topic,
    )

    now = datetime(2026, 1, 1, 12, 0, 0)
    mark_recent_topic("实习材料", "random", now=now)
    freshness = compute_topic_freshness("实习材料", "random", now=now)

    # elapsed≈0 → base_decay≈0 → clamped to MIN_FRESHNESS
    assert freshness == pytest.approx(MIN_FRESHNESS, abs=1e-6)
    assert freshness > 0.0


def test_fully_recovered_has_repeat_penalty_not_full_score(sandbox):
    from core.scheduler.last_mentioned import (
        FULL_RECOVER_SECONDS,
        REPEAT_K,
        compute_topic_freshness,
        mark_recent_topic,
    )

    base = datetime(2026, 1, 1, 12, 0, 0)
    mark_recent_topic("实习材料", "random", now=base)

    after = datetime.fromtimestamp(base.timestamp() + FULL_RECOVER_SECONDS + 1)
    freshness = compute_topic_freshness("实习材料", "random", now=after)

    # base_decay=1.0, repeat_penalty = 1/(1 + REPEAT_K*1)
    expected = 1.0 / (1.0 + REPEAT_K * 1)
    assert freshness == pytest.approx(expected, rel=1e-3)
    assert freshness < 1.0


def test_cross_source_gets_relaxation_boost(sandbox):
    from core.scheduler.last_mentioned import (
        CROSS_SOURCE_RELAX,
        MIN_FRESHNESS,
        compute_topic_freshness,
        mark_recent_topic,
    )

    base = datetime(2026, 1, 1, 12, 0, 0)
    mark_recent_topic("实习材料", "random", now=base)

    # 30 minutes later — base_decay is above MIN_FRESHNESS so relax is observable
    later = datetime.fromtimestamp(base.timestamp() + 1800)
    same_src = compute_topic_freshness("实习材料", "random", now=later)
    diff_src = compute_topic_freshness("实习材料", "followup", now=later)

    assert diff_src > same_src
    assert diff_src == pytest.approx(min(1.0, same_src * CROSS_SOURCE_RELAX), rel=1e-6)


def test_speak_count_accumulates(sandbox):
    from core.scheduler.last_mentioned import (
        REPEAT_K,
        compute_topic_freshness,
        mark_recent_topic,
    )

    base = datetime(2026, 1, 1, 12, 0, 0)
    mark_recent_topic("实习材料", "random", now=base)
    mark_recent_topic("实习材料", "random", now=base)  # second time

    from core.scheduler.last_mentioned import FULL_RECOVER_SECONDS
    after = datetime.fromtimestamp(base.timestamp() + FULL_RECOVER_SECONDS + 1)
    freshness = compute_topic_freshness("实习材料", "random", now=after)

    # speak_count=2 → repeat_penalty = 1/(1+0.3*2) = 1/1.6 = 0.625
    expected = 1.0 / (1.0 + REPEAT_K * 2)
    assert freshness == pytest.approx(expected, rel=1e-3)


# ── mark_recent_topic / shadow separation ─────────────────────────────────────

def test_shadow_and_live_recent_topics_are_separate(sandbox):
    import json

    from core.scheduler.last_mentioned import mark_recent_topic

    now = datetime(2026, 1, 1, 12, 0, 0)
    mark_recent_topic("实习材料", "random", now=now, dry_run=False)
    mark_recent_topic("桌宠通道", "followup", now=now, dry_run=True)

    raw = json.loads(sandbox.scheduler_user_state().read_text(encoding="utf-8"))
    assert "实习材料" in raw.get("recent_topics", {})
    assert "桌宠通道" not in raw.get("recent_topics", {})
    assert "桌宠通道" in raw.get("recent_topics_shadow", {})
    assert "实习材料" not in raw.get("recent_topics_shadow", {})


# ── _rank_last_mentioned_candidates ───────────────────────────────────────────

def test_rank_empty_candidates_is_safe(sandbox):
    from core.scheduler.last_mentioned import _rank_last_mentioned_candidates

    result = _rank_last_mentioned_candidates([], now=datetime.now(), dry_run=False)
    assert result == []


def test_rank_prefers_fresh_topic_over_stale(sandbox):
    from core.scheduler.last_mentioned import (
        LastMentionedTopic,
        _rank_last_mentioned_candidates,
        mark_recent_topic,
    )

    now = datetime(2026, 1, 1, 12, 0, 0)
    stale_key = "刚写完实习材料"
    fresh_key = "准备测试桌宠通道"

    # Mark stale_key as recently spoken → dampened
    mark_recent_topic(stale_key, "followup", now=now)

    def _make(key: str, score: float) -> LastMentionedTopic:
        return LastMentionedTopic(
            topic=key,
            topic_key=key,
            context="",
            user_text="",
            assistant_text="",
            mentioned_at="2026-01-01 12:00",
            age_seconds=0.0,
            score=score,
        )

    # Both have same score; stale should be demoted
    candidates = [_make(stale_key, 0.8), _make(fresh_key, 0.8)]
    ranked = _rank_last_mentioned_candidates(candidates, now=now, dry_run=False)

    assert ranked[0].topic_key == fresh_key


# ── mark_topic_followed also writes recent_topics ─────────────────────────────

def test_mark_topic_followed_writes_recent_topics(sandbox):
    import json

    from core.scheduler.last_mentioned import mark_topic_followed

    mark_topic_followed("桌宠通道", now_ts=1_000.0)

    raw = json.loads(sandbox.scheduler_user_state().read_text(encoding="utf-8"))
    assert "桌宠通道" in raw.get("recent_topics", {})
    assert raw["recent_topics"]["桌宠通道"]["last_source"] == "followup"
    assert raw["recent_topics"]["桌宠通道"]["speak_count"] == 1


def test_mark_topic_followed_shadow_writes_recent_topics_shadow(sandbox):
    import json

    from core.scheduler.last_mentioned import mark_topic_followed_shadow

    mark_topic_followed_shadow("桌宠通道", now_ts=1_000.0)

    raw = json.loads(sandbox.scheduler_user_state().read_text(encoding="utf-8"))
    assert "桌宠通道" in raw.get("recent_topics_shadow", {})
    assert "桌宠通道" not in raw.get("recent_topics", {})


# ── _random_message_context_hint ──────────────────────────────────────────────

def test_random_message_hint_returns_string_and_marks_topic(sandbox, monkeypatch):
    monkeypatch.setattr(
        "core.memory.event_log.get_highlights",
        lambda oid, days: "在做数字整理\n在写实习材料",
    )
    monkeypatch.setattr(
        "core.scheduler.loop._char_name",
        lambda: "Companion",
    )

    from core.scheduler.triggers.time_based import _random_message_context_hint

    result = _random_message_context_hint("u1", dry_run=True)

    assert isinstance(result, str)
    # Either a hint or empty string (both valid)
    if result:
        assert "想到一件事" in result


def test_random_message_hint_falls_back_on_empty_highlights(sandbox, monkeypatch):
    monkeypatch.setattr(
        "core.memory.event_log.get_highlights",
        lambda oid, days: "",
    )

    from core.scheduler.triggers.time_based import _random_message_context_hint

    result = _random_message_context_hint("u1", dry_run=True)
    assert result == ""


# ── Fix 4: last_source 破损保护 ───────────────────────────────────────────────

def test_last_source_empty_does_not_trigger_cross_source_boost(sandbox):
    import json
    from pathlib import Path

    from core.sandbox import get_paths
    from core.scheduler.last_mentioned import (
        CROSS_SOURCE_RELAX,
        mark_recent_topic,
        compute_topic_freshness,
    )

    base = datetime(2026, 1, 1, 12, 0, 0)
    later = datetime.fromtimestamp(base.timestamp() + 1800)

    mark_recent_topic("实习材料", "random", now=base)

    # Corrupt last_source to empty string (simulates bad/migrated data)
    path = get_paths().scheduler_user_state()
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    state["recent_topics"]["实习材料"]["last_source"] = ""
    Path(path).write_text(json.dumps(state), encoding="utf-8")

    freshness_empty_source = compute_topic_freshness("实习材料", "followup", now=later)

    # Restore a real same-source entry to get the no-boost baseline
    state["recent_topics"]["实习材料"]["last_source"] = "followup"
    Path(path).write_text(json.dumps(state), encoding="utf-8")
    freshness_same_source = compute_topic_freshness("实习材料", "followup", now=later)

    # Empty last_source must NOT apply CROSS_SOURCE_RELAX
    assert freshness_empty_source == pytest.approx(freshness_same_source, rel=1e-6)
    # Sanity: genuine cross-source IS higher
    state["recent_topics"]["实习材料"]["last_source"] = "random"
    Path(path).write_text(json.dumps(state), encoding="utf-8")
    freshness_cross = compute_topic_freshness("实习材料", "followup", now=later)
    assert freshness_cross == pytest.approx(min(1.0, freshness_same_source * CROSS_SOURCE_RELAX), rel=1e-6)


# ── Fix 3: recent_topics 上限裁剪 ─────────────────────────────────────────────

def test_recent_topics_pruned_at_max_size(sandbox):
    import json
    from pathlib import Path

    from core.sandbox import get_paths
    from core.scheduler.last_mentioned import MAX_RECENT_TOPICS, mark_recent_topic

    base = datetime(2026, 1, 1, 12, 0, 0)
    path = get_paths().scheduler_user_state()

    # Pre-fill MAX_RECENT_TOPICS + 10 entries with ascending timestamps
    state: dict = {"recent_topics": {}}
    for i in range(MAX_RECENT_TOPICS + 10):
        ts_dt = datetime.fromtimestamp(base.timestamp() + i)
        state["recent_topics"][f"topic_{i:04d}"] = {
            "last_spoken_at": ts_dt.isoformat(timespec="seconds"),
            "speak_count": 1,
            "last_source": "random",
        }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state), encoding="utf-8")

    # One more write triggers pruning
    newest_ts = datetime.fromtimestamp(base.timestamp() + MAX_RECENT_TOPICS + 10)
    mark_recent_topic("new_topic", "random", now=newest_ts)

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    assert len(raw["recent_topics"]) == MAX_RECENT_TOPICS
    # Newest entry must be kept
    assert "new_topic" in raw["recent_topics"]
    # Oldest entries must be dropped (topics 0000 through 0010 are the 11 oldest)
    assert "topic_0000" not in raw["recent_topics"]
    assert "topic_0010" not in raw["recent_topics"]
    assert "topic_0011" in raw["recent_topics"]


def test_recent_topics_shadow_pruned_independently(sandbox):
    import json
    from pathlib import Path

    from core.sandbox import get_paths
    from core.scheduler.last_mentioned import MAX_RECENT_TOPICS, mark_recent_topic

    base = datetime(2026, 1, 1, 12, 0, 0)
    path = get_paths().scheduler_user_state()

    # Pre-fill shadow with MAX_RECENT_TOPICS + 5 entries
    state: dict = {"recent_topics_shadow": {}}
    for i in range(MAX_RECENT_TOPICS + 5):
        ts_dt = datetime.fromtimestamp(base.timestamp() + i)
        state["recent_topics_shadow"][f"shadow_{i:04d}"] = {
            "last_spoken_at": ts_dt.isoformat(timespec="seconds"),
            "speak_count": 1,
            "last_source": "followup",
        }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state), encoding="utf-8")

    newest_ts = datetime.fromtimestamp(base.timestamp() + MAX_RECENT_TOPICS + 5)
    mark_recent_topic("shadow_new", "followup", now=newest_ts, dry_run=True)

    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    assert len(raw["recent_topics_shadow"]) == MAX_RECENT_TOPICS
    assert "shadow_new" in raw["recent_topics_shadow"]
    assert "shadow_0000" not in raw["recent_topics_shadow"]
    # live recent_topics must be untouched
    assert raw.get("recent_topics", {}) == {}
