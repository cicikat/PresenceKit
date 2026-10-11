# Reality 活动连续性

会话与 Runtime task 是权威来源。`GET /activity/records` 需要 `state.read`，默认仅返回配置 owner 与请求角色的元数据；记录引用由沙盒中的同一身份解析，不接受查询者指定 uid。Dream 不参与该查询。

`query_activity_records` 复用 dispatcher 身份。先列会话，再以返回的 `activity:<type>:<session>:state` 引用查状态；末尾可改为 `moves`、`dialogue`、`entry:<行号>` 或阅读 `page:<页码>`。支持页偏移、正文偏移、关键词、ISO 时间范围。续页携带 revision；原记录变化会拒绝旧修订，来源删除后引用不可用。旧 transcript 原地可查，不导入主聊天或重计历史指标。

棋步和页面仍在专用结构化记录内。共享真实对话历史不要求通过全局 WS 播放，也不取消活动窗口。会话列表同时保留多个活动；过期读取不续期、不落盘。任务租约失效或进程身份不同显示 interrupted，执行器负责恢复。


## 工单284当前合同

Reality 活动对话由同一 owner/char 的 canonical turn sink 落盘，使用原有脱敏、来源与回合去重。阅读、棋局和 Minecraft 保留独立交互界面；回复只由调用窗口消费，不进行全局主聊天伪流转播。结构化棋步、页面正文、文件和工具回执不批量转换成聊天消息。Dream 隔离不变。

活动请求可携带 request_id，重试须复用同一值；返回 turn_id、msg_id、request_id、source_ref。配置 owner 是服务端身份，客户端不能用 uid 切换用户。新状态使用 revision 比较写入，旧快照拒绝覆盖；state_events 保留实际状态变化。活动对话的 canonical event 携带 activity_record 引用，派生记忆可通过源 event 回到完整分页记录。旧局部 transcript 可原地查询，不自动导入历史或增加指标。

GET /activity/records（state.read）默认仅列来源元数据；指定 source_ref 才读取原文。query_activity_records 使用 dispatcher 身份；未指定引用时关键词/时间查询跨当前身份的活动原文。概要是 revision 绑定的分段摘录，标注 coverage/pending，回复发送后构建；失败保留原文，后续对话或关闭重试。canonical 撤回也遮蔽已关联的局部召回，并使旧概要失效。

表达状态 has_signal=false 表示无短时信号，区别于明确 neutral。来源 turn/source_at/version/expires_at 可观测，最多30分钟且只读不续期；旧固定 mood 不转成新余韵。prompt 的 expression_afterglow 是可裁剪层，prompt_eligible 表示资格，实际注入以 prompt 观测为准。没有新增模型情绪检测、随机活动或成长等级控制开关。
