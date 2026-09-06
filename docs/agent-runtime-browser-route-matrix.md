# Agent Runtime Browser 路由调用矩阵（Brief 240）

本文档记录浏览器任务迁移后的后端退役审计。当前工作区的 Emerald-client Tauri
command、shared API 和直接 HTTP bridge 已随 Brief 72 删除，因此旧 owner-bridge/观测
路由没有真实调用者并已从后端移除。配置和提交的唯一控制面是
`/settings/agent-runtime-browser`。

| 路由 | 调用方/证据 | scope | 副作用与返回边界 | 决策 |
|---|---|---|---|---|
| `GET /settings/agent-runtime-browser` | 后端 admin 页面 `admin/static/js/agent-runtime-browser.js` | `admin` | 读取 allowlist、worker 健康、限额和计数；不返回凭据/profile/完整 URL | 保留；admin 配置入口 |
| `PUT /settings/agent-runtime-browser` | 后端 admin 页面设置表单 | `admin` | 修改 browser 配置并重启 worker；域名和 adapter 严格 allowlist | 保留；admin 配置入口 |
| `GET /settings/agent-runtime-browser/tasks` | 后端 admin 页面任务列表 | `admin` | 当前 owner/character 的 metadata-only task projection | 保留；admin 观测入口 |
| `POST /settings/agent-runtime-browser/tasks` | 后端 admin 页面安全任务提交 | `admin` | 创建 Reality Task；安全操作可立即运行，高风险始终 `waiting_confirm` | 保留；唯一 admin 提交入口 |
| `POST /settings/agent-runtime-browser/tasks/{id}/confirm` | 后端 admin 页面确认按钮 | `admin` | 先校验创建时 fingerprint，再一次性确认并运行；替换字段不得改变任务状态 | 保留；唯一 admin 确认入口 |
| `GET /observability/agent-runtime-browser` | 旧 Tauri `load_agent_runtime_browser`；Brief 72 已删除，`rg` 无当前 source 调用 | `state.read` | 原 projection 已并入 settings snapshot；重复序列化入口已删除 | **retired**；OpenAPI 不再发布 |
| `POST /agent-runtime-browser/tasks*`、`GET .../{id}` | 旧 Tauri browser commands；Brief 72 已删除，`rg` 无当前 source 调用 | `chat` / `state.read` | 旧 owner bridge 写/读路由与专属 schema 已删除；任务状态仅由 admin service functions 管理 | **retired**；无兼容调用者 |

## 统一安全结论

- 所有现存写路径都调用 `core.agent_runtime.browser.create_task()`、
  `confirm_and_run_task()`、`run_task()` 或对应 Task Manager 控制函数；没有第二套
  fingerprint、owner/character 绑定或 `waiting_confirm` 状态机。
- 高风险操作即使请求带 `confirmed=true` 也先落在 `waiting_confirm`；只有人工确认后
  才能进入 `queued`。admin 确认在状态变更前重新校验 fingerprint。
- Dream principal、`remote_server`、disabled policy、未配置 allowlist 和 adapter
  unavailable 均 fail-closed；浏览器 profile 只位于系统临时目录，不写入仓库或生产
  `data/`。
- 旧写路由、旧观测 GET、专属请求 schema 和 admin UI 白名单条目均已删除；OpenAPI
  路由断言确认它们不再发布。客户端不再拥有浏览器任务配置或提交能力。

## `rg` 审计清单

审计命令覆盖后端、桌面端、测试、脚本和文档：

```text
rg -n --hidden -g '!node_modules' -g '!build' -g '!dist' \
  'agent-runtime-browser|settings/agent-runtime-browser|browser-request.v1' \
  Emerald-presence Emerald-client Emerald-mobile
```

结果归类如下：

- 后端 `admin/routers/settings_browser.py`：canonical admin surface；
  `admin/routers/observability.py` 不再包含旧 browser route。
- 后端 `core/agent_runtime/browser.py`：唯一任务/worker/fingerprint 实现；没有脚本或
  第二个 runtime owner。
- `admin/static/js/agent-runtime-browser.js`、`admin/static/pages/agent-runtime-browser.html`：
  admin 配置、提交、确认和 metadata 列表调用者。
- `Emerald-client/src-tauri/src/lib.rs`：Brief 72 当前 diff 已删除观测、创建、运行、确认、
  暂停、取消 Tauri command；客户端测试断言不存在旧路径引用。
- `tests/test_settings_browser.py`、`tests/test_agent_runtime_browser.py`、
  `tests/test_admin_ui_route_coverage.py`：后端配置、状态机、脱敏、OpenAPI 和 admin UI
  路由守卫；没有仅服务旧路径的孤立脚本/fixture。
- `docs/feature-control-surface.md`、`docs/three-repo-interface-catalog.md`、
  `docs/api-reference.md`、`docs/known-issues.md` 与 Brief 239/客户端 Brief 72：设计、
  三仓契约和退役条件。
- `Emerald-mobile`、后端 `scripts/` 以及 Emerald-client `src/`/`src-tauri/`：本次 `rg`
  未发现浏览器任务路径引用（客户端测试与历史文档中的负向断言/迁移记录除外）。
