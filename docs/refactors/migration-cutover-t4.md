# T4 Session Scope：最终验收进行中

更新：2026-11-25；状态：partial。原施工代码已推送后端 PR #38、桌面 PR #14、手机 PR #21；
本记录不代表 T4 全部验收通过，也不授权生产部署。下面的 2026-11-24 数字是历史检查点，
本轮证据单列，不能用旧 matrix 或旧 head CI 替代。

## T4-01 本轮基线与结果

| 仓库 | 工单指定且本地核验的完整 SHA |
|---|---|
| backend PR #38 | `3d24c0e077698db6e48a106146e1fdcb107b2302` |
| desktop PR #14 | `c497124a8876204ba1c08d2d53af137084e61ff1` |
| mobile PR #21 | `24b8867b11e43e14f130a3f63c417c3420a9275a` |

该精确组合已重新执行 [三仓 protocol contract](https://github.com/cicikat/PresenceKit/actions/runs/45629662402)，
结果 success。这只证明该组合的协议 provider/consumer 测试，不代表完整 CI 或设备验收。
本轮后端仅迁移旧测试前置条件和验收文档；DIARY-01 使用独立提交，不混入 T4 测试补丁。
手机组合根的等价 Session resolver 方法引用另有配套提交；本轮实际验证组合固定如下。

本轮代码验证组合及 [.github/protocol-matrix.json](../../.github/protocol-matrix.json)：
backend `fcd53df8ad40b2581cc78ef2b911ee637a9f5544`、desktop
`c497124a8876204ba1c08d2d53af137084e61ff1`、mobile
`706fe530ce7e6bfd31624e82a663c32435aeda6d`。
[精确组合合同](https://github.com/cicikat/PresenceKit/actions/runs/45631771422) success；
[后端双版本全量/Smoke/Browser/Architecture](https://github.com/cicikat/PresenceKit/actions/runs/45631732610)
及 [Offline](https://github.com/cicikat/PresenceKit/actions/runs/45631732563) 全部通过。
[手机 Flutter/native CI](https://github.com/cicikat/PresenceKit-mobile/actions/runs/45630398563)
全部通过；桌面 PR #14 当前指定 head 的 frontend、Windows/macOS Rust CI 均通过。
matrix 的 compatible 只指精确组合合同；overall 仍为 not_verified，不能据此合并。
后续文档/矩阵记录提交不自动继承代码组合的精确 SHA 合同结论。

| 本轮验证 | 结果与限度 |
|---|---|
| 指定六个旧测试文件，修复前 | 11 failed / 63 passed；与工单失败清单一致 |
| 迁移真实测试 grant 后同组 | 74 passed（Python 3.12.13，Windows）；HTTP 带 Header，直接 endpoint 显式传参；未恢复 fallback/私有 owner 接口 |
| 后端首次全量 | 9051 passed / 1 failed / 13 subtests passed；唯一失败为 Windows C/D 跨盘 relpath，非 Session 行为；按环境文档改同盘 TEMP 后重跑 |
| 后端同盘 TEMP 全量 | 9052 passed / 13 subtests passed（447.95s，Python 3.12.13）；没有修改跨盘失败的测试或生产实现 |
| 桌面完整 Vitest | 119 files / 653 tests passed；当前指定 desktop SHA |
| 桌面生产 build | TypeScript 与 Vite 均通过；不能替代窗口验收 |
| 手机首次完整 Flutter | 478 passed / 1 failed；T4 新增组合行触发 1499 行结构上限 |
| 手机等价方法引用修补后 | 479 passed；analyze 无问题；不放宽结构/业务断言 |
| 手机 dev APK | 构建通过；独立调试包，非正式发行，不替换生产包 |

首轮远端双版本 full-pytest 在 `test_image_presets.py` 另暴露一个 SDK 夹具漂移：
`APIConnectionError(request=None)` 在当前 CI SDK 内访问 headers 时先产生 AttributeError，
导致原 connection_error 业务断言失败。改为合成 `httpx.Request`，保留原 reason 断言；
本地该文件 26 passed。生产代码未改，远端结果须以修补后的当前 head 为准。

旧 chat-log 测试不再把 active 当授权来源：原桶隔离、内容、日期和拒绝前不得解析路径的断言
继续保留；缺 Session 现在断言 422 `session_scope_required`，显式角色与 grant 不符断言
403 `character_not_authorized`，替代切断前的 503/422 状态。

## 当前真实 UI / 设备证据

- 桌面使用指定 SHA 的真实 Tauri debug 窗口与隔离数据根、公共角色。
  角色 A 的普通聊天显示了回复；HTTP 审计确认 `/desktop/chat`、`/chat-log/dates` 带 Session。
  上游辅助调用出现鉴权失败，不能据最终回复声称全部模型链路健康。
- 真实 UI 选取合成 `session-attachment.txt`，通过 `/upload/ingest` 获得 200，
  审计确认携带 Session；回复正确读出附件内的测试词 violet。当前 grant 的只读观测
  确认为公共测试 owner + 角色 A + reality。尚无角色 B 的 UI 越权读取证据。
- 后端重启后，原窗口发送新测试请求得到带 Session 的 404，界面提示
  “暂时无法连接当前角色。请重新打开聊天窗口后再试。”；没有自动 create 或重放。
  随后关闭并重新打开真实窗口，审计新增一次 create 201、带 Session 的历史 dates/day 200，
  原对话和回复恢复；没有自动重发此前拒绝的请求。
- 思考、产物预览/下载与角色 B 隔离：尚未完成完整 UI 证据，保持 open。
  系统原生角色下拉菜单的点击被 Computer Use 目标窗口检查拒绝，未绕过该限制。
- 手机首次 ADB 可见真实设备，但安装 dev 包前连接消失，安装命令未执行成功。
  等待用户恢复连接；普通聊天、录音/转写/语音回执、角色切换、前后台、过期、
  重启、断网恢复与不重放均为 **NOT RUN**，不能用 Flutter 测试替代。
- 两端 PR 的 reviews 在本轮检查时均为空；独立审查门禁未满足。
- 上述未完成门禁满足前不合并相关 PR，不执行生产部署。T3.2 五项保持独立未完成。

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
- [ ] 最终候选提交远端 CI 与独立审查；原检查点已推送 PR，尚未合并或部署。

没有修改 Admin 静态资源、增加运营开关或持久台账；沿用 `/observability/session-scope`，无需新增管理面设置。
