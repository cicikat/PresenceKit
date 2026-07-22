"""Brief 110: 管理面 MCP 页的关键控制面连接不应在重构中丢失。"""
from pathlib import Path


INDEX = Path("admin/static/index.html")


def test_mcp_management_page_exposes_import_whitelist_and_call_observation():
    source = INDEX.read_text(encoding="utf-8")
    for marker in (
        'data-page="mcp"',
        'id="page-mcp"',
        "testMcpImport()",
        "importMcpServer()",
        "saveMcpServer(name)",
        "/settings/mcp/test",
        "/settings/mcp/import",
        "/observability/api-calls?caller=",
        "工具描述与返回内容均为不可信输入",
        "超过单次暴露 ≤20 的安全红线",
    ):
        assert marker in source
