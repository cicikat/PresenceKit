# Brief 239：Agent Runtime 浏览器任务确认绑定与可审计验收

> 状态：`open`
> 优先级：`critical`
> 范围：`Emerald-presence` 后端
> 前置：Brief 230、233、236、237、238

## 背景

Brief 238 已经有 Reality-only 的 Browser Worker、Task Manager receipt、域名白名单、Workspace 文件桥和高风险 `waiting_confirm` 状态，但当前实现仍不能作为可发布的安全边界：

1. 创建任务时记录了 URL 摘要和操作类型，`run` 请求仍可重新提交另一组 `url/operation/params`，服务端没有证明这就是用户确认过的原请求。
2. 新增 Browser REST 写路由没有通过 admin route coverage 守卫；全量测试仍有失败。
3. Browser 工具和相邻 Runtime 工具存在缺失的中英文 UI 描述或参数 schema 描述。
4. 现有专项测试没有覆盖请求替换、跨域重定向、暂停/取消竞态、Workspace 上传下载、worker 重启恢复和 Playwright 真浏览器路径。

## 目标

把浏览器任务变成“确认对象不可替换、失败状态可解释、观测不泄密、全量守卫全绿”的后端能力。不要扩大为通用网页代理，不要默认开启浏览器，不要把浏览器正文、cookie、token、密码、完整 query 或本机 profile 路径写入 receipt、日志或观测端点。

## 必须实现

### 1. 固定任务请求与确认对象

- 在任务创建时生成规范化的 request fingerprint，至少覆盖：Reality principal（uid/char_id）、规范化目标 URL、operation、所有受支持参数及其类型/边界、上传下载 Workspace 路径引用。
- URL 规范化规则必须明确并测试：仅允许 `http/https`，去掉 fragment；query 若参与实际导航必须参与 fingerprint，但不得以明文出现在 receipt、日志或 observability。
- Task Manager 只保存完成重放校验所需的安全摘要或受保护引用，不保存凭据原文；receipt 只能返回不泄密的 fingerprint/版本标识（如确有需要）。
- `run` 必须在 claim/启动浏览器前校验执行请求与创建时 fingerprint 完全一致。任何 URL、operation、selector、value、path、确认标志或参数结构变化都必须返回稳定错误码（建议 `task_request_mismatch`），且任务不能被 claim、不能启动浏览器、不能增加 attempt。
- 高风险任务的确认必须是该 task 的一次性状态转换：未确认只能保持 `waiting_confirm`；确认后只能执行原 fingerprint；不能通过先调用 confirm、再提交另一组请求绕过校验。
- 幂等重试、重复 confirm、重复 run、过期任务、已取消任务必须保持既有终态，不产生第二次副作用。
- 如果为跨重启恢复增加受保护的请求引用或 resume API，必须单独定义权限、TTL、审计字段和泄密测试；不能把原始敏感参数放进客户端可见 receipt。

### 2. 修复全量测试阻断

- 为所有新增/暴露的内置工具补齐 `zh-CN` 与 `en-US` 的 UI description key，不修改工具 schema 的语义文本。
- 为 `process_run` 及所有新增参数补齐非空 `description`，并保持参数类型、范围、required 集合不变。
- 对 `/agent-runtime-browser/tasks`、`run`、`confirm`、`pause`、`cancel` 路由：若它们只供 Tauri owner bridge 使用，加入带理由的 `NO_ADMIN_UI_WHITELIST`；若属于 admin 面板能力，则补真实 admin UI 引用。禁止用无理由或过宽的通配白名单压测试。
- 同步更新 `docs/feature-control-surface.md`、`docs/tools.md`、`docs/known-issues.md`、`docs/three-repo-interface-catalog.md` 和 `/openapi.json` 对应说明，状态必须继续保持 `partial/open`，直到真实浏览器与客户端证据完成。

### 3. 补齐后端安全与生命周期测试

新增测试必须覆盖以下矩阵，且测试断言错误码、Task 状态、attempt 次数及 adapter 是否被调用：

- 安全操作：allowlist 命中成功；未知 operation、`file://`、无 scheme、非 allowlist、跨域 redirect 全部拒绝。
- 请求绑定：创建 A 后以 B 的 URL、operation、selector、value、path、params 分别调用 run，全部拒绝且不 claim；合法原请求只执行一次。
- 确认状态：高风险 create 为 `waiting_confirm`；未 confirm 的 run 拒绝；confirm 后只能执行原请求；重复 confirm/run 不重复执行。
- Realm/deployment：Dream principal、`remote_server`、browser disabled、adapter unavailable 全部 fail closed。
- 生命周期：queued/running 的 pause、cancel 竞态；取消优先于完成；暂停被 worker checkpoint 确认；超时、连接断开、未知异常分别产生正确的 `outcome_unknown`/错误码；服务重启 recovery 不自动重放副作用。
- 数据边界：页面正文、链接、fields、错误、URL query、cookie/header/password/token、profile 路径均按上限截断或脱敏；receipt 和观测端点不得出现原文。
- Workspace：upload/download 只能经过 Workspace capability；路径穿越、宿主绝对路径、未授权 root 全部拒绝；成功/失败均有受控 artifact metadata。
- Playwright：使用仓库内确定性的本地 fixture server，不依赖公网；验证真实 Chromium context 使用独立 profile、页面读取上限、操作超时和最终 redirect host 校验。
- API：路由 scope、uid+char_id 绑定、OpenAPI schema、错误响应和 task observability 过滤均有测试。

## 硬验收条件

以下条件必须全部满足，缺一项即判定不通过：

1. `pytest -n auto -q tests/test_agent_runtime_browser.py tests/test_agent_runtime_task_manager.py`：零失败、零错误。
2. `pytest -n auto -q`：零失败、零错误、无新增 xfail；输出中不能通过删除/跳过相关守卫来“变绿”。
3. 新增请求绑定测试证明：篡改任一请求字段时，adapter 调用次数为 0、attempt_count 不增加、原任务仍可用；合法请求最多产生一次真实副作用。
4. Playwright fixture smoke 在启用配置下实际启动 Chromium 并完成一次 `read_page` 或 `navigate`；测试结束后 profile 位于受控 runtime 根之外的独立 task 目录，且没有凭据/正文泄露到 receipt、日志或观测 JSON。
5. 关闭配置、无 adapter、remote_server、Dream 四种环境均有可重复的 fail-closed 测试。
6. `git diff --check` 通过；`git status --short` 只包含本工单相关文件；不得提交真实 token、cookie、密码、用户标识、绝对路径或公网测试数据。
7. 提交一个独立 commit，并在 commit message 中标明 Brief 239；提交前附上测试命令和完整结果摘要。

## 交付物

- 后端实现与最小必要的 Task Manager/API schema 改动。
- `tests/` 中的请求绑定、生命周期、Playwright fixture、脱敏和路由覆盖测试。
- 更新后的接口/控制面/已知问题文档。
- 一份不含敏感数据的验收记录：配置摘要、测试命令、结果、失败场景错误码和 Playwright 运行证据路径。

## 明确不做

- 不把浏览器改成默认开启或任意 URL 代理。
- 不把 cookie、登录态、密码、完整页面源码、原始参数、宿主路径暴露给 LLM、客户端或 admin 观测。
- 不因为测试环境缺少 Playwright 就把真实浏览器测试标成通过；缺依赖必须明确失败并保持 `partial/open`。
