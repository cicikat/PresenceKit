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
