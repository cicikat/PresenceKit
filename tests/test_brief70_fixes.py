from tests.fixtures.public_assets import TEST_CHAR_ID
import json
import random

import pytest


def _spend_config(provider):
    return {
        "spend": {
            "enabled": True,
            "daily_cap": 100,
            "monthly_cap": 100,
            "payee_whitelist": ["deepseek"],
            "balance_providers": [provider],
        }
    }


@pytest.mark.asyncio
async def test_spend_recovery_confirms_each_mandate_once(sandbox, monkeypatch):
    from core.actions import spend_ledger
    from core.scheduler.triggers import spend_monitor

    provider = {"name": "deepseek", "threshold": 10, "topup_amount": 20}
    monkeypatch.setattr("core.config_loader.get_config", lambda: _spend_config(provider))
    proposed = spend_ledger.append(action="api_topup", payee="deepseek", amount=20, status="proposed", origin="scheduler")
    spend_ledger.append(action="api_topup", payee="deepseek", amount=20, status="notified", origin="scheduler", mandate_id=proposed["mandate_id"])
    monkeypatch.setattr(spend_monitor.api_balance, "fetch_balance", lambda _: _async(30))
    monkeypatch.setattr("core.scheduler.loop._is_ready", lambda _: True)
    monkeypatch.setattr("core.scheduler.loop._mark", lambda _: None)
    monkeypatch.setattr("core.scheduler.loop._owner_id", lambda: "owner")

    for _ in range(3):
        await spend_monitor._check_spend_monitor()

    rows = spend_ledger.read_ledger()
    confirmed = [row for row in rows if row["status"] == "confirmed"]
    assert len(confirmed) == 1
    assert confirmed[0]["mandate_id"] == proposed["mandate_id"]
    assert spend_ledger.budget_usage()["daily_used"] == 20






@pytest.mark.asyncio
async def test_stage_phase_b_does_not_echo_cut_against_owner(sandbox, monkeypatch):
    from core.stage.models import StageSettings
    from core.stage.runner import run_owner_turn
    from core.stage.store import create_stage

    candidate = type("Candidate", (), {"char_id": TEST_CHAR_ID, "total": 1.0, "parts": {}})()
    monkeypatch.setattr("core.stage.runner._rank_candidates", lambda *args, **kwargs: [candidate])

    create_stage("brief70-stage", "owner", [TEST_CHAR_ID], settings=StageSettings(min_responders=0, max_responders=0, max_ai_chain_depth=1))

    async def generate(_stage, _speaker, _transcript, _turn_id, _triggered_by):
        return "请认真看看这句话"

    result = await run_owner_turn("brief70-stage", "请认真看看这句话", generate_reply=generate)
    assert len(result.replies) == 1






@pytest.mark.asyncio
async def test_visual_first_frame_is_not_put_on_cooldown(sandbox, monkeypatch):
    import admin.routers.perception as perception

    perception._last_accepted.clear()
    monkeypatch.setattr("core.config_loader.get_config", lambda: {"visual_perception": {"enabled": True}})
    monkeypatch.setattr(perception.time, "monotonic", lambda: 1.0)
    tasks = []
    def create_task(coro):
        tasks.append(coro)
        coro.close()
    monkeypatch.setattr(perception.asyncio, "create_task", create_task)
    class Upload:
        async def read(self): return b"image"

    result = await perception.ingest_visual(Upload(), "screen", True)
    assert result["processing"] is True and len(tasks) == 1


async def _async(value):
    return value
