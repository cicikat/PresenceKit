from types import SimpleNamespace

from core import prompt_builder


def _character():
    return SimpleNamespace(
        name="Companion",
        system_prompt="system",
        description="",
        personality="",
        scenario="",
        mes_example="",
    )


def _fact_boundary(messages):
    return next(message["content"] for message in messages if message.get("_layer") == "1.5_fact_boundary")


def test_fact_boundary_anchors_desktop_avatar_identity(monkeypatch):
    monkeypatch.setattr(prompt_builder, "_format_realtime_awareness", lambda _tags: "正在使用电脑")

    messages, _ = prompt_builder.build(_character(), "owner", "你好", [], {}, {}, [], tags=set())

    boundary = _fact_boundary(messages)
    assert "桌宠形象是你自己在屏幕上的存在" in boundary
    assert "不是她的角色" in boundary


def test_empty_realtime_awareness_forbids_invented_screen_scene(monkeypatch):
    monkeypatch.setattr(prompt_builder, "_format_realtime_awareness", lambda _tags: "")

    messages, _ = prompt_builder.build(_character(), "owner", "回来了", [], {}, {}, [], tags=set())

    boundary = _fact_boundary(messages)
    assert "没有真实屏幕感知时" in boundary
    assert "不得虚构屏幕画面" in boundary
