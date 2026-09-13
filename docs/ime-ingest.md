# IME 输入观察、编辑事件与主动关心

接收、判定、主动候选与管理面已实现；真机和真实模型体验仍为 observe。

## 控制与调用链

管理面「功能总开关」分别控制 `ime_ingest.enabled` 与 `ime_awareness.enabled`（均默认 false）。
前者只允许接收，后者允许分类和产生主动候选；还需 scheduler、autonomy 和 talk 开关生效。
「模型路由」新增 `ime_judge`，可使用轻量模型，未配置依次回退 sensor_judge、intent、chat。
实际路由、effective、blocking_reason 见 admin-only `GET /observability/ime-drafts` 和管理面
「记录与状态总览 · IME」。权限与热配置继续使用既有 feature-flags API。

POST 接收只等待 SQLite 事务，204 不代表模型已读或角色已发消息。scheduler 每约 60 秒扫描，
最多分类两条五分钟内新修订，每次 20 秒超时；失败显示 failed，至少一分钟后重试。
判定结果有 work/chat/mixed/unknown、summary、evidence、uncertainty、topic、confidence、worth_contact。
不凭应用名断定活动，不把持续输入当忙碌，不因处理事务/聊天避让；给角色的策略用“她”和“我”。
低于 0.65 或缺少文字依据时不产生候选。十分钟内最多产生一次 IME 候选，修订去重，五分钟过期。
测试包排除；本系统手机聊天的普通输入排除，但编辑删除保留。QQ 无法区分收件人，不能自动排除。
手机包 com.presencekit.mobile 明确为与陪伴角色聊天的应用，但不证明具体会话或已发送。
仅上次成功判定之后的新 delete_backward/clear/restore 使本系统聊天进入分析；旧删除和单独
compose_delete 不再触发。new_edit_events 是新增编辑，content 与 edit_events 仅作历史背景。
购物、难过等不是白名单；日常分享、开心、兴趣与计划同样可产生候选。
判定期间修订变化则旧判断不发布；开关和 TTL 在角色执行及工具调用前复核。

信号 source=ime，经统一 autonomy opportunity 与 talk_owner 决策发送，可能选择不说话。
IME 的 RESTLESS 活动状态不阻止评估；本系统聊天在途锁、近期聊天、DND、梦境、预算和连续
未回复限制仍生效。原始输入不能被当成正式用户消息，也不会直接写长期记忆。

## 兼容协议

`POST /v1/ime/drafts`，Bearer sensor.write，token.label 是设备身份。最多 2000 条/4 MiB，
沿用完整记录数组：id、created_at、updated_at、revision、app_package、source、content。
时间 Unix 毫秒；source=keyboard/voice/mixed。按 device_id+id 只接受更大 revision，整批事务后确认。
新增可选 edit_events（旧发送端省略等于 []），最多 256 项，seq 单调递增且不超过 revision：

```json
{"seq":2,"at_ms":1789128000000,"kind":"delete_backward","text":"示例","outcome":"requested"}
```

kind=insert/delete_backward/compose_delete/clear/restore；text 最多 4096 字符；at_ms 在该草稿时间范围。
requested 是删除请求，applied 是输入连接报告操作接受（不是远端应用/消息发送确认）。
compose_delete 只代表拼音组合区。content 仍为历史提交文字的拼接，不是最终输入框快照；
删除不会改写它，也不能据此推断纠结、后悔或发送状态。长按清空/恢复只保留可读取的有界窗口。

Android Room 7→8 增加 edit_events，最近 256 事件、每段最多 1024 字符，数字遮蔽沿用原规则。
及时模式默认选中，但仍须记录与回传双开关：停笔约 5 秒或持续输入约 15 秒一批；失败 60 秒后重试。
空闲扫描/进程重启后补传约一分钟；关闭及时模式沿用分钟间隔。单批限制 3 MiB，只确认实际发送修订。
旧收件端不识别 edit_events 会返回 422，需要先更新后端。IME 必须存活；敏感输入框和锁屏不记录。

## 存储、观测与闭环

SQLite 经 `get_paths().ime_drafts_db()`，接收三小时后不再查询，写入时清理过期行。
同库 ime_analysis 按 uid+char_id+device_id+id 保存最新判断、状态、revision、analyzed_at。
只读观测返回 entries、summary、awareness、analyses；不创建空库。分析原始证据仅 admin 可读。
analyses.result 增加 decision_reason、response_chars、assessed_revision；失败仅记 error_type，
不保存异常正文。区分请求失败、输出校验失败、低置信度、缺证据、不值得联系与后续门控。
回执仅保留最新修订，不能用它还原全部历史成功请求的后续投递。
signal_id 可关联既有 autonomy jobs/runs；queued 只表示入候选，不是发送成功。
摘要候选进入既有 autonomy 持久台账及 prompt 快照，遵循其保留机制，不能承诺全部派生数据三小时擦除。
没有新增 identity/episodic/mid_term 写入；角色实际发出的消息仍走既有 turn_sink 与后处理。

桌面与手机沿用现有主动消息 fanout、去重和通知，没有新 WS/IPC schema 或设备权限。
新增功能配置由后端管理面持有，IME 自己管理记录/回传与及时模式。原生设置审计已同步。
自动测试覆盖修订、过期、来源排除、无效输出、并发更新、候选与角色决策；真机安装和真实模型
理解质量尚未验证，不能把合成单元测试当成真实消息交付验收。
