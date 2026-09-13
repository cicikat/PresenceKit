# 上传资料回读与跨轮接续

## 图片与文档

`reread_image` 按 owner/当前角色资料库校验指纹，群聊不可用。
`mode=cached` 默认读取已有识别描述；`vision` / `ocr` 显式重读原图，不改变全局路由或首次描述。
原图受既有 inbox 清理周期限制，过期后仍可读资料库描述。`search_documents` 返回 sha256。
`read_document` 支持 `mode=summary|context` 和 `query` 定位前后文；summary 明确是开头摘录，
不是模型生成概括。新导入正文最多保留 500000 字符，按 2000 字符分页；历史被截断的正文需重新上传。

后端管理面沿用工具注册表生成的开关/schema，OCR 与 vision 连接仍在模型路由配置。
观测沿用 `/observability/character-library`、`/observability/tool-traces` 和 API 调用台账。
桌面及手机继续通过 `/upload/ingest` 上传，QQ 走原媒体入口；无新增 REST/WS/ack 字段，
无需客户端复制模式配置，角色通过工具参数选择。手机生活记录仍走独立 outbox。

`observe`：真实桌面、手机上传及实际 OCR/vision 服务联合验证未完成；本地测试使用合成图片和模拟模型。

## 未读与历史结果

普通 owner QQ/desktop/mobile 私聊和 autonomy 读取同一份角色隔离的资料投影。
生活记录沿用 enabled + character_readable 及工具角色授权；文档沿用资料回读工具授权。
过去 7 天的未读资料每轮最多 3 条，额外提供 1 条近期已读参考；待选来源各读取最新 100 条。
识别 pending/failed 不投递。标题、用户备注、识别描述、时间和来源分开提供，详情通过工具分页读取。
自动投递表示提供数据，不强制开口，不绕过 DND、Dream、用户活跃、预算或 talk_owner 闸门。
没有新增 signal 或通知，等下一次原本符合条件的对话/主动机会；群聊、Stage、Dream、Companion 不自动注入。

已读回执只在模型成功返回后确认，包括工具调用和静默返回；组装、裁剪、失败不确认。
read_life_records 的详情回读携带内部回执，等结果进入后续模型调用并成功返回才确认；检索索引不算详情已读。
确认前核对当前 revision，旧模型结果不覆盖新记录的未读状态。删除、关闭读取权限会阻止后续投影；
已经发给模型的在途内容无法撤回。既有版本没有已读回执，升级后最近资料可能首次再提供一次。

主动工具成功时保存已有 safe_summary（每条最多 2000 字符），最多 12 条、24 小时；
后续 prompt 最多取 3 条，每条最多 1400 字符，保留工具名和生成时间，标注历史结果。
不保存原图、raw_data 或私有思考；拒绝 sensitive/失败的截图结果，peek_screen_content 保留原受限出口。
工具 trace_result=false 的私有资料不复制正文，生活记录和文档始终从当前源读取，便于撤回。
action_trace.enabled 关闭时停止结果保留和注入；单工具关闭/角色撤权、屏幕观察总闸关闭时不再投影对应结果。

新数据位于 get_paths().context_continuity_db(uid,char_id=...) 的 SQLite：
独立 per-owner/char 文件、250ms 锁等待、原子事务；回执 30 天/5000 条上限。
读投影不建文件，清理在写事务进行；不新增后台线程或长期记忆写入者。
`GET /observability/context-continuity?uid=...&char_id=...`（state.read）只返回有界待评估数量、
记录 ID/回执时间、工具名/时间/字符数，不暴露正文。管理面服务配置的生活记录卡显示状态，
`GET /settings/life-records`（admin）增加 continuity 投影；原 PUT 配置字段不变。

autonomy 中四个资料读取工具在未显式配置时继承读取能力；显式禁用、工具 schema、
Self Capability、owner/Dream/群聊闸门仍优先。未修改现有运行配置。

## 验证与边界

定向回归包含真实 dispatcher → 静默 autonomy → 下轮恢复截图结果，以及版本更新、删除、
权限撤回、模型失败、分页、资料隔离、metadata-only 鉴权观测。管理面使用实际静态资产与隔离后端，
清空浏览器缓存并实测开启/关闭角色读取和待评估计数。

最终合并定向运行：162 passed；额外存储/屏幕检查为 235 passed、5 个既有路径登记失败。
浏览器脚本 tests/context_continuity_browser.py 通过，实际查看截图确认可见结果。
现有部署的生活记录 enabled/character_readable 与四个回读工具聊天/主动可用性已只读核对为开启；
没有输出 owner、凭据或记录正文，没有修改真实配置或发送测试消息。

`open`：全局路径注册审计存在既有缺项 conversation_stats_db / ime_drafts_db / life_records_db /
llm_reasoning_db，对应 5 个失败；本次新增 context_continuity_db 已登记且通过。未修改无关存储模块。
`observe`：运行中后端需重启加载代码，真实设备/模型联合验收未执行。客户端无需更新包。
