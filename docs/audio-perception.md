# 语音识别与听觉印象（Brief 253.6）

管理面「语音合成与声音」末尾提供命名 STT 连接及 `voice_message` 用途。
`stt_presets.enabled` 默认 false；`presets` 保存连接名对应的 `base_url`、`model`、
`api_key` 和 `timeout_seconds`，`routes.voice_message` 选择连接。Base URL 是兼容
`audio/transcriptions` 服务的 API 根；转写请求使用 multipart，不依赖聊天模型协议。
`GET /stt-presets` 与两个 PUT 端点要求 admin，返回 API key 是否已配置而非密钥。
配置保存后热生效，页面显示 enabled/configured/effective、来源和阻断原因。

新语音感知默认关闭。没有 `stt_presets` 块时，原 `/transcribe` 本地 Whisper 路径继续工作，
只返回文字；首次保存命名连接时建立关闭的配置块，选用途并启用后才使用远端连接。
旧本地模型下载/推理在工作线程中运行，请求最多等待 20 秒；超时后线程可能继续执行至结束，
由线程清理临时音频。新 STT 的网络请求默认 12 秒、可设 1–20 秒，失败返回未听清。

输入支持 wav/mp3/ogg/flac/m4a/webm/opus/amr/silk 后缀，最多 25 MiB；实际解码能力由
所选服务决定，尤其 QQ amr/silk 需要服务支持。新路径不把音频或完整供应商响应落盘。
QQ record 消息经过有界下载与转写；HTTP `/upload/ingest` 支持单个音频，失败后继续原聊天
并明确未听清。桌面/手机既有录音继续上传 `/transcribe`（chat scope），失败保持原输入错误提示。

语调仅采纳服务可选旁路 `tone` 字段，允许 calm/tired/bright/tense/unclear；没有字段或非法值
统一 unclear，不根据转写文字推断情绪。独立 `3.8_audio_impression` 层说明这是不确定的听觉印象，
不能当情绪、健康或人格事实。普通文字无此层，没有额外 STT 请求。

`/transcribe` 新路径保留 `text`，另返回 `tone`、`audio_perception_id`。凭据仅存后端内存，
最多 128 条，5 分钟过期，绑定 owner、角色、通道及原转写文本摘要，单次消费。
桌面/手机仅在下一条原样转写文字中附带凭据；编辑文字、切换角色、跨通道、重复或过期时丢弃语调。
关闭感知后未消费凭据也失效。进程重启只丢语调，不影响文字发送。原 WS、poll、ack、TTL 与通知路径不变。

observe：真实录音、QQ 编码、各供应商语调字段和三端联合表现未实测；不把合成夹具测试当准确率证明。
