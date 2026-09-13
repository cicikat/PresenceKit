# 功能控制面事实清单（2026-07-13）

## 管理面图像连接与全局所有者（2026-09-12）

图像连接列表与用途分配分开保存，手机视觉覆盖仍继承通用配置；未新增视觉配置真值。
admin POST /image-recognition/test/{general|ocr|phone} 用合成图片测试已保存连接，
既有 API 账本提供观测，测试不计入角色统计。全局所有者只在配置页编辑，调度页不再
重复提交身份或无消费的签名表单。详细 UI、浏览器与三面核对见 [admin-settings-visual-review.md](admin-settings-visual-review.md)。

## 角色按需截图（2026-09-12，partial）

管理面功能开关 `screen_observation.enabled` 默认关闭，实际可用还需 `visual_perception.enabled`、
视觉模型配置及活跃设备本地授权。功能开关页提供 effective state、设备/请求回执查询
（`GET /perception/screen/status`，state.read）。电脑视觉观察页与手机系统配置页分别有
独立「允许角色按需截图」UI，默认关闭，不等同于旧周期采样或屏幕文字分享。
全局开启时，自主工具 `observe_user_screen` 在未显式配置其策略时继承启用；显式禁用优先。
工具暴露、自我能力、冷却、Dream/对话锁和免打扰保持原有执行约束。
三端构建及定向回归完成，真实设备验收 open；详见 `screen-observation-2026-09-12.md`。

## IME 活动理解与主动关心（2026-09-11）

current：IME 编辑事件与独立 `ime_judge` 活动判定已接入 scheduler → autonomy signal →
talk_owner → turn_sink；管理面功能总开关新增 `ime_awareness`（默认关闭），接收开关独立。
模型路由页可选 ime_judge；未配置依次回退 sensor_judge / intent / chat。
「记录与状态总览 · IME」展示 effective、阻塞原因、判定结果、编辑记录和 signal_id；
最终交付在现有自主性观测中按 opportunity signals 的 source=ime / signal_id 关联。
详见 [ime-ingest.md](ime-ingest.md)。这一条替代早期“未实现角色消费/仅存储”的状态说明。

observe：真实手机安装、不同应用删除反馈、断网后上传以及真实模型主动关心体验未实测。
本地自动化采用合成资料，不给真实角色发送测试消息。桌面/手机沿用普通主动消息展示与通知，
没有新增原生设置或 IME 原文读取权限。处理事务/聊天分类不充当忙碌抑制；正在处理本系统
对话的锁、DND、梦境和主动消息预算仍有效。周期为 scheduler 的约 60 秒扫描，非即时推送。


## API 思考存档（2026-09-09）

后端默认记录 API 返回的思考，保留期无限；这是存储行为，与控制模型是否生成思考的
`thinking.enabled` 独立，不新增开关。只读 `GET /observability/llm-reasoning` 与
`/{call_id}` 均为 admin-only，列表不含正文，详情按需读取。管理面板与双端展开 UI
为 roadmap，标准客户端 token 无权读取，不能把本地展示偏好当作存储开关。

管理服务的设置面分三层：

RPG Dream's `rpg_kp` route is a backend capability, not a client setting; its effective route is visible with the other model categories.

后台练习盲评不属于管理面热更新 API，但其模型选择遵循同一模型路由真值：
`practice.reviewer_category` 是 routing profile category（默认 `consolidation`）；可选旧字段
`practice.reviewer_preset` 是严格的直接 preset 名并优先于 category。未知 preset 会让该次
练习明确失败并进入 scheduler 异常记录，不会静默回落 chat。

所有写入 `config.yaml` 的设置端点统一经过 `admin.config_control`：写请求按进程内锁串行，
用临时文件原子替换，并以读取时快照做三方合并，避免并发设置覆盖无关字段。运行时只使用
`config.yaml`；管理面和手工编辑修改的是同一份配置。

- persona 级：`/settings/model-routing`、`/settings/tts-desktop`、`/settings/tts-auto-play`、`/settings/tool-loop`、`/settings/thinking`、`GET/PUT /output-segment-enforce`，供客户端使用；不返回模型密钥。段落兜底开关热更新 `output.segment_enforce`，只影响发送副本（桌面流式 delta、最终 canonical 与非流式输出），默认关闭。
- admin 专用配置：`/model-presets/*`、`/proxy`、`/tts-config`、`/sticker-config`、`/scheduler/config`、`/settings/relay`、`/settings/mcp`。routing profile 也包含 `sensor_judge` 与 `scenario_reconcile`：前者是后台裁决专用 category，后者是 Dream 发送后的语义校准 category；两者都应映射到稳定的轻量 `chat_completions` preset，缺失时分别兼容回退 `intent → chat`。它们使用短 timeout 与零 SDK retry，未在桌面设置页单独暴露。preset 的 `api_protocol` 由管理面和 `PUT /model-presets/presets/{name}` 管理，取值为 `chat_completions`（默认）或 `responses`；它独立于 `provider_kind` 与 `tool_call_mode`，保存后热重载，不会静默切换 API。`POST /model-presets/presets/{name}/rename` 会原子重命名 preset 并更新所有 routing profile 引用，随后热重载。
- 单人 Scenario 的 Dream setting `scenario_injection_mode` 由 `GET/PATCH /dream/settings` 管理，取值为 `strict_stage`（默认）或 `full_script`；full mode 只在下一次入梦时冻结，预算超限会明确拒绝入梦。Sandbox、Mirror、Group Dream 与 Reality chat 不消费该字段。
- LLM/model preset/vision/proxy 热重载会等待旧 AsyncOpenAI/httpx client 关闭后再返回；关闭失败
  fail-open 记 warning，旧实例已从 registry 摘除，新请求只会按新配置惰性建 client。
- admin 功能开关白名单：`GET/PUT /settings/feature-flags`。只接受 `settings_feature_flags.FLAGS` 中已有运行时消费者的布尔字段，不接受密钥、路径、额度或任意 YAML。每项返回 `apply_mode` / `restart_required`，PUT 返回 `reload_status` 和本次确实改变且需要重启的字段。`qq.enabled` 只在 `main.py` 启动阶段注册通道、回调和监听任务，因此明确为 `restart_required`，不得显示成热生效；`mail` 及其余逐次读配置的功能仍是 `hot_reload`。`private_exchange.enabled` 与 `qq`/`mail` 两个通道总开关均走这条白名单；desktop/mobile/device 通道没有独立 enabled 字段，是否可用只取决于对应 token 是否配置且未停用。管理面编辑入口为「高级设置 → 运行配置」；「系统状态」只读展示通道摘要，不再承载保存控件。
- admin 配置中心（Brief 93 §1，管理面板「配置」页，`GET/PUT /settings/base-model`、`GET/PUT /settings/embedding`、`GET /settings/setup-status`）：`/settings/base-model` 透明兼容 `model_presets` 主聊天 preset 与旧版 `llm:` 块，由 `_resolve_base_chat_preset_name()` 判定写入目标，不引入第三套真值来源；`/settings/embedding` 读写 `embedding:` 块（缺失时向量召回 fail-open 降级为关键词路径，不算必填）；`/settings/setup-status` 的 `needs_setup` 驱动面板首次登录自动跳转与顶部红色横幅，判定标准是 base_url/api_key/model 三者均非空且不是 `config.example.yaml` 里 `YOUR_`/`YOUR-` 前缀的占位符。
- 密钥本快捷入口（Brief 93 §2，`GET /system/secrets-book`、`POST /system/secrets-book/open`）：仅当请求方 `request.client.host` 是 `127.0.0.1`/`::1`/`localhost` 时可用，用系统默认程序打开 `secrets.local.yaml`；非本机请求悬浮按钮隐藏、`open` 端点直接 403。

管理面状态与配置入口（Brief 163）：「系统状态」仅读取 `/status`、`/model-presets`、`/tts-config`、`/proxy`、`/settings/relay`、`/scheduler/status`、`/settings/feature-flags` 和有限错误摘要，展示当前值、配置来源、生效范围、刷新结果及明确的外部健康边界。编辑入口分流到「高级设置 → 运行配置」「高级设置 → TTS 配置」「模型路由」和「调度器」；编辑页的布尔、枚举、数值控件分别使用 checkbox/select/range，并标注 hot reload、immediate 或 restart-required。数据环境只展示 formal/test、脱敏的测试会话标签、逻辑数据位置和隔离测试用户数量，不展示本机绝对路径或用户 ID。
- 401 人话化（Brief 93 §6）：`admin/auth.py` 的 401 响应体 `detail` 从纯字符串改为 `{"message", "hint"}`；`/ws/desktop`、`/ws/device` 鉴权失败的 WS close 附带同语义的 `reason`（受 RFC 6455 123 字节上限约束，文案比 HTTP hint 精简）。桌面端 Brief 34 直接透传 `detail.hint` 显示。

模型从 legacy 迁移时调用 `POST /model-presets/bootstrap`，它把现有 `llm` 连接持久化为 `legacy` preset 和 `default` routing profile；之后客户端只切 routing profile，不需要重新录入 API key/base URL。

`/settings/model-routing` 切的是**全局** active_routing；per-角色覆盖是另一条入口：
`GET/PATCH /character/{char_id}/model-routing`（persona 级，Brief 87）读写角色卡
`presence_ext.model_routing`，绑定对象是 routing profile 整体（不支持绑定单个 preset），
`null` 或字段缺失才表示清除声明、回落全局；`"default"` 是一个真实的 profile 名，和其他字符串 profile 一样会固定绑定角色。可选 profile 清单走 `GET /model-presets/routing-profiles`
（persona 级，不含 api_key/base_url）。管理面 `GET /model-presets` 会附带当前活动角色的有效固定绑定（若有），在切换全局路由时明确提示该角色不受影响。跨群一致——不做 per-group override。

`presence_ext.tool_loop` 是角色卡级 Path C 覆写，不经设置 API：`"on"` 在全局
`tool_loop.enabled=false` 时仍为该卡开启多步工具循环，`"off"` 强制关闭，缺失或非法值回落全局。
全局 `tool_loop.total_timeout_s` 控制单轮工具循环的总墙钟预算，默认 300 秒；管理面可调范围为 5–720 秒。
Path C 固定采用分类按需加载，无新增开关：首轮仅提供最终获授权的非空分类入口。
`max_steps` 之外最多增加非空分类数（至多 9）个纯发现轮，发现/relay/执行共用总超时；
thinking 前处理和无工具最终生成维持原预算边界。只读 `runtime-signals` 的
`tool_loop_discovery` 提供实际 schema 数量、加载/拒绝/耗尽/超时观测。
桌面继续从管理面配置，手机无本地权限副本；详见 [tool-discovery.md](tool-discovery.md)。
它仍要求 owner 私聊与当前 chat preset 的 `tool_call_mode=function_calling`；角色卡不能借此绕过
工具暴露分类或危险工具排除。`examples/assistant.example.json` 展示人机直连组合，普通角色卡未声明时
继续遵从全局默认关闭。

工具暴露面按路径而非端区分：QQ、desktop、mobile 都经 `core.pretool_router.route_pretool()`，共享
`tool_exposure.path_a`；Path C 共享 `tool_exposure.path_c`。每条路径都可设置 `categories`、精确
`tools` 白名单和 `exclude_tools`，后两者只会收窄 schema，不能授予执行权限。Path C 缺少新配置时兼容
`tool_loop.categories/exclude_tools`，且既有模型 `tool_preset` 仍是最终的内置工具收窄层。角色卡可用
`tool_categories_path_a/path_c`、`tool_tools_path_a/path_c`、`tool_exclude_path_a/path_c` 覆盖；旧
`tool_categories` 仅保留为 Path C 兼容别名。`GET/PUT /settings/tools` 读写全局 `tool_exposure`，
`GET /observability/character-permissions` 回显两条路径的解析结果与来源。显式快速路径白名单当前仅
`get_time`，不由设置页或工具 keywords 扩大；Path C 激活时只跳过普通 probe，快速成功会从本轮 loop schema 排除同名工具。
角色加载失败时，普通对话解析保持兼容回落；phone-control 只读诊断则使用同一解析器并 fail-closed，
不会把全局默认误报成当前角色已授权。

TTS 有三个层次的开关：`tts.enabled` 是服务端能力总开关；`tts.desktop_enabled` 是旧桌面语音条显示兼容项，并与 `tts.auto_play.desktop_pet` 双向同步；`tts.auto_play` 则按 `chat`、`dream`、`video_call`、`desktop_pet`、`mobile` 独立决定客户端是否自动请求/播放，全部默认关闭。`GET/POST /settings/tts-auto-play` 是该落盘状态的读回观测面。桌面设置页提供已接入语音条的聊天与桌宠气泡开关，并在收到回复后实际自动合成播放。`POST /tts/synthesize` 只在能力总开关开启且 persona 鉴权通过时按需合成，接受可选 `scene`（旧客户端可省略），并在合成前移除中英文括号中的旁白/动作描写；返回 base64 WAV。手机轮询消息只携带 `voice_available` 轻量标记，绝不携带音频本体；手机端在本机“自动播放语音”开启时，以该标记按需请求并播放音频。管理面“观测 → 资源完整性”分别报告 TTS 服务就绪和桌宠手动语音条可用性；前者为关闭时，后者即使单独开启也会明确报告为不可合成。

TTS provider 由管理面（admin token）经 `GET/PUT /tts-config` 管理：`tts.provider` 当前支持 `gsv` 与明确标注为预留的 `openai_compatible`，每个 provider 可放在 `tts.providers.<provider>`。`GET` 会分别返回各 provider 的脱敏参数块，面板切换 provider 时显示对应参数且保存互不污染；预留 provider 在面板禁用，绝不猜测或发起云厂商请求。GSV 可选 `gpt_model_path` / `sovits_model_path`，留空时分别切回 v3 / v2ProPlus 默认底模；模型切换是 GSV 服务的全局状态，后端会把切换与同次合成串行化，路径错误时自动回退对应底模。GSV 默认启用后端分句：清洗控制/格式字符并识别实际、字面 `\\n` 与 `/n` 换行，按 `。！？；……` 优先切分，只有超过 `segment_max_chars`（默认 42）才在逗号或破折号处兜底；每段以 GSV `不切` 请求、按中文/英文脚本选择语言模式，再校验 PCM WAV 参数并插入 `segment_pause_seconds`（默认 0.25 秒）静音拼接。`external_segment_enabled: false` 可临时恢复 GSV 内部切分。旧有顶层 GSV 字段（`api_url`、`ref_audio`、情绪参数等）会自动映射，保持已有本地 GPT-SoVITS 部署行为不变。`POST /tts-config/test` 只试听已就绪 provider，`GET /observability/api-calls?caller=tts` 可查询最近合成结果与失败类别（`state.read`）。

视觉模型不进 LLM 的 `routing_profiles`：通用图片识别使用 `GET/PUT /vision-params` 的 `vision:` 块；手机自动化可通过 `GET/PUT /vision-params/phone-control` 管理 `phone_control_vision:` 覆盖。两个控制卡片归在管理面的“模型路由”页，接口、落盘语义和热重载保持不变。后者只保存显式填写的字段，空字段会删除覆盖并继承通用视觉配置；保存后热重载。`/phone_control/status` 继续按合并后的 `base_url` 与 `model` 判断 `vision_configured`。

表情包由管理面（admin token）经 `GET/PUT /sticker-config` 管理：`sticker.enabled` 是总开关，`sticker.trigger_prob` 是 0–1 的每轮独立触发概率。缺失该配置块时保持兼容行为（启用、0.06）；关闭时不会发送或广播表情包。TTS 的概率单独掷骰，不会抢占或缩减表情包的配置概率。GET 返回当前有效值，兼作该落盘配置的只读观测面；若已命中概率但目标情绪目录无图，服务端会记录目录路径以便排查。

MCP server 由管理面（admin token）经 `GET/PATCH /settings/mcp`、`POST /settings/mcp/test`、
`POST /settings/mcp/import`、`PATCH /settings/mcp/{name}` 和 `DELETE /settings/mcp/{name}` 管理。URL 导入必须先完成
`initialize + list_tools` 测试才写入配置；URL 导入可选 `streamable-http`（推荐）或 `sse`，而配置文件也可声明
`stdio`；旧 `http` 配置继续按 `streamable-http` 处理。HTTP endpoint URL 与 headers 都支持 `${ENV_VAR}` 展开：
服务商使用路径认证时可将敏感路径段写为 `${MCP_TOKEN}`，缺失变量会 fail-closed；管理面不回显字面 URL
路径或 header 值。新建 server 的 header editor 默认为空，只有操作者显式添加 bearer template 后才会提交。
MCP 不继承环境代理：loopback/localhost 地址始终直连，远程地址可在管理面单独设置
`use_proxy`，启用后使用全局 `proxy.http` / `proxy.https`。删除会立即断开该 server 并摘除它的动态工具；总开关同步所有 session，单 server 的启停/白名单只重载该 server；工具调用以
`caller=mcp__{server}__{tool}` 记录到 API 调用总账。`tool_timeout_s` 是 server 默认值，`tool_timeouts_s.<tool>` 可为个别工具覆盖 1-660 秒的调用上限；设置读取接口会返回两者，更新会热重载对应 server。动作工具超时会记录相同 `request_id` 并返回 `outcome_unknown`，不会自动重放。工具策略新增 `unrestricted`（面板显示“无限制执行”）：管理员选择时强制显式幂等、跳过确认，并以同一 `request_id` 最多重连重试三次。`tool_policy.<tool>.ui_label` 是可选的本地瞬态展示标签（1-48 字符），仅供已配对桌面端的 NOW 状态使用；它不影响权限、确认、重试或调用参数，缺失时统一显示“外部工具”，不会回显远端工具名、说明、参数或结果。

后端管理面 MCP 页的 Tool-call Console 仅通过 admin-only 的 `POST /settings/mcp/console/invoke` 与
`POST /settings/mcp/console/confirm` 调用。路由只接受当前已连接、有效 allowlist、已注册且本地 policy 已确认的动态工具，并在服务端以工具的 JSON Schema 校验参数；绝不接受任意 MCP method 或 server command。它复用 `tool_dispatcher.execute(origin="admin_console")`、effect/确认门、每工具超时和 MCP API 调用总账，不直接触碰 session；高危调用返回一次性确认票据（120 秒、仅原工具原参数可确认）。控制台响应和总账以 `audit_id` 关联，且总账不记录 arguments 或返回正文。桌面客户端不代理 MCP 管理调用、配置或密钥。

每个 MCP server 可保存命名的 `tool_presets`（每项是一组工具白名单）和当前 `active_tool_preset`。选择预设会将该工具集写回运行时实际使用的 `allow_tools` 后热重载；手工改复选框则回到“自定义”选择，避免悄悄改写命名预设。开启 `require_local_policy` 时，`allow_tools` 仍是唯一运行时白名单；管理面 URL 导入和普通白名单保存会在工具探测成功后为新白名单工具写入本地默认 `tool_policy`，保留已有显式策略。MCP annotations（`readOnlyHint` / `destructiveHint`）或名称和描述只能影响默认 effect 建议，不能授予远端权限或自动开启确认：未知语义落为 `write + require_confirm: false`。管理页逐工具“每次执行前确认”复选框显式保存 true/false；导入、普通保存和批量默认授权都只补缺失字段，不覆盖已有选择。无法从当前运行时快照补齐策略时，严格写入会被拒绝，不会注册或调用。保存时会清理已移出白名单的策略项。单 server 保存向对应 owner task 发送重载信号，失败时 API 返回 `reload_status=restart_required`，管理面提示重启。

Brief 137 增加每 server 的可选 `metadata_mapping`、按远端工具精确名称保存的
`metadata_overrides`，以及 `domain_selector`。这是 Emerald 客户端扩展，不是 MCP 官方分类标准；
第三方 server 无需实现即可继续连接和调用。远端 `_meta` 只被压缩为有界 domains、interaction
提示、status/source/version，不改变 `category="mcp"`、allowlist、effect、确认、幂等、origin、
schema 校验、连接或 proficiency。selector 缺失时保持旧行为；启用时只收窄已经授权的本轮 schema，
`include_unclassified: true` 保留普通无 metadata server。管理面逐工具分开展示“已发现 / 已授权 /
当前会话可暴露”，并允许选择使用远端分类、本地覆盖或忽略远端分类。`GET /settings/mcp` 不返回
完整 `_meta`、原始 description 或完整参数 schema；控制台显示有界参数摘要，调用时仍由服务端用
完整 registry schema 校验。直接加载的 MCP JS、i18n 和 page fragment 使用同一静态资源版本。

管理面「运维 → 工具」经 admin-only `GET/PUT /settings/tools` 统一观察内置已注册工具、读写
`tool_exposure.path_a/path_c`，并保存命名的 `tool_loop.tool_presets` 后绑定到
`model_presets.presets.<name>.tool_preset`。Path 暴露配置可以同时收窄 category 和具体工具名；该预设
只继续收窄该聊天模型收到的内置 function schema，且不替代 `tools.<name>.enabled` 全局执行闸门。
MCP 在此页只显示全局启用状态，动态工具目录、连接与配置仍不由此页维护。删除工具预设会同步清除引用它的模型绑定。

LLM 请求快照是独立的高敏感调试开关：管理面 MCP 页通过 admin-only 的
`GET/PUT /llm-debug-requests` 控制 `llm_debug_requests.enabled` 与 `keep_days`（1–7，默认关闭/1 天）。
开启后，`core/llm_client.py` 会在实际请求发出前记录 messages、tools 与生成参数；疑似密钥字段和
`data:image/...` 二进制数据会被遮蔽。读取只能经 admin-only 的
`GET /observability/llm-debug-requests`，并可经同为 admin-only 的 `DELETE /observability/llm-debug-requests` 主动清空；不可复用普通 `state.read` API 调用总账权限。它只应用于短时
排查，关闭后不再产生新快照，既有快照按保留期自动轮转清理。

降级路径：关闭对应功能布尔值时保留其余配置；tool loop 回到普通单次回复，thinking 回到无前置思考，桌面 TTS 回到纯文字，生成后段落兜底关闭后直接发送清理后的模型原文，模型可切回稳定 routing profile。

内置唤醒由 `GET/PATCH /admin/autonomy/config` 与 `GET/PATCH /admin/autonomy/tools` 控制，配置和有限运行记录按 owner/角色写入独立 autonomy state，不写入 `config.yaml`。默认关闭；启用后的 job 仍只由现有 scheduler tick 消费。全局已连接的只读 MCP 工具会直接进入自主工具面，但仍必须通过全局启用、MCP local policy、Self Capability 的有效授权与动态注册检查；写入工具继续要求 autonomy allowlist 和代码审查的 sandboxed write（当前为花园浇水）。`manage_self_capability` 只在存在可由角色修改的、用户已授权且未锁定的能力时暴露。每次 run 会保存只读记忆、最近五轮和基础角色描述组成的 prompt 快照；普通运行列表不返回快照，只有 `GET /admin/autonomy/runs/{run_id}/prompt`（`admin` scope）可读取。关闭 autonomy 会停止 tick 对 pending signal 的消费、job 创建和 run/发言；迁移后的 scheduler conversational trigger 即使留下有界候选事实，也不会退回旧 `_pipeline_send` 发言路径，过期事实会在再次启用后的首次 drain 中丢弃。`desktop_wake` 是一次性特例：autonomy 已关闭时 Path B 不入队；入队后再关闭时，下一次 tick 会删除 wake signal 并写入 terminal suppression，后续重新开启不会补发。普通 owner chat tool loop 与 Wake Bridge 不受影响。`scheduler.morning_greeting`、`night_reminder`、`random_message` 等来源开关仍是各例行信号的权威生产闸门；关闭来源后，已排队但尚未消费的同源事实也不会形成 opportunity。

Self Capability P0 uses `GET /admin/self-management` and fixed `POST` actions for grants, locks, restore, and undo. Its durable state and audit are scoped by `uid + char_id` under the runtime sandbox, separately from autonomy state and `config.yaml`. The global `self_management.enabled` master switch is exposed in System Status -> Feature switches and is hot-reloaded. When off, stored agent overrides are dormant, the management gateway is hidden, and agent requests are rejected; user grants and audits remain intact for a later re-enable. The panel returns capability IDs, status, constraints, revisions, and audit metadata only; it never returns MCP URLs, headers, tokens, or raw tool results. Agent mutations use the internal `manage_self_capability` gateway and require a user grant, `mutable_by_agent`, an unlocked capability, a current revision, and an idempotent action ID. During an autonomy run, the gateway is exposed only while a mutable capability exists; every model step rebuilds the effective schema, and every requested call is checked against that current allowlist before dispatch. Self-management calls use `autonomy_self_management`; business calls use `autonomy_loop`, and the two origins are not interchangeable. Audit records correlate the run/job/action IDs while the autonomy tools endpoint reports the safe final decision matrix. The effective runtime decision still intersects global availability and every existing dispatcher/autonomy gate.
# MCP 批量授权补充（Brief 135）

Signal-first autonomy lifecycle is read-only at `GET /observability/autonomy-opportunities` (`state.read`): it reports queued, silent, tools-only, sent, and user-canceled outcomes without prompt snapshots. Its `funnel` projection aggregates the latest 24 hours and 7 days by source, covering signal queueing, opportunity creation, admission, model silence, talk availability/gate rejection and delivery. The companion `GET /admin/autonomy/runs` view retains bounded run events; memory-reactivation runs expose `memory_read`, `memory_candidate_evaluated`, and `memory_recall_talk_sent`, so a silent evaluation is not confused with a successful proactive recollection. Proactive delivery remains restricted to the autonomy `talk_owner` outlet.

Brief 153 adds `GET /admin/autonomy/effective-state` (`state.read`) as the single scheduler /
autonomy effective-state contract. It joins configured values, Self Capability overrides, effective
runtime values, source lifecycle, talk gate, cooldown, daily budgets, runtime task availability,
and the owning runtime consumer. Scheduler/autonomy pages should consume this contract instead of
inferring state from unrelated status/config/ledger responses. Manual trigger endpoints are
test-only queue fixtures and advertise `direct_delivery=false`; they never bypass production
autonomy admission. `period_reminder` additionally returns `missing_period_date` without queueing
when the owner-side period input is absent; a valid date still follows the migrated signal-first
path and never invokes direct delivery.

The control-center overview reads `GET /admin/control-center/effective-state` (`state.read`) as
its only global state source. The contract includes a row for Tool Loop, MCP, Self Capability,
autonomy, scheduler, channels, model routing, Embedding, TTS, and the frozen Intiface reserve
line. Each row carries default/configured/effective values, override source, runtime status,
blocking reason, reload mode, runtime consumer, and its canonical edit page. The overview does
not infer effective values from unrelated status or settings responses; Intiface is reported as
dormant/frozen independently from MCP.

MCP 管理页的 server 卡片提供“默认授权全部”和“无限制授权全部”两个 server 级动作。
它们分别发送一次 `PATCH /settings/mcp/{name}`，请求体只允许
`{"bulk_authorize":"default"}` 或 `{"bulk_authorize":"unrestricted"}`；服务端从当前连接态
`list_tools()` 快照生成白名单，写入一次并热重载一次。响应的 `processed_count`、最终白名单、
policy 和 `reload_status` 是管理面的唯一状态来源；未连接或没有工具目录时按钮禁用并显示原因。

严格本地策略通过 `GET /settings/mcp` 返回的 `require_local_policy` 显示。严格模式空白
`allow_tools` 是零授权；legacy 非严格模式才保留空白即全开的兼容语义。
# Brief 152: Self Capability management controls

Self Capability management IDs are separate from admin API scopes.  A fresh
installation exposes safe, reversible `setting.*` controls through the internal
management gateway: Tool Loop switches and exposure/presets, ordinary tool
execution switches, the MCP global switch plus configured-server enablement and
allowlists, and ordinary scheduler/autonomy switches, budgets, and intervals.

The registry publishes `default_grant`, `mutable_by_agent`, `user_lockable`,
`requires_confirmation`, `high_risk`, and `value_type` for every management
capability.  MCP capabilities identify a configured server by name; the model
cannot provide a URL, headers, or transport configuration.  Secrets, auth/token
profiles and scopes, proxy/bind/listen settings, destructive deletion/retention,
and MCP imports are protected with stable rejection codes.  High-risk tool
policies remain observable but are never agent-mutable; only an explicit admin
action can enable them.

Every accepted mutation uses the character-scoped optimistic revision and an
idempotent action ID, appends an audit record, and increments the effective
schema revision.  Audit values are bounded and omit URLs, headers, tokens,
passwords, API keys, and raw tool results.  The admin read endpoint is
`GET /admin/self-management` (with `policy_matrix` and `audit`), while the
agent has only the dedicated `manage_self_capability` gateway.

Configured MCP tools may expose a safe `setting.mcp.server:<name>.policy:<tool>`
control for ordinary `read`/`write` policy and confirmation flags. Existing
`actuate`, `emergency`, and `unrestricted` policies are marked high risk and
cannot be changed by the agent.

## Brief 203 Memory Event candidate relations

`event_edge_proposer.enabled` is exposed through the existing hot-reloaded
`GET/PUT /settings/feature-flags` desktop Runtime Configuration surface. The
feature is disabled by default and never sends a turn. Its bounded runtime
settings live in `event_edge_proposer` (`cooldown_seconds`, event window,
per-run candidates, daily call/token budget, and `scope_timeout_seconds`);
the dedicated `event_edge_proposer` routing category is selectable on the
desktop Model Routing page. `GET /observability/memory-event-edge-proposals`
requires `state.read` and returns only content-free counters, daily budget use,
and process-local discovery/timeout counts. No desktop or mobile channel
consumes candidate-edge records.

## Brief 204 Memory Event shadow recall

`event_shadow_recall.enabled` is exposed through the hot-reloaded feature flag
surface and defaults to false. The dedicated
`GET/PUT /settings/event-shadow-recall` endpoint manages bounded `uids` and
`char_ids` allowlists for grey rollout; these scopes may be enabled while the
global flag remains off. Shadow recall runs in parallel with the legacy read
path, records only event IDs and content-free metrics in `recall_trace`, and
never changes prompt injection or memory writes. Use
`GET /observability/memory-event-shadow-recall` (`state.read`) to inspect
status, budget, event/turn overlap and coverage, mapped/unmapped results,
temporal seed ordering, rejection, truncation, and timeout counters.
`overlap_rate` remains an event-level compatibility alias, never a comparison
between episodic/vector IDs and ledger IDs. Turning the flag off or clearing
the allowlists immediately falls back to the legacy path after config reload.

## Brief 158 TTS resource selection

The admin TTS status card queries `/tts-config?char_id=<logical-id>` and
`/tts-resources`. Responses expose the character's named-preset resolution and
logical authored voice resources without local filesystem paths. Preview calls
carry the selected character ID through the same `resolve_tts_config()` path as
real synthesis. Character preset binding remains owned by the existing
character asset-binding endpoint, so role inspection cannot overwrite global
TTS configuration.

## Brief 171 deployment mode and integration controls

`deployment.mode` is a process-owned enum with `local` as the compatibility
default and `remote_server` as the explicit server deployment mode. It is not
an admin hot-switch and is not accepted from owner-turn requests, prompts, or
models. The admin surface exposes only the read-only capability and preflight
projections:

- `GET /observability/deployment-capabilities` (`state.read`) reports logical
  enabled/disabled/online-required states and recent desktop WS ack time.
- `GET /system/deployment-preflight` (`state.read`) reports redacted topology
  and persistence checks; it does not scan ports or claim external tunnel,
  backup, HTTPS, or WSS health.

Remote mode disables server-local shutdown/sleep, exit signaling, filesystem
browsing, and desktop file fallbacks. Desktop actions require WS ack. Diary
mirror writes are a separate `diary.sync` capability and are not part of the
general admin settings surface.

## Brief 173 owner-turn API and admin observability

The admin navigation entry 「工具与连接 → 接口与部署」 is a read-only control
surface for the v1 owner-input contract, token metadata, receipt observability,
and deployment/diary summaries. It does not proxy chat, create a test turn, or
display token plaintext, request bodies, prompts, tool inputs/results, hashes, or
filesystem paths. Its receipt table reads `GET /observability/owner-turns` with
bounded filters and opaque cursor pagination; the endpoint requires `state.read`.

Deployment capability, preflight, desktop WS, and diary sync are shown as
separate signals. `configured`, `online`, `last_success_at`, and `E2E verified`
must not be collapsed into one health claim.

## Brief 163 Admin status and configuration UX

「系统状态」是只读摘要页：`/status` supplies runtime/data-environment basics;
`/model-presets`, `/proxy`, `/settings/relay`, `/scheduler/status`,
`/settings/feature-flags`, `/tts-config`, and the bounded error-log query supply
the remaining summaries. Each card shows its current value, source, effective
scope, refresh result, and a conservative note when configuration does not prove
external health. Editing is routed to 「高级设置 → 运行配置」, 「高级设置 →
TTS 配置」, Model Routing, or Scheduler.

The runtime configuration page uses boolean controls for boolean flags, selects
for enums, and bounded numeric/range controls with units. It keeps the endpoint's
apply mode visible, including `restart_required` for `qq.enabled`. The TTS page
keeps character scope, named-preset binding, global fallback, logical authored
resource selectors, provider parameters, emotion tiers, preview, and call logs in
one independent editor. Provider keys are write-only; advanced key/value rows are
folded and boolean values use a selector rather than free text. Resource labels
are logical and redacted, with no local physical-path input. Preview success is
reported as synthesis success only, never as client playback or provider health.

## Brief 216 Memory Event control and effective state

The backend-only shadow recall and relation proposer remain disabled by default.
Runtime Configuration shows desired state, effective state, and apply mode. The
shadow card retains global, UID, and character allowlists and hot-reload result.
The proposer flag states that it writes unreviewed candidates only; Model Routing
shows the effective `event_edge_proposer` preset and model separately from the
feature flag. Effective states distinguish disabled, no eligible scope, running,
schema blocked, route blocked, and enabled but not yet run.

The Memory Event evidence page consumes the existing `state.read` endpoints and
shows aggregate calls, budgets, source filtering, failures, timeouts, coverage,
and latest-run evidence. Empty evidence is labelled `未运行`, never healthy.
No prompt, body, event ID, token text, or local path is projected. Desktop and
mobile clients do not add settings or consume these backend diagnostics.

## Brief 217 EventContext observer

`event_context_observer.mode` is a hot-reloaded admin runtime-config control with
`disabled` as the default. `GET`/`PUT /settings/event-context-observer` require
`admin`; `observe` records content-free traces, while `enforcing` is accepted
only when the durable S1 sample gate has at least 200 committed canonical turns
and 100 accepted stimuli with no hard identity/orphan failures. Otherwise the
endpoint returns readiness gaps and leaves the mode unchanged. `GET /observability/event-context` requires `state.read` and
returns only desired/effective/run state, aggregate counters, latency buckets,
and redacted status codes. It never returns source text, complete IDs, user IDs,
or media data. No desktop/mobile setting or protocol is introduced.
## Brief 228 character document retention

`character_document_library.retain_raw_uploads` is an admin-only deployment
configuration switch and defaults to `false`. It is read during upload import;
there is no desktop/mobile setting or hot-reload endpoint. Observability reports
effective retention counts without content, raw bytes, or filesystem paths.

## Brief 217 EventContext readiness repair

The observer remains backend-only and defaults to `disabled`. Its read projection
now includes startup ledger readiness, persistent chain linkage, and latency
percentiles. Enabling `observe` reruns existing-ledger initialization and returns
503 without changing config if a ledger fails or the bounded scan truncates.

## Brief 229 Agent Runtime architecture contract

Brief 229 only freezes the future Clock/Trigger, Task, Agent, Capability, and Interaction plane
boundaries. It adds no setting, endpoint, worker, client control, or capability. Existing autonomy,
scheduler, tool, deployment, and EventContext controls retain their current ownership and semantics.
Future Briefs 230-237 must add configured/effective/runtime-observed controls in the same change as
each implemented task or capability; clients must not infer availability from existing tool names.

Brief 237 closes legacy scheduler execution lanes: due schedules use the normal Reality interaction
adapter, the reminder JSON fallback is retired, and manual direct-trigger execution is unavailable.
Task cancellation is admin-only via the metadata endpoint in the interface catalog.

## Brief 232 Agent Work Sessions

Agent Work Sessions are backend-only and have no client setting. The scheduler's
`inner_diary_write` maintenance task uses the Reality Task Manager plus the independent
`work_session_id` store. `GET /observability/agent-runtime-work-sessions` exposes bounded lifecycle,
artifact-kind, digest, and error metadata only; it never exposes work context, prompts, diary正文, or
paths. `daily_journal` remains governed by the existing autonomy signal controls.

## Brief 233 workspace capability

`workspace_access` is a local deployment capability with explicit roots and independent read/list/create/
update/delete permissions. It defaults disabled and is unavailable in `remote_server` mode. Limits are
bounded by file bytes, aggregate bytes, concurrent operations, read characters, and listing entries.
The backend adapter rejects project data, symlinks, and sensitive names; mutating operations use the
Reality Task Manager receipt boundary. The current control surface is backend configuration, the
generic admin feature-flag control, and read-only `GET /observability/agent-runtime-workspace` plus
Task receipt observation; no desktop/mobile setting or protocol is introduced until a client consumes
workspace results.

## Brief 234 process runner capability

`process_runner.enabled` is a local-only, default-off capability. It executes only allowlisted
program types already inside a configured `workspace_access` root, accepts structured argument
arrays, never invokes a shell, and keeps network disabled. `process_runner.limits` bounds wall time,
CPU, memory, process count, and captured output. The effective redacted state and task outcomes are
available from `GET /observability/agent-runtime-processes` with `state.read`; `remote_server`
always disables execution. Desktop and mobile do not yet expose a task/result control surface.

## Brief 239 browser worker confirmation hardening

Brief 239 binds each task to a normalized `http/https` URL (fragment removed, query retained),
operation, typed bounded parameters, Reality principal, and Workspace path digest. The server
stores only a `browser-request.v1` fingerprint and safe summaries; substitutions fail before claim

Browser policy and task control are owned by the backend admin surface. The admin-only
`GET/PUT /settings/agent-runtime-browser` contract edits the local allowlist, worker adapter,
bounded limits, and upload/download switches; task creation and confirmation use the same
backend-owned owner/character scope. Desktop clients do not expose browser task submission or
require a manually entered `botUserId`.
with `task_request_mismatch`. High-risk confirmation is a one-shot state transition, while raw
queries, paths, profiles, credentials, and page payloads stay out of receipts and observability.

`browser.enabled` is an opt-in local capability and remains disabled by default. Explicit
`allowed_domains`, bounded page limits, and an optional reviewed Playwright adapter are required.
Each task runs in a private profile and uses Reality Task Manager receipts; credentials, cookies,
headers, profile paths, URLs, and page source are excluded from observations. High-risk operations
park in `waiting_confirm`; pause, cancel, timeout, browser disconnect, and unknown results are
durably represented. Downloads and uploads must use the Workspace capability. The browser
observability endpoint exposes only effective state, limits, worker health, and aggregate counters.

The backend admin surface is the sole configuration and submission control plane. The former
`/agent-runtime-browser/*` owner-bridge routes and `GET /observability/agent-runtime-browser` had
no current Emerald-client source callers after Brief 72 removed the Tauri commands, so the routes,
legacy request schema, and duplicate serialization have been retired. The three-repository catalog
and OpenAPI assertions record the deletion. The remaining settings/task projections do not return
URL/query, cookie, token, profile path, page body, or raw params in receipts/observability.
## Image upload OCR routing

Model Routing exposes admin-only `GET/PUT /image-recognition` for upload mode
(`vision` default, `ocr` opt-in) and an independent OCR connection. General Vision
Base URLs remain editable. GLM Layout Parsing uses an exact Endpoint URL; OpenAI
Chat Completions uses a Base URL. Keys are write-only; blank saves preserve keys.
The page shows configured/effective state and request address; ready is not tested.
Runtime call metadata uses `/observability/api-calls?caller=image_ocr` (`state.read`).
Desktop/mobile keep `/upload/ingest`; screen automation keeps its existing connections.

## Forced streaming compatibility (2026-09-09)

`model_presets.presets.<name>.force_stream` defaults to false. The admin Preset editor exposes it through existing admin-only GET/PUT model configuration with hot reload. Chat Completions only; unsupported protocol combinations are rejected. The selected preset governs both tool decisions and final generation for all channels. Clients do not duplicate this setting or request extra scopes. Request snapshots expose the actual stream flag. Browser verification is incomplete: this session provides no Browser execution tool. JS syntax and focused API/protocol regressions provide alternative validation; real gateway verification remains observe.


## 设置重整：角色模型只读状态（2026-09-10）

角色模型绑定 GET/PATCH 返回追加 `resolved_chat_model`、`global_profile`、
`binding_source`、`chat_configured`。不返回密钥或连接地址；配置齐全不代表网络测试成功。
清除绑定仍发送 `model_routing: null`，返回当前全局方案和实际模型。
全局生效投影已修正角色开启多步工具循环覆盖全局默认关闭时的错误展示。

## 设置重整（Brief 242，2026-09-10）

当前管理面一级导航为概览、功能与行为、服务配置、创作、观测、运维与调试。
功能页区分全局配置、工具执行许可和后端 effective state，支持搜索；调度、自主活动、
语音和工具各自进入真实细分页。服务首页只展示配置状态，未知与未配置分开，配置齐全
不证明连通性；模型、向量、邮件、日记、代理分别编辑。

角色模型/声音/形象绑定位于服务配置；模型重置仍为 PATCH 的 `model_routing:null`，
纯文本卡在 UI 中只读。创作页可保存 Reality 世界书/提示词启用组合，上传或恢复角色头像。
对话模式、风格、分条、思考、多步工具调用在对话设置；消融、请求快照和自主测试入队
在生成调试，完整自主提示词也移至该页。观测首页按排查任务分组，调用记录显示时间、
状态、调用方、模型、失败类别及耗时，支持筛选，原始详情按需展开。语音入口限定 caller=tts。

桌面只保留界面、连接、本机采集同意、播放、活动/桌宠交互和当前角色切换；当前角色
模型状态只读。Activity 外观并入聊天偏好，原五个偏好 key 不变。后端 scope、REST 写入
路径、配置锁、WS/poll/ack/TTL 与发送链未改变，没有新增存储或客户端权限判断。
`chat_configured` 对兼容协议允许无密钥连接，对原生 Anthropic 保留密钥检查；仍不表示
上游已测试。旧 REST/IPC 包装保留给其他调用方，旧独立偏好组件已删除。

验证：后端相关 UI/API 89 项通过；桌面相关 Vitest 40 项、TypeScript、生产构建通过。
Playwright 加载真实管理面资源并清缓存，18 个新页、中英文切换、搜索、重置、开关和
资产组合写请求通过；桌面真实 React 组件的阅读保持、角色切换、未知状态、聚焦刷新、
失败重试通过。浏览器 API/IPC 使用夹具，未修改生产配置。
`observe`：真实 Tauri 原生窗口与真实上游连通性未验收；手机未做设备回归，本次未修改
其设置调用、HTTP/WS、Flutter/Android、relay 或通知路径。不得把夹具结果当作实机结果。

## 模型目录发现（2026-09-11）

管理面「模型连接」编辑框提供获取可用模型、选择和手填。admin-only
`POST /model-presets/discover` 接收 base_url、api_key、preset_name（可选）、
api_protocol、anthropic_auth_mode；use_base_model 可引用基础模型连接。
空 key 仅在目录地址与已保存连接相同时复用密钥。请求不会保存或修改配置。
根 URL 映射 /v1/models；已有版本/代理路径保留，完整生成端点替换为 /models。
返回 status、models，成功可含 has_more；不支持、空目录、鉴权、格式、网络和超时
分别返回状态，均保留手填。目录不验证生成协议、工具能力或实际生成可用性。
管理面是唯一编辑入口，双端仍消费既有 routing profile，无需新增客户端设置。
本地 36 项回归通过，真实静态资源浏览器清缓存刷新验证了选择与手填降级；
远端目录响应在浏览器测试中模拟，真实中转可用性属于 observe。
## 聊天回合思考读取（2026-09-11）

`current`：GET `/chat/turns/{turn_id}/reasoning` 要求 memory.read，标准 desktop/mobile
profile 可读取已关联的 Reality owner 回合，返回 available/entries（含 parts）。
桌面/手机共享 owner 入口在生成期间关联调用，工具循环子任务同样关联，post-process
前停止采集关联，防止后台思考串入；关联失败不影响回复。旧归档不推测关联。
原 admin-only 全局归档接口不变。无新增生成开关，不修改正文、WS、poll、ack 或 TTL。
`roadmap`：客户端按气泡展示和真机验收、QQ/主动消息/Dream/Stage 回合关联。
前端工单见 cc-tasks/244-frontend-reasoning-handoff.md；按用户要求未跨仓修改。
## 角色心声文风（2026-09-11）

current：管理面「对话与思考」通过既有 GET/POST /settings/thinking（persona）控制
character_voice（默认 true、随 thinking.enabled 生效）。voice_preview 给出当前拼接提示、
情绪/稳定变体和 enabled/effective/blocking_reason；output_guaranteed=false 明确其只是通用
提示引导。native 不增加 LLM 调用，monologue 复用已有前置调用。可能影响最终回复。
桌面只控制本地显示；手机思考展开 UI 仍 roadmap。见 [thinking-voice.md](thinking-voice.md)。

## IME v2 接收预备（2026-09-11，历史条目）

`current`：POST `/v1/ime/drafts`（sensor.write）按设备 token label + id + revision
事务覆盖完整草稿；默认 ime_ingest.enabled=false，通过 feature-flags 的 ime_ingest
控制。admin-only GET `/observability/ime-drafts` 提供三小时内原始字段与 effective 状态。
独立 inbox 不接入 LLM/记忆/任务/广播；原 sensor、聊天、WS、ack、TTL 不变。
`observe`：尚未启用真实 IME 传输；私网 HTTPS、证书与设备配对需部署验证。
只读核对输入法真实 v2 代码，未跨仓修改。详细协议与限制见 docs/ime-ingest.md。
## 周信发送断链修复（2026-09-11）

`current`：letter_writer 曾被标为 migrated，winner 仅转入通用 TALK 信号，
而 autonomy 没有邮件发送适配器，导致原邮件回调永远不执行。现将邮件能力准确标为
active，仍经原 QUIET/活跃窗口/DND/角色主动开关与周契约准入，执行仅走邮件子系统。
无论 autonomy 是否开启，scheduler 都经 run_shadow_tick：已迁移的聊天触发器只入队，
不执行旧回调；原生邮件/明信片可继续交付。没有恢复 retired assistant speech outlet。
周契约未到期（已发送、退避、租约或窗口外）时不再落入事件理由旁路。

`current`：GET `/observability/mail-weekly`（state.read）显示配置齐备、scheduler 开关、
窗口、scope_ready、当前周状态及下一次重试，沿用 `/observability/mail-executions` 查阶段。
周信是“每 ISO 周最多成功一封，到期可候选”，不是固定星期保证送达；阻断可导致本周未发。
原手动 trigger 仍只排队测试信号，不会直接发信，不能再按旧文档把它当 SMTP 验证入口。

`observe`：本地配置中 mail/scheduler 已开启且 SMTP 字段齐备，历史台账最后记录为
empty_content/quality_rejected，没有 SMTP 成功证据。本次仅 mock 回归，不发送真实邮件，
不修改收件地址、凭据或运行配置；部署后下一次合法调度需要观察生成质量和 SMTP。
`roadmap`：Task/Agent authored artifact 邮件迁移尚未完成，不把通用 TALK 信号当邮件实现。
管理面继续使用邮件配置和只读观测；桌面/手机不用新增发送控件，未跨仓修改。

验证：41 项邮件/周契约/gating/信号回归通过；相邻测试文件另有一个既有 MCP 静态版本
断言失败（要求早期 brief-195 版本字串），与邮件执行无关，未修改该旧断言。
## 小红书分享工具（2026-09-11）

`current`：read_xiaohongshu 为默认关闭的 info 工具，管理面工具页通过
GET/PUT `/settings/xiaohongshu` 配置读取服务与数量上限；开关共用
`tools.read_xiaohongshu.enabled`。返回正文摘录、有限图片识别及评论样本。
`local_service` 开启后由后端启动、检查和回收固定版本本地服务；安装/扫码是 admin-only 操作，
GET 返回 installed/state/login_status/install_error 等只读状态。客户端只展示控制面，不自行启动进程。
QQ/desktop/mobile 共用既有探针/工具循环、暴露白名单和 origin 闸门，无新客户端协议。
元数据观测复用 `/observability/api-calls?caller=read_xiaohongshu`；remote_status
明确为 not_checked，配置就绪不代表站点登录或连接健康。详见 docs/xiaohongshu-reader.md。
`current`：本地 Docker 服务已部署、扫码登录并开启配置；用户分享正文、图片识别及10条评论样本实测成功。新增单进程串行与15–25秒冷却、拒绝后5分钟退避；配置查询提供 busy/cooldown_seconds。`observe`：运行中后端重启加载新代码及原生聊天验收。
真实帖子与真机效果、评论完整性不作完成承诺；评论始终标记样本，图片失败明确降级。
`roadmap`：视频转录、完整评论遍历不在本次范围；无跨仓原生客户端代码修改。

## Model probe diagnostics (2026-09-11)

Admin-only preset test now uses 256 output tokens, a 30-second total budget and zero SDK retries. It returns category, safe error/hint, HTTP status, error type, declared protocol and request path. Provider bodies and credentials are never echoed; UI uses textContent. Empty visible output is a warning rather than evidence of working conversation. Network/TLS/timeout, authentication, quota, endpoint/model, rejected parameters and response schema are distinguished.

Validation: 69 related tests passed; cache-cleared Chromium Model Routing test rendered quota/protocol/status guidance. Live bounded probes succeeded for the configured Grok Responses and Gemini Chat Completions presets; another relay returned HTTP 403 INSUFFICIENT_BALANCE. No protocol/routing setting was changed. This is unrelated to desktop/device WS protocols. Native clients continue to open the backend management UI; no new local settings or secrets.


## Life records v1 backend (2026-09-11)

current: /life-records capabilities/sync/list/detail/observability are implemented with dedicated life_records scope (mobile profile), transactional images/jobs/receipts, revisions/tombstones, bounded snapshot pagination, asynchronous OCR/vision, correction locks and owner-only read_life_records tool. Admin Service Configuration owns switches, effective recognition, task/device/audit observation and failed-task retry. See backend docs/life-records.md and brief 245. No changes to chat/poll/ack, notifications or payment.

observe: physical phone/network/Doze and live image-model end-to-end validation remain open. Backend tests include atomic retry, edits versus recognition, deletion, scopes, decimals, snapshot pagination and worker recovery; 72 initial scope/store tests and 39 focused/mobile regressions passed. Android LifeRecords/security/credential targeted task succeeded (cached unit-test output). Admin browser hard refresh used real isolated API. Desktop native record UI and original-image refetch remain roadmap.


## 用户称谓接线补充（2026-09-11）

current：复用 GET/PATCH `/users/{user_id}/pronoun`（admin），默认“她”，允许“她 / 他 / 祂 / TA / 它”；管理面「个人设置 → 用户称谓」编辑，保存后下一次组装生效。GET 返回有效称谓，原始配置可经 `/users/{user_id}/facts` 观测。Reality 身份约定、事实边界、长期观察、日记/历史/重点事实框架使用所选称谓；显示名及多人 speaker 归属保留。引用、角色卡、历史正文不做全文替换。未新增配置源、存储或网络调用。

桌面使用现有 openAdminPanel 管理面桥，手机 `/mobile/chat` 继承后端组装；无新 WS/IPC/ack/TTL/锁或通知协议。手机原生称谓编辑及 Dream 专项称谓统一为 roadmap；真实模型文风、双端真机体验为 observe。

thinking 原生提示标题改为【你们约定的思维链thinking输出方式*特调】，保留原有文风正文与执行闸门。

## Tool display receipts (2026-09-12, partial)
Owner reality execute_structured emits tool_activity with event_id, chain_id, char_id,
source=reality, origin=chat|autonomy, tool_name, status and ts. No tool args/results, ack,
TTS, turn_sink delivery or proactive budget effects. Existing dispatcher gates remain authoritative.
Terminal display_activity metadata reuses the bounded 30-row action_trace store; existing
/observability/tool-traces reads it. /chat-log/dates and /chat-log/{date} expose recent receipts
under memory.read, replacing a matching action_trace echo by event_id. Older echoes are narration.
Desktop defaults chat.toolActivityVisible=true; the switch is display-only. Backend action_trace
settings continue to control persistence. No mobile poll/ack/relay contract changes.
Validation: 57 backend regressions, 7 client regressions, build and Edge IPC fixture passed.
open: native Tauri/restarted backend integration. roadmap: mobile tool chain UI and full unbounded
historical tool receipts. Client evidence: docs/tool-activity-2026-09-12.md in the desktop repository.

## Autonomy XML compatibility and cadence (2026-09-12, partial)
Autonomy opts into existing XML tool encoding in chat_turn; ordinary native-only callers keep
strict FC semantics. Parsed tools remain bounded by the exposed names; private prose never sends.
Existing run.events now retain safe evaluation_error/error_type metadata. No new store or scope.
Runtime settings were updated through admin APIs and read back: interval 30 minutes, 48 evaluations/day,
global proactive gap 45 minutes; daily talk cap 8 and evaluation minimum interval 15 minutes unchanged.
open: restart backend to activate code and observe real delivery/circuit recovery. No real test message sent.
Screenshot planning remains separate: desktop visual sampling is shadow-only and local consent is off;
mobile offers a text snapshot, not this requested on-demand image capture. See desktop docs/proactivity-2026-09-12.md.

## 生活记录描述与路由（2026-09-12）

后端 /settings/life-records 管理同步、后台同步、角色只读、原图保留；饮食/购物车固定 vision，账单固定独立 OCR，连接仍在模型页编辑。/life-records/capabilities 与设置观测逐分类返回 recognition_routes；普通聊天图片用途选择不影响生活记录。管理面显示任务状态和逐条重试按钮，技术台账折叠。手机展示独立图片描述与用户备注，不复制模型配置。角色查询原开关生效，用户备注优先；桌面经管理面配置，原生生活记录列表仍为 roadmap。

## 管理面样式定稿实施（2026-09-12）

灰绿状态色、中性按钮、分隔线设置区、复杂表单折叠及 JSON 填表已接入；观测与工具页复用现有 state.read 全局状态表。原保存 API、客户端管理面 bridge 和手机消费路径不变。
current / open / roadmap / observe 与验证证据见 [admin-design-implementation.md](admin-design-implementation.md)。
roadmap：逐请求的视觉、权限、队列、发送、ack/TTL 尚未合并入十类全局状态表，不能据此宣称端到端链路全部可观测。observe：原生容器与真实服务未联调。
