# 110 — MCP 管理面板与 URL 导入

> 背景：用户想接入外部 http MCP（示例：CEDAR TOY 平台，端点 `https://toy.cedarstar.org/mcp`，streamable http）。
> 前置阅读：`docs/tools.md` MCP 节、`core/mcp_client.py`、109 号决策（MCP=可选外挂、单次暴露 ≤20 红线）。

## 110-a — mcp_client 鉴权支持（无依赖）

现状 config 只有 `url / tool_timeout_s / allow_tools`。外部平台常需 token/绑定码鉴权。新增：

```yaml
servers:
  - name: cedar_toy
    transport: http
    url: https://toy.cedarstar.org/mcp
    headers:              # 新增，可选；值支持 ${ENV_VAR} 展开，禁止明文入库文档
      Authorization: "Bearer ${CEDAR_TOY_TOKEN}"
```

- headers 透传给 http transport 的 ClientSession；stdio 忽略该字段。
- 敏感值走 secrets 机制或环境变量，遵守 AGENTS.md 强制规则 11（不明文入 track 文件）。

## 110-b — 管理面板 MCP 管理页（无依赖，可与 a 并行）

参照 `settings_tool_loop.py` 模式，新增 `admin/routers/settings_mcp.py`：

1. **列表/启停**：展示已配置 servers 及连接状态（最近一次 init/调用成败）；总开关 + per-server 开关，改后热生效（重跑 init/shutdown 该 server）。
2. **URL 导入**：表单填 name/url/headers → 连接测试（init + list_tools）→ 成功后写入 config 并展示发现的工具列表。
3. **工具白名单勾选**：列出 `list_tools()` 结果（名称+描述），勾选映射到 `allow_tools`；全不勾 = 全部允许维持现状语义，但 UI 上引导勾选（109 决策 4：单次暴露 >20 会告警）。
4. **分组显示**：按工具名前缀（首个 `_` 前的段）启发式分组展示，纯 UI 层，不改注册机制。拿到真实平台工具列表后可再调分组规则。
5. 观测：面板展示每个 MCP 工具最近调用记录（复用 B5 的 api_call_log，caller 记 `mcp__{server}__{tool}`）。

## 安全注意（写给施工者）
外部 MCP 的描述与结果均为不可信输入（docs/known-issues.md Brief 29 观察项），面板需在 server 详情处显示这一提示；`trace_args` 维持不落痕策略不变。

## 验收
连接测试对无鉴权/有鉴权 server 各一条路径的测试（mock）；面板改动同步 `docs/feature-control-surface.md` 与客户端设置审计文档。
