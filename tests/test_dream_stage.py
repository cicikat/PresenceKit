"""Brief 100 · 群聊梦境（Dream Stage）后端 v1."""
from __future__ import annotations

import pytest

ROSTER = ["yexuan", "yexuanJ-5412"]
GROUP_ID = "dream-g1"


def _create_reality_group(group_id: str = GROUP_ID, roster=None, **settings_kwargs):
    from core.stage.models import StageSettings
    from core.stage.store import create_stage

    roster = roster or ROSTER
    settings = StageSettings(**settings_kwargs) if settings_kwargs else None
    return create_stage(group_id, "owner", roster, domain="reality", settings=settings)


async def _enter_group_dream(group_id: str = GROUP_ID, entry_reason: str = ""):
    from admin.routers.group_dream import group_dream_enter

    return await group_dream_enter(group_id, {"entry_reason": entry_reason})


# ── §1/§3 enter: freeze contract + conflict checks ────────────────────────────


@pytest.mark.asyncio
async def test_enter_freezes_per_char_snapshots_card_only(sandbox):
    _create_reality_group()
    result = await _enter_group_dream(entry_reason="今晚一起做个梦")
    assert result["ok"] is True
    assert set(result["roster"]) == set(ROSTER)

    from core.stage.dream_state import read_state

    state = read_state(GROUP_ID)
    assert state["status"] == "DREAM_ACTIVE"
    assert state["char_tension"] == {cid: 0.0 for cid in ROSTER}
    assert state["body_state"] == {}

    snaps = state["per_char_snapshots"]
    assert set(snaps.keys()) == set(ROSTER)
    for cid in ROSTER:
        snap = snaps[cid]
        assert snap["memory_access"] == "card_only"
        assert snap["recent_reality_context"] == ""
        assert snap["recent_reality_gist"] == ""
        assert snap["episodic_summary"] == ""
        assert snap["mid_term_context"] == ""
        assert snap["profile_impression"] == ""
        assert "user_hidden_state_snapshot" not in snap


@pytest.mark.asyncio
async def test_enter_conflict_group_already_active(sandbox):
    from fastapi import HTTPException

    from admin.routers.group_dream import group_dream_enter

    _create_reality_group()
    await _enter_group_dream()
    with pytest.raises(HTTPException) as exc:
        await group_dream_enter(GROUP_ID, {})
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_enter_conflict_solo_dream_active(sandbox):
    from fastapi import HTTPException

    from admin.routers.group_dream import group_dream_enter
    from core.dream.dream_state import DreamStatus, write_state as write_solo_state

    _create_reality_group()
    write_solo_state("owner", {"status": DreamStatus.DREAM_ACTIVE.value})
    with pytest.raises(HTTPException) as exc:
        await group_dream_enter(GROUP_ID, {})
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_enter_conflict_reality_turn_in_progress(sandbox):
    from fastapi import HTTPException

    from admin.routers.group_dream import group_dream_enter
    from core.conversation_gate import conversation_lock

    _create_reality_group()
    async with conversation_lock("owner"):
        with pytest.raises(HTTPException) as exc:
            await group_dream_enter(GROUP_ID, {})
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_enter_missing_group_404(sandbox):
    from fastapi import HTTPException

    from admin.routers.group_dream import group_dream_enter

    with pytest.raises(HTTPException) as exc:
        await group_dream_enter("no-such-group", {})
    assert exc.value.status_code == 404


# ── §3 reverse mutual exclusion: solo /dream/enter rejected during group dream ─


@pytest.mark.asyncio
async def test_solo_enter_rejected_while_group_dream_active(sandbox):
    from core.data_paths import DEFAULT_CHAR_ID
    from core.dream.dream_pipeline import enter_dream

    _create_reality_group()
    await _enter_group_dream()

    result = await enter_dream("owner", char_id=DEFAULT_CHAR_ID)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_reality_guard_blocks_owner_while_group_dream_active(sandbox):
    from core.dream.dream_state import DreamGuardStatus, get_reality_guard_status

    _create_reality_group()
    assert get_reality_guard_status("owner") == DreamGuardStatus.ALLOW
    await _enter_group_dream()
    assert get_reality_guard_status("owner") == DreamGuardStatus.BLOCK_ACTIVE


# ── hard_exit: Invariant D — unconditional, always succeeds ───────────────────


@pytest.mark.asyncio
async def test_hard_exit_always_succeeds_and_archives(sandbox):
    from core.sandbox import get_paths
    from core.stage.dream_state import read_state
    from core.stage.dream_store import append_dream_transcript, load_dream_transcript
    from core.stage.models import TranscriptEntry

    _create_reality_group()
    await _enter_group_dream()
    append_dream_transcript(GROUP_ID, TranscriptEntry("owner", "hi", 1.0, "t1", "user"))

    from admin.routers.group_dream import group_dream_exit

    result = await group_dream_exit(GROUP_ID)
    assert result == {"ok": True, "exited": True}

    state = read_state(GROUP_ID)
    assert state["status"] == "REALITY_CHAT"
    assert "per_char_snapshots" not in state
    assert "char_tension" not in state
    assert "body_state" not in state

    assert load_dream_transcript(GROUP_ID) == []
    archive_dir = get_paths().dream_group_archive_dir(group_id=GROUP_ID)
    assert list(archive_dir.glob("*.jsonl"))


@pytest.mark.asyncio
async def test_hard_exit_idempotent_when_not_active(sandbox):
    from admin.routers.group_dream import group_dream_exit

    _create_reality_group()
    result = await group_dream_exit(GROUP_ID)
    assert result == {"ok": True, "exited": True}


@pytest.mark.asyncio
async def test_hard_exit_succeeds_mid_round(sandbox, monkeypatch):
    """exit must succeed even while a round is conceptually "in progress" —
    here modeled as DREAM_CLOSING, the only other ACTIVE-adjacent status v1 has."""
    from core.stage.dream_state import DreamStatus, read_state, write_state

    _create_reality_group()
    await _enter_group_dream()
    state = read_state(GROUP_ID)
    state["status"] = DreamStatus.DREAM_CLOSING.value
    write_state(GROUP_ID, state)

    from admin.routers.group_dream import group_dream_exit

    result = await group_dream_exit(GROUP_ID)
    assert result == {"ok": True, "exited": True}
    assert read_state(GROUP_ID)["status"] == "REALITY_CHAT"


# ── §3 send: 409 when no active dream, accepted shape otherwise ───────────────


@pytest.mark.asyncio
async def test_send_rejected_when_not_dreaming(sandbox):
    from fastapi import HTTPException

    from admin.routers.group_dream import group_dream_send

    _create_reality_group()
    with pytest.raises(HTTPException) as exc:
        await group_dream_send(GROUP_ID, {"content": "你好"})
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_send_accepted_shape(sandbox, monkeypatch):
    from admin.routers.group_dream import group_dream_send

    _create_reality_group()
    await _enter_group_dream()

    # run_dream_stage_turn is patched out entirely — this endpoint only needs
    # to hand back {round_id, status} synchronously and fire the round as a
    # background task, which is exercised separately below.
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr("core.stage.dream_runtime.run_dream_stage_turn", _noop)

    result = await group_dream_send(GROUP_ID, {"content": "你好"})
    assert result["status"] == "accepted"
    assert isinstance(result["round_id"], str) and result["round_id"]


# ── §3 state / settings shape ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_state_projection_fields(sandbox):
    from admin.routers.group_dream import group_dream_state_get

    _create_reality_group()
    idle = await group_dream_state_get(GROUP_ID)
    assert idle["dream_state"] == "idle"
    assert idle["roster"] == ROSTER
    assert idle["char_tension"] == {}
    assert idle["blocks_chat"] is False

    await _enter_group_dream()
    active = await group_dream_state_get(GROUP_ID)
    assert active["dream_state"] == "dreaming"
    assert active["since"] is not None
    assert active["blocks_chat"] is True
    assert set(active["char_tension"].keys()) == set(ROSTER)


# ── transcript polling (mobile: 无 WS，靠轮询拿逐条发言) ────────────────────────


@pytest.mark.asyncio
async def test_transcript_returns_new_entries_since_cursor(sandbox):
    from core.stage.dream_store import append_dream_transcript
    from core.stage.models import TranscriptEntry

    from admin.routers.group_dream import group_dream_transcript_get

    _create_reality_group()
    await _enter_group_dream()

    empty = await group_dream_transcript_get(GROUP_ID, after=0)
    assert empty["entries"] == []
    assert empty["cursor"] == 0
    assert empty["status"] == "DREAM_ACTIVE"

    append_dream_transcript(GROUP_ID, TranscriptEntry("owner", "你好", 1.0, "t1", "user"))
    append_dream_transcript(GROUP_ID, TranscriptEntry(ROSTER[0], "我在", 2.0, "t1", ROSTER[0]))

    first_page = await group_dream_transcript_get(GROUP_ID, after=0)
    assert first_page["cursor"] == 2
    assert [e["content"] for e in first_page["entries"]] == ["你好", "我在"]
    assert [e["is_owner"] for e in first_page["entries"]] == [True, False]
    assert [e["index"] for e in first_page["entries"]] == [0, 1]
    assert all(e["round_id"] == "t1" for e in first_page["entries"])

    second_page = await group_dream_transcript_get(GROUP_ID, after=first_page["cursor"])
    assert second_page["entries"] == []
    assert second_page["cursor"] == 2

    append_dream_transcript(GROUP_ID, TranscriptEntry(ROSTER[1], "也在", 3.0, "t1", ROSTER[1]))
    third_page = await group_dream_transcript_get(GROUP_ID, after=first_page["cursor"])
    assert [e["content"] for e in third_page["entries"]] == ["也在"]
    assert third_page["entries"][0]["index"] == 2


@pytest.mark.asyncio
async def test_transcript_requires_reality_group(sandbox):
    from fastapi import HTTPException

    from admin.routers.group_dream import group_dream_transcript_get

    with pytest.raises(HTTPException) as exc:
        await group_dream_transcript_get("no-such-group", after=0)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_settings_default_and_patch_roundtrip(sandbox):
    from admin.routers.group_dream import group_dream_settings_get, group_dream_settings_patch

    _create_reality_group()
    defaults = await group_dream_settings_get(GROUP_ID)
    assert defaults["world_layer"] == "reality_derived"
    assert defaults["jailbreak_presets"] == ["default"]
    assert defaults["per_char"] == {}

    patched = await group_dream_settings_patch(GROUP_ID, {
        "boundary_level": "numbers_visible",
        "per_char": {"yexuan": {"jailbreak_presets": ["default"]}},
    })
    assert patched["ok"] is True
    assert patched["settings"]["boundary_level"] == "numbers_visible"
    assert patched["settings"]["per_char"]["yexuan"]["jailbreak_presets"] == ["default"]


@pytest.mark.asyncio
async def test_settings_patch_rejects_invalid_enum(sandbox):
    from fastapi import HTTPException

    from admin.routers.group_dream import group_dream_settings_patch

    _create_reality_group()
    with pytest.raises(HTTPException) as exc:
        await group_dream_settings_patch(GROUP_ID, {"boundary_level": "not_a_real_level"})
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_settings_patch_rejects_per_char_outside_roster(sandbox):
    from fastapi import HTTPException

    from admin.routers.group_dream import group_dream_settings_patch

    _create_reality_group()
    with pytest.raises(HTTPException) as exc:
        await group_dream_settings_patch(GROUP_ID, {"per_char": {"not-in-roster": {"jailbreak_presets": ["default"]}}})
    assert exc.value.status_code == 422


# ── §1 D0 fallback chain (per_char → group → default.md) ─────────────────────


def test_resolve_jailbreak_presets_fallback_chain():
    from core.stage.dream_settings import resolve_jailbreak_presets

    # per_char hit
    settings = {
        "jailbreak_presets": ["group_default"],
        "per_char": {"yexuan": {"jailbreak_presets": ["per_char_preset"]}},
    }
    assert resolve_jailbreak_presets(settings, "yexuan") == ["per_char_preset"]

    # per_char miss → group-level
    assert resolve_jailbreak_presets(settings, "hongcha") == ["group_default"]

    # both missing → hardcoded ["default"] (the final default.md link is
    # resolved inside core.dream.dream_pipeline._load_presets_text())
    empty_settings = {"jailbreak_presets": [], "per_char": {}}
    assert resolve_jailbreak_presets(empty_settings, "yexuan") == ["default"]


# ── §2/§4 prompt-layer hard guards for dream_domain="group" ───────────────────


def test_build_dream_prompt_group_domain_disables_d45_ds_dm():
    from core.dream.dream_prompt import build_dream_prompt

    class _FakeChar:
        name = "叶瑄"
        description = "一位老师"
        gender = "male"

    messages = build_dream_prompt(
        character=_FakeChar(),
        user_id="owner",
        user_message="说你要说的话",
        context_snapshot={},
        dream_history=[],
        local_state={"scene_state": None, "symbolic_anchors": [], "body_state": {}},
        dream_mode="sandbox",
        dream_domain="group",
        dg_layer_text="在场角色：叶瑄、风谕J",
        shared_transcript_block="你：你好\n我：我在",
    )
    system_content = messages[0]["content"]
    assert "DG·梦内在场感" in system_content
    assert "在场角色" in system_content
    assert "D9·梦内共享对话" in system_content
    assert "你好" in system_content
    # D4.5/DS/DM must never appear regardless of what a caller mistakenly passes
    assert "D4.5" not in system_content
    assert "DS·" not in system_content
    assert "DM·" not in system_content
    # Only one user message (the generation instruction) — group domain never
    # converts dream_history into per-turn chat messages.
    assert len([m for m in messages if m["role"] == "user"]) == 1


def test_build_dream_prompt_group_domain_hard_disables_scenario_even_if_forced():
    """Scenario-style guard: even a caller bug that leaks dream_mode="scenario"
    into a group call must not inject DS content (Brief 100 §0)."""
    from core.dream.dream_prompt import build_dream_prompt

    class _FakeChar:
        name = "叶瑄"
        description = ""
        gender = "male"

    messages = build_dream_prompt(
        character=_FakeChar(),
        user_id="owner",
        user_message="说话",
        context_snapshot={},
        dream_history=[],
        local_state={},
        dream_mode="scenario",
        scenario_core={"script_id": "x", "current_stage_id": "y"},
        dream_domain="group",
    )
    assert "DS·" not in messages[0]["content"]


# ── §4 isolation contract: end-to-end zero reflow ─────────────────────────────


@pytest.mark.asyncio
async def test_group_dream_round_touches_nothing_reality(sandbox, monkeypatch):
    from core.sandbox import get_paths
    from core.stage.dream_runtime import run_dream_stage_turn
    from core.stage.store import load_transcript

    _create_reality_group()
    await _enter_group_dream()

    async def fake_chat(messages, *args, **kwargs):
        return "梦里的一句话"

    monkeypatch.setattr("core.llm_client.chat", fake_chat)

    result = await run_dream_stage_turn(GROUP_ID, "你好", fanout=False)
    assert len(result.replies) > 0

    # reality group's own transcript.json must stay untouched
    assert load_transcript(GROUP_ID) == []

    # no reality memory files were created for any roster member
    for char_id in ROSTER:
        assert not get_paths().user_memory_root("owner", char_id=char_id).exists()
    assert not get_paths().char_relation(char_a=ROSTER[0], char_b=ROSTER[1]).exists()

    # solo dream_state for the owner is untouched (dream_exit proposer isolation)
    from core.dream.dream_state import read_state as read_solo_state

    solo_state = read_solo_state("owner")
    assert solo_state["status"] == "REALITY_CHAT"
    assert "last_dream_id" not in solo_state


@pytest.mark.asyncio
async def test_group_dream_round_captures_prompt_snapshot(sandbox, monkeypatch):
    """Admin panel observer (Brief 100 follow-up): group dream turns must land
    in the same owner_uid-keyed ring buffer as solo dream, tagged with an
    `origin` the frontend can filter on — mirrors how the reality prompt-layers
    viewer already tells group turns apart from 1v1 ones for the same uid."""
    import core.observe.dream_capture as dream_capture
    from core.stage.dream_runtime import run_dream_stage_turn

    monkeypatch.setattr(dream_capture, "_rings", {})

    _create_reality_group()
    await _enter_group_dream()

    async def fake_chat(messages, *args, **kwargs):
        return "梦里的一句话"

    monkeypatch.setattr("core.llm_client.chat", fake_chat)

    result = await run_dream_stage_turn(GROUP_ID, "你好", fanout=False)
    assert len(result.replies) > 0

    snaps = dream_capture.get_dream_snapshots("owner")
    assert snaps, "group dream turn should have produced at least one capture"
    group_snaps = [s for s in snaps if s.get("origin", {}).get("group_id") == GROUP_ID]
    assert group_snaps, "captured snapshot must carry origin.group_id for the admin panel filter"
    snap = group_snaps[0]
    assert snap["origin"]["origin"] == "stage"
    assert snap["origin"]["char_id"] in ROSTER
    assert snap["llm_output"] == "梦里的一句话"
    assert snap["layers"]


@pytest.mark.asyncio
async def test_positive_sample_reality_projection_enqueues_summarize(sandbox, monkeypatch):
    """Anti-false-green: the negative isolation test above only means something
    if the equivalent *reality* Stage step really does enqueue a summarize job —
    prove that path is alive, not a stub."""
    from core.stage.models import TranscriptEntry
    from core.stage.projection import enqueue_reality_projection
    from core.stage.store import append_transcript, load_stage

    _create_reality_group()
    stage = load_stage(GROUP_ID)
    append_transcript(stage, TranscriptEntry("owner", "你好", 1.0, "t1", "user"))
    append_transcript(stage, TranscriptEntry(ROSTER[0], "我在", 2.0, "t1", ROSTER[0]))

    enqueued: list[str] = []
    monkeypatch.setattr(
        "core.post_process.slow_queue.enqueue",
        lambda task_type, payload: enqueued.append(task_type),
    )

    count = await enqueue_reality_projection(GROUP_ID)
    assert count == len(ROSTER)
    assert enqueued.count("summarize_to_midterm") == len(ROSTER)


@pytest.mark.asyncio
async def test_round_llm_call_budget_hard_cap(sandbox, monkeypatch):
    """一轮调用数上限 = max_responders + max_ai_chain_depth（Phase R/T 强制关闭）."""
    from core.stage.dream_runtime import run_dream_stage_turn
    from core.stage.store import load_stage

    _create_reality_group(
        roster=["yexuan", "yexuanJ-5412", "hongcha"],
        min_responders=1, max_responders=3, max_ai_chain_depth=2,
        talkativeness={"yexuan": 1.0, "yexuanJ-5412": 1.0, "hongcha": 1.0},
    )
    await _enter_group_dream()

    call_count = {"n": 0}

    async def fake_chat(messages, *args, **kwargs):
        call_count["n"] += 1
        return f"回复{call_count['n']}"

    monkeypatch.setattr("core.llm_client.chat", fake_chat)

    result = await run_dream_stage_turn(GROUP_ID, "大家好", fanout=False)

    stage = load_stage(GROUP_ID)
    budget = stage.settings.max_responders + stage.settings.max_ai_chain_depth
    assert call_count["n"] <= budget
    assert call_count["n"] == len(result.replies)

    # Phase R/T are force-off — no entry can carry a "topic_seed" trigger, and
    # no more replies exist than the roster has members.
    assert all(entry.triggered_by != "topic_seed" for entry in result.replies)


@pytest.mark.asyncio
async def test_round_updates_shared_body_and_per_char_tension(sandbox, monkeypatch):
    from core.stage.dream_runtime import run_dream_stage_turn
    from core.stage.dream_state import read_state

    _create_reality_group()
    await _enter_group_dream()

    async def fake_chat(messages, *args, **kwargs):
        return "心跳"  # hits a body_tracker keyword → nonzero delta

    monkeypatch.setattr("core.llm_client.chat", fake_chat)

    result = await run_dream_stage_turn(GROUP_ID, "靠近", fanout=False)
    assert result.replies

    state = read_state(GROUP_ID)
    # body_state is shared: exactly one dict, not per-char
    assert isinstance(state["body_state"], dict)
    assert state["body_state"].get("heat", 0.0) > 0.0
    # char_tension is per-char, one entry per roster member
    assert set(state["char_tension"].keys()) == set(ROSTER)
