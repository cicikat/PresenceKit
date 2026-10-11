# Reality 活动连续性

会话与 Runtime task 是权威来源。`GET /activity/records` 需要 `state.read`，默认仅返回配置 owner 与请求角色的元数据；记录引用由沙盒中的同一身份解析，不接受查询者指定 uid。Dream 不参与该查询。

`query_activity_records` 复用 dispatcher 身份。先列会话，再以返回的 `activity:<type>:<session>:state` 引用查状态；末尾可改为 `moves`、`dialogue`、`entry:<行号>` 或阅读 `page:<页码>`。支持页偏移、正文偏移、关键词、ISO 时间范围。续页携带 revision；原记录变化会拒绝旧修订，来源删除后引用不可用。旧 transcript 原地可查，不导入主聊天或重计历史指标。

棋步和页面仍在专用结构化记录内。共享真实对话历史不要求通过全局 WS 播放，也不取消活动窗口。会话列表同时保留多个活动；过期读取不续期、不落盘。任务租约失效或进程身份不同显示 interrupted，执行器负责恢复。
