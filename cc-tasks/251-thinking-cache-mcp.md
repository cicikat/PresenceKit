# 工单 251：思考链路 / 管理面缓存 / MCP 热重载

日期：2026-09-15。设置只改后端管理面板，不往桌面客户端塞开关。

## 工单 1 — 思考链路与开关（模型连接页）

### 现状（查代码后）

前端（桌面）展开的思考不是模型“心里话”的单独通道，而是：

`GET /chat/turns/{turn_id}/reasoning`（`memory.read`）

它读的是协议出口默认归档的 **API 已返回思考**，和 `thinking.enabled` 无关。来源包括：

| 你看到的 | 实际是什么 |
|---|---|
| 没有 | 中转/模型没回 `reasoning_content` / `thinking` / `<think>`；或 preset 的 `reasoning_extra_body` 把思考关掉了（当前 `deepseek-high` 是 `thinking.type: disabled`） |
| 一段对话口吻 | native 路线：主模型返回的思考摘要 + 可选角色心声提示（`character_voice`） |
| 一堆客观分析文字 | 常是 **前置独白** 那次便宜模型调用的 reasoning 被绑进了同一 `turn_id`；或中转把思维链当成普通 reasoning 吐出来 |

两条生成路线（Brief 32），**不是新 category**：

- **原生思考 `native`**：主生成请求带 `preset.reasoning_extra_body`（网关方言逃生舱）。接口：`GET/POST /settings/thinking` 的 `mode=native` + preset 的 `reasoning_native` / `reasoning_extra_body`。
- **额外思考 / 前置独白 `monologue`**：主生成前多一次 `call_category=monologue` 轻量调用，注入当轮 prompt，不广播。独白用哪个模型由 routing profile 的 `monologue` 分配（模型连接页已有）。
- **`auto`**：chat preset 声明了 `reasoning_native: true` 走 native，否则走 monologue。

管理面开关其实在「聊天方式与思考」`/settings/thinking`，模型连接页只有 `monologue` 用途分配，preset 编辑器也没暴露 `reasoning_native` / `reasoning_extra_body`。

### 要做

- [x] 把思考总开关、mode（自动/原生/前置独白）、心声、独白预算、主动消息开关放到 **模型连接与分工** 页。
- [x] Preset 编辑器补上「该连接有原生思考」和 `reasoning_extra_body` JSON。
- [x] 「聊天方式与思考」只留跳转，不删接口。
- [x] 回合思考读取只关联主聊天 `call_category=chat` 的归档，独白/探针/摘要的 reasoning 不再混进气泡。
- [x] 桌面不新增设置入口。

## 工单 2 — 管理面板缓存

### 这是设计，不是偶发玄学；但会表现为 bug

管理面静态资源靠 `?v=` 和 `ADMIN_UI_FRAGMENT_VERSION` 换缓存。`index.html` 本身、以及部分没带 `?v=` 的脚本（如 `lorebook.js`）走 FastAPI `StaticFiles` 默认缓存。

所以会出现：

1. 普通刷新一瞬间仍是旧 JS/旧 fragment，硬刷新或等缓存失效才变成新 UI。
2. Agent 改了页面却忘了 bump `?v=`，对照的是刷新瞬间那版旧 UI，把新改动修回去或修错位置。

### 要做

- [x] `/` 和 `/static/*` 加 `Cache-Control: no-cache, must-revalidate`，刷新必须向服务器再验证。
- [x] 给未版本化的 admin JS 补 `?v=`。
- [x] 文档写清：这是缓存策略，不是“面板自己会变”。

## 工单 3 — MCP 启用后未连接 + 热重载

### 现状

热重载代码已经在：`PATCH /settings/mcp` → `sync_mcp_servers()`，单 server → `reload_server_from_config()`。**不需要为了启停去重启进程。**

但 `reload_server_from_config()` 在 **连接失败** 时返回 `False`，管理面把它显示成「需要重启服务」。重启后勾选仍在（配置已写盘），连接仍失败（运行态 `connected=false`），看起来像“重启也没连上、热重载不可用”。

### 要做

- [x] 区分 `reloaded` / `connection_failed` / `restart_required`。连接失败不再叫重启。
- [x] 卡片展示 `last_init_error`，提供「重新连接」（再走热重载）。
- [x] 热重载尝试过但没连上时，owner task 仍在，返回成功通道 + 失败原因。
