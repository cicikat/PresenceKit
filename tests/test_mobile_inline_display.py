from unittest.mock import AsyncMock

import pytest


@pytest.mark.parametrize("text, expected", [
    ("<say><hl>hi</hl> <big>large</big> <sm>quiet</sm></say>",
     "<hl>hi</hl> <big>large</big> <sm>quiet</sm>"),
    ("<do>smiles</do>\n<say>hello</say>", "smiles\nhello"),
    ("plain", "plain"),
])
def test_display_copy_preserves_only_inline_tags(text, expected):
    from core.response_processor import inline_display_text, strip_render_tags
    assert inline_display_text(text) == expected
    assert strip_render_tags(expected) == strip_render_tags(text)


async def test_mobile_fanout_keeps_plain_canonical_and_durable_styles(sandbox, monkeypatch):
    from channels import registry
    from channels.mobile import MobileChannel
    from core.turn_sink import _fanout, TurnSource
    mobile = MobileChannel()
    desktop = type("Desktop", (), {"name": "desktop", "is_active": True, "send": AsyncMock()})()
    monkeypatch.setattr(registry, "_channels", {"mobile": mobile, "desktop": desktop})
    monkeypatch.setattr("channels.mobile.schedule_signal_publish", lambda item: None)
    targets, failures = await _fanout(assistant_text="<say><hl>Hello</hl></say>",
        uid="owner", fanout="all", behavior=None, ws_msg_id="styled-turn",
        source=TurnSource.USER_CHAT)
    assert not failures
    assert "mobile" in targets
    desktop.send.assert_awaited_once_with("Hello", "owner", behavior=None, msg_id="styled-turn")
    item = (await mobile.poll())[0]
    assert item["content"] == "Hello"
    assert item["display_text"] == "<hl>Hello</hl>"
    assert item["id"] == "styled-turn"
    assert await mobile.ack(item["seq"]) == 0
