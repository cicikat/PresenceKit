import pytest


@pytest.mark.asyncio
async def test_autogrow_uses_chat_route_and_diary_style_prompt(monkeypatch):
    from core.post_process.toy_autogrow import _judge_turn

    captured = {}

    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            message = type("Message", (), {"content": "夜色很安静，我还在想那句话。"})()
            return type("Response", (), {"choices": [type("Choice", (), {"message": message})()]})()

    class _Client:
        chat = type("Chat", (), {"completions": _Completions()})()

    def model_client(category):
        captured["category"] = category
        return type("Model", (), {"model": "chat-model", "client": _Client()})()
    monkeypatch.setattr("core.model_registry.get_model_client", model_client)

    note = await _judge_turn("我今天有点累", "我陪着你。", "角色")

    assert note
    assert captured["category"] == "chat"
    assert captured["temperature"] == 0.9
    assert "第一人称" in captured["messages"][0]["content"]
