"""Brief 101: fresh-clone default-character dream regressions."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.parametrize("config_text", ["character:\n  default: ''\n", "not: [valid"])
def test_data_paths_unusable_default_falls_back_to_public_id(tmp_path, monkeypatch, config_text):
    from core import data_paths

    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_text, encoding="utf-8")
    monkeypatch.setattr(data_paths, "_CONFIG_PATH", config_path)

    assert data_paths._read_default_char_id() == "default"


def test_custom_configured_primary_keeps_fail_closed_semantics(sandbox, monkeypatch):
    from core.dream import dream_pipeline

    primary_id = "custom-primary"
    monkeypatch.setattr(dream_pipeline, "DEFAULT_CHAR_ID", primary_id)

    snapshot = AsyncMock(return_value={"created_at": 0.0, "user_id": "custom-default-u1"})
    with patch("core.dream.dream_context.build_snapshot", new=snapshot):
        accepted = asyncio.run(
            dream_pipeline.enter_dream("custom-default-u1", char_id=primary_id)
        )
        rejected = asyncio.run(
            dream_pipeline.enter_dream("custom-default-u2", char_id="other")
        )

    assert accepted.get("ok") is True
    assert rejected == {"ok": False, "error": "这个角色还不会做梦"}


def test_fresh_clone_default_character_enter_chat_exit_chain(tmp_path, sandbox, monkeypatch):
    from core import data_paths
    from core.character_loader import Character
    from core.dream import dream_pipeline
    from core.dream.dream_state import DreamStatus, read_state

    clone_root = tmp_path / "fresh-clone"
    characters_dir = clone_root / "characters"
    characters_dir.mkdir(parents=True)
    config_path = clone_root / "config.yaml"
    config_path.write_text("character:\n  default: default\n", encoding="utf-8")
    character_path = characters_dir / "default.json"
    character_path.write_text(
        json.dumps({"name": "Companion", "description": "test", "world_book": []}),
        encoding="utf-8",
    )

    monkeypatch.setattr(data_paths, "_CONFIG_PATH", config_path)
    primary_id = data_paths._read_default_char_id()
    assert primary_id == "default"
    assert character_path.is_file()
    monkeypatch.setattr(dream_pipeline, "DEFAULT_CHAR_ID", primary_id)

    pipeline = MagicMock()
    pipeline.character = Character(name="Companion", description="test")
    pipeline._active_character_id = primary_id

    def discard_background(coro):
        coro.close()
        return MagicMock()

    monkeypatch.setattr(asyncio, "create_task", discard_background)

    async def run_chain():
        with patch("core.pipeline_registry.get", return_value=pipeline), \
             patch(
                 "core.dream.dream_context.build_snapshot",
                 new=AsyncMock(return_value={"created_at": 0.0, "user_id": "fresh-u1"}),
             ), \
             patch("core.dream.dream_prompt.build_dream_prompt", return_value=[
                 {"role": "system", "content": "dream"},
                 {"role": "user", "content": "hello"},
             ]), \
             patch("core.llm_client.chat", new=AsyncMock(return_value="dream reply")):
            entered = await dream_pipeline.enter_dream("fresh-u1", char_id=primary_id)
            chatted = await dream_pipeline.dream_turn("fresh-u1", "hello")
            await dream_pipeline.force_exit_dream("fresh-u1")
        return entered, chatted, read_state("fresh-u1")

    entered, chatted, final_state = asyncio.run(run_chain())

    assert entered.get("ok") is True
    assert chatted.get("reply") == "dream reply"
    assert chatted.get("error") is None
    assert final_state.get("status") == DreamStatus.REALITY_AFTERGLOW.value
    assert final_state.get("char_id") == primary_id
