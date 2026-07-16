from unittest.mock import patch
import time


def test_dream_close_sets_configured_forced_rounds(sandbox):
    from core.dream.dream_state import configured_forced_impression_rounds

    with patch("core.config_loader.get_config", return_value={"dream": {"impression": {"forced_rounds": 5}}}):
        assert configured_forced_impression_rounds() == 5


def test_only_reality_owner_turn_consumes_forced_round(sandbox):
    from core.dream.dream_state import read_state, write_state, consume_forced_impression_round

    uid = "forced-round-owner"
    state = read_state(uid)
    state["forced_impression_rounds_left"] = 3
    write_state(uid, state)

    assert consume_forced_impression_round(uid, reality_owner_turn=False) == 3
    assert read_state(uid)["forced_impression_rounds_left"] == 3
    assert consume_forced_impression_round(uid, reality_owner_turn=True) == 3
    assert read_state(uid)["forced_impression_rounds_left"] == 2


def test_invalid_forced_round_state_fails_open_as_zero(sandbox):
    from core.dream.dream_state import read_state, write_state, consume_forced_impression_round

    uid = "forced-round-invalid"
    state = read_state(uid)
    state["forced_impression_rounds_left"] = "invalid"
    write_state(uid, state)

    assert consume_forced_impression_round(uid, reality_owner_turn=True) == 0
    assert read_state(uid)["forced_impression_rounds_left"] == "invalid"


def _append(uid: str, dream_id: str, plot: str, tags: list[str], ts: float) -> None:
    from core.dream.impression_store import append_impression

    append_impression(uid, {
        "dream_id": dream_id,
        "ts": ts,
        "last_decay_ts": ts,
        "impression_text": f"我记得{plot}",
        "plot": plot,
        "vivid_lines": [],
        "weight": 0.3,
        "emotional_tags": tags,
        "exit_type": "soft",
        "decay_after": ts + 30 * 86400,
        "marked": True,
    })


def test_forced_mode_injects_only_latest_exited_dream(sandbox):
    from core.dream.impression_loader import load_impression_text

    uid = "forced-specific-dream"
    now = time.time()
    _append(uid, "dream-old", "旧梦里的森林", ["安静"], now - 10)
    _append(uid, "dream-latest", "新梦里的灯塔", ["期待"], now)

    text = load_impression_text(uid, forced_rounds_left=3, latest_dream_id="dream-latest")
    assert "新梦里的灯塔" in text
    assert "旧梦里的森林" not in text


def test_recall_mode_requires_and_ranks_topic_match(sandbox):
    from core.dream.impression_loader import load_impression_text

    uid = "topic-recall"
    now = time.time()
    _append(uid, "dream-forest", "在森林深处等雨停", ["安静"], now - 10)
    _append(uid, "dream-lighthouse", "在海边灯塔下重逢", ["期待"], now)

    missed = load_impression_text(uid, forced_rounds_left=0, user_text="今天吃什么")
    assert missed == ""
    matched = load_impression_text(uid, forced_rounds_left=0, user_text="我想起那座灯塔")
    assert "海边灯塔" in matched
    assert "森林深处" not in matched
    tag_matched = load_impression_text(
        uid, forced_rounds_left=0, user_text="说不上来", tags={"安静"}
    )
    assert "森林深处" in tag_matched


def test_recall_can_be_disabled_after_forced_rounds(sandbox):
    from core.dream.impression_loader import load_impression_text

    uid = "recall-disabled"
    now = time.time()
    _append(uid, "dream-lighthouse", "在海边灯塔下重逢", ["期待"], now)
    assert load_impression_text(
        uid,
        forced_rounds_left=0,
        user_text="灯塔",
        recall_enabled=False,
    ) == ""
