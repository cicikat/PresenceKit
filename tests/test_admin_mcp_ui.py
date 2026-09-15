"""Brief 110: 管理面 MCP 页的关键控制面连接不应在重构中丢失。"""
from pathlib import Path

from admin_static_assets import read_admin_client_source

INDEX = Path("admin/static/index.html")


def test_mcp_management_page_exposes_import_whitelist_and_call_observation():
    source = read_admin_client_source()
    for marker in (
        'data-page-fragment="mcp"',
        'id="page-mcp"',
        "testMcpImport()",
        "importMcpServer()",
        "saveMcpServer(name)",
        "mcp-import-use-proxy",
        "mcp-server-use-proxy-",
        "use_proxy",
        "deleteMcpServer(name)",
        "bindPageActions(serversEl)",
        "/settings/mcp/test",
        "/settings/mcp/import",
        "'DELETE', `/settings/mcp/${encodeURIComponent(name)}`",
        "/observability/api-calls?caller=",
        "/observability/llm-debug-requests?limit=10",
        "/llm-debug-requests",
        "loadMcpDebugRequests()",
        "saveMcpDebugRequests()",
        "clearMcpDebugRequests()",
        "tool_presets",
        "active_tool_preset",
        "tool_states",
        "saveMcpToolPolicy",
        "mcp-policy-effect-",
        "policy[toolName] = { ...(state.policy || {}), effect }",
        "mcp.policy.help",
        "function _mcpReloadToastKind(status)",
        "function reconnectMcpServer(name)",
        "status === 'connection_failed'",
        "status === 'restart_required'",
        "'POST', `/settings/mcp/${encodeURIComponent(name)}/reconnect`",
        "data-action=\"reconnectMcpServer\"",
        "selectMcpToolPreset",
        "toggleMcpServerCollapsed",
        "'/observability/llm-debug-requests'",
        "工具描述与返回内容均为不可信输入",
        "超过单次暴露 ≤20 的安全红线",
    ):
        assert marker in source


def test_mcp_servers_start_collapsed_and_persist_ui_state():
    source = read_admin_client_source()
    assert "const MCP_EXPANDED_SERVERS_STORAGE_KEY = 'qq_admin_mcp_expanded_servers_v1';" in source
    assert "const _mcpExpandedServers = _loadMcpExpandedServers();" in source
    assert "const collapsed = !_mcpExpandedServers.has(server.name);" in source
    assert "_mcpExpandedServers.has(name) ? _mcpExpandedServers.delete(name) : _mcpExpandedServers.add(name);" in source
    assert "_persistMcpExpandedServers();" in source
    assert "data-mcp-server-body" in source
    assert "loadMcpPage();" not in source.split("function toggleMcpServerCollapsed(name)")[1].split("async function _saveMcpToolPresets")[0]
    assert "if (!hadContent)" in source
    assert "toast(t('mcp.enabled_saved', 'MCP 总开关已热同步'), 'ok');" in source
    assert "loadMcpPage();" not in source.split("async function saveMcpEnabled()")[1].split("async function testMcpImport()")[0]


def test_mcp_management_page_exposes_optional_metadata_controls_and_safe_summary():
    source = read_admin_client_source()
    for marker in (
        "mcp.metadata_notice",
        "mcp-import-metadata-namespace",
        "metadata_mapping",
        "metadata_overrides",
        "domain_selector",
        "saveMcpMetadataOverride",
        "state.discovered",
        "state.authorized",
        "state.session_exposed",
        "parameter_summary",
    ):
        assert marker in source
    assert "tool?.input_schema" not in source
