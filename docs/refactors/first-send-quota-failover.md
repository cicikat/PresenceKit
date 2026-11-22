# 首发额度拒绝的同轮接管

2026-11-22。后端和桌面已合并，后端已部署并完成线上健康检查与隔离接管验证。打包桌面客户端的线上联验保持 NOT VERIFIED。

OpenAI 兼容网关曾返回 HTTP 403 和 `local:insufficient_quota`。旧逻辑当轮以鉴权拒绝结束，后续请求等熔断打开后才走备用。现在仅识别该结构化组合，首个调用即可在现有授权和预算内换一次已配置的备用。三次连续额度拒绝仍进入既有熔断，冷却后再次额度拒绝时保持同轮接管。普通鉴权和权限拒绝保留原有处置，流式开始输出后不会换路。

备用缺席或未能生成回复时，桌面 HTTP 和失败回执提供脱敏的 `upstream_quota_exhausted`。如果主路由因额度拒绝熔断，冷却期内继续保留额度原因。台账同时保留主路由的熔断跳过、备用尝试结果或备用跳过原因。桌面从回执显示额度提示，在原用户气泡保留原文和显式重发入口。回执查询和同 ID 重试不会重跑已执行的工具。相关 HTTP 合同见 [通道](../channels.md)。

## 验收

- 将现行分类函数替换为提交前版本运行同一套回归，缓冲与流式两项都在首轮拿不到备用回复，恢复修改后通过。测试使用结构化 OpenAI SDK 403、受控时钟及隔离的模型与台账边界。
- Python 3.10.20 与 3.12.13 各 79 项通过，覆盖 failover、连接热重载、桌面 HTTP 回执、import 方向和换行守卫。
- Python 3.12.13 加入完整 tool-loop 文件后共 162 项通过，覆盖跨协议接管后已成功写入的工具动作不重跑。
- 冷却期补丁先复现缓冲／流式 × 无备用／主备同时熔断四个失败。补丁落地后 Python 3.10.20 和 3.12.13 各 83 项通过。
- 桌面 TypeScript 与 19 项单测通过。真实 ChatPanel 隔离浏览器先复现回执缺少专用提示，再验证额度文案、原文保留、双击只发一次、手动重试生成新 ID、下一条草稿保留及晚到正文去重。

## 合并与上线

- 后端 [#29](https://github.com/cicikat/PresenceKit/pull/29) 和 [#30](https://github.com/cicikat/PresenceKit/pull/30) 已合并。冷却期补丁通过双版本 smoke、全量、浏览器和离线评估。最终 main `0e09704a80a37187fe417936efbd78a093b177ce` 的双版本全量及浏览器均通过。[PR CI](https://github.com/cicikat/PresenceKit/actions/runs/44974722818) · [main CI](https://github.com/cicikat/PresenceKit/actions/runs/44975012929) · [main 离线评估](https://github.com/cicikat/PresenceKit/actions/runs/44975012890)
- 桌面 [#13](https://github.com/cicikat/PresenceKit-desktop/pull/13) 已合并，main `af6c4d9368ff61aca09988440a2ee89247a7b275` 的前端、Windows 和 macOS Rust 通过。[桌面 main CI](https://github.com/cicikat/PresenceKit-desktop/actions/runs/44974277076)
- 固定三仓合同使用上述合并 SHA 及手机 `abb8d504bee304957d865cc6de2c2cecb9c67715`，三端均通过。[合同 CI](https://github.com/cicikat/PresenceKit/actions/runs/44975034833)
- 上海时间 10:16:19 完成 VPS 切换，仅更新 `core/llm_failover.py` 与 `admin/routers/chat.py`，停服到健康确认耗时 6.39 秒。独立复核确认安装和回滚副本哈希，服务 active，私有配置校验一致，三个已鉴权检查返回 200，未鉴权 health 返回 401，五类启动异常为零。旧程序副本按部署 manifest 保留。
- 使用 VPS 实际安装的代码与 SDK，在隔离目录通过缓冲、流式、普通权限拒绝、无备用和冷却期五项验证。8 次网关调用全部由合成传输返回，真实推理请求为零。生产真实额度拒绝、打包桌面联验仍待自然观测。TTS 按安排跳过。

## 生产只读复核

服务器日志在 2026-11-18 09:34 至 11-22 09:34（上海时间）记录桌面 7 次、手机 9 次聊天 HTTP 500。各失败时点在台账各有一条时间匹配的最终失败，样本中没有 `ge-eco`。错误附近可见 4 次上游 403、8 次上游 400 和 4 次上游 5xx。8 次上游 400 中 5 次来自备用，3 次来自主路由，均为 Chat Completions 且未带工具结果。没有可供定位的参数字段，仍需核对请求合同或中转适配。

单独复测 `ge-eco` 时，使用已配置凭据的 `/models` 与 `/v1/models` 都能列出目标模型，无凭据的 `/v1/models` 返回 401。生成用的 `/chat/completions` 与 `/v1/chat/completions` 均返回 JSON 403。响应没有可用于确认余额、模型权限或网关访问限制的已知错误码。未修改生产预设、鉴权、余额或路由。复测仅使用合成探针。
