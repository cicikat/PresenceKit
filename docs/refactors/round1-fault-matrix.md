# 第一轮故障注入与恢复证据

全部使用公开角色fixture、合成身份/消息和独立可写沙盒。以下是合成环境验证，
不代表真实模型、真机或生产端到端验收；三仓固定SHA/CI与生产证据另记联合交付。

| 故障或边界 | 真实入口与可观测断言 | 证据 |
| --- | --- | --- |
| 回合内503/备用/熔断/热重载 | 原备用驻留；下一独立回合读取新路由；旧连接池等租约结束才关闭 | test_llm_failover.py、test_llm_client_reload.py、test_tool_loop.py |
| 相同短回复、跨角色/日期、改名重试 | 每个turn+speaker+正文仅一个完整记录；日/full各自补缺 | test_round1_event_log_idempotency.py |
| 跨午夜恢复 | 实际capture写回发生日23:59，不在恢复当日新增投影 | test_recovered_turn_stays_in_original_day_projection |
| 日/full写到UTF-8字符中间后退出 | 独立进程os._exit后重启，两个投影可解码、每个speaker只一条 | test_process_exit_mid_utf8_record_repairs_exact_projection |
| 追加尾部被外部编辑 | 原字节保持不变；拒绝不确定恢复；HTTP观测pending，不暴露正文/路径 | test_conflicting_external_tail_is_preserved_and_remains_observable |
| short_term用户行写后退出 | 新进程补回助手/日志/证据，短期历史不重复 | test_queued_memory_work_survives_process_exit |
| Ledger写入失败 | 捕获保持待修复，恢复后默认查询仍不可见web源，短期历史仍两条 | test_ledger_failure_is_repaired_without_duplicate_history |
| 摘要mid落盘后退出 | 重启补办反思；摘要模型只调用一次；mid、episode与晋升一致 | test_summary_commit_recovers_downstream_reflection_after_process_exit |
| mid写入失败 | 读回实际mid后才完成；恢复后只有一个source_turn_id | test_summary_io_failure_stays_pending_until_real_midterm_exists |
| episode写后、mid晋升前退出 | 恢复仅补血缘，反思模型只一次，episode及派生计数各一份 | test_reflection_recovers_after_episode_write_before_midterm_mark |
| episode/mid晋升IO失败 | 有界重试后仅一个episode；已提交episode不重复调用反思模型 | test_reflection_retries_failed_projection_without_duplicate_episode |
| 身份写后、证据标记前退出/IO失败 | 复用冻结计划，不重复合成；真实证据标记完成才收口 | test_identity_commit_recovers_after_process_exit_without_synthesizing_twice、test_identity_evidence_io_failure_reuses_frozen_plan |
| 恢复期间身份/客观事实更新 | 保留更新值，旧计划停止并进入DLQ，不标记旧证据为完成 | test_identity_commit_recovers_after_process_exit_without_synthesizing_twice |
| Dream/web/coplay与混合来源 | 不调用身份合成，不跨角色写身份 | test_durable_identity_task_keeps_isolated_sources_out_of_synthesis |
| 向量丢失、数据库暂不可用 | canonical证据不变；分批补缺；保留其他web索引；同scope不重复 | test_memory_janitor.py |
| DLQ积压 | 1000份失败任务也占用正文容量；新任务拒绝准入 | test_dlq_payloads_count_toward_outbox_capacity |
| 恢复manifest | 实际HTTP区分关键写入、证据、索引与慢任务完成；不暴露原消息/turnID | test_turn_manifest_reports_real_projection_and_slow_task_progress |
| 主动投递失败/部分失败/取消 | 每通道结果保留；未知不自动重发；claimed/accepted不冒充ACK | test_round1_autonomy_delivery.py |
| 手机正常/抢先ACK、落盘失败 | 真实HTTP或发送返回前ACK仅确认匹配scope；失败503保留队列，恢复重试可确认 | test_mobile_ack_confirms_only_matching_autonomy_receipt、test_mobile_queue_ack.py |
| 主动投递进程退出 | 独立进程重启后显式outcome_unknown，关联ID不可重新占用 | test_interrupted_delivery_is_explicit_unknown_after_process_restart |
| 等待对话锁时撤权 | 取得锁后重新核对，未投递也不补发 | test_autonomy_rechecks_policy_after_waiting_for_conversation_gate |
| 关键落盘await或两通道之间撤权 | 发送前再次核对；已接受通道不重发，后续通道停止并记录原因 | test_autonomy_rechecks_revocation_after_critical_write_wait、test_autonomy_revocation_stops_later_channels_without_replaying_first |
| 手机授权/去抖/过期/换token/角色/会话/进程 | HTTP接受事实只在稳态边沿生成候选，失效后不可投递 | test_phone_ambient_autonomy.py |

## 恢复状态与限制

恢复信息存于现有 `memory_task_outbox()` SQLite，唯一用途为待执行/派生计划和
元数据manifest，不是第二份聊天检索真相。canonical仍是现行记忆与证据文件。
capture完成清正文；慢任务完成清计划正文。最多1000份pending/running/DLQ正文，
最多1000份无正文完成回执；每任务跨进程最多3次尝试。DLQ不自动重放。
临时日志追加意图另限1000，启动最多修复32个；外部编辑冲突不覆盖文件。
向量补缺每scope每日既有janitor批次最多10项；embedding不可用时保留缺口待下轮。

`GET /observability/memory-task-outbox`（state.read）返回任务类型/状态计数、最早积压、
projection_repair及turn_manifest计数。manifest只覆盖保留的capture回执；
evidence_not_required与确已投影分开，索引完成仅依据真实索引元数据确认。
slow_completed表示已处理摘要任务，来源策略拒绝也属于处理完成，不表示一定新增记忆。
`GET /fixation/status`（memory.read）给出janitor_vector_repair的实际缺失/尝试/完成/剩余数量。

更新后的schema只加可选progress元数据列；旧程序使用此恢复DB前应升级到同轮代码，
不要直接降级到使用位置INSERT的早期开发提交。程序回滚顺序和离线恢复见联合交付手册。
