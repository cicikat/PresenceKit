"""Capture auxiliary prompts at their real assembly boundaries."""
from unittest.mock import AsyncMock

import pytest

from core import prompt_language as locale
from tests.fixtures.public_assets import TEST_CHAR_ID


@pytest.fixture
def language(monkeypatch):
    config = {"prompt_language": "en"}
    monkeypatch.setattr("core.config_loader.get_config", lambda: config)
    monkeypatch.setattr("core.deployment_capabilities.is_remote_server", lambda: False)
    return config


def test_coplay_context_and_recall_keep_evidence(language, monkeypatch):
    from core.coplay import game_state, observer, session
    state = {"status": "active", "game_id": "steam:123", "game_name": "游戏原名"}
    game = {"game_name": "游戏原名", "progress_markers": ["第二章"], "last_summary": "历史原文"}
    monkeypatch.setattr(session, "read_state", lambda *a, **k: state)
    monkeypatch.setattr(game_state, "read_game_state", lambda *a, **k: game)
    monkeypatch.setattr(game_state, "match_game_by_text", lambda *a, **k: game)
    monkeypatch.setattr(observer, "peek_moments", lambda *a: [observer.GameMoment("death", "证据原文")])
    result = game_state.build_coplay_context_text("owner", char_id=TEST_CHAR_ID)
    assert "Never predict" in result
    assert all(text in result for text in ("游戏原名", "第二章", "证据原文"))
    assert "<coplay_recall>" in game_state.build_game_log_recall_text("owner", "游戏原名")
    assert "历史原文" in game_state.build_game_log_recall_text("owner", "游戏原名")
    language["prompt_language"] = "zh"
    assert "<陪玩状态>" in game_state.build_coplay_context_text("owner", char_id=TEST_CHAR_ID)
    assert "<陪玩回忆>" in game_state.build_game_log_recall_text("owner", "游戏原名")


def test_result_projection_failure_keeps_evidence(language):
    from core.context_continuity import _readable_content
    assert _readable_content("fixture", '{"ok":false,"error":"原始错误"}') == "Failed: 原始错误"
    assert _readable_content("fixture", '{"ok":') == "The result is incomplete; call the tool to read it again."
    language["prompt_language"] = "zh"
    assert _readable_content("fixture", '{"ok":false,"error":"原始错误"}') == "失败：原始错误"


@pytest.mark.asyncio
async def test_coplay_summary_and_commentary_capture(language, monkeypatch):
    from core import llm_client
    from core.coplay import session_close, commentator, observer
    chat = AsyncMock(return_value="摘要原文")
    monkeypatch.setattr(llm_client, "chat", chat)
    assert await session_close._summarize_session("游戏原名", ["证据原文"], char_id=TEST_CHAR_ID) == "摘要原文"
    content = chat.call_args.args[0][0]["content"]
    assert "Do not invent" in content and "证据原文" in content and "游戏原名" in content
    captured = {}
    async def execute(**kwargs):
        captured["prompt"] = kwargs["prompt_factory"]()
    monkeypatch.setattr("core.scheduler.execution.execute_prompt", execute)
    await commentator._make_execute(observer.GameMoment("death", "证据原文"), TEST_CHAR_ID)(dry_run=True)
    assert "death was just observed" in captured["prompt"] and "证据原文" in captured["prompt"]
    for value in (*observer._DIFF_KIND_SUMMARY.values(), *observer._OCR_KIND_SUMMARY.values()):
        assert locale.text(value) != value


@pytest.mark.asyncio
async def test_visual_adapter_captures_privacy_schema_and_raw_hint(language, monkeypatch):
    from core.perception import vlm_client
    captured = {}
    class Response:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def json(self):
            return {"choices": [{"message": {"content": '{"scene":"desk","activity":"reading","confidence":0.8,"sensitive":false,"caption":"可见窗口"}'}}]}
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def post(self, url, **kwargs):
            captured.update(kwargs["json"])
            return Response()
    monkeypatch.setattr("aiohttp.ClientSession", Client)
    monkeypatch.setattr("core.api_call_log.append", lambda **k: None)
    cfg = {"base_url": "http://localhost/v1", "model": "fixture"}
    result, reason, _ = await vlm_client._describe_once(cfg, b"fixture", "背景原文")
    assert result.caption == "可见窗口" and reason is None
    system = captured["messages"][0]["content"]
    assert "sensitive=true" in system and "do not transcribe" in system
    assert "desk|away|bed|meal|outdoor|other" in system and "Chinese description" in system
    assert "背景原文" in captured["messages"][1]["content"][0]["text"]
    language["prompt_language"] = "zh"
    await vlm_client._describe_once(cfg, b"fixture", "背景原文")
    assert captured["messages"][0]["content"] == vlm_client._SYSTEM_PROMPT
