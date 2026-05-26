from core.perception.events import create_event
from core.perception.policy import decide


def test_policy_defaults_to_all_false_for_unmatched_event():
    event = create_event(
        source="phase0_stub",
        modality="unknown",
        type="unmatched",
        payload={"feature": "derived"},
    )

    affects = decide(event)

    assert affects.can_affect_state is False
    assert affects.can_affect_mood is False
    assert affects.can_write_memory is False
