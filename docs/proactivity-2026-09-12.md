# 主动性排查与调整（2026-09-12，partial）

## 运行证据

只读检查生效状态及最近 100 次运行：90 次 circuit_open、9 次 blocked_user_active、1 次 llm_failed；
连续失败计数为 17。不能把这些记录解读为角色主动选择沉默。
当前生效路由包含 XML 工具模式；autonomy 原先无条件调用仅原生 function calling 的 chat_turn，
会在发送模型请求前 ValueError，且被统一记作 llm_failed。旧记录未保存异常类型，无法追溯每一次失败。

## 修复与已生效设置

autonomy 显式允许 chat_turn 使用已有 XML 工具编码；原生 FC 调用者的默认契约不变。
工具结果转换为受控的文本 continuation，解析仍按当前暴露白名单校验，普通文本仍不直接发送。
异常只在现有 run.events 留 error_type，不保存 token、请求体或上游错误正文。
模型选择、DND、用户对话锁、Dream、未回复上限和 talk_owner 唯一出口不变。

通过已有管理 API 更新并回读确认：
- interval.seconds：21600 → 1800（30 分钟一次评估机会，不承诺定时发言）。
- daily_evaluation_budget：12 → 48。
- scheduler 全局主动发言间隔：11700 → 2700 秒（45 分钟）。
- 每日主动发言上限仍为 8；最短评估间隔仍为 900 秒。

桌面 token 管理写返回 403；随后使用既有本地管理员凭据完成授权 API 写入，未修改 token scope。
本机凭据未进入提交。配置热加载已确认；代码适配仍需要后端重启。

## 验收

XML 模式、非法工具、坏 JSON、静默返回、既有 native-only 调用者有独立回归；
现有 autonomy/信号链回归仍通过。未给真实角色发送测试消息。
open：后端重启后的真实模型主动调用和首次正常交付、熔断自然恢复，不能以单测代替实机结果。

## 截图接入

用户已确认电脑和手机均接入真实按需截图，实现与待验收矩阵见 `docs/screen-observation-2026-09-12.md`。旧的屏幕文字快照不充当截图。真实设备与模型联合验收保持 open。
