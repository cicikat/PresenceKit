"""Contracts for the autonomy admin control surface."""

from admin_static_assets import read_admin_client_source


def test_autonomy_admin_router_and_observability_page_are_wired_together():
    """A shipped page must not point at endpoints omitted from the FastAPI app."""
    from admin.admin_server import app

    paths = {route.path for route in app.routes}
    expected = {
        "/admin/autonomy/status",
        "/admin/autonomy/effective-state",
        "/admin/autonomy/config",
        "/admin/autonomy/runs",
        "/admin/autonomy/tools",
        "/admin/autonomy/test-enqueue",
    }
    assert expected <= paths

    source = read_admin_client_source()
    for endpoint in (
        "/admin/autonomy/status",
        "/admin/autonomy/effective-state",
        "/admin/autonomy/config",
        "/admin/autonomy/runs",
        "/admin/autonomy/tools",
        "/admin/autonomy/test-enqueue",
    ):
        assert endpoint in source


def test_autonomy_tools_matrix_renders_explainable_decision_columns():
    source = read_admin_client_source()
    for marker in (
        "decision_source",
        "全局只读继承",
        "自主白名单",
        "决策来源",
        "全局开关",
        "MCP 策略",
        "自主策略",
        "危险/确认",
        "最终状态",
        "item.allowed ?? item.execution_allowed",
        "item.danger || item.confirmation",
    ):
        assert marker in source
