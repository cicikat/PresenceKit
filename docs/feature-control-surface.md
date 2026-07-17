# 功能控制面事实清单（2026-07-13）

管理服务的设置面分三层：

- persona 级：`/settings/model-routing`、`/settings/tts-desktop`、`/settings/tool-loop`、`/settings/thinking`、`GET/PUT /output-segment-enforce`，供桌面客户端使用；不返回模型密钥。段落兜底开关热更新 `output.segment_enforce`，只影响发送副本（桌面流式 delta、最终 canonical 与非流式输出），默认关闭。
- admin 专用配置：`/model-presets/*`、`/proxy`、`/tts-config`、`/scheduler/config`、`/settings/relay`。
- admin 功能开关白名单：`GET/PUT /settings/feature-flags`。只接受 `settings_feature_flags.FLAGS` 中已有运行时消费者的布尔字段，不接受密钥、路径、额度或任意 YAML。

模型从 legacy 迁移时调用 `POST /model-presets/bootstrap`，它把现有 `llm` 连接持久化为 `legacy` preset 和 `default` routing profile；之后客户端只切 routing profile，不需要重新录入 API key/base URL。

`/settings/model-routing` 切的是**全局** active_routing；per-角色覆盖是另一条入口：
`GET/PATCH /character/{char_id}/model-routing`（persona 级，Brief 87）读写角色卡
`presence_ext.model_routing`，绑定对象是 routing profile 整体（不支持绑定单个 preset），
`null` 清除声明回落全局。可选 profile 清单走 `GET /model-presets/routing-profiles`
（persona 级，不含 api_key/base_url）。跨群一致——不做 per-group override。

TTS 有两个不同开关：`tts.enabled` 是服务端能力总开关，`tts.desktop_enabled` 决定桌面是否显示/请求语音条。`POST /tts/synthesize` 仅在两者均开启且 persona 鉴权通过时按需合成，返回 base64 WAV。

降级路径：关闭对应功能布尔值时保留其余配置；tool loop 回到普通单次回复，thinking 回到无前置思考，桌面 TTS 回到纯文字，生成后段落兜底关闭后直接发送清理后的模型原文，模型可切回稳定 routing profile。
