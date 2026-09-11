from datetime import datetime


def _paths(tmp_path):
    from types import SimpleNamespace
    return SimpleNamespace(letter_weekly_state=lambda: tmp_path / "weekly.json")


def test_weekly_success_blocks_duplicate_and_next_week_is_due(monkeypatch, tmp_path):
    from core.mail import weekly_contract
    monkeypatch.setattr("core.sandbox._instance", _paths(tmp_path))
    monday = datetime(2026, 8, 3, 12).timestamp()
    assert weekly_contract.claim("u", "c", now=monday)
    weekly_contract.finish("u", "c", sent=True, message_id="message-id", now=monday)
    assert not weekly_contract.is_due("u", "c", now=monday + 3600)
    assert weekly_contract.is_due("u", "c", now=monday + 7 * 86400)


def test_weekly_smtp_failure_retries_but_quality_waits(monkeypatch, tmp_path):
    from core.mail import weekly_contract
    monkeypatch.setattr("core.sandbox._instance", _paths(tmp_path))
    now = datetime(2026, 8, 3, 12).timestamp()
    weekly_contract.finish("u", "c", sent=False, failure_code="smtp_timeout", now=now, retry_base_seconds=10)
    assert not weekly_contract.is_due("u", "c", now=now + 9)
    assert weekly_contract.is_due("u", "c", now=now + 10)
    weekly_contract.finish("u", "q", sent=False, failure_code="quality_rejected", now=now)
    assert not weekly_contract.is_due("u", "q", now=now + 60)
    assert weekly_contract.is_due("u", "q", now=now + 6 * 3600)


def test_weekly_claim_prevents_concurrent_delivery(monkeypatch, tmp_path):
    from core.mail import weekly_contract
    monkeypatch.setattr("core.sandbox._instance", _paths(tmp_path))
    now = datetime(2026, 8, 3, 12).timestamp()
    assert weekly_contract.claim("u", "c", now=now) is not None
    assert weekly_contract.claim("u", "c", now=now) is None


def test_weekly_due_proposal_bypasses_only_global_cooldown(monkeypatch):
    from core.scheduler.gating import TriggerProposal, _proposal_cooldown_ready
    from core.scheduler.state_machine import TriggerState
    proposal = TriggerProposal("letter_writer", 1, "letter", [TriggerState.QUIET], weekly_delivery_due=True)
    monkeypatch.setattr("core.scheduler.gating.is_trigger_ready", lambda *_args, **_kwargs: False)
    assert _proposal_cooldown_ready(proposal)


def test_sent_or_backoff_never_falls_through_to_event_letter(monkeypatch):
    from core.scheduler.triggers import letter_writer
    monkeypatch.setattr('core.config_loader.get_config', lambda: {'mail': {'enabled': True}})
    monkeypatch.setattr('core.mail.weekly_contract.is_due', lambda *a, **k: False)
    monkeypatch.setattr(letter_writer, '_check_trigger_conditions', lambda *a, **k: 'strong reason')
    assert letter_writer.propose({'uid': 'example', 'char_id': 'example'}) is None


async def test_mail_executes_but_migrated_speech_does_not(monkeypatch):
    from unittest.mock import AsyncMock
    from core.scheduler import gating
    from core.scheduler.state_machine import TriggerState
    execute = AsyncMock()
    winner = gating.TriggerProposal('letter_writer', 1, 'letter', [TriggerState.QUIET], execute=execute)
    monkeypatch.setattr(gating, 'write_shadow_tick', lambda uid: winner)
    monkeypatch.setattr(gating, 'is_live_mode', lambda: True)
    await gating.run_shadow_tick('example')
    execute.assert_awaited_once_with(dry_run=False)
    execute.reset_mock()
    from dataclasses import replace
    winner = replace(winner, trigger_name='morning_greeting')
    await gating.run_shadow_tick('example')
    execute.assert_not_awaited()
