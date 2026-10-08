# 长文档阅读与接续（280）

上传 TXT、Markdown、DOCX 时，完整解析正文保存到当前 uid/char 的 character_library；单文件上限 5 MiB，解析正文上限 500000 字符。超过上限明确拒绝，不静默保存半篇。旧 DOC 不支持，需转 DOCX。TXT/MD 支持 UTF-8（含 BOM）、UTF-16 BOM 和 GBK；Markdown 保留标题，DOCX 按文档顺序提取段落和表格，并将 Heading 样式投影为标题。嵌入图片、脚注、文本框及 Word 的排版页码不属于正文提取合同，不伪造页码。

QQ 与 `/upload/ingest` 使用同一投影：初始最多 3000 字符，但明确全文长度、已提供范围、稳定 document_id 及继续读取方法。这是摘录，不是全文阅读。资料库保存失败返回明确错误；HTTP 保持既有 422 `{code,message}`，新增 `document_text_too_large` / `document_archive_failed`。

`read_document(document_id, mode, offset, limit, query)`：

- `context`：从 offset 读正文，最多 6000 字符；返回 end_offset、next_offset、总长度、所在章节及起始行。query 定位字面匹配，未命中明确返回。
- `continue`：从该文档持久记录的最早未提供位置接续。跨段跳读不会假报读完；重复回读不会重复累计覆盖。
- `overview`：查看结构与阅读进度；offset 此时是目录条目偏移，一次10项，返回 next_section_offset。无标题时按字符偏移和行定位。
- `summary`：读取或推进分块概要，最多推进4个新片段，再合并已完成片段；`pending` 仅有开头摘录，`partial` 明确概要覆盖位置，`ready` 才表示全部解析正文已参与概括。概要由模型生成，不是原文或理解验收。

文档工具构造独立有界 ToolResult：最多6000字正文加有界元数据，整体小于8000字符；仍用原 frame_tool_message 不可信数据边界。它不再经过普通2000字裁剪，避免分页游标越过没有提供的正文。普通工具结果上限不变。

search_documents 最多8项，按字段限制名称与摘录，保留每项稳定ID、媒体类型和图片sha256；有界结果小于5000字符，不让通用裁剪吞掉后面的资料身份。

发送后后台默认调用冻结 char_id 的现有 summary 路由：每10000字符生成分段概要，分段保存起止位置，再合并为全文概要。每次只处理一份文档，后台最多120秒，超时或模型失败保留检查点，下轮后台/显式 summary 请求可接续，不影响回复发送。没有新增模型连接或运营开关；时间、分块和输出上限属于内部资源约束。概要与已提供范围保存在资料库索引中，重复上传同一正文保留状态，撤回不被后台任务复活。

现有 `10.6_pending_material` / `10.7_recent_material` 只投影有界已保存概要及进度，仍使用既有权限、窗口和裁剪。概要更新不会把旧上传重新当成新事件。长久以后可通过 search_documents/read_document 按需回读；资料派生概要不进入 episodic/identity/storyline。普通聊天中用户对资料的讨论沿既有记忆合同，本单不改变来源隔离。

进度记录语义是“正文工具结果已提供”，不证明模型已经理解；初始3000字摘录不充当正文工具完整阅读回执。既有 `/observability/character-library`（state.read）新增无正文的文档长度、概要状态/覆盖及提供进度，失败计数同端点。接口和观测由后端拥有，无桌面/手机新增设置或通知/ack/TTL。

验收：自动回归覆盖万字尾段、实际 dispatcher 无二次截断、跨轮/重开、非顺序回读、目录定位、编码、DOCX表格、摘要重试及撤回。真实模型概要质量、QQ、桌面/手机实际上传体验待部署后验收。
