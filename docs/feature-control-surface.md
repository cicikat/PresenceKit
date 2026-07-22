# 功能控制面事实清单（2026-07-13）

管理服务的设置面分三层：

- persona 级：`/settings/model-routing`、`/settings/tts-desktop`、`/settings/tool-loop`、`/settings/thinking`、`GET/PUT /output-segment-enforce`，供桌面客户端使用；不返回模型密钥。段落兜底开关热更新 `output.segment_enforce`，只影响发送副本（桌面流式 delta、最终 canonical 与非流式输出），默认关闭。
- admin 专用配置：`/model-presets/*`、`/proxy`、`/tts-config`、`/sticker-config`、`/scheduler/config`、`/settings/relay`、`/settings/mcp`。
- admin 功能开关白名单：`GET/PUT /settings/feature-flags`。只接受 `settings_feature_flags.FLAGS` 中已有运行时消费者的布尔字段，不接受密钥、路径、额度或任意 YAML。`private_exchange.enabled`（角色私下往来）与 `qq`/`mail` 两个通道总开关均走这条白名单；desktop/mobile/device 通道没有独立 enabled 字段，是否可用只取决于对应 token 是否配置且未停用。
- admin 配置中心（Brief 93 §1，管理面板「配置」页，`GET/PUT /settings/base-model`、`GET/PUT /settings/embedding`、`GET /settings/setup-status`）：`/settings/base-model` 透明兼容 `model_presets` 主聊天 preset 与旧版 `llm:` 块，由 `_resolve_base_chat_preset_name()` 判定写入目标，不引入第三套真值来源；`/settings/embedding` 读写 `embedding:` 块（缺失时向量召回 fail-open 降级为关键词路径，不算必填）；`/settings/setup-status` 的 `needs_setup` 驱动面板首次登录自动跳转与顶部红色横幅，判定标准是 base_url/api_key/model 三者均非空且不是 `config.example.yaml` 里 `YOUR_`/`YOUR-` 前缀的占位符。
- 密钥本快捷入口（Brief 93 §2，`GET /system/secrets-book`、`POST /system/secrets-book/open`）：仅当请求方 `request.client.host` 是 `127.0.0.1`/`::1`/`localhost` 时可用，用系统默认程序打开 `secrets.local.yaml`；非本机请求悬浮按钮隐藏、`open` 端点直接 403。
- 401 人话化（Brief 93 §6）：`admin/auth.py` 的 401 响应体 `detail` 从纯字符串改为 `{"message", "hint"}`；`/ws/desktop`、`/ws/device` 鉴权失败的 WS close 附带同语义的 `reason`（受 RFC 6455 123 字节上限约束，文案比 HTTP hint 精简）。桌面端 Brief 34 直接透传 `detail.hint` 显示。

模型从 legacy 迁移时调用 `POST /model-presets/bootstrap`，它把现有 `llm` 连接持久化为 `legacy` preset 和 `default` routing profile；之后客户端只切 routing profile，不需要重新录入 API key/base URL。

`/settings/model-routing` 切的是**全局** active_routing；per-角色覆盖是另一条入口：
`GET/PATCH /character/{char_id}/model-routing`（persona 级，Brief 87）读写角色卡
`presence_ext.model_routing`，绑定对象是 routing profile 整体（不支持绑定单个 preset），
`null` 清除声明回落全局。可选 profile 清单走 `GET /model-presets/routing-profiles`
（persona 级，不含 api_key/base_url）。跨群一致——不做 per-group override。

`presence_ext.tool_loop` 是角色卡级 Path C 覆写，不经设置 API：`"on"` 在全局
`tool_loop.enabled=false` 时仍为该卡开启多步工具循环，`"off"` 强制关闭，缺失或非法值回落全局。
它仍要求 owner 私聊与当前 chat preset 的 `tool_call_mode=function_calling`；角色卡不能借此绕过
工具暴露分类或危险工具排除。`examples/assistant.example.json` 展示人机直连组合，普通角色卡未声明时
继续遵从全局默认关闭。

TTS 有两个不同开关：`tts.enabled` 是服务端能力总开关，`tts.desktop_enabled` 决定桌面是否显示/请求语音条。`POST /tts/synthesize` 仅在两者均开启且 persona 鉴权通过时按需合成，返回 base64 WAV。桌面端契约仍是 `{text, emotion}` 请求与 `{audio_b64, mime}` 响应，不接触 provider 或密钥。

TTS provider 由管理面（admin token）经 `GET/PUT /tts-config` 管理：`tts.provider` 当前支持 `gsv` 与明确标注为预留的 `openai_compatible`，每个 provider 可放在 `tts.providers.<provider>`。`GET` 会分别返回各 provider 的脱敏参数块，面板切换 provider 时显示对应参数且保存互不污染；预留 provider 在面板禁用，绝不猜测或发起云厂商请求。旧有顶层 GSV 字段（`api_url`、`ref_audio`、情绪参数等）会自动映射，保持已有本地 GPT-SoVITS 部署行为不变。`POST /tts-config/test` 只试听已就绪 provider，`GET /observability/api-calls?caller=tts` 可查询最近合成结果与失败类别（`state.read`）。

表情包由管理面（admin token）经 `GET/PUT /sticker-config` 管理：`sticker.enabled` 是总开关，`sticker.trigger_prob` 是 0–1 的每轮独立触发概率。缺失该配置块时保持兼容行为（启用、0.06）；关闭时不会发送或广播表情包。TTS 的概率单独掷骰，不会抢占或缩减表情包的配置概率。GET 返回当前有效值，兼作该落盘配置的只读观测面；若已命中概率但目标情绪目录无图，服务端会记录目录路径以便排查。

MCP server 由管理面（admin token）经 `GET/PATCH /settings/mcp`、`POST /settings/mcp/test`、
`POST /settings/mcp/import` 和 `PATCH /settings/mcp/{name}` 管理。URL 导入必须先完成
`initialize + list_tools` 测试才写入配置；HTTP headers 支持 `${ENV_VAR}` 展开，管理面不回显
字面 header 值。总开关同步所有 session，单 server 的启停/白名单只重载该 server；工具调用以
`caller=mcp__{server}__{tool}` 记录到 API 调用总账。桌面客户端不代理这些 admin 配置或密钥。

降级路径：关闭对应功能布尔值时保留其余配置；tool loop 回到普通单次回复，thinking 回到无前置思考，桌面 TTS 回到纯文字，生成后段落兜底关闭后直接发送清理后的模型原文，模型可切回稳定 routing profile。
