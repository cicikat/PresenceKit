# AR-01 模块化重构（325）

## 范围与基线

起点：`c78f65d1d1ef269dd58e0e7a957fa26aa354e0c3`。只改后端/Admin，
不部署、不迁移、不写生产私有状态，不改桌面/手机协议与配置。
开工时已有其他工单、known-issues 和三仓接口总账修改；不纳入本次提交。
一次性快照和日志在 ignored `.tmp/ar01-*`，输入使用 pytest 的公开合成配置。

开工定向：278 passed，0 failed，0 skipped（Python 3.12.13 / Windows）。
快照包含全部注册字段（排除 callable）、公开配置下的 schema/探针、四个 router
的 OpenAPI/注册顺序及 HTML 的实际脚本装载顺序（排除注释）。

| 合同 | 基线数量 | SHA-256 |
|---|---:|---|
| 工具注册字段及顺序 | 163 | `40c9fbf7ca1856c00f988944babd1a882e1e051b46f10df83fa49c1ebe2857f8` |
| 默认公开配置 schema | 96 | `ff6c3925a1f5ebe5022725462e81fae4fa319b5ae15a37dfabd862213d663951` |
| Path A 探针 | 4515 字符 | `f888a6fafcea59a708c02bd29d1a9ef1ea68e3fb759430494f1323e6c4bd3c92` |
| chat/dream/mobile/owner_turn OpenAPI | 4 顶层键 | `6d7ba2b70a6048dfb09d650661d6f6cfb8bd1cfe5bacf17035753e95d855952f` |
| 含 FastAPI 文档路由的注册顺序 | 65 | `29b448f1b9c807b70ff04c7c812dc0e83980a87ecb9069e148a0a4e3ccdfb33e` |
| 实际 script 标签 | 38 | `3c62a780d3a2bf299fc1c6bc0831e088fc0c26733440333dbea91b6f876c072c` |

## 调用与责任图

```text
原来：tool_dispatcher（定义/注册/暴露/准入/调用/结果）
现在：tool_dispatcher（稳定 API、历史注入 seam、唯一可变 registry）
        → tool_runtime.registry（只初始化一次、保持旧显示顺序）
          → tools/*_definitions（同域实现、schema、调用 adapter）
          → 已有 register_tools/register 扩展（实现不复制）
        → tool_runtime.exposure / policy / validation
        → tool_runtime.execution → invoker → 域 adapter → 原工具
        → tool_runtime.outcome / 原 action_trace、tool_activity、tool_audit

Path A: route_pretool → get_probe_prompt/get_tools_schema → execute_structured
Path C: Pipeline.run_agentic_loop → tool_discovery → get_tools_schema
        → execute_structured（仍经过全部统一闸门）
MCP: init_mcp_servers → 同一个 _TOOL_REGISTRY（运行期增删）

/desktop/chat、/mobile/chat、/v1/owner/turns
  → chat.run_owner_chat_turn（同对象导出 owner_chat.service）
  → admission / 冻结 scope+EventContext / conversation_lock
  → owner_chat.prepare（探针与上下文并发、取消、工具事实）
  → owner_chat.generation（原 Pipeline/Agent、流式过滤/start/delta/end）
  → owner_chat.delivery → turn_sink.record_assistant_turn
  → channels desktop/mobile / 同 msg_id canonical
QQ/主动触发继续使用自己的 ingress 与既有 Pipeline/turn_sink，不改为 HTTP 请求。

/dream/* → Dream router → core/dream 状态机/存储/资产路径
index.html → defer i18n → core.js（api/auth/state/事件委托/页面加载）
           → 页面脚本 → AdminPageLayout / AdminUI（同节点新旧呈现）
```

## 兼容与风险控制

- Facade 直接导出同一个函数/类对象；领域函数通过惰性 facade 依赖读取保留
  既有 monkeypatch（get_config、作用域 helper、桌面连接等），不复制状态。
  这些旧注入点有现有测试和本地扩展使用；仅在使用者迁移后才考虑移除。
- `_TOOL_REGISTRY` 正常加载只构建一次；新执行/暴露模块按调用读取 facade
  的 registry，兼容测试对整个属性的替换，也保留 MCP 直接持有字典的行为。
- Invocation 是冻结的可信主体上下文；模型参数仍单独传递，原有 owner/scope
  校验在统一准入或域 adapter 内，未新增确认策略。
- builtin 的静态调用绑定也用于旧扩展整体替换 spec 的兼容路径，避免 spec
  未携带 adapter 时丢失身份注入或作用域检查；新工具可直接注册自己的 invoke。
- 原有结果归一化、失败/未知状态、指纹、审计、推送与持久化顺序未重写。
- 旧 `exit_yandere` 的外部桌面 IPC 路径实现原样迁移，裸 data 路径守卫的
  既有例外从旧文件移到新归属（未增加例外数量，非后端私有数据路径）。
- facade/领域模块无初始化回调循环；运行时惰性引用只在初始化完成后使用。

## 阶段证据

| 阶段 | 迁移 | 验证 |
|---|---|---|
| A | 工具定义归域；共享暴露/校验/准入/结果/执行分层；元数据调用 | 全部六类快照与基线逐项一致；主组 377 passed / 1 failed（迁移时漏掉生成器的常量引用，已修复）；修复后含失败组的 90 passed；补充设备/邮件/QQ空间/浏览器/schema 47 passed；无 skip |
| B | owner_chat service / prepare / generation / delivery；Router 保留同对象 API | 六类快照仍一致；入口/取消/锁/流式/失败/清洗组 230 passed；媒体/鉴权/开发入口/外部 companion/调度组 159 passed；0 failed / 0 skipped |
| C | Dream 功能路由 | 待执行 |
| D | Admin/i18n | 待执行 |

A 补充：新增 1 项旧 spec 整体替换的入口回归，验证可信 uid/char 注入、空角色
拒绝、伪造主体不触发读取。临时撤销 builtin adapter 回退时，业务成功断言失败；
恢复后通过（`.tmp/ar01-adapter-mutation.log`）。

B 的阶段参数是单轮冻结值对象，不创建队列、缓存或第二条 Pipeline。旧 Router 的
`run_owner_chat_turn` 和 `_execute_owner_chat_turn_locked` 是同对象导出；
`_probe_and_execute_tools` 的旧测试/本地扩展注入点以一次惰性导入保持。
admission/prepare/generate/stream/commit/deliver 的 debug 日志只加阶段、已有
request_id 和异常类型，不包含请求正文。media Router 留在原文件：其上传/下载与
会话准入共享 helper，额外切文件没有本阶段的净收益。

## 删除 brief 候选（本轮不执行）

1. 已返回 410 的管理面 `/chat` 后面的不可达实现，连同其专属旧变量/文档，
   需单独核实兼容消费者后删除；保留 410 路径合同。
2. 新旧 Admin UI 的 Legacy adapter，仅在用户完成新版验收并另行授权后，
   同步删除旧分支、守卫、测试和文档，不复制新版 DOM。
3. 旧 facade 的私有 helper 导出，在测试/本地扩展都迁至所属模块后再逐项退役；
   当前不得直接删除或变成两份实现。
