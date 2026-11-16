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

## 删除 brief 候选（未授权，不执行）

后续单独评估退役 `/system/meta-mode` 与危险模式的过期文档/守卫；
仅清除确已退役的条目，保留现行 owner/grant/scope/device control。
