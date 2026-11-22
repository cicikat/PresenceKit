from __future__ import annotations


def test_action_trace_query_classifies_filters_and_lists_persisted_uids(sandbox, monkeypatch):
    from core.data_paths import DEFAULT_CHAR_ID
    from core.memory import action_trace
    import core.config_loader as config_loader

    monkeypatch.setattr(config_loader, "get_config", lambda: {"action_trace": {"enabled": True}})
    action_trace.record("tool_audit", DEFAULT_CHAR_ID, tool="get_time", origin="user_live", status="ok")
    action_trace.record(
        "tool_audit", DEFAULT_CHAR_ID, tool="mcp__calendar__lookup",
        origin="assistant_loop", status="failed", args_digest="must_not_be_raw",
    )

    rows = action_trace.query("tool_audit", DEFAULT_CHAR_ID)
    assert [row["category"] for row in rows] == ["mcp", "info"]
    assert [row["execution_path"] for row in rows] == ["path_c", "path_a"]
    assert [row["provider"] for row in rows] == ["mcp", "builtin"]
    assert [row["tool"] for row in action_trace.query("tool_audit", DEFAULT_CHAR_ID, category="mcp")] == [
        "mcp__calendar__lookup"
    ]
    assert action_trace.list_uids(DEFAULT_CHAR_ID) == ["tool_audit"]


async def test_tool_trace_route_returns_safe_summary_and_category_counts(monkeypatch):
    from admin.routers import observability
    import core.memory.action_trace as action_trace

    monkeypatch.setattr("admin.observation_scope.resolve_observation_character_id", lambda _: "default")
    monkeypatch.setattr(
        action_trace,
        "query",
        lambda *_args, category="", limit=30: [
            {"tool": "get_time", "category": "info", "status": "ok", "result_digest": "safe"}
        ] if not category or category == "info" else [],
    )

    payload = await observability.tool_traces("tool_audit", category="info", limit=30, char_id="", _auth=None)
    assert payload["uid"] == "tool_audit"
    assert payload["categories"] == {"info": 1}
    assert payload["entries"][0]["result_digest"] == "safe"
