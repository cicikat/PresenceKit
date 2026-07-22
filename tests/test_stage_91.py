"""Brief 91: 群聊哑火修复 — 静默轮闸门语义反转回归网。

根因：runner.py 的 may_be_silent 用 `or` 短路了低信息闸门，导致普通陈述句
（裸分 ~0.25 < SILENCE_THRESHOLD 0.35）永远静默，min_responders 保底被绕过。
修复：闸门改为 `allow_silent_rounds and is_low_information(owner_content)`。
"""
from __future__ import annotations

import uuid

import pytest


def _settings(**overrides):
    from core.stage.models import StageSettings

    values = {
        "min_responders": 1,
        "max_responders": 2,
        "respond_threshold": 0.5,
        "talkativeness": {"yexuan": 0.5, "yexuanJ-5412": 0.5},
        "topic_seed_prob": 0.0,
    }
    values.update(overrides)
    return StageSettings(**values)


def _stage(roster=("yexuan", "yexuanJ-5412"), **setting_overrides):
    from core.stage.store import create_stage

    group_id = f"cc91-{uuid.uuid4().hex[:8]}"
    return create_stage(group_id, "owner", list(roster), settings=_settings(**setting_overrides))


async def _generate(stage, speaker_id, transcript, turn_id, triggered_by):
    return f"{speaker_id}-reply"


@pytest.mark.asyncio
async def test_plain_statement_gets_min_responders_reply(sandbox):
    """核心回归：不点名、无问号的普通陈述句裸分低于 SILENCE_THRESHOLD，
    但不是 backchannel，因此不该静默——min_responders 保底必须生效。
    此前因 may_be_silent 的 `or` 短路，这类消息会永远哑火（0 回复）。"""
    from core.stage.runner import run_owner_turn

    stage = _stage()
    result = await run_owner_turn(
        stage.group_id,
        "我今天去了趟医院",
        generate_reply=_generate,
        turn_id="t-plain",
    )

    assert len(result.replies) >= stage.settings.min_responders


@pytest.mark.asyncio
async def test_backchannel_silent_when_allowed(sandbox):
    """backchannel（低信息量）+ allow_silent_rounds=True → 允许整轮静默。"""
    from core.stage.runner import run_owner_turn

    stage = _stage(min_responders=0, allow_silent_rounds=True)
    result = await run_owner_turn(
        stage.group_id,
        "嗯",
        generate_reply=_generate,
        turn_id="t-backchannel-silent",
    )

    assert len(result.replies) == 0


@pytest.mark.asyncio
async def test_backchannel_honors_min_responders_even_when_silent_rounds_allowed(sandbox):
    from core.stage.runner import run_owner_turn

    stage = _stage(min_responders=1, allow_silent_rounds=True)
    result = await run_owner_turn(
        stage.group_id, "在吗", generate_reply=_generate, turn_id="t-backchannel-minimum",
    )

    assert len(result.replies) >= 1


@pytest.mark.asyncio
async def test_backchannel_still_replies_when_silent_rounds_disallowed(sandbox):
    """backchannel + allow_silent_rounds=False → 仍走 min_responders 保底。"""
    from core.stage.runner import run_owner_turn

    stage = _stage(allow_silent_rounds=False)
    result = await run_owner_turn(
        stage.group_id,
        "嗯",
        generate_reply=_generate,
        turn_id="t-backchannel-forced",
    )

    assert len(result.replies) >= stage.settings.min_responders


@pytest.mark.asyncio
async def test_vocative_never_silent(sandbox):
    """点名角色 → 永不静默（既有行为，不受本次改动影响）。"""
    from core.character_name_provider import get_char_name
    from core.stage.runner import run_owner_turn

    stage = _stage()
    name = get_char_name("yexuan")
    result = await run_owner_turn(
        stage.group_id,
        f"@{name} 在吗",
        generate_reply=_generate,
        turn_id="t-vocative",
    )

    assert len(result.replies) >= 1


@pytest.mark.asyncio
async def test_silent_round_trace_records_reason(sandbox):
    """静默轮 arbiter trace 应带 silent_reason=low_information 字段，供面板查看。"""
    import json

    from core.stage.runner import run_owner_turn

    stage = _stage(min_responders=0, allow_silent_rounds=True)
    await run_owner_turn(
        stage.group_id,
        "哦哦",
        generate_reply=_generate,
        turn_id="t-trace",
    )

    lines = sandbox.stage_arbiter_trace(group_id=stage.group_id).read_text(encoding="utf-8").strip().splitlines()
    last = json.loads(lines[-1])
    assert last.get("silent_round") is True
    assert last.get("silent_reason") == "low_information"
