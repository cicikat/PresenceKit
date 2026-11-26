# DIARY-01 / T4 后续收口与最终验收记录

日期：2026-11-26。状态：代码已合并，整体验收 **partial / not_verified**。
同日后续授权：用户要求部署到 VPS，已完成后端程序切换，详情见文末部署记录。
下文“没有部署”描述本报告原交付检查点，不能据此判断后续 VPS 状态。
用户最新指令覆盖原工单的 Draft、测试绿灯、设备及独立审查合并门禁；不覆盖如实记录证据的要求。
本轮没有生产部署、生产数据测试、APK 或桌面正式发布。T3.2、T5–T9、HINT 未施工。

## 合并事实与精确源码组合

| 仓库 | 原 T4 PR 最后 head | 原 PR 合并提交 | 本轮合同请求时 main |
|---|---|---|---|
| backend #38 | `a4360b97397647c8143754cb5af89baa817e54db` | `7409e3d6b0575ce07f523ee831602a66e7b87c5f` | `bcc7da2a96af426f86f34f2060b764e4e5795331` |
| desktop #14 | `c497124a8876204ba1c08d2d53af137084e61ff1` | `c598e5ac8b0a8ec5a814148b631315d6c5aff5f3` | `895f3e2081ade604a3770ed08da0f44dd2a40850` |
| mobile #21 | `706fe530ce7e6bfd31624e82a663c32435aeda6d` | `3a5d88d0528b308889790cdd3844db6d2a9a1a9b` | `6439fcea5ea3b964b6e8c4a8674ba30c89ffd2f3` |

原三仓先合并手机、桌面，再合并后端。默认提交/push/合并规则随后通过 backend #39、desktop #15、
mobile #22 同步。日记故障补测独立为 backend #40（head `9f64346`），媒体修补独立为 #41
（head `0748d7b9ce09159e393e0656a18ff17621a874f5`）。桌面和手机本轮集成文档 head 分别为
`8982a02`、`a22fb0f`，均已合并。表格记录合同派发时的 main，后续证据文档提交不能自动继承新的精确 SHA 结论。

T3.1/T3.1.1 已由 #37 合并 `d2fe511200b90f55e6dbead72e2a62608b238829`，过期状态已校准。
T3.2 仍保留：TurnSink 显式角色一致性、LoreEngine 快照、Tool Schema 间接 Registry、宽泛 TypeError
兼容、Pipeline 旧属性代理五项。T5–T9 与 HINT 继续未开始。

## 实际修改、所有者与依赖

- DIARY-01 原修复已存在，未重复施工；本轮补真实 lease 占用及日记写入失败/退避恢复用例。
  唯一生成/存储和 Task/Work Session 所有权不变，见 [日记报告](diary-01-delivery.md)。
- T4 原 11 个旧测试已迁移真实 grant，未恢复 `_owner_qq` 或无 Session fallback。
  手机组合根已用正式方法引用减行，未提高结构门槛；本轮无额外 Flutter 运行时改动。
- 验收发现 inbox 全局字节被其他角色持摘要读取、生成图 ID lookup 覆盖调用者身份两处漏洞，
  本轮修补 `core/chat_media.py`、`core/image_gen.py`，补实际 HTTP/引用回归，详见 [媒体报告](t4-media-scope-followup.md)。
- 无删除文件；无新增 facade/adapter/bridge/wrapper、静默 fallback、白名单例外、重复业务状态或仅为旧测试服务的兼容实现。
  未更改 HTTP schema、配置字段、数据布局、scope 权限或客户端设置；无需管理面静态资源升级。
  所有实际媒体消费者已传 uid/char_id，工具与 HTTP 共用正式授权证据；无未迁移调用者。
- 其余修改是三仓状态、集成说明、迁移清单和 matrix；不借此删除 T3.2/T5–T9 旧入口。
  后续删除候选仍须各自工单的真实调用图、迁移与数据条件，本轮不授权清理。

## 自动检查与 CI

| 检查 | 实际证据 |
|---|---|
| 日记定向，Windows Python 3.12.13 | `python -m pytest tests/test_inner_diary_write.py tests/test_diary_backfill.py -q --tb=short`，53 passed |
| 媒体/产物/参考隔离，Python 3.12.13 | `python -m pytest tests/test_session_scope_cutover.py tests/test_chat_media.py tests/test_chat_artifacts.py tests/test_image_gen_reference.py -q --tb=short`，103 passed；修补前有实际越权失败 |
| 依赖/路径/schema 守门 | `python -m pytest tests/test_ar02_dependency_directions.py tests/test_memory_direct_path_lint.py tests/test_tool_schema_portable.py -q --tb=short`，229 passed |
| 本轮本地 Smoke | Windows / Python 3.12.13，`python -m pytest -n auto -m smoke --junitxml=.tmp/t4-02-smoke.xml -q --tb=short`，450 passed（170.75s），失败清单为空 |
| 矩阵/换行 | `python -m pytest tests/test_protocol_matrix.py tests/test_line_endings.py -q --tb=short`，3 passed；本轮新增文档 LF、相对链接与 diff 检查通过 |
| backend main `bcc7da2…` | [Tests](https://github.com/cicikat/PresenceKit/actions/runs/45746570095)：3.10/3.12 full、Architecture、Browser 全部 success；[Offline](https://github.com/cicikat/PresenceKit/actions/runs/45746570076) success |
| backend JUnit | 两版本各 parallel XML 9072 tests / 71 skipped / 0 failures / 0 errors；各 serial 77 tests / 0 skipped / 0 failures / 0 errors。71 项分类为跨仓 66、Windows-only 5；跨仓项在固定 SHA 三仓合同另外执行，Windows-only 不由 Linux CI 代验。不得把 XML tests 数直接当成 passed 数。失败清单为空 |
| desktop main `895f3e2…` | [CI](https://github.com/cicikat/PresenceKit-desktop/actions/runs/45747427964) frontend、Windows/macOS Rust success；此前功能源码完整 Vitest 653 tests、TypeScript/Vite build 通过 |
| mobile 功能源码 `706fe53…` | 既有完整 Flutter 479 passed、analyze 无问题、dev APK build 通过；未重做等价修补 |
| mobile main `07033f5…` | [Flutter/native CI](https://github.com/cicikat/PresenceKit-mobile/actions/runs/45744803953)、[Android instrumented](https://github.com/cicikat/PresenceKit-mobile/actions/runs/45744804032) success；不能当成物理手机 |
| mobile main `6439fce…` | [Flutter/native CI](https://github.com/cicikat/PresenceKit-mobile/actions/runs/45747410308) success；该 SHA 的 Android instrumented 另行跟进，不当成物理手机 |
| 本轮固定 SHA 合同 | [run 45747493358](https://github.com/cicikat/PresenceKit/actions/runs/45747493358) success；机器 evidence 的 requested/actual 三仓 SHA 全部等于上表，三端结果均 success；matrix compatible 仅指合同 |

JUnit 与隔离运行日志保存在本机 ignored `.tmp/t4-02-ci-media`、`.tmp/t4-02-live-runtime`。
只读摘要包含合成身份/请求结果，正式凭据和私人配置未入库；临时服务已停止，原客户端配置逐字节恢复。

## 桌面实际窗口与 HTTP 证据

Windows Tauri debug 窗口使用 desktop `c497124…` 运行时代码；后续合并只改文档，运行时代码 diff 为空。
隔离 localhost 服务与公共角色 A/B；通过正式 capture_turn、artifact、reasoning 存储入口预置样本。
这是合成历史/产物的真实读取验收，不是模型生成质量或生产内容验收。

| 工单项目 | 实际操作、请求与可见结果 |
|---|---|
| A 绑定、普通聊天、附件 | 2026-11-25 已实际完成，HTTP `/desktop/chat`、`/upload/ingest` 与历史均带 A grant；模型回复读出合成附件词。上游辅助鉴权失败仍单列，不宣称全模型健康 |
| 历史、思考 | 本轮开窗显示 A 样本历史；展开思考显示合成 monologue；`/chat/turns/t4-02-seed-a/reasoning` 200，同 A grant |
| 产物预览/下载 | 实点文本预览显示样本 Markdown；实点图片预览打开 modal（1×1 PNG，不能代表大图观感）。两种产物预览、下载均 200；系统保存对话框保存成功，文本内容一致，PNG SHA-256 与源一致 |
| B 隔离 | 实际选择 Fixture Peer，A 气泡/产物消失；正常键盘选项绕开原生 popup 自动化点击限制，未绕过目标校验。同窗口实际 B grant 补只读 HTTP 核对：A 媒体 410、产物预览/下载 404、显式 A 历史 403；A 思考 200 但 entries=[]、available=false。相同 A grant 的对应读取均 200 |
| 断网/恢复 | 停止隔离后端后发送合成样本；恢复后只查询原回执，没有 `/desktop/chat` POST 自动重发。服务重启初始化的角色变更另触发正常角色绑定，不能把该次 create 解释为旧请求重放 |
| 同角色后端重启 | 保持 A 后再次重启隔离后端；发送新样本返回 404 `session_not_found`，窗口显示“暂时无法连接当前角色。请重新打开聊天窗口后再试。”；没有自动 create/重发 |
| 关闭/重开 | 关闭失效窗口再启动，新增 create 201，A history dates/day 200，原样本历史可见；被拒绝请求没有自动重发 |

HTTP audit 记录 path/status、是否带 grant、合成 owner/角色和 request_id；本轮思考、产物成功读取均携带
冻结 A grant，B 读取使用另一个实际 UI grant。原始 grant 不写入本报告。
回执 GET 用于未知结果核对，不属于生成请求重放。角色 B 对 A 的真实已完成聊天回执隔离仍由既有自动
测试覆盖，本轮没有额外预置跨角色 completed receipt 进行 UI 攻击；该细分人工场景未单独执行。

## 合同逐项归属与验证入口

| 合同 | 实现所有者/消费者 | 对应验证 |
|---|---|---|
| discover/create/hold/失效；无 refresh/destroy API | backend session_scope、桌面 RealitySessions、手机 DeviceController | backend `test_session_scope.py`、`test_session_scope_cutover.py`；desktop `src/shared/api/sessionScope.test.ts`；mobile 现有 session 用例 |
| Header、owner/char/domain、错误码 | backend session_requests、三端 HTTP transport | 三仓 protocol fixtures；backend scope cutover；桌面本轮旧 grant 拒绝与 A/B HTTP |
| request_id、msg_id/turn_id、持久回执 | backend owner_turn/session_scope、桌面回执与手机 outbox | `test_dedupe_key_stability.py`、`test_mobile_realtime.py`；desktop chatReceiptPolling；mobile chat recovery/outbox/protocol |
| media/attachments、artifact preview/download | backend scoped upload/ledger/artifact；桌面 IPC，手机 BackendClient | `test_chat_media.py`、`test_chat_artifacts.py`、`test_image_gen_reference.py`；本轮桌面预览下载和 B 拒绝 |
| history/reasoning | backend chat_log/reasoning；双端读取 | `test_event_log_known_violations_fixed.py`、`test_reasoning_turn.py`；本轮桌面真实窗口 |
| transcription/voice receipts | backend scoped transcription/receipt；桌面/手机录音消费者 | scope cutover 和双端既有语音/迟到结果回归；本轮手机物理录音 NOT RUN |
| 角色/连接/过期/重启与未知结果 | 三端会话和请求生命周期 | scope cutover、桌面 sessionScope/authGate/chatReceiptPolling、手机聊天恢复与 stale generation 回归；桌面实际重启/重开 |
| owner 共享 poll/ACK 游标 | backend mobile queue、手机 durable inbox | `test_mobile_realtime.py`、三仓 protocol fixtures、手机 protocol/ACK 用例；本轮物理手机 NOT RUN |

固定组合命令：backend `python -m pytest -n 4 -q -ra tests/test_protocol_fixtures.py tests/test_activity_contract.py`；
desktop `npm test -- --run tests/protocol-fixtures.test.ts`；mobile `flutter test test/protocol_fixtures_test.dart`。
逐项自动测试覆盖与上述真实窗口证据组合使用，不能把 fixture 合同声明为每个完整端到端场景均已执行。

## 未完成项与下一步

- **手机真机十项全部 NOT RUN**：本轮多次 `adb devices` 为空；用户确认有测试机，已请求连接及 USB 调试授权。
  未安装本轮 dev 包。待设备可用后按原工单第 9.2 节逐项验收，包含普通聊天、切换、录音转写/语音回执、
  前后台、过期、重启、断网、不重绑不重放、迟到响应隔离、owner 共享 poll/ACK。
- **独立审查未记录**：由用户协调 ChatGPT，Codex 不代签。按用户指令已合并，仍不能把审查记为通过。
- **桌面细分人工场景**：A 已完成回执的 B 攻击、真实模型生成大图和大附件完整观感未在本轮重做；
  已有隔离测试不替代这些人工证据。整体 matrix 必须保留 not_verified。
- 无生产部署或正式发布授权；旧无 Session 客户端不能直接搭配强制 Session 后端。

## 同日后续：用户授权 VPS 部署

用户在原报告交付后明确要求先部署 VPS，以恢复前端使用。已部署后端 main
`3aefc4031c9f2027d740a6c3431b98e79df96de6` 的 113 个变化程序文件。
基于实际线上文件哈希和上次 P2 清单比对，保留历史记忆、模型和管理面既有定制；
历史记忆查询接线迁到 `RealityContextReader`，没有恢复已删除的 Pipeline 门面。
来源 SHA 与服务器 overlay 差异分别记录在部署 manifest，不能把定制安装声称为未经修改的 Git tree。

候选使用公共资产和隔离数据验证：Session/日记/媒体/图像参考/思考 189 passed，
上下文与思考 47 passed，历史查询的 frozen scope、seed/none/群聊闸门及 prompt 接线 3 passed。
未执行生产聊天、模型故障注入或私有记忆检索测试；没有配置或数据迁移。
按工作入口规则没有默认创建新数据备份，仅保存本次覆盖程序文件和校验清单用于回滚。

停止服务到健康恢复 8.16 秒；独立复核：服务 active/running、NRestarts=0，健康 200，
无凭据健康 401，能力发现 session_scope=v1，Session create 201 且角色匹配；
无 Session 的聊天和历史入口均 422 session_scope_required，未执行测试消息。
所有部署程序哈希、私有配置及历史模块保护哈希匹配；Traceback/ImportError/
ModuleNotFoundError/SyntaxError/startup failed 五类启动错误计数均为 0。

部署与独立验证记录保存在本机服务运维目录；服务器 stage 保留程序回滚副本，
失败恢复不会自动回滚用户数据。本次只部署后端，没有发布 APK、桌面安装包或 GitHub Release。
前端应重新打开聊天窗口以签发新 Session。手机真机、独立审查及原人工缺口仍未关闭。

## T4.1 A 后端语音回执修补（未部署）

2026-11-26 后续工单 A：`core/audio_perception.py` 原先先 pop 再检查 scope；
新用例在旧实现实际失败四次：错误 owner/角色/通道销毁合法回执，错误 domain 甚至取得 payload。
现在先核验 owner/角色/domain/world/通道与有效期，再原子 pop。没有 await 或网络临界区，
并发合法消费者只有一个获得 payload。过期清理、合法 scope 的改文/分析关闭销毁、多段与 marker-only
沿用原安全语义。Session grant ID 仍不作为回执业务主键，同 frozen scope 更换 grant 不改变归属。
QQ 使用直接 impression，未经过此一次性 HTTP 回执缓存；无显式 scope 的原路径仍解析 Reality 身份。

`python -m pytest -n 4 tests/test_audio_perception.py tests/test_a3_voice_marking.py
tests/test_session_scope_cutover.py tests/test_mobile_chat_channel.py tests/test_ar02_dependency_directions.py
-q --tb=short`：Windows / Python 3.12.13，335 passed（48.32s）。
没有新增 API、配置、持久数据、facade、兼容 fallback 或第二份所有者；手机修补单独交付。
本项尚未生产部署，不把此前 VPS 部署证据转记为本补丁已上线。
