"""Dream pseudo-stream remains isolated; Reality uses canonical local responses."""
from tests.fixtures.public_assets import TEST_CHAR_ID
from unittest.mock import patch
import pytest

@pytest.mark.asyncio
async def test_dream_chat_wires_pseudo_stream_and_msg_id(sandbox, monkeypatch):
    from admin.routers import dream as dream_router

    async def fake_dream_turn(uid, message):
        return {
            "reply": "梦里的风很轻。",
            "exit_accepted": False,
            "force_exited": False,
            "segments": [],
            "segmented_content": "",
        }

    monkeypatch.setattr("core.dream.dream_pipeline.dream_turn", fake_dream_turn)

    class FakePipeline:
        _active_character_id = TEST_CHAR_ID

    monkeypatch.setattr("core.pipeline_registry.get", lambda: FakePipeline())

    calls = []

    async def fake_pseudo_stream_push(text, *, msg_id, char_id="", round_id="", profile="default"):
        calls.append((text, msg_id, char_id, profile))

    monkeypatch.setattr("channels.ui_push.pseudo_stream_push", fake_pseudo_stream_push)

    with patch("admin.routers.dream._owner_uid", return_value="owner"):
        result = await dream_router.dream_chat({"message": "你好"}, _auth=None)

    assert result["reply"] == "梦里的风很轻。"
    assert result["msg_id"]
    assert calls == [("梦里的风很轻。", result["msg_id"], TEST_CHAR_ID, "dream")]


@pytest.mark.asyncio
async def test_dream_chat_no_msg_id_when_reply_empty(sandbox, monkeypatch):
    from admin.routers import dream as dream_router

    async def fake_dream_turn(uid, message):
        return {"reply": "", "exit_accepted": False, "force_exited": False}

    monkeypatch.setattr("core.dream.dream_pipeline.dream_turn", fake_dream_turn)

    calls = []
    monkeypatch.setattr(
        "channels.ui_push.pseudo_stream_push",
        lambda *a, **kw: calls.append((a, kw)),
    )

    with patch("admin.routers.dream._owner_uid", return_value="owner"):
        result = await dream_router.dream_chat({"message": "你好"}, _auth=None)

    assert "msg_id" not in result
    assert calls == []
