# Reality 聊天控制实施记录（2026-11-27）

## A：状态/协议底座

current：新增 `/conversation/executions`、`/conversation/requests/{request_id}`、`/conversation/mutations` 只读接口，Bearer chat + Reality session；owner/character 由 grant 决定。请求查询隔离 credential，活动执行允许同 owner/character 的另一客户端发现。输出 `Cache-Control: no-store`，mutation 仅含 ID/状态，不含 prompt、正文、credential。

存储为 `DataPaths.conversation_control(owner, char_id=...)` 独立 SQLite，注册 canonical/reality/per_char_user；在既有 backup inventory 的 data protection root 内，不另行备份生产数据。每条执行包含不可变 execution_id、原始 request_id、credential hash、digest、state、turn_id、时间、boot 身份。重启读到旧 boot 的非终态标为 interrupted，永不自动重跑。控制库必须由同一个 runtime 独占，与当前单实例运行约束一致；不支持两个后端同时共享目录。

状态：queued → running → committing → completed；queued/running → stop_requested → stopped；失败 failed，进程中断 interrupted。SQLite IMMEDIATE 事务决定 stop/commit 顺序；commit 已赢时 stop 不能覆盖完成。A 不接模型/前端按钮，B 才接真实执行与取消。未实现的写接口不注册，避免假功能。

mutation revision 独立于手机 ACK/队列 seq/history_index；按 scope 升序分页，客户端取 cursor 并在 has_more 时继续。当前未裁剪 mutation，故不返回伪造的 reset_required。operation/recovery 数据结构在 C 单扩展，不把 active 元数据冒充回收快照。

### 调用链核查

- 手机 `/mobile/chat` → `admin.session_requests.run_session_request` → `session_scope.execute_request`（进程内去重，shield）→ `run_owner_chat_turn`。
- 桌面带 `X-Presence-Chat-Request` 的请求 → `_execute_desktop_request` → `owner_turn_service.execute_receipted_turn`（持久 receipt，shield）→ `run_owner_chat_turn`。不带该 header 的兼容请求走 session_request。两种 receipt 不得混用。
- 上传入口也使用上述两种适配，停止需要包裹真正的 executor，而不是取消 HTTP 外壳；旧 receipt 查询要认识 stopped，不能将其当 completed 正文重放。
- owner_chat.service 在 conversation_lock(uid) 下 prepare → generation → delivery；prepare 有 probe/context 两个子任务；generation 的 streaming 与非 streaming/tool-loop 都必须取消。
- delivery → turn_sink → TurnEffects.capture_critical；turn_id 先用 EventContext 已冻结值，否则生成 owner+毫秒 ID；capture_turn 写短期记录和含 turn_id 的 event_log，再进 event ledger。不能拿 WS msg_id 当此 ID。
- capture_critical 会另起 event_log 向量 upsert、失败时 enqueue capture_turn_retry；send 后 run_deferred_effects 会启动 LLM/情绪/slow queue。C 必须按来源 ID 防止延迟写回，而非只删眼前磁盘文件。
- 日期日志经 chat_log 解析，再从 event ledger、mobile_finals 补正文/样式/ID；tombstone 没有消除原始日志 fallback。手机历史是 event_store 中的 mobile_finals 投影，其触发器删投影但不会在 restore 时补建。
- short_term._turn_id 将分段归组；有可靠 turn_id 的 user/assistant/主动 assistant-only 才能精确删除；legacy raw 无法唯一归组时禁用。跨日以 canonical ID 和原始时间为准，不按当前日期推断。
- 手机 realtime final-only；新增状态/mutation 应为独立控制类型。桌面 WS 与手机订阅的控制帧、客户端缓存过滤和 cursor 在 D 施工，不声称已经实时。

### 证据与边界

- 支持环境：项目 Python 3.12.13，隔离 sandbox，未调用真实模型/生产数据。
- `pytest tests/test_conversation_control.py tests/test_session_scope.py tests/test_session_scope_cutover.py -q`：76 passed。
- 真实第二进程读取同一测试目录，验证 interrupted；挂载 FastAPI Router 走 Bearer/session，验证跨角色隔离、错误 token 拒绝、metadata-only 分页。
- 故障注入：临时允许 stop_requested → committing，关键用例按预期在 DID NOT RAISE 业务断言失败，恢复 guard 后通过。
- 首次误加当前环境没有的 `--no-cov`，pytest 参数拒绝；去掉后执行上述验证，不算测试失败或 PASS。
- 桌面调查 SHA：139d0bceff94b322f2f193ab79f38c14494d5453；手机：6aedfe834ed3853d53901bf48ddcf14b93d6c112。A 尚无客户端改动，不构成三仓联调通过；端到端、浏览器、设备、真实 provider 均未验收。

## B：真正停止执行

current：手机与兼容桌面 session_request、桌面持久 receipt executor 均接入控制层，不更改断线不取消的 shield 语义。新增 `POST /conversation/requests/{request_id}/stop`（本 credential，支持先到取消意图）和 `POST /conversation/executions/{execution_id}/stop`（同 owner/character 跨端精确取消）。queued/running 可停，committing/completed 不改终态，重复 stop 不打断 finally 清理。

owner-chat 在取得对话锁时转 running，在唯一 turn_sink 提交前转 committing。停止取消真正 executor 并等待子任务退出；prepare 原有 sibling 清理保留，流式 generator、Chat Completions/Responses SDK stream 显式 close/aclose。CancelledError 不走普通异常重试/fallback。工具实际副作用不会被停止撤销，上游是否立即停算/计费不作承诺。现有 tool loop 总预算/步数、provider category timeout/max_tokens 仍有效，未新增运营开关。

停止记录独立存于控制库 stopped_turns，保留用户可见输入、已过滤且二次清洗的可见前缀、媒体引用和 canonical stopped ID；不调用 turn_sink、不写 short_term/向量/长期记忆。日期历史与日期发现支持该记录，响应含 state=stopped/incomplete；前端标签和双端控制帧仍待 D。手机 final-only 通道不新增 token，收到停止状态后由日期历史补展示。

验证：Python 3.12.13，`pytest tests/test_conversation_control.py tests/test_session_scope.py tests/test_session_scope_cutover.py tests/test_llm_protocol_responses.py tests/test_llm_force_stream.py tests/test_final_p1_blockers.py::TestFullRouteAuthInventory -q`：200 passed。另含 anti-collapse 组共 147 passed。新增真实 owner pipeline 的 desktop/mobile 两分支取消、shield 后断线仍可停止、先 stop 后 POST、跨端执行 ID 授权及 SDK close 回归。无真实 provider、设备或 UI 实测。

A 提交 CI：offline-evals 通过；Tests 失败。其中新 Router 的鉴权 inventory 因只在子依赖声明 scope 被静态门禁报错，本单在 Router 直接声明 chat 并定向验证通过；其余多项为已有角色 fixture/生成能力测试前置条件，未夹带修复。B 的 CI 待当前提交检查，不用本地结果冒充 CI。

### B CI 修正

B 的远端检查抓到三个本单问题：Python 3.10 没有 Task.cancelling；generation 的 locals() 清理写法触发架构守卫；新 Pipeline 测试替身缺少已提交版本仍会读取的 character 能力，本地并行重构掩盖了该缺项。修正为执行 ID 级取消发送集合、显式初始化 stream source、给替身建立完整前置条件。未改/提交其他任务的 prepare 重构；不将受脏工作区影响的结果冒充干净提交 CI 结果。

定向复核：control + architecture 共 207 passed、1 failed；唯一失败为并行未提交的 coplay_watch 行号变动导致 retained-registry 清单不一致，未改该文件或批准清单。真正的 dependency direction/namespace 检查与新增控制回归通过。


## C：精确删除与独立恢复库（2026-11-27）

- `conversation_control.sqlite3` 独立于事件库与向量库，归属已有受保护的 per-character/user memory 根。删除档案包含精确轮次快照、派生实体补丁和操作状态；不存入普通聊天上下文。
- 删除先持久化档案，再持久化隐藏栅栏，再处理 history、长消息、日记块、事件正文、mobile finals、队列和向量。中途失败返回 `repair_pending`，读侧与迟到写入继续受栅栏保护；桌面恢复权限支持 repair 重试。
- 恢复使用实体级 CAS 和相邻轮次锚点，保留后续无关写入；冲突返回 409。恢复中途失败仍隐藏，原操作号可重试。不会重放模型、工具、通知或改变 mobile ACK/seq。
- `chat.recovery` 仅加入 desktop profile，mobile profile 只能删除；共享管理员凭据天然拥有管理权限，不能把客户端型号当安全边界。
- 精确关联的 mid_term、episodic、storyline 结论会失效；混合来源结论整体退出召回，其他证据保留。旧 identity/facts/情绪等没有可靠来源链的数据列入人工复核，不做文字匹配删除。Dossier 删除按现有 evidence invalidation 合同；恢复只还原源证据，不复用旧结论，标记 `recompute_required`。向量恢复标记 `index_pending`，由既有重建机制重算，不调用模型伪造旧向量。
- 默认不物理清除 archive；恢复记录本身也保留审计。不授权生产迁移、部署或全库清理。
- 定向验证：Python 3.12，控制/恢复、事件账本与查询、向量、失效链、日记、中期、storyline、scope、short_term 共 166 passed（补充重启用例另记）。新增权限/失败保护均由挂载路由或真实持久化入口观察；不使用生产数据。


## D：双端交互与实时校准

- desktop：消息气泡右键删除整轮，确认后请求；停止按钮控制后端真实 executor；已删除对话列表/详情/恢复/修复仅此端提供。shared/api → allowlisted Tauri IPC → no-proxy/session_request，无 fetch 旁路。
- mobile：canonical turn 长按菜单提供删除确认，旧的无身份气泡不猜测删除；聊天输入上方停止按钮可控制同角色其他设备的活动回复。恢复数据通过 state/history 自动出现，不给手机提供恢复入口。
- 控制 WS 是无正文 revision 提示；state 全快照 + 2s 前台兜底补校准，独立于原 mobile final cursor/ACK。所有迟到写入和已加载页都走持久隐藏规则；恢复重读可见旧日页，保留滚动上下文。
- 取消与 HTTP waiter、provider stream 资源所有权分开；点击 stop 后仍可能在上游计费/缓冲中产生少量 token，不宣称撤回既成工具动作。
- 当前相关后端定向回归 58 passed；desktop 全部 Vitest 121 files / 662 tests passed、build passed（仅已有 chunk size 提醒）、cargo check passed；mobile 定向 88 passed（control HTTP、recovery upload、outbox deadline、interaction、realtime）。后续改动还需复核。
- 浏览器隔离合成数据页已实点 stop → 右键/确认 → 删除列表/详情 → restore → 原气泡恢复；不使用真实凭据或模型。仅说明真实组件的可见交互，不冒充完整 native Tauri/实体手机/生产端到端验收。
- C 远端 CI：offline-evals success，full pytest 16 failed / 9014 passed（3.12）。本单两项是新接口未声明客户端 UI 白名单、原 receipt 测试把模型 finished 当持久回执 finished：本阶段补消费者说明，并按真实可观测回执做有界等待，最终断言不减弱。其余角色资产 fixture/character 依赖失败属于并行架构收口，未混入本单修复。
- 删除 brief 候选：待新旧请求回执收口验收后，移除双套 executor receipt 适配与旧无 turn_id 文本去重；本次不删除、不扩展重构。


D 最终后端复核：control/recovery/desktop receipts/mobile realtime/Admin 消费者清单/session cutover/scopes/data registry/Responses 协议，502 passed（Python 3.12）。补充独立解释器栅栏与权限验证 76 passed。桌面最终全套和手机旧日恢复回归以各自交付文档/SHA 为准，不把本地脏工作区全量当干净 CI。


### D 收尾：停止竞态、离线本地未确认副本

新增 `withdrawn_requests` 元信息，按 execution canonical turn 与归档 final/stopped 的精确 request_id 关联，防止另一设备删除时，离线客户端仅有 request_id 的旧未知气泡在重开后残留；不是按文字推断。恢复后该集合随 visibility 消失。客户端同时结束已停止请求的本机等待/未完成凭据，不补发模型请求；迟到 stream-start 也不重新开气泡。

停止意图恰在 preliminary lookup 与 admit 之间到达时，以 admit 返回的 stopped 状态为准，不创建 executor。已删除停止记录不允许通过原请求重放重建。撤销这条 admission 守卫后，新回归真实失败 `stopped admission executed provider`；还原后 1 passed。手机撤销旧日重读后，新用例在可见 restored answer 断言失败；还原后通过。最终后端相关组 503 passed。

`e9267750862089f82e9776f21447161599880ba7` 干净远端 CI：offline-evals success；Python 3.10/3.12 full pytest 均 9018 passed、14 failed、71 skipped（另 13 subtests passed）。本单先前 route coverage 与 receipt 时序失败已消失；剩余失败为角色资产 fixture/loaded character/name 能力前置条件，未修改无关生产逻辑或降低断言。


## E：三仓实现交付与剩余验收（2026-11-27）

### 代码组合

- Backend 功能提交：`6f5c0eca239f171ed29cf334864a53cb4f4dc139`（A/B/C/D 独立提交均已 push 到 main）。本节为后续纯文档交付，不代表生产已经升级。
- Desktop：`9569de6535c5bd98d438afc6195f4144af92ef58`（前序 `df28376a70e53346da500a6ef591c9d99ee46fe1`）。右键“删除本轮”二次确认、单次停止当前一个 execution/未准入 request、电脑专有删除档案列表/详情/恢复/repair；新的删除操作在确认成功后结束重试身份，允许同一次应用会话内恢复后再次删除。
- Mobile：`0585b768fc1fa3d983f63e5e16856a72c804d4fd`（前序 `08104877085a2d125ebe1a7fa0a54b5c7f349b01`）。长按 canonical 气泡/媒体卡片删除、停止按钮、独立状态轮询与实时提示；没有恢复 API/UI，不更改原 final-only 通道为 token streaming。
- compatibility：上述组合的各仓定向/构建和合成入口验证通过；三端真实服务、native 窗口、实体手机同时在线端到端组合为 `not_verified`。旧客户端没有新增控件；自定义 scopes 不会自动增加恢复权限。

### 最终证据

- Backend Python 3.12：control/recovery/desktop receipts/mobile realtime/Admin route consumers/session cutover/scopes/data registry/Responses 协议相关组 **503 passed**；覆盖独立进程栅栏、归档失败、精确 lineage、恢复冲突、跨 scope 拒绝、真实 executor 取消与 stopped admission 竞态。不使用生产数据。
- Desktop：`npm run build` passed（已有 chunk size 提醒）；全套 Vitest **121 files / 664 tests passed**；Rust `cargo check --manifest-path src-tauri/Cargo.toml` passed。最终取消/删除/恢复/再次删除已用真实 ConversationControls 组件在隔离浏览器实点；仅替换 auth/网络/角色边界，合成内容无真实 credential，不等于 Tauri 原生端到端。
- Mobile：`flutter analyze --no-pub` **No issues found**；`flutter test --no-pub test/conversation_control_test.dart test/chat_recovery_upload_test.dart test/mobile_outbox_deadline_test.dart test/chat_interaction_regression_test.dart test/mobile_realtime_test.dart` **92 passed**；`flutter build apk --debug --flavor dev --no-pub` passed，未安装、未签发正式包。
- 故障注入：移除 delete 成功后的操作号释放，真实 loopback HTTP 的“恢复后第二次删除”会因旧幂等结果而失败；恢复修复后同一请求/可见状态回归通过。其他 admission/旧日恢复负向证据见 D。
- 干净远端 Backend 功能 SHA `6f5c0eca239f171ed29cf334864a53cb4f4dc139`：offline-evals **success**；Python 3.10/3.12 均 **9019 passed / 14 failed / 71 skipped / 13 subtests passed**。14 个失败仍为 autonomy intentions、hardware/imagegen/self tools、run_llm_retry 的角色资产/loaded character/name 前置条件；未混入本次修复，完整 CI 仍红，不能以 503 定向通过代替。
- Desktop 前序 SHA `df28376a70e53346da500a6ef591c9d99ee46fe1` 与 Mobile 前序 SHA `08104877085a2d125ebe1a7fa0a54b5c7f349b01` 普通 CI 已 success；最新补丁 CI 另行核对，不把前序绿灯移作当前证据。

### 保留边界与 open

- 删除范围是精确 canonical round 与可靠来源关联的证据/派生投影；无 lineage 的旧 identity/facts/情绪不猜测删除，列 manual_review。恢复向量为 index_pending、Dossier 旧结论 recompute_required，不承诺立即还原旧结论。
- 停止不撤销已经执行的外部工具，不保证上游立即停止计费；committing/completed 不改写为 stopped，不清空后续排队输入。请求失败不假装删除成功。
- 尚未完成：真实模型无休止符场景、原生桌面右键/手机真机长按、双设备跨端停止/删除/恢复、弱网/后台/进程重启实测。自动化和隔离浏览器仅覆盖各自范围。
- 未生产部署、未操作生产聊天记录、未安装 APK、未发布桌面/正式 APK。未额外清理旧分支/brief/legacy；删除候选见原工单。其他并行未提交代码与原手机插件生成文件保持原样。
