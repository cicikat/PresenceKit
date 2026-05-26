from core.hardware.adapters.mic import MicDevice
from core.perception.sources import mic_source


def test_mic_features_and_event_payload_do_not_leak_audio_bytes(monkeypatch):
    raw_audio = b"\x80\x80\xff\x00\x7f"
    frame = {"audio": raw_audio, "device_meta": "ignored"}
    device = MicDevice()

    feature = device.analyze_frame(frame)

    assert set(feature) == {"vad", "rms_bucket"}
    assert raw_audio not in feature.values()
    assert all(not isinstance(value, (bytes, bytearray, memoryview)) for value in feature.values())

    submitted = []
    monkeypatch.setattr(mic_source.intake, "submit", lambda event: submitted.append(event) or event)

    events = mic_source.submit_features([feature], now_ts=1_000.0)

    assert events == submitted
    payload = events[0].payload
    assert payload == {"vad": feature["vad"], "rms_bucket": feature["rms_bucket"]}
    assert raw_audio not in payload.values()
    assert all(not isinstance(value, (bytes, bytearray, memoryview)) for value in payload.values())
