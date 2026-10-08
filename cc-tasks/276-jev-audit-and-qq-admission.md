# 276 — Jev 接入核查与 QQ 回复准入

用户授权：核查 Jev 与 Minecraft 快层；审计 QQ 群聊及其他用户链路，在消息分发与客户端设置增加开关并暂时关闭。

- [x] 核查工单 263、协议出口、保存预设与隔离连接测试：263 仍规划；保存的 Chat Completions 请求错误路径返回 404。
- [x] 核查 Minecraft：独立 minecraft_reaction，未配置回退 sensor_judge / intent / chat；仅支持文本 JSON action，尚无 systemone。
- [x] 核查 QQ：群聊不写主记忆但准入前会触碰 owner 状态；其他用户私聊复用 reality、工具和 UI 工具推送链，不能宣称完整隔离。
- [x] QQ 默认关闭群聊和其他用户回复；解析入口及出队入口检查，拒绝发生在状态/媒体/模型处理前。
- [x] admin 热更新开关、effective state、页面控件、缓存版本；本地配置两项关闭。
- [x] 更正 Minecraft Jev 支持说明；登记隔离遗留与原生适配待施工。
- [x] 相关回归、隔离浏览器实测与换行差异检查；独立提交本工单。
- [ ] 生产后端重启与完整管理页浏览器验收（当前旧进程，未替换）。

验收边界：此次不施工 263，不擅自切换模型路由，不重设计群聊人格与多用户数据合同。两项 QQ 开关独立：群聊控制所有群消息；其他用户回复控制非 owner 私聊。owner 未绑定时不将任何私聊推定为 owner。QQ 主连接仍沿用重启生效，准入开关逐消息热读。

删除 brief 候选：后续多用户设计确定后，连同旧群上下文/非 owner reality 分支及对应测试文档一起评估替换；本次不删除。


验证：62 项相关回归通过（QQ 准入、群聊隔离、设置读写、梦境守卫及换行/角色名守卫）。隔离浏览器使用实际 client-settings fragment / JS 与真实 feature-flags router：两项初始关闭，群聊勾选后 effective=enabled，取消并刷新后 disabled；不开放生产入口。生产旧进程仍需重启加载 Python，新页面在旧后端缺少开关时禁用控件并提示重启；生产完整浏览器实测未完成（导航仍停在概览）。

Jev 两次隔离连通性测试：HTTP 404、NotFoundError，2.56s / 2.19s；请求路径 /v1/systemone/chat/completions。仅证明现有协议/路径错误，未验证原生端点密钥/额度可用。官方文档确认 typed choice/score/noul 返回结构化结果；不能生成自由文本与任意开放字段，不等于不能输出 JSON。Minecraft 尚未配置独立路由，沿既有 fallback 使用轻量文本预设，不是 Jev。

QQ 链路：NapCat → _parse_event（@ / 黑名单 / 新准入）→ message_queue → handle_message（新准入复核）。群聊 → group_context → 角色卡+run_llm → QQ 直发 → group_context；主用户群消息在旧链还触碰 owner 梦境/活跃/DND。其他用户私聊 → frozen reality scope(uid,char) → media/pretool/pipeline → turn sink 写入 → QQ 直发；普通正文 fanout=[]，但工具事件用 shared push_tool_status、部分角色级状态及主记忆后处理仍需访客合同审计。
