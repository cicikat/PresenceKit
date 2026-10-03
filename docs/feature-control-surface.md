# 功能控制面事实清单（最后核对：2026-09-28 模型出站网络自适应）

## 模型出站网络自适应（2026-09-28，current）

`proxy.model_connection_mode` 默认 `follow_global`，兼容旧配置：全局代理开则模型走
`proxy.http`，关则直连。可选 `auto` / `direct` / `proxy`。`auto` 每次模型请求解析目标
DNS：`198.18.0.0/15` Fake-IP 或解析失败时用已配置 HTTP 代理，其余直连；DNS 变化不需
重启。`auto`/`proxy` 保存时要求有效 `proxy.http`。`GET/PUT /proxy` 读写该字段，保存后
热重载文本模型与视觉客户端。管理面「代理与中继」提供下拉与说明；系统状态摘要只读展示
当前模式，编辑入口仍在该页。手机聊天继续复用后端模型连接，桌面/手机无新设置。真实模型
网络与手机端验收 observe。静态版本 `v1-call-presence-1`。其他出站工具仍用各自代理
策略。

## 通话内主动性放宽 `video_call_presence`（2026-09-30，默认关闭）

配置块 `video_call_presence`：`enabled`（默认 `false`）、`max_talks_per_call`（1–10，默认 3）、
`min_gap_seconds`（30–3600，默认 180）、`silence_seconds`（5–120，默认 30；全局规则是 120）、
`camera_signal_interval_seconds`（15–600，默认 60，与原常量相同）。管理面「自主活动安排」页有卡片，
`GET/PUT /video-call/presence`（`admin` scope，含本进程发言计数与被拦下的闸分布，同样并入
`/observability/video-call` 的 `presence` 字段）。桌面/手机无新设置。

生效条件：开关开、摄像头会话存活、且 job 带 `video_call_camera` 信号。此时只放宽三件事：
`camera_silent` 准入的静默窗口与循环内"用户又说话了"取消窗口（两处同一判据）、相机来源的最小评估
间隔（取全局值与 `min_gap_seconds` 的较小者）、全局发言间隔与每日上限改为**每通电话**上限 + 间隔
（计数放在摄像头会话里，挂断即恢复，下一通从 0 开始）。不放宽：免打扰、梦境守卫、连续未回应硬停、
熔断、`record_send` 记账（豁免的是"能不能发"，不是"要不要记"）。

与开关无关的修复：`policy.admission()` 里 `latest` 原取所有 source 的 `last_evaluated_at` 最大值，
interval / topic_followup 每 tick 刷新它，相机信号总被判 `duplicate`；现在**只有相机 job**（
`allow_camera_silence`）按 `video_call_camera` 自己的时钟判定，其余 source（ime、desktop_wake 等）语义不变。
同文件还修了一个既有崩溃：函数内的 `import time` 使 `time` 成为局部变量，`camera_silent` 那一行在
CHATTING 状态下 `UnboundLocalError`，即通话中用户一说话相机 job 就在准入处崩掉。

## 本地 STT 运行参数 `stt_local`（2026-09-30，backend current；工单 H 增加 sherpa-onnx 引擎）

配置块 `stt_local`：`engine`（`faster_whisper` | `sherpa_onnx`，**默认 `faster_whisper`**，不改变现有部署）、
`timeout_seconds`（5–120，默认 20，两个引擎共用），以及按引擎分组的参数：

* faster-whisper 组（保持原有扁平键）：`model_size`（tiny/base/small/medium/large-v3，默认 `small`）、
  `device`（auto/cpu/cuda，默认 `auto`）、`compute_type`（int8/int8_float16/float16/float32，默认 `int8`）、
  `beam_size`（1–10，默认 5）。
* sherpa-onnx 组（子块 `sherpa_onnx`）：`model`（目前一个：`zipformer-bilingual-zh-en-2023-02-20`）、
  `decoding_method`（`modified_beam_search` 默认 | `greedy_search`；热词只在束搜索下生效）、`num_threads`（1–16，默认 2）、
  `max_active_paths`（1–16，默认 4）、`hotwords_score`（0–10，默认 1.5）、`endpoint_silence_seconds`（0.3–5，默认 0.8，
  句间停顿切分）、`repeat_collapse_min_run`（3–20，默认 4：连续相同字 ≥N 折成 2 个）、`download_base`（模型下载源，默认
  HuggingFace，可填镜像；下载始终按 SHA-256 校验）。

只严格校验**当前引擎**那一组；另一组按有效值保留、无效值退回默认，所以来回切换不丢参数。实例身份（`_key`）含引擎，
切换必重建。远程 OpenAI 兼容连接仍在 `stt_presets`，两处不重叠；**配置里存在 `stt_presets` 块时 `POST /transcribe`
远程优先**，此时这里选的本地引擎不会被用到（管理面页会提示）。
生效方式：下一次 `/transcribe` 发现配置与已加载实例不一致即重建，新模型加载并跑通后才替换，
失败时保留旧实例；手写无效值退回默认。**选了 sherpa-onnx 而缺包/缺模型/校验失败/自检失败时明确报错，绝不回落到 Whisper。**
`auto` 回落 CPU 与原因可在 `core.stt_local.snapshot()`
看到。观测：`/observability/api-calls`（`state.read`；`provider=sherpa_onnx|faster_whisper`，`model` 含模型 id）。
模型权重不进 git：显式下载到 `get_paths().stt_model_dir()`（`data/cache/stt_models/<model>/`，约 200 MB），
`POST /settings/local-runtime/stt/sherpa/download`（`admin`，后台线程，进度见 `GET /settings/local-runtime` 的 `sherpa.download`）。

管理面页面「本地模型运行」（`local-model-runtime`，服务分组，`admin` scope）只接本地 STT：
`GET /settings/local-runtime`（configured / effective / 回落原因 / 最近一次切换结果）、
`GET /settings/local-runtime/hardware`（只读探测，不加载模型，缺运行库时给出下一步）、
`PUT /settings/local-runtime/stt`（先热切换，成功后才写 `config.yaml`；失败返回 409，
`saved:false` 并带仍在使用的实例，配置与旧实例都不动）。每个选项旁标注内存/速度/精度代价。
桌面与手机无新设置。

## 每日互动预算加倍（2026-09-21，current）

`scheduler.max_daily_proactive` 默认 16；`PUT /scheduler/config` 可写 1–64，管理面调度器页可改。
自主评估默认 `daily_evaluation_budget` 96（API 上限仍 100）。屏幕观察本身没有独立日计数，
卡点是评估预算与主动发言账本。热加载后当日剩余额度立即按新上限计算。桌面/手机无新设置。

## 音频分析与共同听歌（2026-09-21，A–G current）

工单 260 四路默认关闭开关，配置根 `audio_music`：`speech_analysis`、
`music_analysis`、`music_control`、`music_autonomy`。STT 已配置不等于声学分析可用。
管理面功能开关总览热读写这四路；`GET /settings/feature-flags` 同时返回 desired 与
effective（语音分析还看 STT effective 与 numpy）。B 提供 `core/audio_analysis.py`
（PCM WAV + numpy）；缺 numpy 或解码失败时分析 `unavailable`/`failed`，不挡现有转写。
C：`speech_analysis` 开启后接入凭据与 `3.8_audio_impression`；关闭时仍只注入 253.6 tone。
D：`core/listening_store.py` 维护曲库/session/history/stats/角色注释；
`GET /observability/listening`（state.read，无标题/注释/正文，含开关 effective 与
adapter 元数据）与 `GET /listening/history`、`GET /listening/notes`（memory.read）。
`music_analysis` 关闭或音频非 backend_readable 时分析保持 unavailable。
E：`music_control` 默认关；开启后 `core/player_adapter.py` 接受命令，真实事件由管理面
HTMLAudioElement 宿主经 `/player/*`（admin，状态只读走 state.read）写入账本。断连策略
`local_may_continue_unsynced`。
F：六项听歌工具挂在 `music_control`；`music_autonomy` 关闭时不入队 `music_playback`。
G：管理面观测页 `observe-listening` 只读展示账本/播放器/开关；静态版本
`v1-260-admin-surface-1`。隔离管理面 Playwright 证明真实 HTMLAudioElement 出声并暂停
同页 TTS peer。
设置写 admin；转写凭据仍 chat；正式桌面 transport 仍预留 ws.desktop，本轮不扩展
v0.1 desktop action。桌面/手机无新设置。旧 `play_netease` / 媒体键不是本控制面。
精确单位、状态机、计数口径和宿主盘点见 [audio-perception.md](audio-perception.md)。

## 天气查询直连（2026-09-18）

`weather` 仍是已注册 info 工具，角色可读取当前天气。`tools.weather.use_proxy` 默认
`false`：请求直连 wttr.in，不继承全局 `proxy`。梯子环境下直连通常更稳；需要走代理时
在管理面「功能开关总览」打开天气工具后，到「工具」页勾选「天气请求走全局代理」。
`GET/PUT /settings/tools` 读写 `weather.use_proxy`，热更新，无新端点。桌面/手机无独立
天气设置。

> 本文是后端功能开关、effective state、权限和观测入口的权威文档。跨仓调用方只记录
> 自己的设置归属与接入差异，完整映射请查 [三仓文档总索引](three-repo-doc-index.md) 和
> [三仓接口总账](three-repo-interface-catalog.md)。

## 固定会话 scope（2026-09-17，backend current）

`GET /auth/whoami` 广告 `capabilities.session_scope=v1`；`POST /v1/sessions` 签发
24 小时进程内 grant。无独立开关，旧请求仍跟 live `active_character`。
`GET /observability/session-scope`（`state.read`）提供脱敏 effective state、TTL、计数和拒绝原因；
不得复用 deployment-capabilities。合同见 [session-scope-contract.md](session-scope-contract.md)。
Dream settings 归属为 per-character：`GET/PATCH /dream/settings` 读写当前角色树
`data/runtime/dreams/{char_id}/settings/{uid}.json`，不把 live active / default 目录
偶然位置当共享语义。旧 uid-only 文件仅冻结的历史默认角色可读。桌面/手机消费者仍走
既有 `/dream/settings`，无新设置 UI；真实联调仍 open。只读观测
`GET /observability/dream-settings`（`state.read`）返回 effective char、canonical 是否存在、
是否有资格读 legacy，不含正文。

## 聊天产物文件（2026-09-16）

Path C `artifacts` 类（`write_artifact` / `update_artifact` / `read_artifact` / `list_artifacts`）写出沙盒文本文件；不进 Path A 探针。没有独立客户端开关，暴露面由 tool loop categories / 角色 `presence_ext.tool_categories` 决定。管理面观测页 `observe-chat-artifacts` 只读元数据（state.read）；下载/预览走 chat scope。桌面气泡消费 live payload，不新增设置。手机 UI 为 roadmap。

## 资料接续（2026-09-13）

生活记录角色可读沿用 enabled/character_readable；已就绪未读资料随下一次 owner 私聊/主动机会提供。
管理面服务配置卡增加有界待评估数量、结果保留窗口与“已读不等于回复”说明。
GET /settings/life-records 增加 continuity，独立 state.read 观测为 /observability/context-continuity。
主动工具 safe_summary 沿用 action_trace.enabled 控制，保留 24 小时/最多 12 条，prompt 最多 3 条。
未配置的四个资料回读工具在 autonomy 继承读取授权，显式禁用和角色权限优先；不新增客户端设置真值。
图片 cached/vision/ocr 是单次工具参数，不改全局模型配置。三端闭环与限制见 [施工记录](media-continuity-2026-09-13.md)。

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

现实资产启用（2026-09-14）：管理面创作页把启用组合与头像拆成设置行。
`GET /settings/prompt-assets` 的 lorebooks/jailbreaks `label` 由扫描写入（标题/关键词/条目标题），`id` 仍是 stem。
`PATCH` 只接受 id，不接受 label/filename。无新落盘或观测端点；桌面仍走管理面，手机若展示资产名需改读 label（observe）。

危险模式与功能分类（2026-09-14）：`PATCH /system/meta-mode` 开启 danger 不再写过期时间，忽略 `ttl_seconds`；GET 在 danger 时 `expires_at=null`。管理面功能与行为按感知与电脑、输出与互动、外部能力分组，开关行带细分跳转；`device-policy` 去掉 TTL。手机若仍传 ttl 被忽略且保持常驻（observe）。

管理服务的设置面分三层：

RPG Dream's `rpg_kp` route is a backend capability, not a client setting; it is now selectable in admin Routing Profiles alongside `sensor_judge` and `monologue`（前置独白）. Effective routes remain visible with the other model categories. Unmapped ordinary categories resolve `default_preset → chat → first preset`; do not describe this as “未配置直接回落 chat”. Desktop/mobile do not gain a new toggle.

后台练习盲评不属于管理面热更新 API，但其模型选择遵循同一模型路由真值：
`practice.reviewer_category` 是 routing profile category（默认 `consolidation`）；可选旧字段
`practice.reviewer_preset` 是严格的直接 preset 名并优先于 category。未知 preset 会让该次
练习明确失败并进入 scheduler 异常记录，不会静默回落 chat。

所有写入 `config.yaml` 的设置端点统一经过 `admin.config_control`：写请求按进程内锁串行，
用临时文件原子替换，并以读取时快照做三方合并，避免并发设置覆盖无关字段。运行时只使用
`config.yaml`；管理面和手工编辑修改的是同一份配置。

- persona 级：`/settings/model-routing`、`/settings/tts-desktop`、`/settings/tts-auto-play`、`/settings/tool-loop`、`/settings/thinking`、`GET/PUT /output-segment-enforce`，供客户端使用；不返回模型密钥。段落兜底开关热更新 `output.segment_enforce`，只影响发送副本（桌面流式 delta、最终 canonical 与非流式输出），默认关闭。
- admin 专用配置：`/model-presets/*`、`/proxy`、`/tts-config`、`/sticker-config`、`/scheduler/config`、`/settings/relay`、`/settings/mcp`。routing profile 也包含 `sensor_judge`、`rpg_kp` 与 `scenario_reconcile`：`sensor_judge` 是后台裁决专用 category，`rpg_kp` 是 RPG Dream 中立裁决，`scenario_reconcile` 是 Dream 发送后的语义校准 category。`sensor_judge` / `scenario_reconcile` 都应映射到稳定的轻量 `chat_completions` preset，缺失时分别兼容回退 `intent → chat`；`rpg_kp` 缺失时回退 `chat`。它们使用短 timeout 与零 SDK retry，未在桌面设置页单独暴露。preset 的 `api_protocol` 由管理面和 `PUT /model-presets/presets/{name}` 管理，取值为 `chat_completions`（默认）或 `responses`；它独立于 `provider_kind` 与 `tool_call_mode`，保存后热重载，不会静默切换 API。`POST /model-presets/presets/{name}/rename` 会原子重命名 preset 并更新所有 routing profile 引用、`default_preset` 和 `fallback_routes`，随后热重载。`POST /model-presets/routing-profiles/{name}/rename` 会同步 `active_routing` 与 `fallback_routes` 键并改写角色卡 `presence_ext.model_routing`；`DELETE /model-presets/routing-profiles/{name}` 不能删最后一个，删当前生效方案时切到剩余方案（优先 `default`），绑定该方案的角色卡清除为跟随全局，该 profile 的失败兜底一并删除。`PUT /model-presets/default-preset` 设置未映射 category 的默认 preset；空字符串清除。`PUT /model-presets/routing-profiles/{name}` 允许空字符串清除单个 category 映射；可选独立 `fallback` 映射不嵌进 category 字符串，缺省不改已有兜底。仍被 `default_preset` 或 `fallback_routes` 引用的文本 preset 拒绝删除。失败兜底旧配置默认关闭；主路由用尽现有重试后最多换一次已配置的兜底 Preset。观测入口 `GET /observability/llm-failover`（`state.read`）返回尝试/逻辑/兜底分母与跳过原因，不保存 prompt、正文或密钥，也不覆盖 `GET /observability/api-calls`。桌面继续只切已有 profile，不新增失败兜底设置。
- 单人 Scenario 的 Dream setting `scenario_injection_mode` 由 `GET/PATCH /dream/settings` 管理，取值为 `strict_stage`（默认）或 `full_script`；full mode 只在下一次入梦时冻结，预算超限会明确拒绝入梦。Sandbox、Mirror、Group Dream 与 Reality chat 不消费该字段。该端点按当前角色隔离，不是 per-user 共享。
- LLM/model preset/vision/proxy 热重载会等待旧 AsyncOpenAI/httpx client 关闭后再返回；关闭失败
  fail-open 记 warning，旧实例已从 registry 摘除，新请求只会按新配置惰性建 client。
- admin 功能开关白名单：`GET/PUT /settings/feature-flags`。只接受 `settings_feature_flags.FLAGS` 中已有运行时消费者的布尔字段，不接受密钥、路径、额度或任意 YAML。每项返回 `apply_mode` / `restart_required`，PUT 返回 `reload_status` 和本次确实改变且需要重启的字段。`qq.enabled` 只在 `main.py` 启动阶段注册通道、回调和监听任务，因此明确为 `restart_required`，不得显示成热生效；`mail` 及其余逐次读配置的功能仍是 `hot_reload`。`private_exchange.enabled` 与 `qq`/`mail` 两个通道总开关均走这条白名单；desktop/mobile/device 通道没有独立 enabled 字段，是否可用只取决于对应 token 是否配置且未停用。管理面编辑入口为「高级设置 → 运行配置」；「系统状态」只读展示通道摘要，不再承载保存控件。
- 邮件连接页 `GET/PUT /settings/mail` 提供 `connection_mode=auto|direct|proxy`。`auto` 兼容旧配置（有 `proxy_url` 时走代理）；`direct` 即使保留代理地址也让 SMTP 直连；`proxy` 要求代理地址。邮件发送逐次读取该值，保存后热生效。
- `GET/PUT /proxy` 增加 `model_connection_mode=follow_global|auto|direct|proxy`。默认跟随全局 `proxy.enabled`；`auto` 按每次请求的 DNS 选路，命中 Fake-IP 或解析失败时用 `proxy.http`；`auto`/`proxy` 需要有效 HTTP 代理地址。保存后热重载模型与视觉客户端。管理面入口为「代理与中继」；系统状态只读展示当前模式。桌面/手机无新设置。
- admin 配置中心（Brief 93 §1，管理面板「配置」页，`GET/PUT /settings/base-model`、`GET/PUT /settings/embedding`、`GET /settings/setup-status`）：`/settings/base-model` 只读写 `model_presets` 主聊天 preset；缺少该块时 GET 标 `mode=missing`、PUT 返回 400。扁平 `llm:` 合成已退出，不引入第三套真值来源。`/settings/embedding` 读写 `embedding:` 块（缺失时向量召回 fail-open 降级为关键词路径，不算必填）；`/settings/setup-status` 的 `needs_setup` 驱动面板首次登录自动跳转与顶部红色横幅，判定标准是 base_url/api_key/model 三者均非空且不是 `config.example.yaml` 里 `YOUR_`/`YOUR-` 前缀的占位符。
- 密钥本快捷入口（Brief 93 §2，`GET /system/secrets-book`、`POST /system/secrets-book/open`）：仅当请求方 `request.client.host` 是 `127.0.0.1`/`::1`/`localhost` 时可用，用系统默认程序打开 `secrets.local.yaml`；非本机请求悬浮按钮隐藏、`open` 端点直接 403。

管理面状态与配置入口（Brief 163）：「系统状态」仅读取 `/status`、`/model-presets`、`/tts-config`、`/proxy`、`/settings/relay`、`/scheduler/status`、`/settings/feature-flags` 和有限错误摘要，展示当前值、配置来源、生效范围、刷新结果及明确的外部健康边界。编辑入口分流到「高级设置 → 运行配置」「高级设置 → TTS 配置」「模型路由」和「调度器」；编辑页的布尔、枚举、数值控件分别使用 checkbox/select/range，并标注 hot reload、immediate 或 restart-required。数据环境只展示 formal/test、脱敏的测试会话标签、逻辑数据位置和隔离测试用户数量，不展示本机绝对路径或用户 ID。
- 401 人话化（Brief 93 §6）：`admin/auth.py` 的 401 响应体 `detail` 从纯字符串改为 `{"message", "hint"}`；`/ws/desktop`、`/ws/device` 鉴权失败的 WS close 附带同语义的 `reason`（受 RFC 6455 123 字节上限约束，文案比 HTTP hint 精简）。桌面端 Brief 34 直接透传 `detail.hint` 显示。

扁平 `llm:` 合成与 `POST /model-presets/bootstrap` 已退出。缺少 `model_presets` 时
须按 `config.example.yaml` 配置该块，不能再从顶层 `llm:` 一键初始化。

`/settings/model-routing` 切的是**全局** active_routing；per-角色覆盖是另一条入口：
`GET/PATCH /character/{char_id}/model-routing`（persona 级，Brief 87）读写角色卡
`presence_ext.model_routing`，绑定对象是 routing profile 整体（不支持绑定单个 preset），
`null` 或字段缺失才表示清除声明、回落全局；`"default"` 是一个真实的 profile 名，和其他字符串 profile 一样会固定绑定角色。可选 profile 清单走 `GET /model-presets/routing-profiles`
（persona 级，不含 api_key/base_url）。管理面 `GET /model-presets` 会附带当前活动角色的有效固定绑定（若有），在切换全局路由时明确提示该角色不受影响。跨群一致——不做 per-group override。

`presence_ext.tool_loop` 是角色卡级 Path C 覆写，不经设置 API：`"on"` 在全局
`tool_loop.enabled=false` 时仍为该卡开启多步工具循环，`"off"` 强制关闭，缺失或非法值回落全局。
全局 `tool_loop.total_timeout_s` 控制单轮工具循环的总墙钟预算，默认 300 秒；管理面可调范围为 5–720 秒。
Path C 固定采用分类按需加载，无新增开关：首轮仅提供最终获授权的非空分类入口。
`max_steps` 之外最多增加非空分类数（至多 10）个纯发现轮，发现/relay/执行共用总超时；
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

TTS 有三个层次的开关：`tts.enabled` 是服务端能力总开关；`tts.desktop_enabled` 是旧桌面语音条显示兼容项，并与 `tts.auto_play.desktop_pet` 双向同步；`tts.auto_play` 则按 `chat`、`dream`、`video_call`、`desktop_pet`、`mobile` 独立决定客户端是否自动请求/播放，全部默认关闭。`GET/POST /settings/tts-auto-play` 是该落盘状态的读回观测面。桌面设置页提供已接入语音条的聊天、桌宠气泡与视频电话开关，并在收到回复后实际自动合成播放。`POST /tts/synthesize` 只在能力总开关开启且 persona 鉴权通过时按需合成，接受可选 `scene`（旧客户端可省略），并在合成前移除中英文括号中的旁白/动作描写；返回 base64 WAV。手机轮询消息只携带 `voice_available` 轻量标记，绝不携带音频本体；手机端在本机“自动播放语音”开启时，以该标记按需请求并播放音频。管理面“观测 → 资源完整性”分别报告 TTS 服务就绪和桌宠手动语音条可用性；前者为关闭时，后者即使单独开启也会明确报告为不可合成。

TTS provider 由管理面（admin token）经 `GET/PUT /tts-config` 管理：`tts.provider` 当前支持 `gsv` 与明确标注为预留的 `openai_compatible`，每个 provider 可放在 `tts.providers.<provider>`。`GET` 会分别返回各 provider 的脱敏参数块，面板切换 provider 时显示对应参数且保存互不污染；预留 provider 在面板禁用，绝不猜测或发起云厂商请求。GSV 可选 `gpt_model_path` / `sovits_model_path`，留空时分别切回 v3 / v2ProPlus 默认底模；模型切换是 GSV 服务的全局状态，后端会把切换与同次合成串行化，路径错误时自动回退对应底模。GSV 默认启用后端分句：清洗控制/格式字符并识别实际、字面 `\\n` 与 `/n` 换行，按 `。！？；……` 优先切分，只有超过 `segment_max_chars`（默认 42）才在逗号或破折号处兜底；每段以 GSV `不切` 请求、按中文/英文脚本选择语言模式，再校验 PCM WAV 参数并插入 `segment_pause_seconds`（默认 0.25 秒）静音拼接。`external_segment_enabled: false` 可临时恢复 GSV 内部切分。旧有顶层 GSV 字段（`api_url`、`ref_audio`、情绪参数等）会自动映射，保持已有本地 GPT-SoVITS 部署行为不变。`POST /tts-config/test` 只试听已就绪 provider，`GET /observability/api-calls?caller=tts` 可查询最近合成结果与失败类别（`state.read`）。

TTS 管理页默认选择当前活跃角色，也可切换到其他角色或全局默认。`PUT /tts-config` 携带 `char_id` 时写入该角色的命名 preset 和 `tts.role_routes` 显式路由；不携带时修改全局回退。运行时优先使用显式路由，再使用角色卡 `presence_ext.tts_preset`，最后使用全局值。参考音频从该角色 authored `voice` 目录按逻辑名解析，已有中文文件名可直接选择；`reference_texts` 保存该作用域内每份音频的手填参考文本，选音频时管理面带出对应文本，合成优先使用绑定文本，未配置映射时兼容旧 `prompt_text`。GPT `.ckpt`/`.pt` 与 SoVITS `.pth`/`.safetensors` 模型从当前角色 authored 目录及其子目录发现并按逻辑名解析，旧外部路径仍可读。GSV `gsv_version` 可选 `auto`（兼容旧配置）、`v2`、`v3`、`v2Pro`、`v2ProPlus`；`version_models` 为每个显式版本保存独立的 GPT/SoVITS 权重配对。显式版本未配齐两项时状态为未就绪且试听/合成不调用服务；版本值仅决定选择哪组权重，实际架构仍由 GSV 从权重识别。视频电话本地视觉请求时限 105 秒、单帧总时限 110 秒，低于桌面客户端的 120 秒 HTTP 上限；仅一个帧请求在途，后续帧不排队。

视觉模型不进 LLM 的 `routing_profiles`：管理面「模型路由」页用 `GET /image-presets` +
`PUT/DELETE /image-presets/presets/{name}` + `PUT /image-presets/routes` 管理命名图像连接
（`kind: vision|ocr`）及用途（聊天图 / 生活记录饮食·购物车·账单 / 屏幕 / 摄像头）。无
`image_presets` 块时从 `vision:` / `image_recognition:` / 旧 `phone_control_vision:` 合成，
不改运行语义。旧 `GET/PUT /vision-params` 仍写 legacy 槽位并热重载；
`/vision-params/phone-control` 已退役（手机自动化并入 `screen` 路由）。
`screen` 是按需看屏幕、影子观测、手机自动化共用的一条路由，可经 `image_presets.fallbacks.screen`
指定备用连接：主连接连不上（超时/连接失败/5xx 等，规则同文本模型 failover）才换备用，
模型已回复但内容不合格不换连接重试。`/phone_control/status` 按 `screen` 主连接的
`base_url` 与 `model` 判断 `vision_configured`。删除仍被用途引用的连接（含仅作备用）返回 409。

图像连接新增显式隐私标记 `image_presets.presets.{name}.is_local`（bool，默认 false，
fail-closed）：管理面「模型路由」连接编辑器手动勾选「本地模型」，连接列表显示「本地 / 云端」
标识；新建连接时按 loopback 地址给默认勾选，勾选与地址不一致仅给提示、不阻断保存，已有连接
升级后保持 false。`GET /image-presets` 的 `presets.{name}.is_local` 与 `purposes[].is_local`（含 screen）与 `purposes[].fallback_is_local`（fallback 连接单独判定，无 fallback 为 false）
可读。本字段目前无运行时消费方，不改变任何识图行为；与文本 preset 的 `provider_kind: local`
无关。

工单 264：`video_call` 是独立图像用途，默认未路由；管理面只接受启用的本机 HTTP
Chat Completions 视觉连接（`localhost`、`127.0.0.1`、`::1`），拒绝远端与 OCR。
`GET /video-call/state`（chat）返回 effective 与阻断原因；`GET /observability/video-call`
（state.read）另含无图像内容的处理计数。摄像头预览可在路由无效时独立显示，但不会送帧。
本机 TTS 与视频视觉共用推理锁；视觉忙时跳过帧，TTS 按已有播放队列等待。GSV 调用有硬超时（`segment_timeout_seconds` 默认 30、`model_switch_timeout_seconds` 默认 60，写在 tts provider 配置块）；超时后该 `api_url` 标记不健康，下次请求先探测通过才进锁，任何失败都清除模型缓存。等待中的 TTS 请求上限 `max_pending`（默认 4），超出直接丢弃。`GET /admin/tts-config` 的 `runtime_status` 只读暴露锁持有者、队列长度、丢弃数、最近错误与健康状态。
本地视觉连接拒绝时停止 SDK 自动重试，15 秒内跳过后续帧并在客户端提示服务不可达；
推理超时与连接失败分别显示。TTS 配置读取若遇到不能作为 authored 资产索引的旧角色 ID，
仍返回现有 TTS 设置和外部参考音频，资源列表置空并给出 `blocking_reason`。

工单 265：视频电话摄像头开启且 `video_call` 路由有效时，周期识别结果每隔至少 60 秒可形成一次有期限的 autonomy 候选，即使画面没有变化；候选有效期 10 分钟，通话持续且最近 120 秒无用户发言时可进入主动评估。角色仍通过梦境、免打扰、预算、冷却与发言门控决定是否通知。`observe_video_call_camera` 仅在活跃视频电话中请求新帧，走 `/video-call/camera/poll` 与 `/video-call/camera/result` 的 10 秒单次请求，再用独立的 `video_call_tool` 路由识别（未配置时沿用 `video_call`，显式留空则工具关闭而周期观察保留）；两条路由都只接受本机 loopback HTTP 的 Chat Completions 视觉连接，`GET /video-call` 快照的 `tool_connection` 给出按需路由的 connection/effective/blocking_reason。`POST /video-call/close` 清除会话与待消费结果；`GET /observability/video-call` 增加活跃会话及待领取请求数，不返回图像或描述正文。屏幕的 `observe_user_screen`、实验性 `/perception/visual` 和 `visual_perception` 配置均不参与摄像头链。
工单 266：`tools.invite_video_call.enabled` 与 `allowed_char_ids` 限制发起角色；autonomy 另需该工具的显式 allowlist，不能由普通对话开关推导为主动可用。来电 10 秒超时，接通只进入视频页，主人仍自己决定摄像头和麦克风。挂断生成一次主动候选，是否通知仍遵循 Dream、DND、预算及发言间隔。只读观测见 `/observability/video-call.invites`。

摄像头描写（周期帧与 `observe_video_call_camera` 共用同一 prompt）：以镜头内人物的动作、姿态、表情、视线为首要且写细腻（只描写可见状态，不推断内心情绪），环境只给轮廓，人物与物件互动或举物展示时才展开该物件；总长约 300 字。`video_call` 视觉用途的 `max_tokens` 由 120 放开到 500（`core/llm_client.py::VIDEO_CALL_MAX_TOKENS`），落库截断 `MAX_OBSERVATION_CHARS=800` 保持兜底，三者须一致（见 `core/video_call.py` 顶部注释）；回执 TTL 仍为 45 秒，单帧耗时逼近时优先降 `max_tokens`。

视频通话帧差分 `video_call_frame_diff.enabled`（默认 `false`，`GET/PUT /settings/feature-flags` 白名单 `video_call_frame_diff`，热生效；设置中心按 API 返回的 label 自动列出，无专属页面）：关闭时行为与此前完全一致。开启后只对周期帧（`POST /video-call/observe`，`purpose=video_call`）生效，按需工具 `observe_video_call_camera`（`video_call_tool`）始终走全量 prompt。一次通话的第一帧仍用全量 prompt 并存为底稿；之后每帧把上一次描述作为数据传入，要求只输出相对变化（约 100 字，输出上限由 500 收紧到 `VIDEO_CALL_DIFF_MAX_TOKENS=200`），并以 `NO_CHANGE` / `[minor]` / `[major]` 前缀标注幅度，解析不出前缀按 `minor`（fail-safe，不触发主动）。写给聊天模型的回执仍是「底稿 + 最近 3 条变化」，不是裸增量；无变化时回执沿用当前场景并刷新采集时间。记忆只是会话 dict 的子字段 `frame_memory`（纯内存、不落盘），随 15 秒断帧失效或 `POST /video-call/close` 一并清掉；开关中途关闭也会丢弃。开启后心跳式 60 秒 signal 被取代：只有 `[major]` 才入队一条 `source=video_call_camera` 的 autonomy 候选（`evidence.fact=video_call_camera_change`，priority 0.5，同一通话两次触发至少隔 20 秒，`dedupe_key` 为变化文本指纹），入队前先过 `receive_perceive_event`（`low_trust` + Dream Guard + 幂等）。该 source 沿用 `video_call_camera`，所以通话内放宽判定、`close_camera()` 的待办信号清理、autonomy 的会话关闭检查与「摄像头文本是不可信数据」包装都原样适用；DND、Dream Guard、未回应硬停、熔断与 `max_talks_per_call` 一律不放宽。观测：`GET /observability/video-call`（`state.read`）的 `counts` 增加 `frame_diff_no_change`、`frame_diff_minor`、`frame_diff_change_triggered`、`frame_diff_signal_suppressed`，不含图像或描述正文。客户端仍按固定周期上传，差分全在服务端；让客户端先做图像差分是更彻底的省成本方案，已决定本阶段不做，待本开关的实测节省数字再评估（open：尚无真实通话的 token 对比数据）。
`GET/PUT /stt-vocabulary`（admin）保存最多 32 条发音/误识别词映射，默认关闭；
本地 Whisper 与兼容 STT 都只收到词表的纯词表字符串（逗号分隔，不再带「以下是语音中的专有名词」前缀），
仅对确切匹配文本做有界纠正；转写结果若与本次提示词高度相似（Whisper 把提示当成转写复读）会被丢弃并按「未能听清」处理。

表情包由管理面（admin token）经 `GET/PUT /sticker-config` 管理：`sticker.enabled` 是总开关，`sticker.trigger_prob` 是 0–1 的每轮独立触发概率。缺失该配置块时保持兼容行为（启用、0.06）；关闭时不会发送或广播表情包。TTS 的概率单独掷骰，不会抢占或缩减表情包的配置概率。GET 返回当前有效值，兼作该落盘配置的只读观测面；若已命中概率但目标情绪目录无图，服务端会记录目录路径以便排查。

MCP server 由管理面（admin token）经 `GET/PATCH /settings/mcp`、`POST /settings/mcp/test`、
`POST /settings/mcp/import`、`PATCH /settings/mcp/{name}`、`POST /settings/mcp/{name}/reconnect`
和 `DELETE /settings/mcp/{name}` 管理。URL 导入必须先完成
`initialize + list_tools` 测试才写入配置；URL 导入可选 `streamable-http`（推荐）或 `sse`，而配置文件也可声明
`stdio`；旧 `http` 配置继续按 `streamable-http` 处理。HTTP endpoint URL 与 headers 都支持 `${ENV_VAR}` 展开：
服务商使用路径认证时可将敏感路径段写为 `${MCP_TOKEN}`，缺失变量会 fail-closed；管理面不回显字面 URL
路径或 header 值。新建 server 的 header editor 默认为空，只有操作者显式添加 bearer template 后才会提交。
MCP 不继承环境代理：loopback/localhost 地址始终直连，远程地址可在管理面单独设置
`use_proxy`，启用后使用全局 `proxy.http` / `proxy.https`。删除会立即断开该 server 并摘除它的动态工具；总开关写盘后把同步信号入队即返回，不把每台 MCP 连上当作 HTTP 完成条件；单 server 的启停/白名单只重载该 server；工具调用以
`caller=mcp__{server}__{tool}` 记录到 API 调用总账。`tool_timeout_s` 是 server 默认值，`tool_timeouts_s.<tool>` 可为个别工具覆盖 1-660 秒的调用上限；设置读取接口会返回两者，更新会热重载对应 server。动作工具超时会记录相同 `request_id` 并返回 `outcome_unknown`，不会自动重放。工具策略新增 `unrestricted`（面板显示“无限制执行”）：管理员选择时强制显式幂等、跳过确认，并以同一 `request_id` 最多重连重试三次。`tool_policy.<tool>.ui_label` 是可选的本地瞬态展示标签（1-48 字符），仅供已配对桌面端的 NOW 状态使用；它不影响权限、确认、重试或调用参数，缺失时统一显示“外部工具”，不会回显远端工具名、说明、参数或结果。

后端管理面 MCP 页的 Tool-call Console 仅通过 admin-only 的 `POST /settings/mcp/console/invoke` 与
`POST /settings/mcp/console/confirm` 调用。路由只接受当前已连接、有效 allowlist、已注册且本地 policy 已确认的动态工具，并在服务端以工具的 JSON Schema 校验参数；绝不接受任意 MCP method 或 server command。它复用 `tool_dispatcher.execute_structured(origin="admin_console")`、effect/确认门、每工具超时和 MCP API 调用总账，不直接触碰 session；高危调用返回一次性确认票据（120 秒、仅原工具原参数可确认）。tuple `execute()` 已删除。控制台响应和总账以 `audit_id` 关联，且总账不记录 arguments 或返回正文。桌面客户端不代理 MCP 管理调用、配置或密钥。

每个 MCP server 可保存命名的 `tool_presets`（每项是一组工具白名单）和当前 `active_tool_preset`。选择预设会将该工具集写回运行时实际使用的 `allow_tools` 后热重载；手工改复选框则回到“自定义”选择，避免悄悄改写命名预设。开启 `require_local_policy` 时，`allow_tools` 仍是唯一运行时白名单；管理面 URL 导入和普通白名单保存会在工具探测成功后为新白名单工具写入本地默认 `tool_policy`，保留已有显式策略。MCP annotations（`readOnlyHint` / `destructiveHint`）或名称和描述只能影响默认 effect 建议，不能授予远端权限或自动开启确认：未知语义落为 `write + require_confirm: false`。管理页逐工具“每次执行前确认”复选框显式保存 true/false；导入、普通保存和批量默认授权都只补缺失字段，不覆盖已有选择。无法从当前运行时快照补齐策略时，严格写入会被拒绝，不会注册或调用。保存时会清理已移出白名单的策略项。单 server 保存向对应 owner task 发送重载信号：连上返回 `reload_status=reloaded`；热重载已执行但未连上返回 `connection_failed`（附 `last_init_error`，owner task 仍在，可用「重新连接」再走同一热重载）；只有信号未能交给 owner 时才是 `restart_required`。连接失败不再叫重启。

Brief 137 增加每 server 的可选 `metadata_mapping`、按远端工具精确名称保存的
`metadata_overrides`，以及 `domain_selector`。这是 Emerald 客户端扩展，不是 MCP 官方分类标准；
第三方 server 无需实现即可继续连接和调用。远端 `_meta` 只被压缩为有界 domains、interaction
提示、status/source/version，不改变 `category="mcp"`、allowlist、effect、确认、幂等、origin、
schema 校验、连接或 proficiency。selector 缺失时保持旧行为；启用时只收窄已经授权的本轮 schema，
`include_unclassified: true` 保留普通无 metadata server。管理面逐工具分开展示“已发现 / 已授权 /
当前会话可暴露”，并允许选择使用远端分类、本地覆盖或忽略远端分类。`GET /settings/mcp` 不返回
完整 `_meta` 或完整参数 schema；管理面工具目录返回最多 1000 字符的远端 description 供管理员核对，控制台显示有界参数摘要，调用时仍由服务端用
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

内置唤醒由 `GET/PATCH /admin/autonomy/config` 与 `GET/PATCH /admin/autonomy/tools`、`POST /admin/autonomy/tools/bulk`（一键开启/关闭内置可授权工具，MCP 不在批量范围）控制，「自主活动安排」页有对应的「主动时段可用工具」卡片，配置和有限运行记录按 owner/角色写入独立 autonomy state，不写入 `config.yaml`。默认关闭；启用后的 job 仍只由现有 scheduler tick 消费。`tool_eligibility()` 只判断 autonomy allowlist 能否显式打开一项工具，不是最终 schema/execute 答案。`GET /admin/autonomy/tools` 返回唯一 `AutonomyToolDecision`：`allowed`、`decision_source`（`autonomy_allowlist` 或 `global_read_inheritance`）、`global_enabled`、`deployment`、`self_capability`、`mcp_policy`、`autonomy_policy`、`danger`、`confirmation` 及兼容字段 `final_schema`/`execution_allowed`。全局已连接的只读 MCP 工具仍可经 `global_read_inheritance` 进入自主工具面，且必须通过全局启用、部署闸、MCP local policy、Self Capability 与动态注册；写入工具继续要求 autonomy allowlist 和代码审查的 sandboxed write（当前为花园浇水）。schema 暴露、管理面矩阵和 run audit 读同一决策；执行前仍复查当前 allowlist，展示允许但执行时撤权会记 `tool_call_denied`。`manage_self_capability` 只在存在可由角色修改的、用户已授权且未锁定的能力时暴露。每次 run 会保存只读记忆、最近五轮和基础角色描述组成的 prompt 快照；普通运行列表不返回快照，只有 `GET /admin/autonomy/runs/{run_id}/prompt`（`admin` scope）可读取。关闭 autonomy 会停止 tick 对 pending signal 的消费、job 创建和 run/发言；迁移后的 scheduler conversational trigger 即使留下有界候选事实，也不会退回旧 `_pipeline_send` 发言路径，过期事实会在再次启用后的首次 drain 中丢弃。`desktop_wake` 是一次性特例：autonomy 已关闭时 Path B 不入队；入队后再关闭时，下一次 tick 会删除 wake signal 并写入 terminal suppression，后续重新开启不会补发。普通 owner chat tool loop 与 Wake Bridge 不受影响。`scheduler.morning_greeting`、`night_reminder`、`random_message` 等来源开关仍是各例行信号的权威生产闸门；关闭来源后，已排队但尚未消费的同源事实也不会形成 opportunity。

Self Capability P0 uses `GET /admin/self-management` and fixed `POST` actions for grants, locks, restore, and undo. Its durable state and audit are scoped by `uid + char_id` under the runtime sandbox, separately from autonomy state and `config.yaml`. The global `self_management.enabled` master switch is exposed in System Status -> Feature switches and is hot-reloaded. `enabled=false` closes the overlay only: durable agent overrides go dormant, `effective()` returns `(True, None)` so legacy tool/autonomy defaults apply, the management gateway is hidden, and agent requests are rejected. It is not a capability kill-switch; user grants and audits remain intact for a later re-enable. This round keeps the field name; renaming needs old-config compatibility and client intake. The panel returns capability IDs, status, constraints, revisions, and audit metadata only; it never returns MCP URLs, headers, tokens, or raw tool results. Agent mutations use the internal `manage_self_capability` gateway and require a user grant, `mutable_by_agent`, an unlocked capability, a current revision, and an idempotent action ID. During an autonomy run, the gateway is exposed only while a mutable capability exists; every model step rebuilds the effective schema, and every requested call is checked against that current allowlist before dispatch. Self-management calls use `autonomy_self_management`; business calls use `autonomy_loop`, and the two origins are not interchangeable. Audit records correlate the run/job/action IDs while the autonomy tools endpoint reports the single `AutonomyToolDecision` matrix. The effective runtime decision still intersects global availability and every existing dispatcher/autonomy gate; execution rechecks that current matrix before dispatch.
Self Capability 的角色只读面现有 `list_self_capabilities` 与 `read_self_action_history`；两者仅在总闸开启时进入 owner Path C / autonomy，不进入 Path A 或群聊。前者列出能力与当前状态、授权/锁定、可修改性，后者检索同一 uid+char 的角色变更审计（24h/7d/30d、能力与结果筛选，最多 50 条）。复杂值在角色回执中脱敏。写回执分别表示操作成功与最终值；审计继续由管理面 `/admin/self-management` 只读展示。
工具调用的独立按日审计可通过 `GET /observability/tool-audit/{uid}`（`memory.read`）读取，也随 `read_self_action_history` 返回。它保存白名单关键参数、执行状态、错误码、request_id 和可取得的前后值，不保存原始工具输入或结果。Path C 在回答过去动作的问题前查询近七日审计；部署前没有该审计回执的调用不能据此补造细节。

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

Self Capability management IDs are separate from admin API scopes. The
character's default self-mutable setting allowlist is limited to
`setting.autonomy.talk_enabled`, `setting.autonomy.min_interval_seconds`, and
`setting.autonomy.interval.seconds`, all scoped to the owner and character.
The legacy `autonomy.enabled` and `autonomy.min_interval_seconds` IDs still
require an explicit owner grant. Tool switches, Tool Loop exposure and presets,
MCP server/policy controls, global scheduler controls, device, account, file,
and privacy-read permissions remain owner-controlled.

The registry publishes `default_grant`, `mutable_by_agent`, `user_lockable`,
`requires_confirmation`, `high_risk`, and `value_type` for every management
capability. An owner grant cannot make a capability mutable when its registry
spec marks it owner-only; requests to create such grants return
`managed_by_user_only`, and old stored broad grants are rejected at execution.
Owner-managed tool-use grants still govern actual tool availability. MCP
capabilities identify a configured server by name; the model cannot provide a
URL, headers, or transport configuration. Secrets, auth/token profiles and
scopes, proxy/bind/listen settings, destructive deletion/retention, and MCP
imports remain protected.

Every accepted mutation uses the character-scoped optimistic revision and an
idempotent action ID, appends an audit record, and increments the effective
schema revision.  Audit values are bounded and omit URLs, headers, tokens,
passwords, API keys, and raw tool results.  The admin read endpoint is
`GET /admin/self-management` (with `policy_matrix` and `audit`), while the
agent has only the dedicated `manage_self_capability` gateway.

Configured MCP policies remain visible for owner review but cannot be changed
through the character's management gateway, including ordinary read/write
policies. Existing actuate, emergency, and unrestricted policies retain their
high-risk markers.

## 轻量判定与外部依赖失败降噪（工单 F）

失败仍可查，但不再每次写一条 ERROR 栈。统一口径：每次失败都计入 `runtime_signal_observability`
（`GET /observability/runtime-signals`，`state.read`），日志只留每个 context 的首条与每 20 条一条。

| 来源 | 计数 `category/code` | 日志/台账变化 |
|---|---|---|
| wttr.in 天气（`core/tools/weather.py`） | `third_party_upstream/weather_unavailable` | 网络/证书/超时/HTTP 非 200 不再进 `error.log`；每次失败写 `api_call_log`（`caller=weather`，`error_category=upstream_unavailable`）。`get_weather` 带 45 分钟内存缓存，失败时回「（这是约 N 分钟前的缓存天气…不是此刻的实况）」；解析异常等真 bug 仍走 `log_error` |
| 爱意探针 `detect_affection`（具身爱心） | `model_quality/probe_failed` | 不再 `log_error`；`detect_affection_checked()` 区分「判定为否」与「探针失败」（None） |
| 爱心触发 `core/embodiment/heart.py` | `model_quality/heart_probe_skipped`（`reason=backoff\|sampled_out`） | 探针连续失败退避 `min(60·2^(n-1), 900)` 秒，一次成功判定清零；新增 `embodiment.heart.sample_rate`（0–1，默认 `1.0` = 每条回复都判，健康时行为不变）。管理面无专属 UI，只能改 `config.yaml`，`config.example.yaml` 已示例 |
| `sensor_judge` | `model_quality/sensor_judge_failed` | 只聚合日志，**不采样**：每次调用对应一个真实传感器事件，且失败是 fail-closed（丢弃该事件），跳过调用会改变哪些主动决策被做出，不只是降噪 |
| `event_edge_proposer` scope 超时 | `model_quality/edge_proposer_scope_timeout` | 只聚合日志，**不调高** `scope_timeout_seconds`：实测超时是持续性的（上游模型本身不可用，见 known-issues「轻量模型路由整体不可用」），加大阈值只会把系统性过慢藏得更深 |

熔断器本身（阈值与恢复）未改；熔断拒绝（`breaker_open`）现在只计数。`empty_completion`、`detect_emotion`
主路径与调用时机不在本单范围。

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

## 工单 S1 召回兜底

`recall.semantic_min_similarity`（config.yaml，默认 0.0 = 不过滤）：episodic 纯语义候选（无关键词命中）相似度低于该值时不进候选池。管理面见“工单 S 系列开关接入管理面”，下一轮生效。`fetch_context(query_free_fallback=)` 为代码参数，非配置：普通聊天 False，调度器主动开口 True；recall_trace 新增 `episodic_fallback_mode`（`GET /observe/recall/{uid}`）。

## 工单 S5b 隐性状态置信度门控

`hidden_state.confidence_gating`（config.yaml，默认 false）与 `hidden_state.min_confidence`（默认 0.3）：开启后按字段置信度门控 overflow `hidden_need_score`、letter_writer 隐性原因与 dream 快照（不足输出 `unknown` 不渲染）。管理面见“工单 S 系列开关接入管理面”，下一轮生效；关闭时行为与现状一致。观测：`GET /debug/user-hidden-state`（`scalar_views` / `evidence`）与 autonomy overflow Signal evidence 的 `hidden_need_raw/gated/confidence`。

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
between episodic/vector IDs and ledger IDs. The same endpoint now includes
`retirement_draft`: denominators are mapped old events, residual unmapped
legacy, and non-disabled timeout/busy/cancelled calls. Draft thresholds stay
`pending_approval` and `used_as_gate` is always false. Turning the flag off or
clearing the allowlists immediately falls back to the legacy path after
config reload.

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
the remaining summaries. The logs page also exposes a separate admin-only
`GET /logs/runtime-warnings` window over WARNING+ JSONL (UTC start/end, level,
logger substring, offset/limit). It reports retention, truncated days, and
write/read faults; an unreadable rotated file is never shown as “no warnings”.
Clearing still only empties `error.log`. Desktop/mobile do not consume this
endpoint; no new client settings. Each card shows its current value, source, effective
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
latest-run evidence, and a pending retirement draft. Empty evidence is labelled
`未运行`, never healthy. No prompt, body, event ID, token text, or local path is
projected. Desktop and mobile clients do not add settings or consume these
backend diagnostics. Existing owner-turn, Agent Runtime, autonomy, proactive,
action, mail, and API ledgers keep their own endpoints; I does not add a
universal ledger.

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
boundaries. The Agent plane is the same character's durable/specialized 副链 lifecycle layer, not a
second agent. It adds no setting, endpoint, worker, client control, or capability. Existing autonomy,
scheduler, tool, deployment, and EventContext controls retain their current ownership and semantics.
Future Briefs 230-237 must add configured/effective/runtime-observed controls in the same change as
each implemented task or capability; clients must not infer availability from existing tool names,
and must not infer that a capability is unavailable on the foreground 主链 merely because it also
has a durable 副链 path.

Brief 237 closes legacy scheduler execution lanes: due schedules use the normal Reality interaction
adapter, the reminder JSON fallback is retired, and manual direct-trigger execution is unavailable.
Task cancellation is admin-only via the metadata endpoint in the interface catalog.

Work order 256 B is current for backend/external read effective state. Historical sentence
above remains true for Brief 229 itself. See
[character-files-and-agent-autonomy.md](character-files-and-agent-autonomy.md).
`fs_access.backend_read` defaults on unless explicitly `false`; `external_read` follows the
new key or legacy `enabled`. `allow_roots` are discovery hints, not the sole admission for
ordinary external files. Whole `data/` deny and substring `deny_names` are retired for fs
reads. `GET /observability/backend-read` (`state.read`) reports configured/effective,
blocking reasons, redaction version and counts; no file body, secret, or full path.
Self space is H current: default grant, admin revoke, quotas in `self_access`
(including `agent_md_chars`, default 2000 / hard 4000), tools `self_*` plus thin
`read_toy_file`/`write_toy_file` self-relative compatibility entry in category `info`, observability
`GET /observability/character-self` (`state.read`) with remaining quota, grant
revision, file counts, AGENT.md inject status (no body), legacy toy ownership
and recent op metadata; no private notes. `toy_autogrow.enabled` remains the
character habit switch; its cooldown is owner+char scoped in self meta. Reminders are H current: tools
`list/get/add/update/cancel/restore_reminder` write Runtime schedules for the
frozen character; `GET /observability/character-reminders` (`state.read`) is
metadata-only; legacy JSON live readers and maintenance fallbacks are retired. Agent-task
lifecycle observation reuses the existing metadata-only `GET /observability/agent-runtime-tasks`.
`agent_tasks.enabled` is the separate, default-off admission gate for the F worker.
`start_agent_task/get_agent_task/cancel_agent_task` use the frozen owner+character
principal and a server-defined `workspace_manifests.<workspace_id>` revision/expiry. A
manifest narrows operations to `read/create/update/run`; it cannot add an operation
disabled by `workspace_access` or `process_runner`. Chat tool visibility grants no
autonomy authority: autonomy also requires its existing explicit tool allowlist,
character capability, deployment gate, and the same manifest. `max_steps`,
`max_seconds`, `max_tokens`, and `max_concurrent_per_character` are hard-capped by
the worker. The worker makes one routed model call, so token capping is also its
current cost bound; no currency estimate is exposed because presets have no pricing
metadata. Task receipt observation remains metadata-only; private goals, bounded
input text, model plans, and command output stay in the Agent-task payload store.

`workspace_access` roots now also accept `{id, path}` entries. Legacy string roots
retain compatibility IDs (`default`, `workspace-2`, ...). Process/browser behavior
is otherwise unchanged and model-facing reads/outputs reuse the same redaction service.

## Brief 232 Agent Work Sessions

Agent Work Sessions are backend-only and have no client setting. They are the same character's
durable/specialized 副链 sessions, not a second acting subject. The scheduler's
`inner_diary_write` maintenance task uses the Reality Task Manager plus the independent
`work_session_id` store. `GET /observability/agent-runtime-work-sessions` exposes bounded lifecycle,
artifact-kind, digest, and error metadata only; it never exposes work context, prompts, diary正文, or
paths. `daily_journal` remains governed by the existing autonomy signal controls.
A failed session may be explicitly retried while its task is queued, or while a
claimed worker already holds a valid lease for that same task. If that day's
Reality task is already terminal and the diary file is still missing, the next
scheduler tick mints a fresh task instead of reusing the exhausted key.

`backfill_diary` is an owner-private info write tool (`tools.backfill_diary.enabled`，默认启用)
that reuses the same authored_diary Work Session, fact/feeling generator, and
`authored_diary:{char_id}:{date}` lock. Local time 23:00 onwards may backfill today;
yesterday may be backfilled all day. Existing files, including empty files, are never
overwritten. Missing records are not invented. Desktop/mobile keep consuming the existing
diary and chat interfaces; there is no new protocol or settings UI. Observation reuses
Agent Runtime task / work-session read endpoints and the existing diary read tools.

## Brief 233 workspace capability

`workspace_access` is a local deployment capability with explicit roots and independent read/list/create/
update/delete permissions. It defaults disabled and is unavailable in `remote_server` mode. Limits are
bounded by file bytes, aggregate bytes, concurrent operations, read characters, and listing entries.
The backend adapter rejects project data, symlinks, and sensitive names; mutating operations use the
Reality Task Manager receipt boundary with `causation_ref.kind=tool_request` (request fingerprint),
not `reality_turn`. The current control surface is backend configuration, the
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

Model Routing exposes named image connections (`GET /image-presets`,
`PUT/DELETE /image-presets/presets/{name}`, `PUT /image-presets/routes`) plus
legacy `GET/PUT /image-recognition` for the OCR slot and chat-upload mode.
Purpose rows pick any saved connection. GLM Layout Parsing uses an exact Endpoint
URL; OpenAI Chat Completions uses a Base URL. Keys are write-only; blank saves
preserve keys. Ready is not tested. Runtime metadata uses
`/observability/api-calls?caller=image_ocr` (`state.read`). Desktop/mobile keep
`/upload/ingest`; screen automation never inherits OCR.

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

current：管理面「模型连接与分工」通过既有 GET/POST /settings/thinking（persona）控制
总开关、mode、character_voice（默认 true、随 thinking.enabled 生效）、独白预算、主动消息
和 display_prefer_monologue（默认 true，只改气泡顺序：有独白先显示独白）。
开关（生成思考 / 角色心声 / 应用于主动消息 / 气泡优先显示前置独白）用 `admin-toolbar` +
`checkbox-row` 分组，方式与独白预算单独一组 `field`，仍一次保存，不逐项 PATCH。
思考卡片只读展示当前角色实际生效的前置独白路由（profile / 主 Preset / 兜底 Preset / 继承来源 / 启用状态）
与最近一次独白状态；native 使用 chat preset，auto 说明实际解析。角色固定绑定时标明覆盖，
不把正在编辑的 profile 报成当前生效方案。GET 附加 `monologue_route` / `last_monologue` 为脱敏元数据，
不返回独白正文，桌面/手机不新增设置或权限。
「聊天方式与思考」只留跳转。voice_preview 给出当前拼接提示、
情绪/稳定变体和 enabled/effective/blocking_reason；output_guaranteed=false 明确其只是通用
提示引导。native 不增加 LLM 调用，monologue 复用已有前置调用。可能影响最终回复。
桌面只控制本地显示，不新增设置；手机思考展开 UI 仍 roadmap。见 [thinking-voice.md](thinking-voice.md)。

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
open: native Tauri/restarted backend integration after the 2026-09-21 observer
rebinding (chat/QQ attach `push_tool_status` at emit time; autonomy NOW overlay
now forwards when desktop WS is connected). roadmap: mobile tool chain UI and full unbounded
historical tool receipts. Client evidence: docs/tool-activity-2026-09-12.md in the desktop repository.

## Autonomy XML compatibility and cadence (2026-09-12, partial)
Autonomy opts into existing XML tool encoding in chat_turn; ordinary native-only callers keep
strict FC semantics. Parsed tools remain bounded by the exposed names; private prose never sends.
Existing run.events now retain safe evaluation_error/error_type metadata. No new store or scope.
Runtime settings were updated through admin APIs and read back: interval 30 minutes, 96 evaluations/day,
global proactive gap 45 minutes; daily talk cap 16 and evaluation minimum interval 15 minutes unchanged.
open: restart backend to activate code and observe real delivery/circuit recovery. No real test message sent.
Screenshot planning remains separate: desktop visual sampling is shadow-only and local consent is off;
mobile offers a text snapshot, not this requested on-demand image capture. See desktop docs/proactivity-2026-09-12.md.

## 生活记录描述与路由（2026-09-12）

后端 /settings/life-records 管理同步、后台同步、角色只读、原图保留；饮食/购物车固定 vision，账单固定独立 OCR，连接仍在模型页编辑。/life-records/capabilities 与设置观测逐分类返回 recognition_routes；普通聊天图片用途选择不影响生活记录。管理面显示任务状态和逐条重试按钮，技术台账折叠。手机展示独立图片描述与用户备注，不复制模型配置。角色查询原开关生效，用户备注优先；桌面经管理面配置，原生生活记录列表仍为 roadmap。

## 管理面样式定稿实施（2026-09-12）

灰绿状态色、中性按钮、分隔线设置区、复杂表单折叠及 JSON 填表已接入；观测与工具页复用现有 state.read 全局状态表。原保存 API、客户端管理面 bridge 和手机消费路径不变。
current / open / roadmap / observe 与验证证据见 [admin-design-implementation.md](admin-design-implementation.md)。
roadmap：逐请求的视觉、权限、队列、发送、ack/TTL 尚未合并入十类全局状态表，不能据此宣称端到端链路全部可观测。observe：原生容器与真实服务未联调。

工单 253.4：owner 私聊 Path C 的 11.5_file_path_hints 提供有界路径候选，不自动读取、不改变授权。256 B 之后：相对路径仍以 allow_roots 为发现提示，绝对普通外部文件不再因跨根拒绝；同名歧义仍 `path_not_found`；NUL 文本按二进制拒绝。管理面工具页继续显示 file_access enabled/configured/effective；`remote_server` 保留 fs schema 以读本进程 backend，外部本机路径路径级拒绝。无新客户端设置或协议。

工单 253.6：语音页「语音合成与声音（TTS、STT）」提供 STT 命名连接、voice_message 用途和默认关闭的感知开关。GET/PUT /stt-presets* 为 admin；客户端仅传音频和短期凭据，不维护后端权限副本。完整兼容、有效状态和超时语义见 [audio-perception.md](audio-perception.md)。

## Brief 256 G: character file and Agent-task effective state

The admin call-records page now links to
`GET /observability/character-file-autonomy` (`state.read`). The projection
combines backend/external read, character self, and Agent-task configured/effective
state, blocking reasons, quotas, grant revisions, and redaction version. Agent-task
execution remains default-off under `agent_tasks.enabled`; each workspace also
needs a live server-defined manifest. Desktop and mobile gain no setting or
confirmation field. An absent grant is reported as waiting for admin approval.

## Brief 259 history reconciliation inventory

`GET /observability/memory-history-inventory?uid=...&char_id=...` (`state.read`)
returns a versioned, content-free inventory of the scoped history stores. It is
strictly read-only: it does not initialize stores, create migration state,
invoke a model, or change production memory. The inventory reports source
counts, byte totals, ingest time ranges, separate evidence/derived/archive
denominators, lineage-missing counts, estimated tokens, and a 30-day first-night
candidate cut versus remaining history. Independent experience counts stay
`unknown` until a later semantic pass. Metadata revisions still let a separately
authorized batch manifest detect source changes before retrying. Background
consolidation continues to use the existing
`memory_consolidation.background_preset`; set it to the configured cheap
`grok-see` preset for bulk work.

## Brief 258 memory dossier consolidation

`config.yaml:memory_consolidation` is the backend authority. It is default-off.
The admin page writes it through `PATCH /settings/memory-consolidation` (`admin`)
and can start, stop, pause, resume, revoke, or explicitly resolve unverified
scope outcomes through `POST /memory-consolidation/control` (`admin`). Revocation
increments `grant_revision`, disables new work, and causes any in-flight patch to
fail its commit-time authority check.

`GET /observability/memory-consolidation` (`state.read`) reports
configured/effective state, blocking reason, night window, limits and remaining
daily budgets, backoff, current batch, checkpoint, backlog, status counts and
hashed run/task/session identifiers. With `uid + char_id` it adds one Reality
scope; without them it returns only global state. Dossier text, evidence and
revision detail are excluded and require `memory.read` through
`GET /memory/dossiers*`.

The controls are backend-admin only. Desktop and mobile have no duplicated
switch, permission, protocol field, queue, acknowledgement or TTL change.

## Brief 259 history reconciliation

The read-only inventory is exposed at
`GET /observability/memory-history-inventory` (`state.read`). A persisted,
content-free store-level manifest and status ledger are created with
`POST /memory-history-reconciliation/manifest` and read through
`GET /observability/memory-history-reconciliation`. Creating a manifest also
seeds the scoped dossier `source_items` table with one pending receipt per
stable source identity; it never copies source prose. Status reports both
store-level counts and `source_item_counts`. Bounded claims mark a stably
ordered slice `running` with a recoverable lease; related dossiers are looked
up by evidence ID, not title. Bounded item receipts and processing reasons are
read with `GET /memory/history-source-items`
(`memory.read`). Admin pause/resume/dry-run uses
`POST /memory-history-reconciliation/control`; `action=calibrate` runs an
isolated same-character side-chain sample (latency, estimated tokens, retry
rate, structural quality flags, rate band with headroom) and freezes the
first-night priority range. It does not apply patches, enable the scheduler, or
admit production first-night. `action=freeze` pins the
manifest revision and that frozen range before apply, and `action=apply` additionally
requires a server-side verified offline snapshot path and a bounded batch size.
`action=admit` freezes production-admission artifacts: go-live date, timezone,
first-night manifest revision, range totals, character grant, preset,
call/token/cost hard budgets, morning stop, and verified-snapshot restore
strategy. It records 258 B–E, recovery drill, and spot-check as preconditions
and never enables the scheduler. Isolated calibration freeze is not admission.
`action=run` is the explicit first-night runner: it additionally requires the
frozen manifest revision, a verified snapshot, a matching admission record, and
a stop deadline no later than the admitted morning cutoff, then returns a
metadata-only closeout report. Dossier passes go through
`run_operator_pass`, which bypasses only scheduler enablement and the night
window; it never flips `memory_consolidation.enabled` or sends a conversation
message. A later claimed attempt reopens a failed work session for the same
task, and the maintenance request keeps a user turn. After a terminal failed
first-night task, a later operator pass opens a new claimable task; leftover
dossier backlog is not treated as understood. Conservation checks after each batch stop that batch on count/revision
regression while leaving other scopes untouched. Status exposes `last_closeout`
and combinable `source_item_outcomes`; excluded/deferred are never treated as
understood. It is never invoked by the scheduler automatically. The apply path
is default-off at the operational level and is not a production rollout switch.

The current adapter applies the existing event-log migration in bounded
batches. When `consolidate=true` (the admin apply default), it then admits one
same-scope dossier pass through the existing `memory.consolidation` capability,
pinned to the configured cheap `便宜小模型grok-see` preset. Other historical
derived historical stores may be closed only as `evidence_only` when the
provider is unavailable; this is not semantic calibration or an active dossier
conclusion. The verified offline snapshot and recovery drill are recorded
outside this control surface. Isolated calibration may freeze a first-night
range from inventory; that freeze is not production admission. Production
admission may freeze go-live artifacts after 258 B–E, recovery, and
spot-check; it still does not run first-night. Isolated tests now cover the
operator-pass bypass, conservation stop, incremental watermark and closeout
denominator. No production snapshot, first-night apply, or morning closeout
has been performed in this slice.

When the configured provider is unavailable, admin may explicitly use
`action=evidence_only` with a non-empty `reason`. This closes only the
processing receipt as `evidence_only`; it creates no dossier fact or inference
and does not alter source evidence. Provider timeout remains observable in the
consolidation task and work-session status.
# HDS 本地心率接收

`GET/PUT /settings/hds-local`（`admin`）由调度器管理页读写本机 `config.yaml`
中的 `enabled`、`port`、`source_mode`、`interface`、`allowed_subnets`。
默认关闭；自动模式按选中网卡实时发现地址和网段，不保存当前 IP。
开关启用或端口变更需要重启后端；来源网段变化即时生效。独立 HTTP 接收端口不走代理；
`GET /watch/hds-local`（`state.read`）只读显示留存样本和最近接收状态。
此功能不提供桌面或手机设置入口。
实机 HDS 普通 IP 输入只填当前网卡 IP（不含协议、端口和斜线），关闭 Advanced IP entry；
该模式使用默认 3476 端口。接收端会确认并忽略非心率的 motion/calories 报文，
仅留存 `heartRate:<bpm>` 样本。

## 工单 S2 StatePacket 影子模式

`state_composer.shadow`（`enabled` 默认 false，`uids` / `char_ids` 灰度名单，语义同
`event_shadow_recall`）在 `build_prompt()` 之后记录每轮各 prompt 层的来源、authority、
字符数、reason 与 item id，写入 recall_trace 同级的 `state_packet/{date}.jsonl`。
关闭时不计算；不改变 prompt。观测：`GET /observability/state-packet`（`state.read`）。

## 工单 S3 Dossier 解释权收拢

`memory_dossiers.prompt_injection`（默认 true）：false 时 `6b_memory_dossiers` 不注入且不触发
对 episodic / event_search 的抑制；dossier 工具与夜间 worker 不受影响。
`memory_dossiers.suppression`（`global` 默认 | `overlap`）：`global` 为 dossier 有文本即整体清空旧召回
（现状）；`overlap` 只移除与本轮 dossier 引用证据（event id）有交集的 episodic 项。event_search 命中拿不到
event id 时整体抑制并在 recall_trace `dossier_suppression.suppression_fallback` 标记。观测：
`GET /observe/recall/{uid}` 的 `dossier_suppression`（mode / removed_episode_ids / removed_event_ids / kept_count）。

## 工单 S 系列开关接入管理面（状态权威）

`GET/PUT /settings/state-authority`（`admin` scope，热生效，写 config.yaml 后 `reload_config()`）统一管理：
`state_composer.shadow`（enabled / uids / char_ids，观察用，默认关）、`memory_dossiers.suppression`（`global|overlap`，默认 global）、
`memory_dossiers.prompt_injection`（默认 true）、`hidden_state.confidence_gating`（默认 false）与 `hidden_state.min_confidence`（0~1，默认 0.3；
confidence 是估计的可信度而非强度）、`recall.semantic_min_similarity`（0~1，默认 0 = 不过滤，页面放“高级设置”）。取值非法返回 422。
页面位置：管理面「运行配置」页“状态权威与记忆解释开关”卡片。所有默认值保持现状，关闭/恢复默认即回到现状。
上方 S2 / S3 / S5b 小节中“无管理面开关”的描述以本节为准。
