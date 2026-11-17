# 第一轮可靠性与功能联合交付

状态：施工中；合成验证与真实服务、打包、生产验收分别记录。
审计基线：backend `69769380c262ec3c4e3f1fad5cedb3eda4054e3e`；
desktop `c69b522b1f198e183723c178ee3858a17e5489e0`；
mobile `09809d44fea621a0b329ca6e5faac490b466752f`。

## R2：event_log 幂等

- 基线复现：新增回归中跨回合重复短回复、多天 full_log 重复正文、改名重试共四个业务断言失败。
- 修复：以相邻正文和 metadata 的完整 turn_id、speaker、正文摘要联合判定；角色显示名称不参与身份。
  每个投影独立检查，失败重试仅补缺。读取错误返回失败，不冒充未写入。
- 性能：八文件 LRU 缓存流式构建的指纹集合，正常追加增量更新；外部修改、轮转、截断使缓存失效。
  缓存可重建、不存正文，不新增持久真相源。
- 验证：Python 3.12.13，`pytest -q tests/test_round1_event_log_idempotency.py
  tests/test_event_log_source_tag.py tests/test_event_log_union.py tests/test_p1_memory_fields.py`：33 passed。
- 范围：后端记忆投影内部修复，客户端字段和设置不变；独立进程的半条恢复见下方追加证据。
- 追加跨午夜恢复：实际capture回归先失败（原日期投影不存在）；event_log现接收捕获时的
  occurred_at，恢复写回原日期和分钟，不在重启当天重复投影。相关capture/eventlog/outbox
  与字段回归60项通过（Python3.12）。
- 半条写入失败先行：两个独立进程用例在日文件/full_log写到UTF-8字符中间后os._exit，
  恢复文件仍不可解码。现于既有恢复SQLite暂存追加字节意图，校验位置和尾部前缀后仅补缺；
  fsync完成再清除，外部尾部冲突不截断、不覆盖，脱敏观测保留pending。
  启动恢复最多32个、待修复上限1000；意图不参与聊天检索，完成即清正文。
  日志/source/capture/outbox/janitor定向68项通过（Python3.12），含两种真实进程退出恢复。
- 状态：partially verified（合成 verified；真实模型、打包客户端、生产端到端 not verified）。

## 待完成

- [x] R1：补齐热重载下的回合驻留；合成回归通过，真实模型/生产仍 not verified
- [x] R3：多存储补偿、关键慢任务恢复与观测的合成验证；生产仍 not verified
- [x] R4：主动投递状态、每通道尝试与手机持久收件ACK；真实通道仍 not verified
- [ ] R5：权限文档、调度结果与固定 SHA 合同
- [ ] F1：原生 Gemini 适配、控制面与真实服务
- [ ] F2：双端安全 Markdown 与可见验收
- [ ] F3：手机环境候选、授权、设置和真机验收
- [ ] S5：联合固定 SHA CI、恢复演练、部署与回滚

## R5：调度结果与权限文档（部分）

- 失败先行：`test_post_process_scheduled_reports_actual_background_work` 的四种组合中，
  默认 critical+slow 和禁用 slow 的异步模式两项失败。
- 修复：返回值在真实任务创建或注册后置为 true；禁用调度保持 false。
- 清理 README 双语、tools、security、feature-control-surface 中已退役危险模式与二次确认说明；
  保留现行 owner、角色授权、scope 和设备控制。
- Python 3.12.13：`pytest -q tests/test_turn_sink.py tests/test_llm_failover.py` 67 passed
  （此结果早于新增 R1 热重载回归）。HTTP/WS/mobile 与固定 SHA CI 联验仍待完成。
- 状态：partially verified。无新增配置或客户端协议字段。

## R1：冻结回合路由与连接池租约

- 基线：已有备用接管、工具历史保护、流尾包校验和去重，不重复实现。
  新增热重载回归先失败：下一轮用了 reconfigured-primary，而非已接管的 fallback。
- 修复：每个回合冻结 category/char/显式 preset 的主备上下文；配置重载不改变已开始回合。
  reload 将被回合租用的旧连接池延迟关闭，最后一个回合结束/取消/关闭流后释放。
  新回合继续读取最新配置；不重跑任何工具。
- 验证：Python 3.12.13，`pytest -q tests/test_llm_failover.py
  tests/test_llm_client_reload.py tests/test_tool_loop.py`：118 passed，含 transport failover、
  breaker、取消、工具历史保护、双工具/流尾包与空收尾的既有覆盖。
- 状态：partially verified；真实 provider 与生产端到端 not verified。

## R4：主动投递回执（部分）

- 基线：claim 只记录关联 ID，异常、取消和部分通道失败没有独立投递状态。
  新增四个失败先行用例确认回执缺失。
- 修复：同一 scoped autonomy 状态记录 claimed/attempted/accepted/failed/outcome_unknown；
  包含 run_id、correlation_id、canonical turn_id、persisted 与每通道接受/失败结果。
  不存消息正文或异常文本。claimed/attempted 均不等于送达，accepted 不等于客户端 ACK。
  异常或进程退出保留已消费关联 ID，不自动唤醒或重发。
- 观测：`GET /observability/autonomy-deliveries`，`state.read`，当前 owner/char，最多100条。
  手机沿现有ACK接线，acknowledgement_channels明确mobile=durable_client_inbox、
  desktop=not_available；手机持久收件不表示用户已读，桌面正文仍无收件ACK。
- 验证：Python 3.12.13，投递+autonomy+管理面 39 passed；
  投递/信号/Dream retry 16 passed，包含新增 scoped HTTP 回执读取。
- 追加失败先行：关键落盘await期间撤权、两通道之间撤权、手机正常/抢先ACK未回流、
  进程退出后仍显示attempted。现绑定transport ID后才fanout，逐通道尝试/结果持久化，
  每次实际send前复查门控；手机ACK先写回执再删队列，失败503保留队列，可精确重试ACK。
  终态记录不覆盖抢先ACK；重启将未完成尝试显式显示outcome_unknown且不重发。
  原ACK落盘回归升级为真实HTTP请求，保留失败时队列字节不变与恢复断言。
  Python3.12投递/ACK/门控/作用域/依赖/管理面及源码守卫组合161项通过；
  管理面真实资产浏览器验证ACK、未知、每通道原因和ID转义，截图
  `.tmp/round1-autonomy-deliveries-admin.png`，无真实消息正文或凭据。
  桌面历史fallback活跃但WS断开时不再误准入；实际capture不得调用的回归先失败，修复后
  投递/ACK/手机事实/晚间门控93项通过。无新桌面wire协议或手机操作权限。
  普通自主job的发言开关也改为发送前重读；真实runner等待对话锁期间撤销talk_enabled的
  回归先复现talk_sent=true，修复后与turn sink/信号/手机事实组合56项通过。
- 状态：partially verified；合成/独立进程 verified，真实客户端与生产端到端 not verified。

## R3：关键记忆恢复（部分）

- 基线复现：独立进程 enqueue 后退出，新进程无法恢复任务，输出文件缺失。
- 修复：捕获回合在首个投影写入前持久化 scope、turn_id、来源与原始授权源；
  short_term/event_log/ledger 完成后清除执行快照正文。Ledger 的 best-effort 失败仍不阻断发送，
  但保持待补偿任务；恢复 handler 要求 evidence 真正完成才收口。
  summarize_to_midterm 复用 source_turn_id 幂等和来源隔离；其他任务不自动入库。
- 恢复预算：每任务跨重启最多三次 handler 尝试；待处理上限1000；完成回执最多1000条；
  取消仍完成内存 queue accounting，持久状态留待下次启动恢复。
- 观测：`GET /observability/memory-task-outbox`（state.read），仅状态计数与最早积压时间。
- 验证：Python 3.12.13，354 项定向测试通过；独立进程验证排队退出、执行中异常退出、
  user short_term 写入后强制退出与恢复、完成任务重复入队不重复执行。后续取消清理改动的定向复验见最终结果。
- 取消清理及 ledger IO 补偿复验：`pytest -q tests/test_round1_slow_recovery.py tests/test_slow_queue.py`
  4 passed；`test_ledger_failure_is_repaired_without_duplicate_history` 同时断言 web 默认不可召回。
- 向量补偿追加：缺失索引的真实sqlite回归先失败，现通过既有每日janitor从canonical
  episodic与近30天安全event_log补缺，每scope每轮上限10项，保留web等其他索引，
  过滤web/Dream/coplay来源，修复不刷新证据时间。数据库不可用保留缺口；
  `/fixation/status` 的 janitor_vector_repair 提供脱敏进度，不需要新增运营开关。
  Python3.12 janitor/vector/scoped fixation组合35项通过；包含真实sqlite的失败恢复、
  两轮有界补缺、已有web索引保留、来源隔离与跨角色空结果。
- 身份整合追加：保存身份后强制退出的回归先失败（证据一直未标记）；现在冻结episode集合
  并持久化派生计划，重启/证据IO失败复用原结果、不重复调用LLM。比较当前身份后再写，
  新身份/画像冲突停止旧计划并进入DLQ，隔离来源不进入整合，完成清执行快照正文。
  Python3.12恢复/身份/固化组合73项通过，包含不覆盖恢复期间更新和独立进程退出。
  后续身份事实检查加入同一scope锁内比较与幂等确认，既有user_facts/身份/恢复52项通过；
  独立进程复验3项通过（正常恢复、新身份、更新的客观事实），只合成数据，未测试生产状态。
- 反思追加：episode已落盘、mid晋升尚未写时强制退出的独立进程回归先失败；
  现按mid集合持久去重，恢复只补血缘、从canonical证据重建计数，不重复调用模型或造episode。
  episode/mid写入失败必须核验真实文件后才完成；核心合并保留mid血缘，来源隔离继续生效。
  恢复/固化/去重/身份组合70项通过，两个逐投影IO失败补偿用例通过。
- 摘要下游追加：mid已落盘但反思尚未入队时退出的独立进程回归先失败（恢复后episode为0）。
  现重放仍补办下游，不重复调用摘要模型；mid IO失败不冒充成功，情绪段落先持久入队再清理，
  同mid重放不重复推进情绪段落。摘要/反思/情绪段落/outbox组合59项通过（Python3.12）。
- 恢复manifest追加：HTTP按实际进度区分接收、关键写入、证据完成/不需证据、索引及慢任务；
  任务类型/状态分开计数，不暴露正文、路径或turnID。旧库加可选progress列。
  失败先行确认DLQ原来不占准入容量；现pending/running/DLQ正文合计限1000，完成元数据限1000。
  恢复/观测/索引/作用域/换行组合31项通过；详见 [故障矩阵](round1-fault-matrix.md)。
  合成 verified，真实模型、生产进程故障与生产端到端 not verified，整体 partially verified。
  状态：partially verified；真实模型/生产端到端 not verified。

## F1：Gemini 原生文本

- 基线：旧边界拒绝 gemini_generate_content；首批11项回归中7项业务失败，其余拒绝场景
  在未接入协议时不能当成功证据。实现后全部通过。
- 实现：显式原生 REST、x-goog-api-key、系统提示/参数/usage、严格 SSE 完成态、
  多函数校验/重复ID处理、原始签名续轮、错误脱敏；禁止跟随重定向转发凭据。
  模型 registry、原子配置校验、目录、管理面创建/编辑/JSON/流式/连接测试闭环。
- 验证：Python3.12.13 协议/预设/CRUD/目录/旧协议 162 passed；此前含 failover、client reload、
  字面姓名和换行守卫的组合 205 passed。实际 chat_turn 续轮覆盖 sanitize 后签名保留。
- 浏览器：`tests/round1_gemini_browser.cjs`，Edge headless，清缓存并重载；真实静态资产与控件，
  API为合成边界；验证导入、未知协议拒绝、保存请求、编辑密钥遮罩、连接测试显示。
  截图 `.tmp/round1-gemini-admin.png`（本地可重建产物）。
- 当前配置没有官方样式 Gemini 凭据，未进行官方计费调用；不拿兼容网关配置冒充官方连接。
  合成/管理面 verified；真实模型、打包客户端、生产端到端 not verified；总体 partially verified。

## F2：双端安全 Markdown

- 基线：桌面4项显示/分块断言失败，手机正文仍含 Markdown 标记且代码标签被误处理。
- 实现：桌面 React Markdown/GFM，手机 Flutter Markdown Plus（SDK兼容版1.0.7）。
  只改展示，结构化块保持换行；禁止 HTML 执行、自动远程图片与非 HTTP(S)/带凭据链接；
  hl/big/sm 与代码字面量分开，流中保留安全文本，完成后解析；正文与记忆合同不变。
- 桌面 `5efbaba`：90项聊天/协议回归、TS/Vite、真实 ChatPanel 隔离浏览器通过，
  覆盖历史/live/stream canonical、不重复、代码/注入/图片/URL/大字；较新发送恢复浏览器通过。
  旧脚本文字“重试”选择器与已有图标实现不符，仍失败，保留记录。
- 手机：98项 widget/发送/历史回归、7项 Markdown/链接/日夜大字复验、Dream14项通过，
  analyze零问题，Java21 Android dev debug APK 构建通过。实际字体离屏图已检查。
- 状态：合成与浏览器 verified；Android打包 verified；真实手机、Windows安装包、
  实际 TTS 与生产端到端 not verified；总体 partially verified。

## R4 追加：等待对话锁期间撤权

失败先行：持有真实 conversation_lock 后发起 talk_gate.send，在等待期间改变门控，
旧实现仍返回成功且发出消息。现在在取得锁后、关键写入和通道调用之前重新核对硬门控、
软门控及可选来源授权；拒绝原因进入脱敏投递 receipt，不补发。
`tests/test_turn_sink.py` 的对应回归及 autonomy/receipt/signal 组合87项通过（Python3.12）。
该证据只覆盖合成环境，不表示客户端 ACK 或生产投递已验收。

## F3：手机环境事实候选

基线：手机上传已有，独立policy HTTP入口不存在（11项业务断言404），无稳态候选链路。
修复：默认关闭的角色授权，三项分开、原子保存与配置修订；稳定边沿、TTL、置信度和
独立冷却；消费前/投递前核对凭据、会话、角色及授权。重启不恢复旧观测。
候选只描述设备光照/朝向/匿名窗口，不推断人体/睡眠；保持原有门控和静默候选默认。
新增手机设置与管理面、只读脱敏观测；后台尽力清除事实，离线由TTL兜底。
管理面实际静态资产与控件浏览器通过（tests/round1_phone_ambient_browser.cjs），
截图 .tmp/round1-phone-ambient-admin.png；手机控制器/设置4项、原权限/生命周期14项通过。
Android锁屏回归失败先行，修复后2项通过；Flutter analyze零问题。
后端HTTP/权限/观测13项通过；scheduler/admin/ambient/门控/源码守卫组合47项通过。
合成/浏览器 verified；真实设备/真实模型/生产端到端 not verified，总体 partially verified。

## 删除 brief 候选（未授权，不执行）

后续单独评估退役 `/system/meta-mode` 与危险模式的过期文档/守卫；
仅清除确已退役的条目，保留现行 owner/grant/scope/device control。
