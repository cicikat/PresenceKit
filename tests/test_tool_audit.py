from __future__ import annotations

from core.tool_dispatcher import ToolExecutionOutcome


def test_tool_audit_records_safe_params_and_scopes_queries(sandbox):
    from core.tool_audit import query, record

    record("owner", "char_a", tool="read_life_records", origin="autonomy_loop",
           args={"date_from": "2026-09-26", "query": "private meal details", "category": "diet"},
           outcome=ToolExecutionOutcome(status="tool_executed", result="private result"))
    row = query("owner", "char_a")[0]
    assert row["tool"] == "read_life_records"
    assert row["status"] == "success"
    assert row["key_params"] == {"date_from": "2026-09-26", "category": "diet"}
    assert row["request_id"]
    assert "private meal details" not in repr(row)
    assert "private result" not in repr(row)
    assert query("owner", "char_b") == []
    assert query("other", "char_a") == []


def test_tool_audit_uses_capability_receipt_for_actual_outcome(sandbox):
    from core.self_management.service import agent_change, user_grant
    from core.self_management.models import CapabilityChange
    from core.tool_audit import query, record

    assert user_grant("owner", "char_a", capability_id="autonomy.enabled", allowed=True,
                      mutable_by_agent=True, constraints={}, reason="allow").ok
    assert agent_change("owner", "char_a", CapabilityChange(
        "disable", "autonomy.enabled", None, "quiet", 1, "change-1",
    ), source="assistant_self_management").ok
    record("owner", "char_a", tool="manage_self_capability", origin="assistant_self_management",
           args={"capability_id": "autonomy.enabled", "action_id": "change-1", "action": "disable"},
           outcome=ToolExecutionOutcome(status="tool_executed", result="wrapper result"))
    row = query("owner", "char_a")[0]
    assert row["status"] == "success"
    assert row["after"] is False
    assert row["key_params"]["requested_value"] is False


def test_recap_intent_requires_past_and_action():
    from core.tool_audit import is_recap_request

    assert is_recap_request("昨晚你调用了哪些工具，哪些失败了？")
    assert is_recap_request("What did you change yesterday?")
    assert not is_recap_request("今天吃什么？")
