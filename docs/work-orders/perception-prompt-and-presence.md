# 感知描写精度 · 心声注入 · 在场信号 · 日记取样 工单组

本组 6 张工单，覆盖五块需求：视频通话人物描写精度、屏幕识图详细度（限本地模型）、
心声误入主模型、键鼠在场信号进 prompt 与主动触发、23 点日记丢早期素材。
每张独立验收、独立提交。

开工前先读「零、调查结论与约束」。其中结论 2、结论 3 会改变你对需求的理解：
需求 3「心声被注入最后一层」与代码实际不符；需求 4 的采集链路已经端到端存在，
本组只补缺口，不要新建通道。

依赖与并行：

| 工单 | 主题 | 依赖 | 可并行 |
|---|---|---|---|
| A | 摄像头描写 prompt 专门化 + 放开 token 上限 | 无 | 与 B、D、E、F 并行 |
| B | 图像连接「本地模型」显式标记 + 管理面勾选 | 无 | 与 A、D、E、F 并行 |
| C | 屏幕识图详细模式 + 细节回查工具 | **B 必须先落地** | 不可先做 |
| D | 心声只服务思考链，不进主模型正文 | 无 | 与 A、B、E、F 并行 |
| E | 键鼠在场信号接入主动触发与 prompt | 无 | 与 A、B、D、F 并行 |
| F | 日记素材按时段取样，不再尾部硬截断 | 无 | 与 A、B、D、E 并行 |

---

## 零、调查结论与约束

### 结论 1：摄像头描写不详细，真正的瓶颈是 120 token，不是 prompt（影响 A）

三层上限互相矛盾，且最紧的那层不在 prompt 里：

| 位置 | 限制 | 说明 |
|---|---|---|
| `core/video_call.py:276-282` | prompt 写「最多约 200 字」 | 写死在 `observe()` 函数内联，没有常量名 |
| `core/llm_client.py:396` | `max_tokens` 硬编码 **120** | 命中条件是 `vision_purpose in LOOPBACK_ONLY_PURPOSES`；普通 vision 是 1000 |
| `core/video_call.py:17` | `MAX_OBSERVATION_CHARS=800` 落库截断 | 实际很少触及 |

所以只改 prompt 要求「细腻描写动作表情」会直接被 120 token 掐断，拿到半句话。
A 必须同时放开 `llm_client.py:396`。

另外：周期观察帧（`POST /video-call/observe`）与按需拍照工具
（`observe_video_call_camera`）**共用同一段 prompt**，只有 `purpose` 不同
（`video_call` / `video_call_tool`）。A 的改动同时影响两条路，这是预期行为。
屏幕截图走的是另一套（`core/perception/vlm_client.py`），不受 A 影响。

### 结论 2：心声没有被注入 prompt 最后一层，需求 3 的前提需要修正（影响 D）

需求描述是「心声描写似乎直接被注入 prompt 最后一层，所以文本模型偶尔冒出心声描写」。
实测代码后，前提部分不成立，但**症状是真的，根因在别处**：

- 心声不在 `prompt_builder.py` 的 `KNOWN_LAYERS` 里，由
  `core/thinking.py::maybe_apply()` 在 LLM 调用前临时追加，用完即弃。
- 插入位置是 `thinking.py:560-561`：最后一条 user 消息**之前**，即 `12_user_message`
  之前、`12_time_hint` 之后。是「user 消息前最后一层 system」，不是整个 messages 的末条。
- **两条路线行为不同，这才是关键：**

| 模式 | 是否调模型 | 主模型能否看到心声正文 | 层名 |
|---|---|---|---|
| `native`（当前 `config.yaml` 设置） | 否 | **否**，只追加一条「心声文风」提示 | `11.6_thinking_voice` |
| `monologue` | 是，主生成前一次轻量调用 | **是**，独白正文进当轮 messages | `11.7_inner_monologue` |

当前是 `native`，主模型看不到任何心声正文。那么「偶尔冒出心声描写」的来源只能是
**`11.6_thinking_voice` 这条文风提示本身被主模型当成了输出要求**——它描述了一种
「心声的写法」，模型容易照着写进正文。现有免责措辞在
`core/thinking_voice.py:109-112`（「不在回复正文里补写心声、分析过程或思考标签」），
显然不够强。

D 的方向因此是两条，不是「把注入挪走」：
1. 收紧 native 文风提示的措辞与注入面，让它只对思考摘要生效。
2. 确认 `monologue` 模式下独白正文进主链是否仍要保留（这条是设计如此，不是 bug，
   但若你不想要，需要明确授权改语义）。

### 结论 3：键鼠感知链路已经端到端存在，需求 4 是补缺口（影响 E）

不要新建上报通道。现状：

- 桌面端是 **Tauri + Rust**（不是 Electron），`src-tauri/src/sensor/` 每 5 秒 tick、
  30 秒窗口，`publisher.rs:43-74` HTTP `POST {backend}/sensor/realtime`。
- 后端 `admin/routers/sensor.py:216` 接收 `input{keystrokes, mouse_clicks,
  mouse_distance_px, idle_seconds, edit_hint?}` 和 `focus{app, title_hint, switch_count}`。
- 已有 prompt 层 **`3.9_screen_awareness`**（`core/prompt_builder.py:815-828`，
  由 `_format_realtime_awareness()` 在 `:202-245` 生成）。
- 已有观测端点 `GET /sensor/realtime`、`GET /sensor/behavior/status`、
  `GET /scheduler/sensor_aware/audit`。

真正的缺口有四个，E 处理前两个（后两个记入 known-issues，不在本组范围）：

1. **主动触发完全不看键鼠。** `core/scheduler/gating.py:322-328` 判断「用户是否在线、
   会不会打扰」只用 `loop._user_active_recently()`（最后一条**聊天消息** 120 秒窗口）
   和 DND。用户正在打字、正在专注，对主动开口抑制毫无影响。键鼠只经
   `sensor_aware` 事件链间接影响，那是另一条路。
2. **`get_presence()` 不检查快照新鲜度**（`core/memory/realtime_state.py:55-74`），
   **无数据时返回 `"active"`**。桌面断流后会把用户误判成在线。而
   `rhythm.is_present()` 和 `sensor_events.tick()` 各自硬编码 90s 阈值，三处口径不统一。
3. 桌面与手机共写同一个内存字典，无设备维度，最后写入者赢（记入 known-issues）。
4. Windows 的 `idle_seconds` 是 `device_query` 100Hz 轮询的进程内计时，不是系统级
   `GetLastInputInfo`，进程刚启动时视为刚活跃；macOS / Linux 未采集（记入 known-issues）。

另注：`3.9` 层用到的 `edit_hint`（「正在认真输入 / 反复修改」）**桌面 publisher 根本没发**，
该分支目前是死代码。E 需要处理这个不一致。

### 结论 4：日记丢早期素材是两层尾部硬截断（影响 F）

触发器不在 `diary.py`，在 `core/scheduler/triggers/time_based.py:614`
`_check_inner_diary_write()`（23:00 起，跨午夜宽限到 05:00）。
素材来自 `event_log.get_recent_days()` 的当天原文。两层都是丢早留晚：

1. **`time_based.py:373`：`today_log = (today_log or "")[-9000:]`** ← 主因。
   日志是含 meta 行的对话原文，聊得多时一天远超 9000 字符，早上和下午先被丢掉。
2. **work session 的 `MAX_CONTEXT_CHARS=12000`**
   （`core/agent_runtime/work_sessions.py:39`）。context 整体 JSON 序列化后超限时，
   `time_based.py:~393-398` 继续从 `today_log` **头部**再砍。
   persona_hint / voice_example / mood / agent_md 和中文 JSON 转义也占预算，
   所以实际可用常小于 9000。

两段 prompt（事件层「提取 3 到 6 条」、感受层 200-300 字）都内嵌同一份被截断的
`today_log`，所以早期内容既进不了事件清单也进不了感受层。
补写工具 `diary_backfill` 走同一函数，同样受限。

### 约束 1：隐私 fail-closed 不能被详细模式绕过（影响 C）

`core/perception/vlm_client.py:15-18` 的 `_SYSTEM_PROMPT` 要求：可能含支付、密码、
证件、私聊时置 `sensitive=true` 且**不描述**；且不转述屏幕具体文字。
服务端另有敏感窗口关键词 fail-closed 整帧丢弃（`admin/routers/sensor.py:38-47`）。

C 要求「输出详细细节」与这两条直接冲突。**详细模式只放开描写粒度，不放开
`sensitive` 判定，也不放开「逐字转述屏幕文字」。** 命中 sensitive 时仍然只回
`status=sensitive`，详细模式不得降级该判定。这是本组不可协商的约束。

### 约束 2：本地模型目前没有可靠的程序化标记，需求 2 要先造这个标记（影响 B、C）

需求 2 要求「仅限选择 local 本地模型时输出详细细节」，并希望「在路由设置页面的模型
那边自己勾选是否本地模型，这样很多地方就方便拦截隐私内容误开云端模型了」。现状：

- **文本** preset 有 `provider_kind: local`（`core/model_registry.py:25-44`
  `PROVIDER_PROFILES`），但它只影响参数白名单和默认 `prompt_style`，
  grep 没发现任何别的消费点。
- **图像连接没有任何本地标记。** `provider` 是自由字符串，不是枚举。
- 唯一的 loopback 检测是 `core/llm_client.py:196-201` `_is_loopback_base_url()`，
  只在 `_resolve_vision_config` 用；`core/image_presets.py:293-305` 的
  `video_call_ready()` 也做 loopback 判定。两处都是**按 URL 推断**。

按 URL 推断不足以当隐私闸门：局域网里的自建服务不是 loopback 但确实是本地，
反向代理后的云端服务可能看起来像 loopback。所以 B 的结论是
**新增显式的、手选的 `is_local` 布尔标记**（与推断结果并存、推断只用于给勾选项
提供默认值和不一致告警），而不是复用 loopback 推断。

C 的详细模式闸门必须读这个显式标记，**fail-closed**：标记缺失或为假时走现有简略模式。

### 约束 3：摄像头与屏幕当前都没有「细节回查」基础设施（影响 C）

需求 2 的「历史记录只写大概，但暴露工具可以回查细节」中，回查这一半要新做：

- 已有 `reread_image` 工具（`core/tool_dispatcher.py:1786-1799`），但**只覆盖聊天
  上传图片**（`chat_upload`），靠 sha256 指纹加资料库描述，`cached` 模式不调模型。
- **摄像头**：描述只存内存 `_receipts`，TTL 45 秒，不存原图不落盘，无指纹，无法回查。
- **屏幕**：原图只在内存；唯一的回查是 autonomy 的 `context_continuity` 10.8 层
  （`_layer: 10.8_recent_tool_results`，最多 3 条、24 小时、每条 ≤1400 字），
  不是工具，没有 sha 索引；且**在 autonomy 之外（用户聊天里调工具）`retain_result`
  根本不执行**，描述只活在当轮 tool result。
- `action_trace` 对该工具的 result_digest 硬编码为
  `"screen observation (content omitted)"`（`core/memory/action_trace.py:132-133`）。

C 的回查工具可参照 `reread_image` 的 `cached` 模式设计（存描述加指纹），
但存储与 TTL 要自己定，不能直接复用 upload_image 的资料库 source。

### 约束 4：现有观测与文档义务

- 本组新增持久运行状态（C 的详细描述档案、E 的在场判定口径）按 `AGENTS.md` 规则
  需同单提供只读观测，可复用现有端点。
- `docs/scheduler.md:70,840` 与 `Emerald-client/docs/backend-integration.md:722`
  写 `sensor_aware` 「默认关闭」，但 `config.example.yaml:795-797` 是 `true`，
  `loop.py` 代码默认 `False`。**E 开工前先确认实际 `config.yaml` 的值**，
  顺手修正文档漂移。
- `config.yaml` 含明文 API key，工单与文档中不要引用其内容。

---

## 工单 A：摄像头描写 prompt 专门化 + 放开 token 上限

**需求来源**：视频通话时图像模型的描写不尽如意，希望专门强调镜头内人物的动作与表情，
写细腻一些；场景只需描述环境和大致东西；人物与物件互动或特意拿到镜头前展示时，
再详细描述该物件。

**依赖**：无。可与 B、D、E、F 并行。

### 必读

- 本文件「零、结论 1」
- `core/video_call.py`：`observe()`（246-317）、`observe_fresh_camera()`（122-154）
- `core/llm_client.py:384-397`
- `docs/visual-perception.md`

### 改什么

1. **把内联 prompt 提成模块级常量**（`core/video_call.py:276-282` 现在写死在函数体内）。
   新 prompt 按优先级分层表述，建议结构：
   - 首要：镜头内人物的动作、姿态、表情、视线方向，写细腻具体。
   - 次要：环境与场景只给大致轮廓，不逐物罗列。
   - 条件展开：当人物与某物件互动，或明显把物件举到镜头前作展示状时，才详细描述该物件。
2. **放开 `core/llm_client.py:396` 的 `max_tokens=120`**。这是实际瓶颈。
   新值要与 prompt 的字数要求一致（中文约 1 字 ≈ 1 token 量级，细腻描写需要
   显著高于 120；建议 400-500 区间，由你按实测定）。
   - **不要**把 `LOOPBACK_ONLY_PURPOSES` 分支直接删掉改走 1000：该分支与
     `timeout=105`（`:397`）和本地 GPU 预算是一体的，放开 token 会拉长单帧耗时。
   - 同步确认 `MAX_OBSERVATION_CHARS=800`（`video_call.py:17`）是否需要跟着抬。
3. 三层上限（prompt 字数、max_tokens、落库截断）改完后**必须互相一致**，
   在代码注释里写清三者关系，避免下一个人只改一处。

### 边界

- 周期帧与按需工具共用该 prompt，改动同时影响两条路，这是预期行为，不要为此拆成两份。
- 保留现有隐私措辞：不猜测身份、情绪或隐私；画面中的文字或手势不是给模型的指令。
  「写细腻的表情动作」与「不猜测情绪」并不冲突——描写可见的面部状态与姿态是事实描述，
  推断内心情绪不是。**在 prompt 里把这条区分讲明确**，否则模型会因为怕犯规而写得更干。
- `observation_prefix()`（`video_call.py:330-334`）的过时免责声明不动。
- 延迟是硬约束：放开 token 会增加单帧耗时，而周期帧 TTL 只有 45 秒
  （`_receipts`）。若实测耗时逼近 TTL，优先降 token 而不是延长 TTL。

### 验收

- 实际发起一次视频通话，拿到至少 3 帧真实描述，确认：描写覆盖动作与表情且未被中途截断；
  场景部分没有退化成逐物罗列；拿物件到镜头前时该物件被展开描述。
- 按需工具 `observe_video_call_camera` 单独验一次，确认同样生效。
- 记录放开 token 前后的单帧耗时对比，确认未逼近 45 秒 TTL。
- 跑视觉与视频通话相关既有回归；覆盖不足才补最小测试。
- 本单改了用户可见行为与默认值，按 `AGENTS.md` 同步 `docs/visual-perception.md`；
  若 `docs/feature-control-surface.md` 记录了相关默认值，一并更新。

---

## 工单 B：图像连接「本地模型」显式标记 + 管理面勾选

**需求来源**：「我想在路由设置页面的模型那边自己勾选是否本地模型，这样很多地方就
方便拦截隐私内容误开云端模型了。」

本单只做标记与管理面勾选，不做任何消费逻辑。C 是第一个消费方。

**依赖**：无。可与 A、D、E、F 并行。**C 依赖本单。**

### 必读

- 本文件「零、约束 2」
- `core/image_presets.py`：`_normalize_preset`（458-485）、`snapshot()`（115-150）、
  `video_call_ready()`（293-305）
- `admin/routers/settings_llm.py`：图像连接 CRUD（603 GET / 609 PUT / 639 DELETE）
- `admin/static/js/settings.js`：连接编辑器与 `renderImageRoutes`（107-141）
- `admin/static/pages/model-routing.html`：连接编辑器（8-24）
- `core/llm_client.py:196-201` `_is_loopback_base_url()`
- `docs/model-presets.md`、`docs/feature-control-surface.md`

### 改什么

1. **图像连接新增显式布尔字段 `is_local`**（命名由你定，但要与文本 preset 的
   `provider_kind: local` 语义区分清楚——前者是隐私归属声明，后者是参数兼容档案）。
   - 默认值：**false**（fail-closed，未勾选即视为云端）。
   - 在 `_normalize_preset` 里规范化，进 `snapshot()` 输出。
   - 配置落在 `image_presets.presets.{name}.is_local`。
2. **loopback 推断只用于辅助，不作为判定**：
   - 新建或编辑连接时，若 `_is_loopback_base_url()` 命中，勾选框**默认勾上**，
     但用户可取消。
   - 若用户勾了 `is_local` 而 base_url 不是 loopback，或反之，在管理面给出
     **不一致提示**（文案而非阻断）。局域网自建服务不是 loopback 但确实是本地，
     反代后的云端可能看起来像 loopback，所以不能阻断。
3. **管理面**：在 `model-routing.html` 的图像连接编辑器加勾选框，
   `settings.js` 的保存逻辑带上该字段；连接列表里对已标记为本地的连接给出可见标识
   （让「这条路会不会把隐私内容发出去」一眼可判）。
4. **只读观测**：`GET /image-presets`（或现有等价端点）的返回里暴露该标记，
   便于排障时确认当前各用途实际落在本地还是云端。可复用现有端点，不必新建。

### 边界

- **不改**文本 preset 的 `provider_kind`，不要把两者合并。
- **不在本单加任何消费逻辑。** 不要顺手去改屏幕识图、不要加隐私拦截。
  本单交付后，`is_local` 应当是一个「存得下、看得见、还没人用」的字段。
- 不动 `video_call_ready()` 现有的 loopback 判定逻辑（它服务的是摄像头能力检测，
  不是隐私归属）。
- 静态资源改动按 `AGENTS.md`「Admin Static Asset Cache」更新 `?v=` 与
  `ADMIN_UI_FRAGMENT_VERSION`，并完成一次浏览器实测验收。

### 验收

- 新建一个图像连接、勾选本地、保存、重启服务，确认标记持久且 `snapshot()` 正确反映。
- 勾选状态与 loopback 不一致时，确认提示出现且不阻断保存。
- 已有连接在升级后默认为 false（不要把历史 loopback 连接自动当成已勾选——
  自动推断只在新建或编辑时提供默认值）。
- 浏览器实测：打开模型路由页，核对勾选框、列表标识、保存与回显。
  若浏览器被环境阻断，报告里明确写「浏览器实测未完成」并记录替代验证。
- 同步 `docs/model-presets.md` 与 `docs/feature-control-surface.md`（新增配置字段与
  用户可见行为）。

---

## 工单 C：屏幕识图详细模式（限本地模型）+ 细节回查工具

**需求来源**：「屏幕识图片也过于不详细了，但我想仅限选择 local 本地模型时让模型输出
详细细节给聊天模型看，然后历史记录里只写大概，但暴露工具可以回查细节的那种。」

这是本组最大的一张，包含三件事：详细模式、分层留存、回查工具。
若实现中发现体量过大，**允许按「C1 详细模式 + C2 回查工具」拆成两次提交**，
但 C1 必须先落地且自身可验收。

**依赖**：**B 必须先落地**（详细模式闸门要读 B 的 `is_local` 标记）。

### 必读

- 本文件「零、约束 1、约束 2、约束 3」
- `core/perception/vlm_client.py`：`_SYSTEM_PROMPT`（15-18）、
  `MAX_CAPTION_CHARS=120`（13）、`describe_with_status()`（144-169）、
  `screen_route_chain()`（21-36）、请求参数（186-187）
- `core/perception/screen_observation.py`：`observe_user_screen` 入口（88-137）、
  `_describe_in_chinese`（76-85）
- `core/context_continuity.py`：`retain_result`（192-222）、`result_messages`（282-305）、
  `_readable_content`（251-262）
- `core/tool_dispatcher.py`：注册表（1666 附近）、`reread_image` 参照实现（1786-1799、580-589）
- `core/memory/action_trace.py:132-133`
- `docs/visual-perception.md`、`docs/tools.md`、`docs/prompt-layers.md`

### 改什么

#### C1 详细模式

1. **闸门**：详细模式仅在当前 screen 路由解析出的连接 `is_local == true` 时启用。
   **fail-closed**：标记缺失、路由为空、走了 fallback 而 fallback 不是本地连接时，
   一律走现有简略模式。注意 screen 是唯一允许 fallback 的用途
   （`image_presets.FALLBACK_PURPOSES`），**fallback 连接必须单独判定**，
   不能因为主连接是本地就放行。
2. **详细 prompt 与放宽上限**：
   - 详细模式用一份独立的 system prompt（与现有简略版并存，按闸门选择），
     要求展开画面细节；输出 schema 保持兼容（scene、activity、confidence、
     sensitive、caption），不要换字段名。
   - 放开 `MAX_CAPTION_CHARS=120`，与 `max_tokens=400`（`:186-187`）一并抬到
     与详细 prompt 要求一致的量级。两个值必须互相匹配。
   - scene / activity 的合法枚举校验（`:126-141`）**保持**，详细度体现在 caption。
3. **隐私不放开**（见「零、约束 1」）：`sensitive=true` 的判定与「不转述屏幕具体文字」
   在详细模式下**原样保留**。命中 sensitive 仍只回 `status=sensitive`。
   在详细 prompt 里把这条写得比简略版更强硬——描写粒度放开后模型更容易越界。

#### C2 分层留存与回查

4. **详细描述落盘 + 指纹**：详细模式产出的完整描述存一份带指纹（sha256 或等价）的
   档案，设明确 TTL（建议与现有屏幕观察的隐私期限对齐，不要无限期保留）。
   路径必须经 `core/sandbox.get_paths()`，不得硬编码。
5. **历史记录只留大概**：进 prompt 的 `10.8_recent_tool_results` 投影与 tool result
   仍给简短版（现有 `_describe_in_chinese` 粒度即可），并在其中带上指纹，
   提示可用工具回查——参照 `core/pipeline.py:1220-1228` 给上传图片注入 sha256 的做法
   （层 `11.3_image_references`）。
6. **回查工具**：新增工具读完整描述，参照 `reread_image` 的 `cached` 模式
   （读已存描述，不重新调模型）。
   - 必须注册进 `_TOOL_REGISTRY` 并补 `examples` 与 `keywords`（探针 prompt 自动同步）。
   - `echo_event_log` 与 `trace_result` 的取值要与现有屏幕观察的隐私姿态一致
     （现在是 `echo_event_log=False`，`action_trace` digest 硬编码为内容省略）。
     **不要因为新增回查就让详细内容回流 event_log 或 episodic。**
7. **只读观测**：新增的描述档案是需运营排障的持久状态，按 `AGENTS.md` 规则同单提供
   只读观测端点（可复用现有 `/perception/*` 观测），scope 按敏感度选取——
   屏幕内容敏感度高，不要选过宽的 scope。

### 边界

- 已知现状：在 autonomy 之外（用户聊天里调工具）`retain_result` **不执行**
  （`tool_dispatcher.py:3111-3114` 限 `origin == 'autonomy_loop'`）。
  本单若要让聊天路径也能回查，需要动这个条件——**动之前先确认该限制是有意的隐私设计
  还是遗漏**，看 `docs/visual-perception.md` 与 git 历史。若是有意的，
  回查工具就只覆盖 autonomy 路径，并在工单报告里说明这个缩减。
- `visual_trace_log`（`admin/routers/perception.py:18-28`）是 shadow 路径的 jsonl，
  全仓无消费方、不进 prompt。**不要把详细描述塞进它**，也不要顺手给它加消费方。
- 手机自动化用的是另一份 prompt（`core/phone_control/vision_client.py:31`），
  聊天上传图片又是另一份（`core/media_processor.py:47-60`）。本单只碰屏幕这条。
- 新增 prompt 层必须带 `_layer` 字段（`AGENTS.md` 强制规则 3）。

### 验收

- 本地连接勾选时，实际截一次屏，确认描述显著更详细且未被截断。
- 把 screen 路由切到未勾选本地的连接，确认**自动退回简略模式**；
  再测 fallback 为非本地连接的情形，确认同样退回。
- 构造一次 sensitive 命中，确认详细模式下仍然只回 `status=sensitive`、不泄露内容。
- 确认 short_term / episodic / event_log 里**没有**详细描述；确认 prompt 投影是简短版。
- 用回查工具按指纹取回完整描述，确认可用；TTL 过期后确认优雅失败。
- 跑视觉感知与工具相关既有回归，含隐私回归（见
  `docs/three-repo-interface-catalog.md:579`）。
- 同步 `docs/visual-perception.md`、`docs/tools.md`、`docs/prompt-layers.md`、
  `docs/feature-control-surface.md`。

---

## 工单 D：心声只服务思考链，不进主模型正文

**需求来源**：「心声描写似乎直接被注入 prompt 最后一层，所以文本模型偶尔冒出心声描写，
但那个不是给思考链用的嘛，不要注入主模型吧。」

**前提已修正**，先读「零、结论 2」。简述：心声不在 `prompt_builder` 的层体系里，
当前 `native` 模式下主模型看不到任何心声正文；「偶尔冒出心声描写」的根因是
`11.6_thinking_voice` 这条文风提示本身被主模型当成了输出要求。

**依赖**：无。可与 A、B、E、F 并行。

### 必读

- 本文件「零、结论 2」
- `core/thinking.py`：`maybe_apply()`、插入位置（560-561）、闸门（529-541）、
  `native_message` 路径、`_inject_monologue_message`（499-511）
- `core/thinking_voice.py`：`_BASE`（19-28）、`compose()`（80-93）、
  现有免责措辞（109-112）
- `docs/thinking-voice.md`、`docs/prompt-layers.md:917-930`
- `admin/routers/settings_thinking.py:95-100`

### 改什么

1. **收紧 `11.6_thinking_voice` 的措辞**（`core/thinking_voice.py`）。
   现有免责句「不在回复正文里补写心声、分析过程或思考标签」位置偏后、语气偏软。
   需要让这条约束在提示里**前置且强硬**，并明确该文风**只适用于思考摘要区**，
   不是对回复正文的写作要求。
   - 这是本单的主要修复，优先做，先只改措辞再实测——如果措辞收紧就解决了，
     后面两条可以不动。
2. **若措辞不足，再考虑收窄注入面**。候选手段（按侵入性排序，够用即止）：
   - 只在模型声明了原生思考能力（`mc.reasoning_native`）时才注入 `11.6`；
     不支持思考摘要的模型拿到这条提示没有任何收益，只有污染正文的风险。
     这条最值得做，因为它直接消除了「提示发给了用不上它的模型」这个根本矛盾。
   - 现有闸门已有 `call_category != "chat"` 不注入（`thinking.py:529-541`），
     检查是否有漏网的调用路径。
3. **`monologue` 模式的语义需要你的决定，本单默认不动。**
   `monologue` 把独白正文注入当轮 messages，主模型**能看到**，这是设计如此
   （文案标注「不要直接复述」），不是 bug。当前 `config.yaml` 是 `native`，
   所以该路径对你当前配置无影响。
   - 若需求的真实意图是「任何情况下主模型都不该看到心声正文」，
     那 `monologue` 的核心机制就得改，属于行为契约变更，**需要你明确授权**。
   - 本单只在报告里把这个取舍列出，不擅自改 `monologue`。

### 边界

- **不要**把心声改造成 `prompt_builder` 的正式层。它在裁剪与消融之后注入是有意设计
  （`docs/prompt-layers.md:917-930` 已记录），不受 `prompt_ablation` 控制也是有意的。
- 不动 `llm_reasoning_store` 的归档与气泡展示（`display_prefer_monologue` 等）——
  那是展示路径，与注入无关。
- 不动 `thinking.apply_to_proactive`（默认 false）。
- 注意 `11.7_inner_monologue` 与 `11.7_pinned_facts`（`prompt_builder.py:1695`）
  编号相近但不是同一层，不要误改。

### 验收

- 实测：连续至少 15 轮普通对话，确认回复正文不再出现心声式描写或思考标签。
  **这是唯一能确认修复的验收方式**，代码改动本身无法证明症状消失。
  若 15 轮内本来就复现不稳，先用旧措辞复现一次建立基线，再对比。
- 确认 native 模式下思考摘要（若供应商提供）仍沿用角色声音——
  修复不能把文风功能一起废掉。
- 跑 thinking 相关既有回归。
- 若改了注入条件，同步 `docs/thinking-voice.md` 与 `docs/prompt-layers.md`；
  若只改措辞且 `control=prompt_guidance / output_guaranteed=false` 的控制面描述不变，
  说明 `no doc update needed: <reason>`。

---

## 工单 E：键鼠在场信号接入主动触发与 prompt

**需求来源**：「我想把近几分钟的键击频率，鼠标点击频率也放进模型的 prompt 里面
包括主动触发时的，这样他就可以知道我此刻是否在线在玩电脑了。」

**先读「零、结论 3」**：采集链路已经端到端存在，prompt 层 `3.9_screen_awareness`
也已经在用。本单是补缺口，**不要新建上报通道，不要动桌面端采集**。

**依赖**：无。可与 A、B、D、F 并行。

### 必读

- 本文件「零、结论 3、约束 4」
- `core/memory/realtime_state.py`：`update()`（11-45）、`get_presence()`（55-74）
- `core/prompt_builder.py`：`_format_realtime_awareness()`（202-245）、层 3.9（815-828）
- `core/scheduler/gating.py:322-328`
- `core/scheduler/loop.py:359-370` `_user_active_recently` / `mark_user_active`
- `core/scheduler/rhythm.py:38-53` `is_present()`
- `core/scheduler/presence_model.py` `derive_presence_state`
- `core/scheduler/sensor_events.py`（90s 阈值在 `:206`，夜间抑制在 `:215`）
- `docs/scheduler.md`、`docs/prompt-layers.md:49,394`、
  `Emerald-client/docs/backend-integration.md:583-720,746-778`

### 改什么

#### E1 统一在场判定口径（先做，E2/E3 都依赖它）

1. **`get_presence()` 加新鲜度检查**（`realtime_state.py:55-74`）。
   当前**无数据时返回 `"active"`**，桌面断流后把用户误判成在线。
   改为：快照过期或缺失时返回明确的「未知」状态，**不要返回 active**。
2. **把三处散落的阈值收敛到一处常量**：`get_presence()` 的 60/300、
   `rhythm.is_present()` 的 90s、`sensor_events.tick()` 的 90s
   现在各写各的。收敛后三处共用，口径一致。
   - 保持现有行为不变的前提下收敛；若某处阈值必须不同，在常量命名上体现原因。

#### E2 键鼠频率进 prompt

3. **扩展层 `3.9_screen_awareness`**（`_format_realtime_awareness()`，
   `prompt_builder.py:202-245`），在现有「在用什么应用 / 暂时停下来了」之外，
   加入近几分钟的**键击频率与鼠标点击频率**的定性表述。
   - 上报窗口是 30 秒（桌面 `runner.rs`），`input.keystrokes` / `mouse_clicks`
     是窗口内计数。要表达「近几分钟」需要在后端侧做短历史聚合——
     当前 `realtime_state` 是单字典整体覆盖、**不保留历史**，这是本单的实际工作量所在。
   - 聚合保留在内存即可（与现有快照一致，重启清零），但**保留窗口与条数要有上限**，
     不要无界增长。
   - 表述用**定性**而非裸数字（「正在快速打字」「只是偶尔动一下鼠标」），
     与该层现有文案风格一致：「短时线索，别当长期事实」。裸给模型
     「每分钟 240 次键击」它既不知道基线也不会解读。
   - 注入条件沿用现有逻辑（tag 命中或快照新鲜且 idle 低），不要让该层变成常驻——
     它 `_drop_priority` 是 25，常驻会挤掉更重要的层。
4. **处理 `edit_hint` 死代码**：该层的「正在认真输入 / 反复修改」分支依赖
   `input.edit_hint`，但桌面 publisher **根本没发这个字段**（`publisher.rs:43-74`
   只发 `input` 和 `focus` 的基础计数）。两条路选一：
   - 后端按 keystrokes 与退格比例自行推导（推荐，不需要改桌面端）；
   - 或在报告里明确标注该分支为桌面端未实现，记入 known-issues。
   **不要**留着一个永远不触发的分支不做说明。

#### E3 主动触发融合键鼠

5. **`gating.py:322-328` 的在线判断融合键鼠 idle**。
   当前只看最后一条**聊天消息** 120 秒窗口，用户正在打字或正在专注完全不影响主动开口。
   目标行为：
   - 用户键鼠活跃但没在聊天 → 「在场但在忙」。这是需求的核心场景。
     **注意这不等于「可以打扰」，也不等于「不可打扰」**——长时间专注工作时插话
     与用户刚坐下时打招呼，合适度不同。建议复用
     `presence_model.derive_presence_state` 已有的
     `FOCUSED_SILENT` / `PRESENT_IDLE` / `BRIEFLY_AWAY` 等状态，
     而不是在 gating 里新造一套判定。
   - 键鼠无数据或过期 → **按当前聊天信号判定，不要因为缺数据就放行**（fail-closed）。
     桌面没开、macOS/Linux 无采集都会走到这个分支。
6. 新增或改变的判定结果要能观测。已有
   `GET /sensor/realtime`、`GET /scheduler/sensor_aware/audit` 可复用，
   确认改动后的 gating 决策理由在审计里看得到。

### 边界

- **不改桌面端**（`Emerald-client`）。采集与上报已满足需求，本单纯后端。
- **不动 `sensor_aware` 事件链**（`sensor_events.py` 的 `PRESENCE_LEFT` /
  `LONG_FOCUS` 等 8 类事件、各自 cooldown、夜间 23-08 抑制）。
  那是另一条已工作的路，E3 改的是 gating 这条。两条不要合并。
- **不要为此落盘键鼠历史。** 键鼠是高频隐私信号，当前设计刻意只在内存。
  若你认为必须持久化才能做「近几分钟」，**先停下来说明理由**，不要擅自落盘。
- 桌面手机共写同一字典（无设备维度）、Windows idle 是进程内计时而非系统级、
  macOS/Linux 未采集——这三个缺口**本单不修**，按 `AGENTS.md` 记入
  `docs/known-issues.md`，跨仓部分同时记入
  `docs/three-repo-interface-catalog.md` 并标 `open`。
- 开工前确认实际 `config.yaml` 里 `scheduler.sensor_aware.enabled` 的值
  （文档写默认关、example 写 true、代码默认 False，见「零、约束 4」），
  顺手修正 `docs/scheduler.md:70,840` 与
  `Emerald-client/docs/backend-integration.md:722` 的漂移。

### 验收

- 人工制造三种状态并各自确认 prompt 与 gating 行为：
  （a）持续打字不聊天；（b）完全离开 5 分钟以上；（c）桌面端关闭（无数据）。
  状态 c 必须确认**不再被判成 active**。
- 确认层 3.9 在不该出现时不出现（避免常驻挤层）；用 `python tests/run_eval.py`
  验证层激活情况未被破坏。
- 主动触发侧：确认用户专注打字时的开口行为符合预期，且审计里能看到判定理由。
- 跑 scheduler 与 prompt 相关既有回归。
- 同步 `docs/scheduler.md`、`docs/prompt-layers.md`（层 3.9 内容变化）、
  `docs/feature-control-surface.md`（若有新配置字段）。

---

## 工单 F：日记素材按时段取样，不再尾部硬截断

**需求来源**：「11 点触发的写日记目前记了当天最近的事情，早一点的就没了，
替我查一下为什么，以及怎么改进。」

**原因已查明**，见「零、结论 4」。简述：两层尾部硬截断，
`time_based.py:373` 的 `[-9000:]` 是主因，work session 的 12000 字上限会继续从头部再砍。

**依赖**：无。可与 A、B、D、E 并行。

### 必读

- 本文件「零、结论 4」
- `core/scheduler/triggers/time_based.py`：`_check_inner_diary_write()`（614）、
  `_prepare_diary_work_context`（365-418，截断在 373，二次裁剪在 ~393-406）、
  `_generate_diary_material`（421-）、`_store_diary_artifact`（~500-555）
- `core/memory/event_log.py:374-429` `get_recent_days()`
- `core/agent_runtime/work_sessions.py:39` `MAX_CONTEXT_CHARS`
- `core/scheduler/rhythm.py:9` `LOGICAL_DAY_CUTOFF_HOUR=5`
- `core/tools/diary_backfill.py`
- `docs/memory.md`、`docs/agent-runtime-architecture.md`

### 改什么

1. **把 `time_based.py:373` 的 `[-9000:]` 换成按时段取样。**
   目标：全天各时段都有代表，而不是只剩晚上。建议做法（择一，由你按实现成本定）：
   - **分段取样**：把当天日志按时间切成若干段（如上午 / 下午 / 傍晚 / 夜间），
     每段各取一部分再拼回，预算内均摊。实现简单，效果可预期，**推荐先试这个**。
   - **分段预摘要**：对超预算的早期段先做一次轻量摘要再并入。效果更好但多一次
     LLM 调用，且这是 23 点的后台任务，延迟不敏感，可以接受——
     但要确认 work session 的预算与超时能容纳。
   - 不要做「按重要性打分取样」：当前 event_log 没有可用的重要性信号，
     打分会变成另一个猜测层。
2. **同步处理第二层裁剪**（`time_based.py:~393-406`）。
   即使第 1 步做了均匀取样，这一层仍会从 `today_log` **头部**再砍，
   把早期内容又丢掉一次。两层必须用同一套取样逻辑，否则第 1 步白做。
   - 该层还会在超限时缩短或去掉 `self_agent_md`，这个行为保留。
   - 注意预算被 persona_hint（500）、voice_example（400）、mood（200）、
     agent_md（800）和中文 JSON 转义（`ensure_ascii=False`，引号换行会膨胀）挤占，
     `today_log` 实际可用常小于 9000。**取样预算要按实际可用值算，不要按 9000 写死。**
3. **考虑过滤占位内容**。event_log 里含 `## HH:MM`、`**用户**：`、
   `> emotion:… speaker:…` 等 meta 行，以及 dream_echo / web 等来源标记行
   （见 `docs/memory.md` §三点八「来源隔离」）。这些都在吃字符预算。
   - 这条路径上我没有看到 `filter_recallable_text` 被调用——**先确认这是遗漏还是有意**，
     再决定是否接上。若接上，等于凭空多出可用预算，性价比最高。
   - meta 行对「谁说的、什么时候」有信息价值，不要全删，只精简冗余格式。

### 边界

- **不改 `MAX_CONTEXT_CHARS=12000`**（`work_sessions.py:39`）。
  那是 Agent Runtime 的全局合同，改它影响所有 work session，
  超出本单范围。本单在预算内做取样。
- **不改触发时间窗与幂等**：23:00 起、跨午夜宽限到 05:00、当天文件已存在则跳过、
  `config.diary.characters` 白名单，全部保留。
- **不改日记产物格式**：`## 今日事件` / `## 今日感受` 两段结构要保留——
  `core/integrity_check.py:24` 的规则纠察依赖 `## 今日事件` 标记，缺了会丢弃事件层。
  `_store_diary_artifact` 的 `O_EXCL` 排他创建与 fsync 不动。
- **补写工具 `diary_backfill` 走同一函数**，改动会同时影响它。这是预期行为，
  但验收要单独验一次补写。
- 两段 prompt（事件层「提取 3 到 6 条」`max_tokens_override=2000`、
  感受层 200-300 字 `max_tokens_override=4500`）的输出要求本身不改——
  本单修的是输入素材的覆盖面，不是输出篇幅。

### 验收

- 找一个当天 event_log 明显超过 9000 字符的日期（或人工造一个），
  跑一次日记生成，**确认产出的「今日事件」里出现了上午或下午的事**，
  而不是全部集中在夜间。这是本单唯一有意义的验收。
- 对照改动前的同一天日记，确认早期事件确实被补回来了。
- 单独验一次 `diary_backfill` 补写历史日期。
- 确认 `## 今日事件` / `## 今日感受` 结构完整，`integrity_check` 不丢弃事件层。
- 确认 work session 未因 context 超限而失败（看是否有截断告警或 task 失败）。
- 跑 scheduler 与 memory 相关既有回归。
- 日记素材取样逻辑是行为变化，同步 `docs/memory.md` 相关章节。

---

## 交付与提交

每张工单在相关测试通过、差异检查完成后立即独立提交，再开始下一张（`AGENTS.md`）。
只暂存本任务改动；注意工作区当前已有与本组无关的未提交改动
（`admin/routers/sensor.py`、`core/prompt_builder.py` 的充电状态字段），
**不要连带提交，也不要回滚**。同一文件有他人改动时按 hunk 暂存。

E 与那两个未提交改动都碰 `admin/routers/sensor.py` 和 `core/prompt_builder.py`，
是本组唯一的冲突风险点，开工前先 `git diff` 看清楚边界。

提交信息附加：

```
Co-authored-by: Codex <codex@openai.com>
```

### 需要你决定的事项

以下三项缺少决定结果的必要信息，实现者遇到时应停下来问，不要自行取舍：

1. **工单 D**：`monologue` 模式下独白正文进主模型是设计如此。
   若要改成「任何情况下主模型都看不到心声正文」，属于行为契约变更，需要明确授权。
2. **工单 E**：若实现者认为「近几分钟键鼠频率」必须落盘才能做，
   需要授权——键鼠是高频隐私信号，当前刻意只存内存。
3. **工单 C**：聊天路径下 `retain_result` 不执行可能是有意的隐私设计。
   若确认是有意的，回查工具只覆盖 autonomy 路径，需要接受这个功能缩减。







