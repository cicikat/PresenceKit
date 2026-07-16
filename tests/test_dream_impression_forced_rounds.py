from unittest.mock import patch


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
