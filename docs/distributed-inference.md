# 分布式推理调用链（工单 300）

主后端持有配置、权限、会话和业务数据。桌面与手机继续请求主后端。推理节点仅收到对应模型协议的输入。MCP 继续由工具子系统管理。

## DIST-01 路由盘点

| 调用入口 | 用途及配置来源 | 请求协议及执行位置 | 失败行为 |
|---|---|---|---|
| `pipeline`、Stage、调度与记忆后台任务 → `llm_client` → `model_registry` | `model_presets` 的 profile/category；角色卡 `presence_ext.model_routing` 可覆盖全局 profile，部分调用可显式指定 preset | 按 preset 的 `base_url` 发送 Chat Completions、Responses、Anthropic Messages 或 SystemOne | 缺类别按既有默认链解析；实际请求失败后，符合策略时使用 profile/category 的 `fallback_routes`，受总时间预算限制 |
| 聊天图片上传、生活记录图像、截图观察和手机自动化 → `image_presets` | `chat_upload`、`life_diet`、`life_cart`、`life_bill`、`screen`；旧配置从 `vision` 等合成 | 视觉 Chat Completions/Responses；OCR 按连接协议；执行地址由各连接指定，截图 raw HTTP 适配器与通用上传 SDK 路径分开 | 上传视觉失败返回识别失败；截图仅按显式 screen 备用连接和既有错误白名单重试 |
| 视频电话周期上传、按需新帧 → `video_call` → `llm_client`；摄像头 shadow → `vlm_client` | `video_call`、`video_call_tool`（缺省继承周期路由） | OpenAI 兼容 Chat Completions；见摄像头授权边界 | 无公网或主聊天模型兜底；通话结束作废观察结果 |
| 桌面/手机 `/transcribe`、QQ 语音与单音频上传 → `audio_perception` | 启用的 `stt_presets.routes.voice_message`；无命名块时 `/transcribe` 走 `stt_local` | 远程 multipart `POST /audio/transcriptions`；旧路径在主后端加载 Whisper 或 sherpa | 无法转写时按通道返回错误或走既有文字降级，保持词表、置信度和回执绑定 |
| `/tts/synthesize`、QQ 语音 → `voice_adapter` | 角色 `tts_preset` 或全局 `tts`；provider 和情绪决定音色及参考资源 | OpenAI 兼容 `POST /audio/speech` 或 GPT-SoVITS Gradio/节点接口，地址由 provider 指定 | 合成失败保留文字；角色、分段和播放由原链路负责 |
| 记忆语义检索与向量写入 → `vector_store` → `embedding.embed` | `embedding` 的 `openai_compat` | `POST /embeddings`，地址由 `embedding.base_url` 指定 | 未配置或不可用时降级为非向量路径；自托管服务可提供 OpenAI 兼容协议 |

### 代理与验证边界

- 文本与通用 SDK 视觉默认 `follow_global`。显式全局 HTTP 代理会接管私网文本请求。私网可用 `auto`（正常 DNS 直连，Fake-IP 或解析失败走代理）或 `direct`。本机视觉及已授权摄像头连接直连。SDK 文本与视觉不继承系统代理环境变量。
- 截图 raw HTTP、OpenAI 兼容 TTS 默认直连；命名 STT 的旧默认使用全局 aiohttp 代理配置。Embedding 的 SDK 默认传输遵循进程代理环境，和 `proxy.model_connection_mode` 独立。部署时检查 `NO_PROXY` 或运行环境的代理边界。
- 连接目录、HTTP 响应、模型推理成功分别取证。使用合成图像和音频，禁止用真实通话帧或私人录音探测。连通性验证在测试环境进行。

### 当前设备核对

- 后端、桌面和手机本地 `main` 与各自远端 `main` 一致。现有配置包含本机 VLM 与 GPT-SoVITS，STT 使用后端进程内的旧路径。核对时配置中的两个服务端口未监听；另一个运行中的 Ollama 监听地址返回空模型目录。
- 实际跨机器推理、私网链路与故障切换的设备验收为 `observe`，见后续工单的验证记录。

## DIST-02 摄像头私网连接

在管理面「模型路由 → 图像连接」选择视觉 Chat Completions。默认只允许 loopback HTTP。需要向 Tailscale 等可信加密私网发送摄像头帧时，把该节点的 IP Base URL 同时填入「摄像头授权的私网端点」，然后在摄像头用途选择它。支持 RFC1918、Tailscale IPv4 地址段和 ULA IPv6 字面地址。授权绑定到保存的确切 URL，单独的 `is_local` 只用于隐私归因。部署方应在私网 ACL 中仅允许主后端访问视觉服务。

`video_call_tool` 未配置时继承 `video_call`，显式留空时关闭。两条链及摄像头 shadow 直连、禁重定向和公网兜底。原始帧仅在请求内存中处理。配置变更、关闭摄像头或会话失效后，迟到结果作废。

`/video-call/state` 按用途显示 `endpoint_type`、`configured`、`effective`、`blocking_reason`、`retry_after_seconds`。服务拒连、模型 404 或其他 HTTP 故障会标记对应路由 15 秒。到期允许请求恢复检测。有效状态表示当前允许发起请求。保存连接的合成图片测试用于单独验证推理。
