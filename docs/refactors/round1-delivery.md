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
- 范围：后端记忆投影内部修复，客户端字段和设置不变；真实进程强杀的半条写入留待 R3 联合验收。
- 状态：partially verified（合成 verified；真实模型、打包客户端、生产端到端 not verified）。

## 待完成

- [x] R1：补齐热重载下的回合驻留；合成回归通过，真实模型/生产仍 not verified
- [ ] R3：多存储补偿、关键慢任务恢复与观测
- [ ] R4：主动投递状态与每通道回执
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
  客户端确认接线未完成，明确返回 acknowledgement_available=false。
- 验证：Python 3.12.13，投递+autonomy+管理面 39 passed；
  投递/信号/Dream retry 16 passed，包含新增 scoped HTTP 回执读取。
- 状态：partially verified；真实通道 ACK 与进程强杀验证仍 not verified。

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
- 未完成：vector 缺失补偿、reflect/identity 的持久恢复、逐投影完整故障矩阵与恢复 manifest。
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

## 删除 brief 候选（未授权，不执行）

后续单独评估退役 `/system/meta-mode` 与危险模式的过期文档/守卫；
仅清除确已退役的条目，保留现行 owner/grant/scope/device control。
