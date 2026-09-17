from pathlib import Path

from core.scheduler.gating import TRIGGER_MIGRATION_STATUS
from core.scheduler.proposer_registry import registered_trigger_names
from core.tool_dispatcher import _TOOL_REGISTRY


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "agent-runtime-architecture.md"


def _text() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def test_contract_maps_every_registered_scheduler_lifecycle() -> None:
    text = _text()
    registered = set(TRIGGER_MIGRATION_STATUS) | set(registered_trigger_names())
    missing = sorted(name for name in registered if f"`{name}`" not in text)
    assert missing == []


def test_contract_maps_known_unregistered_scheduler_producers() -> None:
    text = _text()
    producer_names = {
        "event_edge_proposer",
        "dream_postcards",
        "practice_help",
        "weather_alert_light",
        "weather_alert_heavy",
        "watch_hr_critical",
        "watch_hr_high",
        "watch_sleep_end",
    }
    assert all(f"`{name}`" in text for name in producer_names)


def test_contract_maps_every_builtin_tool() -> None:
    text = _text()
    missing = sorted(name for name in _TOOL_REGISTRY if f"`{name}`" not in text)
    assert missing == []


def test_contract_freezes_identity_realm_and_soak_boundaries() -> None:
    text = _text()
    required = (
        "task_id",
        "ingress_event_id",
        "turn_id",
        "tool_request",
        "causation_ref",
        "outcome_unknown",
        "Dream create/read/cancel/capability denial",
        "217 soak invariance",
        "EventBus",
    )
    assert all(item in text for item in required)
