from __future__ import annotations

from datetime import date, datetime


def test_mcp_import_headers_start_empty_and_cache_versions_are_bumped():
    source = open("admin/static/js/mcp.js", encoding="utf-8").read()
    index = open("admin/static/index.html", encoding="utf-8").read()
    core = open("admin/static/js/core.js", encoding="utf-8").read()

    assert "const MCP_DEFAULT_HEADERS = Object.freeze({});" in source
    assert "renderKeyValueEditor('mcp-import-headers', MCP_DEFAULT_HEADERS" in source
    assert "mcp.js?v=brief-251-thinking-cache-mcp-1" in index
    assert "ADMIN_UI_FRAGMENT_VERSION = 'brief-251-thinking-cache-mcp-1'" in core


def test_selected_migrated_winner_queues_only_bounded_festival_facts(sandbox):
    from core.autonomy import store
    from core.autonomy.signal_adapters import emit_scheduler_proposal_signal
    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState

    proposal = TriggerProposal(
        trigger_name="festival",
        urgency=0.8,
        topic_source="random",
        requires_state=[TriggerState.QUIET],
        metadata={
            "autonomy_evidence": [{
                "fact": "calendar_festival",
                "festival_key": "qixi",
                "reality_date": "2024-08-10",
                "calendar_source": "chinese_lunar",
                "legacy_prompt": "must not persist",
            }],
            "autonomy_action_mode": "talk",
        },
    )

    queued, status = emit_scheduler_proposal_signal("owner", "char", proposal, now=1_000)

    assert queued is True
    assert status == "queued"
    signal = store.load("owner", "char")["pending_signals"][0]["signal"]
    assert signal["evidence"] == [{
        "fact": "calendar_festival",
        "festival_key": "qixi",
        "reality_date": "2024-08-10",
        "calendar_source": "chinese_lunar",
    }]
    assert "legacy_prompt" not in str(signal)


def test_shadow_tick_enqueues_only_the_selected_migrated_winner(monkeypatch, sandbox):
    from core.scheduler import gating
    from core.scheduler.gating import TriggerProposal
    from core.scheduler.state_machine import TriggerState

    winner = TriggerProposal(
        trigger_name="festival", urgency=0.8, topic_source="random",
        requires_state=[TriggerState.QUIET], char_id="char",
    )
    loser = TriggerProposal(
        trigger_name="period_reminder", urgency=0.7, topic_source="mood_match",
        requires_state=[TriggerState.QUIET], char_id="char",
    )
    calls = []
    monkeypatch.setattr(gating, "_shadow_cfg", lambda: {"enabled": True, "max_size_mb": 1, "keep": 1})
    monkeypatch.setattr(gating, "_build_context", lambda uid: {"uid": uid, "char_id": "char"})
    monkeypatch.setattr(gating, "_collect_native_proposals", lambda ctx: [winner, loser])
    monkeypatch.setattr(gating, "_decide", lambda uid, proposals: (winner, "picked_highest_urgency", []))
    monkeypatch.setattr(gating, "get_current_state", lambda uid: TriggerState.QUIET)
    monkeypatch.setattr("core.autonomy.signal_adapters.emit_scheduler_proposal_signal", lambda *args: calls.append(args) or (True, "queued"))

    assert gating.write_shadow_tick("owner") is winner
    assert calls == [("owner", "char", winner)]


def test_period_and_festival_proposals_provide_factual_evidence(monkeypatch):
    from core.scheduler.triggers import festival, period

    monkeypatch.setattr(period, "_cfg", lambda: {"period_reminder": True})
    monkeypatch.setattr(period, "_days_elapsed", lambda uid, today=None: 4)
    period_proposal = period.propose({"uid": "owner", "today": date(2026, 5, 4)})
    period_evidence = period_proposal.metadata["autonomy_evidence"][0]
    assert period_evidence == {"fact": "period_reminder_window", "stage": "current", "days_elapsed": 4}

    monkeypatch.setattr(festival, "_cfg", lambda: {"festival": True})
    monkeypatch.setattr(festival, "_owner_id", lambda: "owner")
    monkeypatch.setattr(festival, "_get_today_festival", lambda today=None: ("qixi", "legacy prompt"))
    festival_proposal = festival.propose_festival({"now_dt": datetime(2024, 8, 10, 16, 0)})
    festival_evidence = festival_proposal.metadata["autonomy_evidence"][0]
    assert festival_evidence == {
        "fact": "calendar_festival",
        "festival_key": "qixi",
        "reality_date": "2024-08-10",
        "calendar_source": "chinese_lunar",
    }


def test_qixi_uses_lunar_conversion_and_adjacent_dates_do_not_match():
    from core.scheduler.triggers.festival import _is_lunar_date

    assert _is_lunar_date(date(2024, 8, 10), month=7, day=7)
    assert _is_lunar_date(date(2025, 8, 29), month=7, day=7)
    assert not _is_lunar_date(date(2024, 8, 9), month=7, day=7)


def test_autonomy_funnel_distinguishes_silent_blocked_and_talk_sent():
    from admin.routers.autonomy import _opportunity_funnel

    state = {
        "pending_signals": [],
        "jobs": [{
            "id": "job-1", "created_at": 900,
            "signal_sources": ["scheduler"],
            "opportunity": {"signals": [{"source": "scheduler"}]},
        }],
        "runs": [
            {"job_id": "job-1", "finished_at": 950, "evaluation_status": "evaluated_silent", "disposition": "completed_no_op"},
            {"job_id": "job-1", "finished_at": 960, "evaluation_status": "blocked_or_failed", "disposition": "blocked_user_active"},
            {"job_id": "job-1", "finished_at": 970, "evaluation_status": "talk_sent", "talk_sent": True},
        ],
    }

    counts = _opportunity_funnel(state, now=1_000)["24h"]["by_source"]["scheduler"]
    assert counts["opportunity_created"] == 1
    assert counts["evaluated_silent"] == 1
    assert counts["blocked_user_active"] == 1
    assert counts["talk_sent"] == 1
