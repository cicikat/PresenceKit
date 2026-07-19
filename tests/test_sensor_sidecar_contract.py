import asyncio
import time

import pytest
from pydantic import ValidationError

from admin.routers import sensor
from core import prompt_builder
from core.memory import realtime_state


def _payload(*, title: str = "", edit_hint: str = "editing") -> sensor._RealtimeIngest:
    return sensor._RealtimeIngest(
        window_seconds=30,
        ts=time.time(),
        sensor_version="sidecar/test",
        input={
            "keystrokes": 20,
            "mouse_clicks": 1,
            "mouse_distance_px": 100,
            "idle_seconds": 3,
            "edit_hint": edit_hint,
        },
        focus={"app": "Code.exe", "title_hint": title, "switch_count": 0},
        screen={
            "package_name": "Code.exe",
            "app_label": "coding",
            "window_title": "",
            "visible_text": [],
            "clickable_text": [],
        },
    )


def test_realtime_edit_hint_rejects_arbitrary_text():
    with pytest.raises(ValidationError):
        _payload(edit_hint="the user typed a secret")


def test_sensitive_realtime_snapshot_is_not_stored(monkeypatch):
    stored = []
    monkeypatch.setattr(sensor.realtime_state, "update", stored.append)

    result = asyncio.run(
        sensor.receive_realtime_snapshot(_payload(title="BANK Login"), auth=None)
    )

    assert result == {"ok": False, "skipped": "sensitive_window"}
    assert stored == []


def test_realtime_snapshot_without_sample_has_explicit_no_data_marker(monkeypatch):
    monkeypatch.setattr(sensor.realtime_state, "get", lambda: None)

    result = asyncio.run(sensor.get_realtime_snapshot(auth=None))

    assert result == {"_no_data": True}


def test_realtime_snapshot_with_sample_keeps_complete_shape(monkeypatch):
    now = time.time()
    snapshot = _payload().model_dump()
    snapshot["received_at"] = now - 3
    monkeypatch.setattr(sensor.realtime_state, "get", lambda: snapshot)
    monkeypatch.setattr(sensor.realtime_state, "get_presence", lambda: "active")
    monkeypatch.setattr(
        sensor.realtime_state,
        "get_continuous_at_desk_seconds",
        lambda: 30,
    )

    result = asyncio.run(sensor.get_realtime_snapshot(auth=None))

    assert result["stale_seconds"] in {2, 3}
    assert result["window_seconds"] == 30
    assert result["input"]["keystrokes"] == 20
    assert result["focus"]["app"] == "Code.exe"


def test_realtime_awareness_uses_summary_without_window_title(monkeypatch):
    now = time.time()
    monkeypatch.setattr(
        realtime_state,
        "get",
        lambda: {
            "received_at": now,
            "input": {"idle_seconds": 3, "edit_hint": "editing"},
            "focus": {
                "app": "Code.exe",
                "title_hint": "private-chat-with-alice",
            },
            "screen": {"app_label": "coding", "window_title": "secret document"},
        },
    )

    result = prompt_builder._format_realtime_awareness(set(), now=now)

    assert result == "大致在写代码，正在认真输入"
    assert "private-chat" not in result
    assert "secret document" not in result


def test_realtime_awareness_ttl_and_tag_gate(monkeypatch):
    now = time.time()
    monkeypatch.setattr(
        realtime_state,
        "get",
        lambda: {
            "received_at": now - 240,
            "input": {"idle_seconds": 5, "edit_hint": "idle"},
            "focus": {"app": "Code.exe", "title_hint": ""},
        },
    )

    assert prompt_builder._format_realtime_awareness(set(), now=now) == ""
    assert (
        prompt_builder._format_realtime_awareness({"query.what_doing"}, now=now)
        == "在用 Code.exe"
    )
