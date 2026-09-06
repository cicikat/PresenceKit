# Brief 241：浏览器任务迁移跨仓合并闸门与大任务收尾

 > 状态：`done`
> 优先级：`critical`
> 范围：`Emerald-presence` + `Emerald-client`
> 前置：Brief 240、Brief 72 必须分别完成并提交独立 commit
> 推荐模型：GPT-5.5 或 GPT-5.6-sol；由一名 agent 做集成，另一名 reviewer 只读复核

## 目标

确认“后端 admin 单一控制面、客户端不暴露执行环境”的迁移没有幽灵代码、没有接口漂移、没有记忆污染，然后才允许合并并关闭本轮大任务。合并不是本工单开始前的默认动作。

## 执行顺序

1. 核对两仓工作区干净，确认 Brief 240 与 Brief 72 的独立 commit、测试摘要和文件范围。
2. 在隔离临时目录启动后端与本地 fixture；使用 admin token/测试配置，不触碰生产配置。正常开发端口与临时验收端口可不同，但必须在记录中写明“临时实例”。
3. 真实 Chromium 只验证 allowlisted `read_page` 成功、受保护 `post` 进入 `waiting_confirm`、人工确认后执行一次；不得点击真实发布/支付/不可逆按钮。
4. admin 页面硬刷新并核对：worker 可用、允许域名数量正确、任务 metadata 脱敏、发布任务仍显示等待人工确认。
5. 检查客户端：偏好页没有浏览器任务配置/提交表单；客户端只保留有证据的兼容读取路径。
6. 对生产 `config.yaml`、`data/`、用户记忆文件做内容哈希或等价只读对比，确认临时验收没有写入；确认临时 profile、fixture、receipt 不会进入提交。
7. 通过所有仓库测试和 diff 检查后，按团队约定合并两个独立 commit；合并后再跑一次最小跨仓 smoke，不产生新的功能代码。

## 合并硬门槛

以下任一项不满足，保持 `open`，不得合并：

- Brief 240、72 未完成，或任一仓有无关未提交改动；
- 任一自动化测试失败、出现新增 xfail、OpenAPI/路由覆盖与实现不一致；
- 真实 Chromium 未启动成功，或只做了后端模拟而没有真实浏览器证据；
- 发现旧写入口无调用者却仍保留，或删除了仍被兼容客户端使用的路径；
- 发现 receipt、日志、观测、客户端状态中出现凭据、cookie、profile 路径、完整 query、页面正文、原始 params；
- 生产配置/记忆文件发生非预期变化；
- 发布任务被自动确认、自动重试或绕过人工确认。

## 2026-09-06 合并门验收记录

### 两仓提交与范围

- 后端 Brief 240 独立提交：`1454a2d`（浏览器旧路由退役、admin 单一控制面、worker/确认闸门及回归测试）。提交前后 `git status --short` 仅保留本工单文件。
- 客户端 Brief 72 独立提交：`c6e5097`（客户端浏览器任务表面退役及文档/测试收尾）。客户端仓库提交前后工作区干净。
- `git diff --check`：后端与客户端均通过。移动端仓库未发现浏览器任务 source 调用者；三仓接口总账已标记旧路径 `retired`，当前提交面为后端 admin `/settings/agent-runtime-browser*`。

### 自动化验证

- 后端：`pytest -n auto -q tests/test_settings_browser.py tests/test_agent_runtime_browser.py tests/test_agent_runtime_task_manager.py tests/test_admin_ui_route_coverage.py` → **26 passed**。
- 客户端：`npm.cmd test -- --run` → **52 files / 216 tests passed**；`npx.cmd tsc --noEmit` 与 `npm.cmd run build` 均通过（仅既有 chunk-size warning）。
- 路由审计：OpenAPI 不发布 `/agent-runtime-browser/*` 与 `/observability/agent-runtime-browser`；旧观测路由实测 404；allowlist、redirect、fingerprint 替换、Dream/remote/disabled/unavailable fail-closed 均有回归覆盖。

### 隔离实例与真实 Chromium

使用仓库内 ignored 临时目录 `.tmp/brief241-acceptance` 的独立配置、`YEXUAN_DATA_PREFIX` 和本地 fixture 启动临时 admin（`127.0.0.1:18752`）与 fixture（`127.0.0.1:18753`）。生产 `config.yaml`、`data/` 与用户记忆未写入。

真实 Chromium（Playwright bundled Chromium）硬刷新 admin `index.html` 后，通过页面上下文携带临时 Bearer token 完成：

- allowlisted `read_page` → `200` / receipt `succeeded`，正文仅返回 fixture 内容；
- `post` 提交 → `200` / receipt `waiting_confirm`，未自动执行；
- 人工确认同一任务 → 单次 `succeeded`，`attempt_count=1`，未执行真实发布/支付/不可逆动作。

截图证据保存在 ignored `.tmp/brief241-acceptance/chromium-hard-refresh.png`；截图与 receipt 均不含 token、cookie、profile 路径、完整 query 或页面正文之外的敏感数据。临时进程已停止，临时配置、fixture、profile 和 receipt 均未进入 Git。

### 生产只读对比与收尾

对生产 `config.yaml`、`data/` 记忆目录和 `user_identity` 文件仅做 SHA-256 只读对比；临时实例前后哈希一致。未执行真实发布、支付、上传、删除或其他不可逆动作。

合并门结论：两仓独立提交、自动化测试、真实 Chromium admin 硬刷新、人工确认闸门、脱敏与生产只读隔离均通过；本工单可关闭。

## 收尾判定

只有在上述门槛全部通过、两个仓合并后工作区再次干净、跨仓 smoke 通过并记录证据时，本轮浏览器任务迁移才可以标记 `done`。发布平台显示“任务暂停，等待人工确认”不是失败，而是预期的高风险状态；它仍需人工在发布系统完成最终确认，不能由本工单代操作。

## 交付物

- 两仓 commit/合并记录；
- 自动化测试命令和结果摘要；
- 真实 Chromium 与 admin 硬刷新脱敏证据；
- 生产记忆/配置只读对比结果；
- 最终 `git status --short` 与未完成事项清单。
