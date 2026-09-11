from core.observe import prompt_capture as capture


def test_injection_updates_matching_snapshot_without_new_turn():
    messages = [{"role": "user", "content": "hello", "_layer": "user"}]
    capture.capture("capture-fixture", messages, {"char_estimate": 5})
    before = capture.get_snapshots("capture-fixture")[-1]
    updated = [{"role": "system", "content": "voice", "_layer": "11.6_thinking_voice"}, *messages]
    capture.capture_injected_messages(messages, updated)
    assert capture.get_snapshots("capture-fixture")[-1] is before
    assert before["char_estimate"] == 10
    assert before["layers"][0]["content"] == "voice"
    capture.capture_injected_messages(list(updated), [])
    assert len(before["layers"]) == 2
