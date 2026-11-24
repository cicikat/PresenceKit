# T4 Session Scope：本地施工检查点

2026-11-24；状态：partial。用户要求先提交当前施工结果；本记录不代表 T4 全部验收通过。

## 实际修改与依赖

- 桌面共享 HTTP 入口按角色及连接持有服务端 grant，Rust 桥校验连接身份并携带 Session；聊天、回执、上传、转写、wake、历史、思考、产物和摄像头调用接入同一机制。
- 后端会话请求帮助函数强制要求 `X-Presence-Session`，相关聊天、媒体、产物、历史和转写入口删除隐式 active-character 分支。上传删除 `read_character` 兼容注入。
- 产物只能在 grant 的 owner/角色目录中读取，持有产物 ID 不再授予跨角色读权。语音回执签发与消费传入冻结 scope。
- 桌面持久请求回执保留；键加入 owner/角色/domain，写回 canonical 角色，已知角色的回放不扫描其他角色。
- 手机删除会话失效后自动重绑重发，转写传入 grant；连接或角色变化时不采用旧转写结果。共享 mobile poll/ACK 游标不变。
- 唯一会话授权所有者仍为 `core.session_scope`；HTTP 错误映射在 `admin/session_requests.py`。未新增全局业务状态所有者。
- 原消费者为桌面 Rust HTTP 桥与手机 BackendClient。版本化 owner-input、Dream、Stage 及管理面独立观测不是本次 Reality grant 协议切换对象。

## 本地验证

Windows；后端 Python 3.12.13。全部使用公共测试资产与隔离可写目录。

| 验证 | 证据 |
|---|---|
| Session、媒体、历史、产物、桌面回执、音频回归 | 186 passed |
| wake、calendar、手机聊天、文档上传、Dream 闸门、协议 fixture 等 | 197 passed |
| 架构、换行、鉴权、video-call、通道设置等 | 326 passed |
| 最后一次回执与 Session/owner-turn 复查 | 67 passed；包含最终冻结回执角色改动 |
| 桌面定向 Vitest | 20 passed；`authGate`、`sessionScope`、`backend`、`chatArtifacts`、`turnReasoning` |
| Rust | `cargo check` 与 `cargo build` 通过 |
| 手机定向 Flutter | 134 passed；含 session、backend request、profile、chat recovery、outbox deadline、chat interaction、protocol fixture |
| 手机静态分析 | `flutter analyze --no-pub`：No issues found |
| 实际桌面操作 | 隔离后端启动，公共角色真实模型对话显示回复；HTTP 审计确认 `/desktop/chat` 和历史请求携带 Session |

上表多轮测试有重叠，不累计为唯一测试数量。前端最后一次生产 build 在本次提交前复核。
实际对话中存在上游辅助调用鉴权失败及公共角色 note 警告，不能据回复成功宣称所有模型链路正常。
测试中的原无 Session 前置条件已迁到明确 grant，保留原隔离、脱敏、持久化和错误断言。

## 架构检查

- 新增 facade / adapter / bridge / wrapper：没有新的业务转发层；扩展已有 Rust HTTP 桥及共享请求入口。
- 新增 fallback：没有；已知角色回执只读对应角色。
- 新增静默降级：没有；Session 准备/授权失败停止发送并反馈错误。
- 新增白名单例外：没有。
- 双份业务逻辑：没有新增。
- 未迁移调用者：已检查的桌面与手机相关调用已修改；完整三仓消费者最终审计尚未完成，不能宣称全部穷尽。

## 未完成验收

- [ ] 最终版本桌面窗口重开后的历史恢复、服务端重启后失效反馈，以及完整附件/产物 UI 联验。用户按 Escape 停止 Computer Use，未继续操作；原桌面连接配置已逐字节恢复。
- [ ] 手机真机对话、录音与生命周期联合验收；当前为 Flutter 合同和回归证据。
- [ ] 三仓最终协议 fixture/matrix、文档历史兼容段落清理和完整消费者审计；旧 matrix 不代表本次组合已验收。
- [ ] 当前提交远端 CI、PR、独立审查；本次仅本地提交，未推送、合并或部署。

没有修改 Admin 静态资源、增加运营开关或持久台账；沿用 `/observability/session-scope`，无需新增管理面设置。
