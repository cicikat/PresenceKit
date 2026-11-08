# 293：角色可自选的图像生成路由

> 状态：**参考图 P2 已交付**，待真实图像服务联调。设计原则同文本/多模态路由：命名预设 + 用途路由、角色卡 override、描述由用户手写并勾选是否暴露给 LLM，prompt 与选型均由角色决定。

## 已交付

- `core/image_gen.py`：`image_gen.enabled` 总闸（默认关）+ `presets`（description/show_to_llm/base_url/api_key/model/extra_params）；`presence_ext.image_gen` 白名单；`discovery_note()` 供 discovery 描述注入。
- `core/tools/image_gen.py`：`generate_image(prompt, preset?, width?, height?)`，分类 `media`（仅 Path C，`tool_exposure.path_c` 默认含 `media`，Path A 恒不含；群聊禁止；`echo_event_log=false`）。
- `core/tools/chat_artifacts.py`：`store_generated_image()` 二进制落盘（kind=image），文本工具（read/update/read_text）拒绝图片。
- 交付：桌面/手机走 `artifacts[]` 卡片（`turn_sink._fanout` 已有链路，预览支持 png/jpg/webp）；QQ 走 post_process_slow 的 `send_turn_images`（`core/output/image_delivery.py` → `qq_adapter.send_image`）。
- 管理面：`GET/PUT/DELETE /image-gen-presets/presets/{name}`（admin scope，key 打码）、`PUT /image-gen-presets/enabled`、`GET /observability/image-gen`（state.read，只含元数据）；角色卡 `presence_ext.image_gen` 经 asset-bindings 读写；`config.example.yaml` 含示例块。
- 测试：`tests/test_image_gen.py`（catalog 过滤、解析、图片落盘守卫、真实 HTTP 顶替）。

## 现实约束（核查结论）

1. **工具暴露是“两段式”**：Path C 首轮只返回 `load_tools_<category>` 入口，LLM 需先 `load` 再下一轮调具体工具（`core/tool_discovery.py`）。描述注入有现成先例：`self_tool` 在 `load_tools_self` 后缀里拼「你做过的工具：…」（`core/pipeline.py` 的 `_discovery_notes`），模型可见的预设清单应照此方式拼，不要改 `generate_image` 自身 schema 的 description，也不要给 schema 加动态 enum。
2. **Path A 探针读静态 `_TOOL_REGISTRY` 描述**，看不到动态注入；`generate_image` 若要进 Path A，需另在 probe prompt 里拼预设清单。本单先只开 Path C（已有 owner 私聊 + function_calling 两道硬闸），Path A/relay 不做。
3. **单工具超时现状**：`execute_structured` 无单工具超时，只受 `tool_loop.total_timeout_s`（默认 300s，上限 720s）总墙钟约束。图像生成常需 30–120s，必须给 `generate_image` 单独放宽或把总墙钟抬到 ≥300s，异步 queue 留作后续。
4. **产物流转现状**：
   - `artifacts` 链路走桌面/移动端：`turn_sink._fanout` 把 `artifacts` 列表随 `channel_message` 发给 desktop/mobile，`GET /chat/artifacts/{id}`（`chat` scope）可下载；QQ/设备 `send()` 不接 `artifacts` kwarg。
   - `sticker` 链路是独立的：`maybe_send_sticker` → QQ 走 `send_image` 本地文件路径（OneBot base64），桌面/手机走数据 URL 载荷广播（`registry.broadcast(..., sticker=payload, exclude_channels={"qq"})`）。
   - QQ-channel 的图片能力只有 `send_image` 这一条本地文件入口，无远程 URL 上传。
5. **记忆回流**：工具成功默认经 `action_trace` 写痕迹且 `echo_event_log=True` 时会把「做了一件事：<tool> — <摘要>」经 `capture_turn` 回流到 event_log；且生成结果（artifact id/文件名）会成为下一轮上下文的 tool_result。生成图本身**不会**自动进 vision/`media_processor` 识别链（那是入站图片的链路），是否要回看需由角色主动 `reread_image` 或另加工具。
6. **`chat_artifacts` 当前只支持文本扩展名**：`ALLOWED_EXTENSIONS` 无 `.png/.jpg`，`PREVIEWABLE_EXTENSIONS` 亦无；`read_artifact_text`/`update_artifact` 均按 UTF-8 文本读写。若直接复用 artifacts 目录存图片，必须把二进制图片从「可读写的产物」里剥出来，不能走 `write_artifact`。
7. **配置面**：文本走 `model_presets.presets + routing_profiles`，图片（vision/ocr）走 `image_presets.presets + routes`；图像生成应仿 `image_presets` 自立命名空间，不要与 `model_presets` 混用。

## 方案

### 配置层（仿 `image_presets`）

新增 `image_gen` 顶层块：

```yaml
image_gen:
  enabled: false                       # 总闸，默认关（CONTROL-SURFACE 见下）
  presets:
    flux-pro:
      description: "光影与复杂意境强，适合氛围感插画；慢、贵"  # 用户手写，给 LLM 看
      show_to_llm: true                # 勾选后才进入预设清单
      provider_kind: openai_compatible # 首版只做 OpenAI /images/generations 兼容
      base_url: https://...
      api_key: ...
      model: flux-pro
      extra_params: {}                 # 逃生舱：透传 size/steps 等，不逐家适配
```

- 解析器仿 `core/image_presets.py`：`catalog()` 只读、key 打码快照、connection_ready 校验、per-char override 读角色卡。
- 角色卡 `presence_ext.image_gen: ["flux-pro"]` 声明该角色可用预设白名单；未声明回落全局可见集合（`show_to_llm: true`）。

### 工具层

- 注册单工具 `generate_image(prompt, preset?, width?, height?)`，分类新建 `media`（或并入 `artifacts`；推荐 `media` 一分开，免得和「文本产物」混一类），只暴露 Path C。
- 预设清单不塞 schema：复用 `_discovery_notes["media"]` 注入 `load_tools_media` 描述（仿「你做过的工具：…」），列出 `show_to_llm` + 角色白名单交集的预设名与 description。`preset` 参数为可选字符串，不用 enum；未知/不可用预设时返回明确失败，模型可换其余预设重试。
- 参数只留通用子集；`extra_params` 按预设透传。

### 交付层（两条腿，按通道）

- **图片文件先落 `chat_artifacts_dir`**（`{artifact_id}.png`），复用 `turn_links.json` 的公开 payload 关联到 `turn_id`；但**不注册到 `ALLOWED_EXTENSIONS`**，二进制图片走单独的 `store_generated_image()`，`read_artifact`/`update_artifact` 对图片 id 直接拒绝。
- **桌面/移动端**：`turn_sink._fanout` 已有的 `artifacts[]` 链路只透传桌面/手机——把图片 artifact 的公开 payload（含 `preview_url`，图片的 CSP 放宽到 `img-src`）放进去，两端直接用下载卡片链展示，不需新协议字段。
- **QQ**：`send_image(target_id, path, is_group)` 已有本地文件入口，在出站后处理（post_process slow，同 `maybe_send_sticker` 的位置）把图片发一条 QQ 图片消息；不依赖 `turn_sink` 的 artifacts kwarg（QQ send 不接）。
- 生成图**不进** `media_processor` 入站识别链；是否需要「自己看一张图」另立项。

### 管理面 & 观测（AGENTS.md 规则 13）

- `GET/PUT/DELETE /image-gen-presets` 增删改 + 连通性测试（发一张最小图，不回显正文）；热重载，key 不回显。
- 角色卡 asset-bindings 端点补 `image_gen` 字段（同 `tts_preset`/`sticker_pack` 同构）。
- 调用记 `api_call_log(caller=image_gen)`；`GET /observability/image-gen` 只返回元数据（预设、耗时、状态、每预设计数），不存图与 prompt 正文。

## 参考图/图生图（P2 已交付）

目标：角色可按她自己判断，拿之前的图当参考再生成一张（换风格、延续画面、改细节）。来源全选：

| 来源 | ID 形式 | 字节入口 |
|---|---|---|
| 本轮图片消息 | sha256（media_refs，已在 tool result 里） | `resolve_chat_media(sha256)` |
| 之前生成的图 | artifact id | `artifact_file_path(record)` |
| 用户上传资料库 | sha256 / document_id | `resolve_chat_media` / library blob |
| 本轮对话可见 | sha256（最后 8 张清单，仿 `11.3_image_references`） | 同上 |

接口形态：`multipart /images/edits`（OpenAI 兼容 edits 端点：`image` = 参考图文件，`prompt` = 改图指令），兼容 FLUX/SD 聚合服务；JSON base64 / URL 输入不做（逐家差异大/云不可达）。

工具增量：`generate_image(..., reference_sha256?, reference_artifact_id?)` 互斥二选一；传了参考图时走 edits 端点，`prompt` 语义变为改图指令（schema 描述里写清）；`echo_event_log` 保持 false，prompt 不落盘。

解析器新增：`image_reference_bytes(user_id, char_id, *, sha256?, artifact_id?)` → (bytes, mime, 来源标签)，scope 校验 fail-closed，不在资料库/不存在直接 ValueError。

自由度设计：模型完全自己抉择（不设必选/限额），只设资源上限（单图 10MB、参考图总尺寸≤20MB）；每预设 `supports_edits` 声明（默认 true，未声明的预设在 discovery 清单里标注「不支持参考图」，调用传参考图时明确失败）。

## 分期

1. **MVP**：`image_gen` 配置块 + 单预设 + `generate_image`（Path C only）+ artifacts 落盘 + 桌面/手机卡片 + QQ 图片消息 + 管理面 CRUD。
2. **P2（参考图）**：上面设计。
3. **Deferred**：异步队列（>120s 生成）、本地 ComfyUI/SD WebUI adapter、生成图回读（reread）、Dream/Stage/群聊暴露。

## 参考图耦合核查

- 字节入口：`core/chat_media.py::resolve_chat_media(sha256, uid, char_id)` 是唯一权威解析（资料库 blob 优先、其次 inbox），调用方不拼路径；生成图 artifact 在 `chat_artifacts_dir` 不走该链。
- 资料库行自带 sha256（`search_documents` 结果行含 `sha256`），模型已有 ID 来源；本轮 media_refs 的 sha256 已注入 `11.3_image_references`，无需新层。
- edits 端点是 multipart 字节上传，与现有 JSON generations 只有传输形态差异；无记忆/prompt 耦合。
- 生成图 artifacts 目前没有 sha256 字段与 message_history 关联；P2 需给 kind=image 的记录加 sha256（store 时计算），供后续本轮对话清单消费。

## 耦合清单（已核查）

| 链路 | 结论 | 备注 |
|---|---|---|
| Path A 探针 | 不做 | 探针看不到动态描述，误报风险 |
| Path C discovery | 复用 `_discovery_notes` | 与 self_tool 同构 |
| 单工具超时 | 总墙钟 300s 够用 | 图像单步 30–120s 需保留预算，P2 再收细 |
| Prompt/schema 出口 | 无新增耦合 | 走 `portable_tool_spec` 即可 |
| action_trace/event_log | `echo_event_log=false` | 已设为关闭，不回流记忆；action_trace 仍记痕迹（摘要 only） |
| vision/记忆固化 | 无耦合 | 入站图片链不覆盖出站生成图 |
| 桌面/手机交付 | 复用 artifacts 链 | 需放宽图片预览 CSP |
| QQ 交付 | 复用 `send_image` | 本地文件路径，需出站后处理 |
| 管理面 | 新增端点（规则 13） | feature-control-surface 同步 |
