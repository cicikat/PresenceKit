from core.memory import health_state
from scripts.migrate_character_user_facts import migrate


def test_health_and_period_do_not_cross_characters(sandbox):
    health_state.save("owner", {"heart_rate_events": [{"value": 80}]}, char_id="alpha")
    health_state.set_period_date("owner", "2026-10-01", char_id="alpha")
    assert health_state.load("owner", char_id="alpha")["heart_rate_events"] == [{"value": 80}]
    assert health_state.load("owner", char_id="beta")["heart_rate_events"] == []
    assert health_state.get_period_info("owner", char_id="beta") == {"last_period_date": None}


def test_shared_health_requires_explicit_assignment(sandbox):
    source = sandbox.legacy_health_state("owner")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'{"heart_rate_events": [{"value": 80}]}')
    before = source.read_bytes()
    assert health_state.load("owner", char_id="alpha")["heart_rate_events"] == []
    assert migrate("owner", "alpha", artifact="health_state")["status"] == "dry_run"
    assert migrate("owner", "alpha", artifact="health_state", apply=True)["status"] == "imported"
    assert migrate("owner", "alpha", artifact="health_state", apply=True)["status"] == "already_imported"
    assert health_state.load("owner", char_id="beta")["heart_rate_events"] == []
    assert source.read_bytes() == before
