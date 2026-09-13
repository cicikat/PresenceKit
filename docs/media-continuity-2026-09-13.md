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
