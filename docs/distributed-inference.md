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
- 截图 raw HTTP、OpenAI 兼容 TTS 默认直连；命名 STT 默认使用全局 aiohttp 代理配置，可按连接选择直连。Embedding 的 SDK 默认传输遵循进程代理环境，和 `proxy.model_connection_mode` 独立。部署时检查 `NO_PROXY` 或运行环境的代理边界。
- 连接目录、HTTP 响应、模型推理成功分别取证。使用合成图像和音频，禁止用真实通话帧或私人录音探测。连通性验证在测试环境进行。

### 当前设备核对

- 后端、桌面和手机本地 `main` 与各自远端 `main` 一致。现有配置包含本机 VLM 与 GPT-SoVITS，STT 使用后端进程内的旧路径。核对时配置中的两个服务端口未监听；另一个运行中的 Ollama 监听地址返回空模型目录。
- 实际跨机器推理、私网链路与故障切换的设备验收为 `observe`，见后续工单的验证记录。

## DIST-02 摄像头私网连接

在管理面「模型路由 → 图像连接」选择视觉 Chat Completions。默认只允许 loopback HTTP。需要向 Tailscale 等可信加密私网发送摄像头帧时，把该节点的 IP Base URL 同时填入「摄像头授权的私网端点」，然后在摄像头用途选择它。支持 RFC1918、Tailscale IPv4 地址段和 ULA IPv6 字面地址。授权绑定到保存的确切 URL，单独的 `is_local` 只用于隐私归因。部署方应在私网 ACL 中仅允许主后端访问视觉服务。

`video_call_tool` 未配置时继承 `video_call`，显式留空时关闭。两条链及摄像头 shadow 直连、禁重定向和公网兜底。原始帧仅在请求内存中处理。配置变更、关闭摄像头或会话失效后，迟到结果作废。

`/video-call/state` 显示 `endpoint_type`、`configured`、`effective`、`runtime_effective`、`blocking_reason`、`retry_after_seconds`。聊天权限的顶层 `effective` 保留配置就绪语义，使旧桌面也能启动观察定时器并从短时故障恢复。`runtime_effective` 以及管理面、只读观测的 `effective` 反映当前退避状态。服务拒连、模型 404 或其他 HTTP 故障会标记对应路由 15 秒，到期由后续请求恢复检测。保存连接的合成图片测试用于单独验证推理。

本机 Ollama 用合成纯蓝 JPEG 返回正确描述和一次性凭据。测试将全局代理指向不可用地址，摄像头请求仍直接抵达配置中的本机 VLM。浏览器验证私网授权、用途选择和撤销。另一台机器上的 VLM 推理仍为 `observe`。

## DIST-03 GPT-SoVITS 私网节点

1. 在推理节点启动 GPT-SoVITS `api_v2.py`，只监听 loopback，供节点代理独占使用。通过 `-c` 指向节点专用推理配置，因为上游切换权重时会更新这份文件。上游有打印合成文本、参考文本和文件路径的版本，应在该进程禁用常规访问日志及输出采集。准备专用资源目录，把参考音频和配对的权重部署到该目录。编写 UTF-8 JSON 清单，路径相对该目录，键为稳定逻辑 ID，可用中文。

   ```json
   {
     "reference": {"角色_普通": "references/neutral.wav"},
     "gpt": {"角色_v2_gpt": "weights/voice.ckpt"},
     "sovits": {"角色_v2_sovits": "weights/voice.pth"}
   }
   ```

2. 在节点进程环境设置 `PRESENCEKIT_GSV_NODE_TOKEN`。从 PresenceKit 仓库运行 `python -m scripts.serve_gsv_node --root <资源目录> --manifest <清单> --gsv-url <本机API地址> --host <加密私网IP> --port <端口>`。节点代理只接受 Bearer 鉴权。使用单 worker，并在私网 ACL 中仅放行主后端。需要重载清单时重启该节点代理。
3. 在主后端「语音合成与声音」选角色，填 GPT-SoVITS 私网节点 URL 和令牌。在其他 Provider 参数增加 JSON 对象 `node_resources`。其 `reference`、`gpt`、`sovits` 分别将原有参考音频与权重选择值映射到清单 ID，例如 `{ "reference": {"neutral.wav":"角色_普通"}, "gpt": {"voice.ckpt":"角色_v2_gpt"}, "sovits": {"voice.pth":"角色_v2_sovits"} }`。已有情绪引用同样需要映射。选定版本需要成对提供两项权重，自动模式也须显式选两项。清空节点 URL 即恢复 Gradio。

PresenceKit 按角色、版本和情绪选资源，在节点模式只发送逻辑 ID 与合成文本参数。节点校验资源位于根目录内，完整切换两项权重后才逐段合成并返回 WAV。未配置或缺失的资源报错，不回退底模。节点同一时间处理一个请求，忙时立即拒绝。管理面试听和现有调用台账提供离线、忙碌、超时、资源缺失和模型加载失败分类。

本机实测经 PresenceKit → HTTP 节点 → GPT-SoVITS API v2 合成两句中文。中文资源 ID 与英文合成参考音频得到 32 kHz、173120 帧的 WAV；纯中文参考模式复测得到 32 kHz、139840 帧的 WAV。两台物理设备、两组真实角色声线及客户端播放为 `observe`。

## DIST-04 STT 和文本回退

- 命名 STT 的 `direct: true` 跳过全局代理，默认 `false` 兼容已有配置。每连接超时为 1–20 秒，默认 12 秒。管理面保存后立即生效。`stt_presets.fallbacks.voice_message` 默认空，只有管理员显式选择的不同备用连接才会收到音频。触发条件为连接失败、超时或 HTTP 502–504，每次最多尝试一次备用。证书校验失败、鉴权失败、无效音频、空转写、词表回声和协议错误都不会触发备用。未配置命名块时 `/transcribe` 仍走本地。配置命名块后，即使关闭远程也不预热本地模型。
- `GET /stt-presets` 的 `fallback_effective` 表示备用路由配置已生效。`GET /observability/api-calls?caller=stt` 用 `logical_call_id` 关联尝试，记录 `route_role`、`switch_reason` 和结果。录音、转写文本和词表提示均不写入台账。
- 文本继续使用原按角色/用途选择、SDK 连接策略、取消与总时间预算，以及 profile 的显式备用路由。私网文本地址使用 `proxy.model_connection_mode=auto` 或 `direct`。本次未调整文本 Failover 策略。

### 实际 HTTP 验证

| 链路 | 证据 |
|---|---|
| LLM | 两个独立公网 API 域名分别返回有效文本。隔离配置中令主地址拒连后，既有 Failover 调用公网备用成功，台账记录 `primary` 失败及 `fallback/connection_error` 成功。 |
| VLM | 本机配置的 Ollama 对合成 JPEG 返回描述，主后端签发观察凭据。 |
| STT | 本机独立 HTTP 端点用真实 Faster Whisper small 推理。主端口拒连后，桌面和手机的 `/transcribe` 请求均从备用端点取得正确中文、置信度和凭据。主后端 STT 模型缓存为空。 |
| TTS | 节点代理经本机 GPT-SoVITS API v2 返回可解码 WAV，见 DIST-03。 |
| Embedding | 配置的公网 OpenAI 兼容 API 为合成文本返回 1 条 1024 维向量。 |

真正由 VPS 访问另一台设备的 VLM、STT 和 GPT-SoVITS，以及私网设备断线与重连，仍为 `observe`。DIST-05 至 DIST-07 依赖此前置验收，仍为 `roadmap`。
