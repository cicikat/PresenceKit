"""Maintained core suites; collection validates every entry before smoke runs."""

SMOKE = {
    "security": {"test_n8_auth.py", "test_sec_auth1.py", "test_sec_ws1_auth.py"},
    "storage": {"test_n9_safe_write.py", "test_memory_direct_path_lint.py"},
    "chat": {"test_qq_fast_path_tool_loop.py", "test_brief171_owner_turn_diary.py", "test_turn_sink.py"},
    "tools": {"test_tool_grounding.py", "test_tool_schema_portable.py"},
    "oauth": {"test_gmail_oauth.py"},
    "contract": {"test_protocol_fixtures.py", "test_protocol_matrix.py"},
}


def mark_suites(items, config):
    import pytest

    expected = set().union(*SMOKE.values())
    collected = set()
    for item in items:
        name = item.path.name
        if name in expected:
            item.add_marker(pytest.mark.smoke)
            collected.add(name)
        if name == "test_activity_contract.py":
            item.add_marker(pytest.mark.cross_repo)
        if name == "test_windows_accept_recovery.py":
            item.add_marker(pytest.mark.windows_only)
    if config.option.markexpr == "smoke" and expected != collected:
        raise pytest.UsageError(f"Smoke inventory missing collected modules: {sorted(expected - collected)}")
