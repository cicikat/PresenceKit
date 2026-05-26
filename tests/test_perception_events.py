import pytest

from core.perception.events import create_event, event_to_dict, validate_event


def test_payload_rejects_raw_bytes():
    event = create_event(
        source="phase0_stub",
        modality="audio",
        type="feature",
        payload={"raw": "placeholder"},
    )
    raw = event_to_dict(event)
    raw["payload"] = {"raw": b"not allowed"}

    with pytest.raises(ValueError):
        validate_event(raw)


def test_payload_rejects_large_object():
    with pytest.raises(ValueError):
        create_event(
            source="phase0_stub",
            modality="image",
            type="feature",
            payload={"vector": list(range(128))},
        )


def test_missing_schema_version_fails_validation():
    raw = event_to_dict(
        create_event(
            source="phase0_stub",
            modality="motion",
            type="feature",
            payload={"step_count": 12},
        )
    )
    raw.pop("schema_version")

    with pytest.raises(ValueError):
        validate_event(raw)
