# Brief 238：Agent Runtime 真实浏览器 Worker 与凭据隔离

> 状态：`open`
> 优先级：`critical`
> 前置：Brief 230、233、236、237
> 范围：`Emerald-presence` 后端；这是受控实验能力，不是默认开启的通用网页代理

## 背景

Brief 236 目前只有浏览器策略检查、Task receipt 和可注入 adapter，尚未形成真正可执行的
浏览器 worker。这里的“真实浏览器”是：Agent 在独立浏览器进程/profile 中实际打开网页、读取
有限页面内容并执行 allowlist 操作；它不是把浏览器 cookie、token、密码或完整 profile 交给模型，
也不是允许 Agent 任意联网。

## 目标

把 `core/agent_runtime/browser.py` 从 capability 外壳补成可审计的 Reality-only 实验能力：

- 独立 browser worker、独立 profile、独立 task receipt；不得复用用户默认浏览器 profile。
- 通过 Task Manager 管理创建、lease、暂停、取消、过期、失败和 `outcome_unknown`。
- 模型只能获得 bounded 页面投影和结构化操作结果，不获得 cookie、header、password、token、
  本地 profile 路径、浏览器存储或未截断页面源文档。
- `navigate/read_page/click/fill/select` 受域名、元素/操作类型和资源限制约束。
- `login/pay/post/delete/send_email/change_password` 等高风险动作必须进入
  `waiting_confirm`，由用户显式确认后才执行；未知结果不得自动重试副作用操作。
- 下载和上传必须调用 Workspace capability，浏览器 worker 不得自行访问宿主路径。
- Dream realm、`remote_server` 和没有明确实验开关的环境 fail-closed。

## 后端契约

1. 提供稳定的 browser task API/adapter，具体 REST 或内部调用形式以实现前的 `/openapi.json`
   和三仓接口总账为准；不得让客户端猜测私有字段。
2. task request 至少包含：`uid`、`char_id`、`operation`、经过 allowlist 的目标域名引用、
   幂等键和 bounded 参数摘要；不得保存或返回原始凭据。
3. task receipt 至少能表达：`queued/running/waiting_confirm/paused/succeeded/failed/canceled/expired/outcome_unknown`，
   并携带错误码、尝试次数、截断标记和 artifact 元数据。
4. 能力观测只返回 enabled/effective state、域名数量、限制、worker 健康和计数；不返回 URL
   中的敏感 query、页面正文、profile 名称或绝对路径。
5. 所有 owner-facing 查询、确认、暂停、取消入口都必须有明确 scope、owner/character 绑定、
   幂等和审计记录。管理面观测仍保持 `state.read` metadata-only。

## 安全边界

- 浏览器网络只允许显式配置的 `http/https` 域名；禁止任意代理、file/data/javascript URL、
  任意下载目录和任意上传路径。
- 登录由用户在隔离浏览器中完成，或由独立凭据代理完成；模型永远不能读取凭据。
- 页面提取必须有大小、字符数和字段白名单；日志、receipt、trace、错误响应都必须经过脱敏。
- worker 崩溃、服务重启、浏览器断连和页面结果未知均 terminalize 为稳定状态，不隐式重放。
- 不调用 `capture_turn()`、`record_assistant_turn()`、Memory Event、short-term 或长期记忆写入。
- 不新增 universal EventBus，不改变 Dream/Reality EventContext 语义，不把服务端机器当作用户桌面。

## 测试与验收

- 真实隔离浏览器 smoke：打开 allowlisted 测试站点、读取 bounded 内容、执行一个无副作用操作。
- 负向测试：非 allowlist 域名、跨域跳转、凭据读取、profile 读取、`file://`、路径穿越、
  未确认高风险操作、Dream、remote_server、禁用开关全部 fail-closed。
- 生命周期测试：暂停、取消、超时、worker 崩溃、浏览器断连、重启恢复、`outcome_unknown`、
  未知结果不重试副作用操作。
- 上传/下载测试证明所有文件流经 Workspace capability，不能读写宿主任意路径。
- 观测/API 测试证明 prompt、日志、receipt、观测端点不含 token/cookie/password/profile/绝对路径。
- tool loop 集成测试证明 capability 暴露受全局开关、origin、角色授权、确认和部署模式共同约束。
- 更新 `docs/feature-control-surface.md`、`docs/tools.md`、`docs/known-issues.md`、
  `docs/three-repo-interface-catalog.md`；在真实浏览器和客户端未完成前保持 `partial/open`，
  不得把 adapter 存在写成能力已完成。

## 完成条件

只有在真实 worker、隔离 profile、owner 确认、文件边界、未知结果处理、观测脱敏和上述测试全部
通过后，Brief 236 才能从 `proposal`/`partial` 改为 `implemented`，并把 Brief 239（客户端）
所需的公开 schema 固定下来。未达到条件时，不得开放默认开关，也不得进入生产 soak。
