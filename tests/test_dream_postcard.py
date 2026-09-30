"""Dream postcard generation and delivery contracts."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from datetime import date
from pathlib import Path

import pytest


def _turns(count: int = 5) -> list[dict]:
    return [{"role": "assistant", "content": f"turn {i}", "ts": 1_700_000_000} for i in range(count)]


@pytest.mark.asyncio
async def test_postcard_system_prompt_contains_invariant_hint():
    from core.dream import postcard

    chat = AsyncMock(return_value="letter")
    invariant = {"situation": "你退缩时", "response": "先停下来等你"}
    with (
        patch.object(postcard, "_load_schedule", return_value=[]),
        patch.object(postcard, "_archive_turns", return_value=postcard.ArchiveSnapshot(_turns(), True)),
        patch.object(postcard, "_save_schedule", return_value=True),
        patch.object(postcard, "_template_text", return_value="template"),
        patch("core.dream.invariants.select_for_postcard", return_value=invariant),
        patch("core.llm_client.chat", chat),
    ):
        await postcard.generate_postcard("u", "d", "soft_exit")

    system = chat.await_args.args[0][0]["content"]
    assert "你退缩时" in system
    assert "先停下来等你" in system


@pytest.mark.asyncio
async def test_postcard_system_prompt_omits_invariant_hint_when_none():
    from core.dream import postcard

    chat = AsyncMock(return_value="letter")
    with (
        patch.object(postcard, "_load_schedule", return_value=[]),
        patch.object(postcard, "_archive_turns", return_value=postcard.ArchiveSnapshot(_turns(), True)),
        patch.object(postcard, "_save_schedule", return_value=True),
        patch.object(postcard, "_template_text", return_value="template"),
        patch("core.dream.invariants.select_for_postcard", return_value=None),
        patch("core.llm_client.chat", chat),
    ):
        await postcard.generate_postcard("u", "d", "soft_exit")

    system = chat.await_args.args[0][0]["content"]
    assert "跨梦观察" not in system


@pytest.mark.asyncio
async def test_long_hard_exit_can_generate_postcard_when_complete():
    from core.dream import postcard

    with (
        patch.object(postcard, "_load_schedule", return_value=[]),
        patch.object(postcard, "_archive_turns", return_value=postcard.ArchiveSnapshot(_turns(), True)),
        patch.object(postcard, "_save_schedule", return_value=True),
        patch.object(postcard, "_template_text", return_value="template"),
        patch("core.dream.invariants.select_for_postcard", return_value=None),
        patch("core.llm_client.chat", new=AsyncMock(return_value="letter")) as chat,
    ):
        await postcard.generate_postcard(
            "u",
            "d",
            "hard_exit",
            completion="complete",
            exit_metadata={"dream_mode": "sandbox"},
        )
    chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_short_hard_exit_is_rejected_as_interrupted():
    from core.dream import postcard

    with (
        patch.object(postcard, "_load_schedule", return_value=[]),
        patch.object(postcard, "_archive_turns", return_value=postcard.ArchiveSnapshot(_turns(4), True)),
        patch("core.llm_client.chat", new=AsyncMock()) as chat,
    ):
        await postcard.generate_postcard(
            "u",
            "d",
            "hard_exit",
            completion="interrupted",
            exit_metadata={"dream_mode": "sandbox"},
        )
    chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_corrupt_archive_does_not_generate_from_valid_prefix(sandbox):
    from core.dream import postcard

    path = sandbox.dreams_archive_dir(char_id="dreamer") / "dream_corrupt.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join([*(json.dumps(turn) for turn in _turns()), "not-json"]) + "\n",
        encoding="utf-8",
    )
    with (
        patch.object(postcard, "_load_schedule", return_value=[]),
        patch("core.llm_client.chat", new=AsyncMock()) as chat,
    ):
        await postcard.generate_postcard(
            "u",
            "corrupt",
            "soft_exit",
            char_id="dreamer",
            completion="complete",
            exit_metadata={"dream_mode": "sandbox"},
        )
    chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_fewer_than_five_assistant_turns_does_not_generate():
    from core.dream import postcard

    with (
        patch.object(postcard, "_load_schedule", return_value=[]),
        patch.object(postcard, "_archive_turns", return_value=postcard.ArchiveSnapshot(_turns(4), True)),
        patch("core.llm_client.chat", new=AsyncMock()) as chat,
    ):
        await postcard.generate_postcard("u", "d", "soft_exit")
    chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_dream_id_does_not_generate():
    from core.dream import postcard

    with (
        patch.object(postcard, "_load_schedule", return_value=[{"dream_id": "d"}]),
        patch.object(postcard, "_archive_turns") as archive,
    ):
        await postcard.generate_postcard("u", "d", "soft_exit")
    archive.assert_not_called()


@pytest.mark.asyncio
async def test_qualified_postcard_persists_schedule_entry():
    from core.dream import postcard

    saved: list[list[dict]] = []
    with (
        patch.object(postcard, "_load_schedule", return_value=[]),
        patch.object(postcard, "_archive_turns", return_value=postcard.ArchiveSnapshot(_turns(), True)),
        patch.object(postcard, "_save_schedule", side_effect=lambda _cid, rows: saved.append(rows) or True),
        patch.object(postcard, "_template_text", return_value="template"),
        patch("core.dream.invariants.select_for_postcard", return_value=None),
        patch("core.llm_client.chat", new=AsyncMock(return_value="letter")),
    ):
        await postcard.generate_postcard("u", "d", "soft_exit")
    assert saved[0][0]["dream_id"] == "d"
    assert saved[0][0]["letter_text"] == "letter"
    assert saved[0][0]["sent"] is False


def test_due_date_retries_collision():
    from core.dream import postcard

    entries = [{"scheduled_date": "2026-01-02", "sent": False}]
    with patch.object(postcard.random, "randint", side_effect=[1, 2]):
        assert postcard._due_date(entries, date(2026, 1, 1)) == date(2026, 1, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("ok, expected_sent", [(False, False), (True, True)])
async def test_delivery_records_attempt_and_only_success_marks_sent(ok: bool, expected_sent: bool):
    from core.dream import postcard

    rows = [{"dream_id": "d", "scheduled_date": "2026-01-01", "sent": False, "attempts": 0, "letter_text": "x"}]
    with (
        patch.object(postcard, "_load_schedule", return_value=rows),
        patch.object(postcard, "_save_schedule", return_value=True),
        patch("core.mail.mail_sender.send_letter", new=AsyncMock(return_value=ok)),
    ):
        sent_count = await postcard.deliver_due_postcards(today=date(2026, 1, 2))
    assert rows[0]["attempts"] == 1
    assert rows[0]["sent"] is expected_sent
    assert sent_count == int(ok)


def test_postcard_isolation_contract_has_positive_control():
    source = Path("core/dream/postcard.py").read_text(encoding="utf-8")
    for forbidden in ("mid_term", "episodic", "user_identity", "mood_state", "hidden_state"):
        assert forbidden not in source
    assert "dreams_archive_dir" in source


@pytest.mark.asyncio
async def test_postcard_prompt_pins_the_character_as_the_writer():
    """归档 role 直接抛给模型时，模型会认领 user 视角，写成用户给角色的信。"""
    from core.dream import postcard

    chat = AsyncMock(return_value="letter")
    turns = [{"role": "assistant", "content": "我在这里", "ts": 1_700_000_000},
             *_turns(4),
             {"role": "user", "content": "别走", "ts": 1_700_000_000}]
    with (
        patch.object(postcard, "_load_schedule", return_value=[]),
        patch.object(postcard, "_archive_turns", return_value=postcard.ArchiveSnapshot(turns, True)),
        patch.object(postcard, "_save_schedule", return_value=True),
        patch.object(postcard, "_template_text", return_value="template"),
        patch.object(postcard, "_char_display_name", return_value="测试角色"),
        patch("core.dream.invariants.select_for_postcard", return_value=None),
        patch("core.llm_client.chat", chat),
    ):
        await postcard.generate_postcard("u", "d", "soft_exit")

    system = chat.await_args.args[0][0]["content"]
    assert "你就是测试角色" in system and "不要替对方写信" in system
    # 归档片段用具体说话人替代裸 role，避免模型把 [user] 当成自己。
    dialogue = chat.await_args.args[0][1]["content"]
    assert "[测试角色] 我在这里" in dialogue and "[对方] 别走" in dialogue
    assert "[assistant]" not in dialogue and "[user]" not in dialogue
