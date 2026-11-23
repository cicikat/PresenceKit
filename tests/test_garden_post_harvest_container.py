from tests.fixtures.public_assets import TEST_CHAR_ID
"""Brief 83 · G4：花园 dry/gift/ask 采后容器最小方案。"""

import json

import pytest


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_harvest(char_id: str, *, flower_id: str, now: float):
    from core.garden import manager

    manager._save(
        manager._storage_path(char_id),
        {
            "harvest": [{
                "flower_id": flower_id,
                "bloomed_at": now - 4 * 86400,
                "expires_at": now + 10 * 86400,
                "status": "fresh",
                "gifted_note": None,
                "handle_notified": False,
            }],
            "vase": [],
            "history": [],
        },
        char_id=char_id,
    )


@pytest.mark.parametrize("ask_roll,self_roll,expected_kind", [
    (0.10, 0.0, "ask"),   # HANDLE_ASK_THRESHOLD 内
    (0.40, 0.10, "dry"),  # self 分支内，第二次 roll < 0.5 → dry
    (0.70, 0.0, "gift"),  # HANDLE_GIFT_THRESHOLD 内
])
def test_daily_check_dry_gift_ask_land_in_history_and_leave_harvest(
    sandbox, monkeypatch, ask_roll, self_roll, expected_kind,
):
    from core.garden import manager

    now = 2_000_000.0
    monkeypatch.setattr(manager.time, "time", lambda: now)
    _seed_harvest(TEST_CHAR_ID, flower_id="daisy", now=now)

    rolls = iter([ask_roll, self_roll])
    monkeypatch.setattr(manager.random, "random", lambda: next(rolls))

    events = manager.daily_check(char_id=TEST_CHAR_ID)
    assert [e["type"] for e in events] == ["harvest_handle"]
    assert events[0]["handle_action"] == expected_kind

    storage = _read_json(sandbox.garden(char_id=TEST_CHAR_ID) / "storage.json")
    assert storage["harvest"] == [], "dry/gift/ask 处理完成后必须离开 harvest"

    assert len(storage["history"]) == 1
    entry = storage["history"][0]
    assert entry["kind"] == expected_kind
    assert entry["flower"] == "daisy"
    assert entry["mood_source"] == manager._mood_source_for_flower("daisy")
    assert entry["ts"] == now
    assert entry["note"]


def test_daily_check_vase_branch_unchanged_no_history_entry(sandbox, monkeypatch):
    """vase 分支不在本工单范围内：仍进 vase 容器，不进 history，不受影响。"""
    from core.garden import manager

    now = 2_000_000.0
    monkeypatch.setattr(manager.time, "time", lambda: now)
    _seed_harvest(TEST_CHAR_ID, flower_id="rose", now=now)

    rolls = iter([0.40, 0.90])  # self 分支，第二次 roll >= 0.5 → vase
    monkeypatch.setattr(manager.random, "random", lambda: next(rolls))

    events = manager.daily_check(char_id=TEST_CHAR_ID)
    assert events[0]["handle_action"] == "vase"

    storage = _read_json(sandbox.garden(char_id=TEST_CHAR_ID) / "storage.json")
    assert storage["harvest"] == []
    assert storage["history"] == []
    assert len(storage["vase"]) == 1


def test_daily_check_silent_branch_stays_in_harvest(sandbox, monkeypatch):
    """silent 分支不在本工单范围内：既不进 history 也不离开 harvest。"""
    from core.garden import manager

    now = 2_000_000.0
    monkeypatch.setattr(manager.time, "time", lambda: now)
    _seed_harvest(TEST_CHAR_ID, flower_id="rose", now=now)

    monkeypatch.setattr(manager.random, "random", lambda: 0.90)  # silent 区间

    events = manager.daily_check(char_id=TEST_CHAR_ID)
    assert events[0]["handle_action"] == "silent"

    storage = _read_json(sandbox.garden(char_id=TEST_CHAR_ID) / "storage.json")
    assert len(storage["harvest"]) == 1
    assert storage["history"] == []


def test_daily_check_history_backward_compatible_with_missing_history_key(sandbox, monkeypatch):
    """旧 storage.json 缺 history 键时按空处理，不报错。"""
    from core.garden import manager

    now = 2_000_000.0
    monkeypatch.setattr(manager.time, "time", lambda: now)
    garden_dir = sandbox.garden(char_id=TEST_CHAR_ID)
    garden_dir.mkdir(parents=True, exist_ok=True)
    (garden_dir / "storage.json").write_text(
        json.dumps({
            "harvest": [{
                "flower_id": "daisy",
                "bloomed_at": now - 4 * 86400,
                "expires_at": now + 10 * 86400,
                "status": "fresh",
            }],
            "vase": [],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(manager.random, "random", lambda: 0.10)  # ask 区间

    events = manager.daily_check(char_id=TEST_CHAR_ID)
    assert events[0]["handle_action"] == "ask"

    storage = _read_json(garden_dir / "storage.json")
    assert len(storage["history"]) == 1
    assert storage["history"][0]["kind"] == "ask"


def test_get_state_exposes_history_recent(sandbox, monkeypatch):
    from core.garden import manager

    now = 2_000_000.0
    monkeypatch.setattr(manager.time, "time", lambda: now)
    manager._save(
        manager._storage_path(TEST_CHAR_ID),
        {
            "harvest": [],
            "vase": [],
            "history": [
                {"kind": "dry", "flower": "rose", "mood_source": "yandere", "ts": now - 100, "note": "旧一条"},
                {"kind": "gift", "flower": "daisy", "mood_source": "neutral", "ts": now, "note": "纯真"},
            ],
        },
        char_id=TEST_CHAR_ID,
    )

    state = manager.get_state(char_id=TEST_CHAR_ID)
    assert state["history_recent"] == [
        {"kind": "gift", "flower": "daisy", "mood_source": "neutral", "ts": now, "note": "纯真"},
    ]


def test_get_state_history_recent_empty_when_no_history(sandbox):
    from core.garden import manager

    state = manager.get_state(char_id=TEST_CHAR_ID)
    assert state["history_recent"] == []


# ─────────────────────────────────────────────────────────────────────────────
# gift 主动消息：走既有 scheduler proposer + ProactiveLedger 记账，
# 受 QUIET/DND/冷却完整 gating（不是绕过账本的直发）；ask/dry 零消息。
# ─────────────────────────────────────────────────────────────────────────────

def _gift_proposal(now_ts: float):
    from core.scheduler.triggers import garden_daily

    return garden_daily.propose_garden_handle_gift({
        "now_ts": now_ts,
        "garden_daily_events": [{
            "type": "harvest_handle",
            "handle_action": "gift",
            "flower_id": "daisy",
            "name": "雏菊",
            "language": "纯真",
            "received_at": now_ts - 10,
        }],
    })


def _patch_gating_env(monkeypatch, *, dnd_active: bool):
    import core.scheduler.loop as _loop
    import core.scheduler.triggers.dnd as _dnd
    from core.scheduler.state_machine import TriggerState

    monkeypatch.setattr("core.conversation_activity.user_active_recently", lambda: False)
    monkeypatch.setattr(_dnd, "is_dnd", lambda uid: dnd_active)
    monkeypatch.setattr("core.scheduler.gating.get_current_state", lambda uid: TriggerState.QUIET)
    monkeypatch.setattr("core.scheduler.gating.is_trigger_ready", lambda name: True)


def test_ask_and_dry_never_produce_scheduler_proposals(sandbox):
    """ask 与 dry 不发消息：对应 proposer 已不存在 / 不再命中这两种 handle_action。"""
    from core.scheduler.triggers import garden_daily

    assert not hasattr(garden_daily, "propose_garden_handle_ask")

    now_ts = 1_000.0
    for action in ("ask", "dry"):
        ctx = {
            "now_ts": now_ts,
            "garden_daily_events": [{
                "type": "harvest_handle", "handle_action": action,
                "name": "雏菊", "received_at": now_ts - 10,
            }],
        }
        assert garden_daily.propose_garden_handle_self(ctx) is None


def test_gift_proposal_blocked_by_dnd(sandbox, monkeypatch):
    from core.scheduler.gating import _decide

    _patch_gating_env(monkeypatch, dnd_active=True)
    proposal = _gift_proposal(1_000.0)
    assert proposal is not None

    picked, reason, _ = _decide("u1", [proposal])
    assert picked is None
    assert reason == "dnd_filtered"


@pytest.mark.asyncio
async def test_gift_proposal_passes_gating_and_records_to_ledger_when_dnd_off(sandbox, monkeypatch):
    from core.scheduler.gating import _decide
    from core.scheduler import loop as _loop
    from core.scheduler.proactive_ledger import snapshot as _ledger_snapshot

    _patch_gating_env(monkeypatch, dnd_active=False)
    proposal = _gift_proposal(1_000.0)
    assert proposal is not None

    picked, reason, _ = _decide("u1", [proposal])
    assert picked is not None
    assert picked.trigger_name == "garden_handle_gift"

    before = _ledger_snapshot()["daily_count"]

    async def _fake_pipeline_send(prompt, **kwargs):
        return "（模拟已发送）"

    monkeypatch.setattr(_loop, "_pipeline_send", _fake_pipeline_send)

    result = await picked.execute(dry_run=False)
    assert result.sent is True

    after = _ledger_snapshot()["daily_count"]
    assert after == before + 1, "gift 主动消息必须经 ProactiveLedger.record_send() 记账"
