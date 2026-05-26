from core.perception.policy import decide
from core.perception.sources.mic_source import event_from_feature


def test_mic_source_event_contract_and_default_policy():
    event = event_from_feature({"vad": True, "rms_bucket": 2}, now_ts=1_000.0)

    assert event.subject is None
    assert event.visibility.value == "runtime"
    assert event.sensitivity.value == "high"
    assert event.modality == "acoustic"
    assert event.type == "vad"
    assert event.expires_at == 1_030.0
    assert event.dedupe_key == "mic:vad:1"
    assert event.payload == {"vad": True, "rms_bucket": 2}

    affects = decide(event)

    assert affects.can_affect_state is False
    assert affects.can_affect_mood is False
    assert affects.can_write_memory is False
