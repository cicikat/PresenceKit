# docs/tools.md — 工具系统

---

## MCP optional authentication (Brief 195)

The MCP import form starts with an empty header map. A bearer header is added
only by an explicit operator action, for example `Authorization: Bearer
${MCP_TOKEN}`. Empty headers are passed through as `{}` so unauthenticated MCP
servers can connect; an explicitly configured missing environment variable
continues to fail closed in the backend resolver. Header values are never
returned by the settings read projection or written to the UI URL.

## Memory Event read tools (Brief 201)

Path C owner-private function calling may expose the three read-only tools
`search_events`, `expand_event_window`, and `get_related_events` when the
effective tool-loop exposure includes category `memory`. They always receive
the frozen `uid + char_id` dispatcher scope and can read only the `reality`
event ledger; Path A, Dream, Stage, group, scheduler, and write paths do not
expose them.

`search_events` is the bounded seed lookup. `expand_event_window` reads a
deterministic temporal window, while `get_related_events` follows only
deterministic stored edges at depth 1. Each result includes `event_id`, time,
actor, topics/kind, source, turn ID, bounded evidence text, and relation
metadata where applicable. Per call limits are 20 events, 12,000 output
characters, 1,200 evidence characters per event, and one relation hop.

Tool results are untrusted data and are framed through the normal tool-result
boundary. Query failures, missing events, invalid scopes, and limit violations
return a structured `outcome_unknown` payload; they never claim success. Calls
are recorded in the existing action trace with bounded arguments and status,
but raw evidence is not written to `short_term`, `event_log`, or action trace.

## Intiface / 硬件工具冻结（Brief 151）

`toy_vibrate`、`toy_stop`、`toy_pattern` 与 `toy_job_status` 的实现和注册表条目保留，
但 `hardware.intiface_opt_in` 默认为 `false`。冻结时它们不会进入普通聊天 schema、
Tool Loop、autonomy 或 Self Capability，也不能通过直接 dispatch 执行；配置中的单工具
开关不能绕过这个总闸。显式 opt-in 后仍保留 owner 私聊、danger-mode 和现有硬件安全门。

## MCP 批量授权与严格白名单（Brief 135）

管理面 `PATCH /settings/mcp/{name}` 支持一次性的 `bulk_authorize` 动作，值只能是
`default` 或 `unrestricted`。后端只使用当前已连接 server 的 `list_tools()` 快照构造
`allow_tools`，不会信任前端提交的工具名；一次请求只写一次配置并触发一次 server 热重载，
响应包含 `processed_count`、最终 `allow_tools`、`tool_policy` 和 `reload_status`。

`default` 只补齐缺失的本地 policy，保留已有显式策略；可靠只读工具使用 `read`，其他工具
使用 `write`，新生成的普通策略统一写入 `require_confirm: false`。是否每次确认是管理员的逐工具
显式选择，不由远端 annotations、名称、描述或风险建议自动开启。导入、普通保存和批量默认授权
共用同一套生成规则。
`unrestricted` 是管理员明确选择的无限制执行模式，会为当前发现的全部工具写入
`effect: unrestricted`、`idempotent: true`、`require_confirm: false`，并只需一次管理面确认。

开启 `mcp_servers.require_local_policy` 后，`allow_tools: []` 表示零授权，不再表示全部允许。
普通保存会先补齐当前运行时快照中的缺失 policy；若仍有 allowlisted 工具没有有效本地 policy，
请求会在写盘前失败。非严格 legacy 模式保留空白 allowlist 的兼容行为。

## 工具触发路径

### 媒介 MCP 熟练度门控（Brief 61）

`mcp_proficiency` 按 MCP server 配置成长域与等级 tiers。连接层仍注册全量工具；
tool-loop schema 暴露层根据角色级 `interest_state` 的同域最高 level 过滤，`execute()`
再做一次防御性校验。未列入配置的 server 以及 tiers 从未列出的工具视为器官类，行为不变。
未解锁调用只返回中性失败文本，不记录动作痕迹，也不暴露等级或配置细节。

当前真正接入主流程的触发路径有两类：

### 静态 action ownership（Brief 132）

后端发出的 action 由 `core.tool_dispatcher.resolve_action_target()` 按静态表路由：
桌宠 v0.1 allowlist 中的既有动作只发给 `desktop_ws`，`show_heart` 只发给
`device_ws`。未登记 action fail-closed，不会广播给任意连接。设备动作只接受设备
ack，离线、NACK 或超时均为明确失败，且没有桌宠文件队列 fallback；桌宠动作保留既有
WS 后 `agent_actions.json` fallback。此处不是 capability negotiation，v0.1 不新增
hello 字段或协商流程。

```
路径A：统一 pre-pipeline 路由
  QQ 私聊 / /desktop/chat / mobile 前台
    → trusted_user_text（media merge 前捕获）
    → core.pretool_router.route_pretool(...)
    → 同时按注册表 keywords 识别明确的查询/操作意图，记录本轮 must-call 标记（只标记，不绕过 execute 闸门）
    → 再检查显式快速路径白名单（当前仅 get_time；与 keywords 无关）
    → 未命中且 Path C 未激活时，走 get_probe_prompt + Path A 的 function schema
    → 探针 user message 只含 trusted_user_text 与短期引用块，不含 media span 或主生成 prompt
    → QQ / desktop / mobile 共用同一 Path A 暴露面；不再由通道决定 category
    → 严格解析 native function call 或完整封闭的 <tool_call> 编码，再 execute_structured(origin="user_live")
    → 结果只以 bounded tool_result 写入 prompt 层10，带生成时间与 validity（current_turn / execution_failed / outcome_unknown）；raw probe 文本绝不进入主 prompt

  WAITING_CONFIRM / WAITING_INPUT 也由同一入口消费，分别返回结构化 confirmation_request
  或 missing_parameter_request；/chat 管理面板冻结入口仍不走工具探针。
```

明确意图会进入 `11_tool_grounding` 事实闸。若工具未暴露、探针未选中或执行失败，
主模型只能如实说明未完成/结果不明；不能把口头承诺、历史 `action_trace` 或失败兜底文案
说成已经查到、控制或完成。输出端还会对完成式断言做一次 fail-closed 校验。

**memory 类工具默认不走探针，路径C（tool loop）激活时才对主 LLM 可见。** 管理员可在
`tool_exposure.path_a` 显式加入该类；这会同时影响 QQ、desktop 和 mobile，不能只为一个端开启。
`read_diary/read_watch/search_diary/get_profile/get_episodic` 已注册且 `execute()` 能执行，
但路径A不把 memory 类喂给探针。Fable R5 已修复与 Author's Note 工具承诺的落差：
层11 Author's Note 现在是条件分支，有 `tool_result` 时提示已提供，无时明确禁止编造，
不再承诺主 LLM 可以调用工具。见 `docs/known-issues.md` F11。

```
路径C：tool loop 多步工具执行器（Brief 28，function_calling 模型专用）
  激活条件（三者同时成立，tool_dispatcher.tool_loop_active(uid)）：
    - 有效 tool_loop 开关 = true：角色卡 presence_ext.tool_loop="on" 强制开启，
      "off" 强制关闭，字段缺失/非法时回落 config.yaml tool_loop.enabled（默认 false）
    - uid 是 owner 的真实私聊轮（QQ 私聊 main.py / /desktop/chat，群聊在到达判断前已提前 return）
    - chat preset 的 tool_call_mode == "function_calling"（xml_fallback 小模型不激活）

  激活后：
    - `route_pretool()` 仍先检查显式快速路径白名单；只有普通 LLM 探针会被跳过，工具决策权
      随后移交主模型。QQ、desktop、mobile 的快速路径判定和执行契约相同
    - 主生成改走 Pipeline.run_agentic_loop()：
        chat_turn(messages, tools) → 有 tool_calls 就 execute(origin="assistant_loop") 回填
        role="tool" 消息（tool_call_id 对齐）→ 继续下一步，直到自然终止 / max_steps 耗尽 /
        总墙钟 total_timeout_s 超时
    - 用过 ≥1 个工具后，最终生成前注入 voice_reanchor system 提示，收尾出口改走
      run_llm()/run_llm_stream()（复用既有反坍缩重试），不再带 tools 参数
    - 高危工具触发 ask_confirm → 立即强制收尾，直接把询问文字作为本轮回复，下一步必须是问用户
    - Tool Ephemeral Status P0：`run_agentic_loop(tool_event_observer=)` 可将工具生命周期
      发给 UI 专用观察者。事件仅在进程内传递，带 status_id、串行 index/total、attempt 和 20 秒
      TTL，绝不经过 `record_assistant_turn()`、short_term、event_log、prompt history、TTS、贴纸或
      action_trace。`queued` 只会在参数、权限和确认闸门通过之后发出，语义是“正在处理”，不代表
    远端或设备已开始；不展示模型原始 tool-call content，且默认不允许 TTS。超过 3 秒才最多补一次 `waiting`。MCP 重连重试复用同一 status_id，仅更新
      attempt 并抑制后续阈值等待气泡；`outcome_unknown` 绝不降级成失败或已完成。当前没有 user cancel API，也没有绕开
      `conversation_lock` 的 emergency-stop 抢占链路，因此 P0 不发伪造的 `cancelled` 成功状态；
      硬件服务主动上报前也不推断排队、开始或百分比进度。状态只经配对桌面 WS 的 `tool_status`
    瞬态帧投递，绝不进入持久移动端队列；配对桌宠客户端在动向 NOW 区域原位展示。MCP 的用户可见标签
    来自本地 `tool_policy.<tool>.ui_label`（最长 48 字符）；未填写时动态工具只显示“外部工具”，
    绝不退回远端工具名、description、参数或结果。
    - 暴露面：统一的 `tool_exposure.path_c`（缺省兼容 `tool_loop.categories/exclude_tools`）先按
      category、显式 tools 白名单和 exclude_tools 收窄；三端完全相同。其后模型专属 preset 仍可继续收窄。
    - 模型专属预设：若 chat model preset 绑定了 `tool_preset`，再按
      `tool_loop.tool_presets` 的同名白名单收窄；未绑定时保持上述旧语义。

  与路径A关系：
    | 场景                                  | 白名单快速路径 | 路径A普通探针 | 路径C loop |
    |---------------------------------------|----------------|---------------|------------|
    | 有效 tool_loop 关 / preset 非 FC / 非owner  | 可执行         | 正常执行      | 不激活     |
    | 有效 tool_loop 开 + owner + FC preset       | 可执行         | 跳过          | 激活       |

  快速路径成功后会把该工具加入本轮 `exclude_tools`，Path C 不再看到同名 schema；快速执行失败时
  不注入伪成功结果，Path C 仍可按正常 schema 重试，并在统一观测中标记
  `fast_failed_then_loop_retry=true`。

  工具意愿软提示（`tool_loop.nudge_hint`，默认 true，Brief 29 · 5）：loop 首步在
  messages 尾部、用户消息之前插入一条 system 提示"需要外部信息或操作时，直接调用可用
  工具，不要凭记忆编造。"（`_layer: "11.5_tool_nudge"`），利用 recency 位置缓解弱代理
  模型不主动调工具的问题。只在 loop 首次组装 messages 时注入一次，只存在于本轮
  `loop_msgs` 副本里，不进 short_term history，也不经过 prompt_builder 的层级消融机制
  （那套只覆盖 `build()` 组装出的 messages，与 loop 的一次性 messages 是两条链路）。

  明确意图 grounding：`route_pretool()` 将关键词命中结果作为本轮上下文元数据传给
  `run_agentic_loop(tool_call_required=True)`。即使工具没有出现在 schema、调用失败或
  结果不明，最终收尾也会经过 `core.tool_grounding.guard_completion_claim()`，禁止完成式断言。
  只有 dispatcher 成功 envelope（`工具已执行：...`）或层10明确标注 `current_turn` 才能
  解除该闸；历史 `10.5_action_trace` 永远只作为参考。
```

---

## per-char 兼容钩子（Brief 29 · "本我"模式）

角色卡 JSON 顶层可选块 `presence_ext`，缺失 = 全默认 = 现有角色零行为变化：

```json
"presence_ext": {
  "disabled_layers": ["0_jailbreak", "2_jailbreak", "11_jailbreak"],
  "model_routing": "claude-main",
  "tool_categories_path_a": ["info", "fs"],
  "tool_categories_path_c": ["info", "memory", "mcp"],
  "tool_tools_path_a": ["get_time", "fs_list", "fs_read"],
  "proactive": "off",
  "tool_loop": "on"
}
```

- `tool_categories_path_a` / `tool_categories_path_c`：分别覆盖角色的 Path A/Path C category。
  `tool_tools_path_a` / `tool_tools_path_c` 是进一步的精确白名单，`tool_exclude_path_a` /
  `tool_exclude_path_c` 只能继续排除。旧 `tool_categories` 保留为 Path C 的兼容别名。全局默认在
  `tool_exposure.path_a` / `tool_exposure.path_c`；Path C 未配置新块时继续回落
  `tool_loop.categories/exclude_tools`。角色覆盖不能绕过执行闸门、危险确认或 MCP local policy。
  示例卡 `examples/benwo.example.json` 把 `mcp` 类加入暴露面；
  角色 authored 文件的 canonical 路径是 `userdata/characters/cards/<char_id>.json`，
  根目录不放模板/示例文件，见 `tests/test_authored_assets.py::test_no_template_files_in_characters_root`；
  要实际加载体验这张卡，复制到 `userdata/characters/cards/` 下改名去掉 `.example` 再改
  `active_character`。旧 `characters/` 只在 compatibility fallback 中读取，不是推荐写入路径。
- `tool_loop`：仅接受 `"on"` / `"off"`。`"on"` 允许这张卡在全局默认关闭时启用 Path C；
  `"off"` 关闭 Path C；字段缺失或非法值回落全局 `tool_loop.enabled`。它不会绕过 owner 私聊
  或 `function_calling` preset 两道硬闸。`examples/assistant.example.json` 是人机直连组合示例。

### 模型专属工具预设

管理面「运维 → 工具」将运行时注册表作为只读观测清单，并提供两层独立控制：

- `tools.<name>.enabled` 是内置工具的全局执行闸门；关闭后 schema 不会暴露，直接执行也会被拒绝。
- `tool_loop.tool_presets` 是内置工具的命名 schema 白名单；`model_presets.presets.<model>.tool_preset` 仅为该聊天模型选用其中一项。它只能在类别、角色权限与全局执行闸门之后继续收窄，不能扩大权限。

未绑定 `tool_preset` 时保持 categories/exclude_tools 语义。引用已删除预设时运行时 fail-closed 为零个内置工具并记录 warning；面板删除预设会同步清除已有模型绑定。MCP 只在工具页显示全局启用状态，不列入这份瞬态注册表或工具预设。
- 另外四个钩子（`disabled_layers` / `model_routing` / `proactive` / `tool_loop`）分别见
  `docs/prompt-layers.md`、`docs/model-presets.md`、`docs/scheduler.md`。

### 角色资产路由（2026-07-25，与 `model_routing` 同构）

`presence_ext` 再加四个可选字段，把"聊天模型走 `model_routing`"的模式复制到 TTS/表情包/
模型上：

```json
"presence_ext": {
  "tts_preset": "cheerful",
  "sticker_pack": "cute_pack",
  "live2d_model": "assistant.model3.json",
  "model_3d": "assistant.glb"
}
```

- `tts_preset`：引用 `config.yaml` `tts.presets.<name>` 命名预设，字段级覆盖全局 `tts:`
  配置（如只换 `ref_audio`/`emotions`，其余继承全局），解析见
  `core/output/voice_adapter.py::resolve_tts_config()`。声明了但 `tts.presets` 里找不到
  → fail-soft 回落全局配置并记 warning，不让语音整体哑掉。
- `sticker_pack`：引用 `userdata/assets/stickers_packs/<pack_name>/`，结构与通用池
  `userdata/assets/stickers/` 一致（六个情绪子目录）；某个情绪在专属包里没有图片时
  （不要求覆盖全部六种），`core/output/sticker.py::_pick_sticker()` 自动回落通用池。
- `live2d_model` / `model_3d`：纯字符串透传，后端不解析、不校验、不做任何模型逻辑——
  值原样经 `GET /characters/active-info` 与
  `GET /character/{char_id}/asset-bindings` 下发，由前端自行映射到本地模型文件。
  这两个字段的消费方在前端仓库，见 `cc-tasks/124`（桌宠客户端语音/UI 工单，同批
  讨论了模型切换需要的落点）。

读写端点（管理面板可调用，与 `model-routing` 端点姐妹篇）：
`GET/PATCH /character/{char_id}/asset-bindings`。PATCH 每个字段独立生效——请求体里
缺省的字段不改动现有值，传空字符串显式清除该字段（不是传 `null` 清除，避免一次只想
改一个字段时把其余三个一起冲掉）。

图像识别（vision）刻意不纳入角色资产路由：识图能力本身通用，不该按角色分裂成多份
配置。只在"日常环境观察"（`vision`，Brief 56）和"桌面自动化专用"（`use_computer_vision`，
新增，2026-07-25，与 `core/phone_control/vision_client.py` 的 `phone_control_vision`
同构：dedicated 字段覆盖 > 回落 `vision`）之间分两个全局槽位——桌面自动化往往需要
更强/更贵的 UI grounding 模型，不该让日常 vision 调用背这个成本。`use_computer_vision`
目前只占位（无消费方，`desktop`/`system` 工具类目今天还是坐标无关的窗口级操作），
供以后真正做"看屏幕点像素"类工具时直接复用配置层，见
`core/perception/vlm_client.py::get_use_computer_vision_config()`。

---

## MCP（Model Context Protocol）外部工具客户端（Brief 29 · 4）

文件：`core/mcp_client.py`。MCP 是 **Tool subsystem 的外部工具传输协议**，不是
desktop/mobile ↔ backend 的客户端协议，不是 Interaction/Event kind，也不是新的事件总线。
**只接外部工具，不接 resources/prompts、不接外部记忆库**——
外接记忆库会绕过 prompt 层注入与固化链，裂成两套真相；MCP 在这套架构里只承担"给主 LLM
多几个可调用的外部工具"这一件事。

MCP 常规调用形态是 owner private turn 中已激活的 Path C：
`Path C tool loop → tool_dispatcher → local tool 或 MCP dynamic tool → MCP client/session →
external MCP server → bounded ToolResult → 当前轮 tool-result 边界`。此外，admin-only 的 MCP Tool-call
Console 可在排障时走 `admin router → tool_dispatcher.execute(origin="admin_console") → MCP dynamic tool`
这一受限路径；它不直连 MCP session，也不能绕过 allowlist、本地 policy、effect/确认门或超时。scheduler、stimulus/trigger、
Dream、Stage 不会隐式升级为 MCP 调用；MCP 结果不重新进入 `perceive_event`，不成为 stimulus，
也不拥有直接 memory writer 权限。`hardware_gateway` 只是外部 MCP Server 的一种实现，不是
PresenceKit 核心模块。

```yaml
mcp_servers:
  enabled: false
  servers:
    - name: filesystem
      transport: stdio            # stdio | sse | streamable-http（http 是旧版 streamable-http 别名）
      command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "D:/some/dir"]
      # sse / streamable-http 时用: url: https://your-mcp-server.example/mcp
      # headers: {Authorization: "Bearer ${MCP_SERVER_TOKEN}"}  # 可选；stdio 忽略
      use_proxy: false              # 远程 server 设 true 才使用全局 proxy；loopback 始终直连
      enabled: true                 # 单 server 开关（默认 true）
      tool_timeout_s: 30
      tool_timeouts_s:              # 可选：只覆盖指定工具，不影响同 server 的其他工具
        hardware_sequence: 60
      allow_tools: []              # legacy 非严格模式空 = 全部；严格模式空 = 零授权
```

第三方工具可选分类是客户端扩展，不是 MCP 官方分类标准。server 不提供 `_meta`、提供 Emerald
不认识的 namespace，或 metadata 损坏时，工具仍按普通 `category="mcp"` 发现、授权和调用。
新建独立 MCP Server 时，先复制并填写
[`docs/mcp-server-authoring-template.md`](mcp-server-authoring-template.md)，固定工具 schema、错误、
幂等、annotations、可选 metadata 和兼容测试，再开始实现。
每个 server 可独立声明通用映射、精确工具名覆盖和本轮 schema 筛选：

```yaml
metadata_mapping:
  namespace: "io.example/tool"
  schema_versions: [1]
  schema_version_field: "schema_version"
  domains_field: "domains"
  interaction_field: "interaction"
metadata_overrides:
  remote_tool_name:
    mode: override                 # remote | override | ignore
    domains: [local_domain]
domain_selector:
  domains: [local_domain]
  include_unclassified: true
```

override 按 `server + remote tool name` 精确定位，工具离线或消失时配置仍保留；工具改名不会继承旧
override。删除 mapping、override 或 selector 后立即回到普通 MCP 行为。配置只保存本地字段，
不复制远端 `_meta` 或 secret。

- **Transport**：当前代码的 canonical transports 是 `stdio`、`sse`、`streamable-http`；配置中的
  `http` 只是兼容别名，归一化为 `streamable-http`。`stdio` 使用 `command`，另外两种使用
  HTTP(S) URL。
- **生命周期**：`main.py` 启动时调 `mcp_client.init_mcp_servers()`，对每个已启用 server 建
  `ClientSession` + `list_tools()`；单 server 初始化失败只跳过它（log + 继续），不影响其他
  server 或主流程。进程退出时 `main.py` 的 `finally` 块调 `shutdown_mcp_servers()` 清理全部
  session。每个 server 由自己的 owner task 持有 session；管理面启停、删除和配置热更新只发
  信号，由 owner task 在自己的生命周期内断开、摘除工具并重连。
- **初始化验证**：管理面测试/导入与运行时连接都先完成 MCP `initialize`，再执行
  `list_tools` 导入工具描述；测试探测用独立 session，成功后立即关闭，不写入配置或注册运行态
  工具，导入只有测试成功后才落盘。
- **管理面**（Brief 110）：admin token 可在 MCP 页选择 Streamable HTTP（推荐）或 SSE URL，并测试
  `initialize + list_tools`、导入或删除 server、切换总/单 server 开关和勾选 `allow_tools` 白名单。导入前的测试
  不注册工具也不写配置；删除会移除配置、关闭该 server 的 owner 并摘除动态工具。保存后总开关走 `sync_mcp_servers()`，单 server 走定点热重载。HTTP
  `headers` 的 `${ENV_VAR}` 会在连接时展开，缺失环境变量即连接失败；管理面仅显示环境变量
  占位符或“已配置”，不回显字面 token。
- **受控手动调用**：后端管理面 MCP 页的 Tool-call Console 只列出已连接、有效 allowlist、已注册并已通过
  local policy 的工具；`POST /settings/mcp/console/invoke` 再次以 registry/config/runtime 交叉验证，并用
  inputSchema 校验 JSON 参数。它以 `admin_console` origin 复用 dispatcher；管理调用不受角色熟练度门控，
  但绝不绕过本地 effect、dangerous confirmation、模式与工具启用门。需要确认时只返回 120 秒一次性的
  ticket，`/confirm` 只能重放 ticket 内的 server/tool/arguments；policy 或连接变化会在确认时重新拒绝。
- **本地 effect 策略**：`require_local_policy: true` 时，管理面 URL 导入会在 `initialize + list_tools`
  成功后为每个选中的工具写入本地默认 `tool_policy`，并保留重导入时已有的显式策略。建议可判定为
  `read` / `write` 的工具按建议落盘；无法判定的工具写为 `write`，新生成的普通策略均默认
  `require_confirm: false`。管理面逐工具复选框是确认行为的唯一显式控制面，已有 true/false 均保留。手动更新
  `allow_tools` 时，管理面会从当前运行时快照补齐缺失策略；无法补齐时严格写入会拒绝。每个已确认的白名单工具都要显式标为 `read`、`write`、`actuate`、
  `emergency` 或 `unrestricted`；校验失败不会写入配置或热重载。`unrestricted` 是管理员在本地明确选定的“无限制执行”模式：强制不确认、
  必须显式 `idempotent: true`，同一 `request_id` 最多重连重试三次。像删除远端帖子这样的操作应标为
  `write`，不根据远端工具描述自动推断。
- **代理**：MCP HTTP client 一律 `trust_env=False`，不会继承 `HTTP_PROXY` / `HTTPS_PROXY`。
  `localhost`、`.localhost`、IPv4/IPv6 loopback 与未指定地址强制直连；远程 URL 只有配置
  `use_proxy: true`（或管理面勾选）才使用全局 `proxy.http` / `proxy.https`，且全局代理未启用或
  未配置时连接会明确失败。
- **工具注册**：转成 `_TOOL_REGISTRY` 动态条目，命名 `mcp__{server}__{tool}`，
  `category="mcp"`，description/inputSchema 直接映射为 OpenAI function schema。与静态注册表
  同名冲突时 MCP 侧让位（记 warning，不覆盖）；连接关闭、断线重连失败后的摘除、单 server
  重载和总开关同步都会移除该 server 的动态条目。
- **可选 metadata 解析**：`list_tools()` 成功后、动态 registry 生成前，
  `summarize_tool_metadata()` 按该 server 的 `metadata_mapping` 解析 `_meta`。registry 只保留
  `mcp_remote_domains`、`mcp_remote_interaction`、`mcp_metadata_source`、
  `mcp_metadata_status`、`mcp_metadata_schema_version` 和最终 `mcp_domains`。domain 最多 8 项、
  单项 48 字符、总长 256 字符；控制字符、超长值和未知 interaction 被丢弃或降为 `unknown`。
  状态为 `absent | recognized | unrecognized | invalid | overridden`。单个工具解析异常只影响该
  工具摘要，不中止同 server 的其他工具注册。完整 `_meta` 不落盘、不进日志、管理面或 prompt。
- **domain selector**：`get_tools_schema()` 只在既有 category、allowlist/local policy、连接、
  registry、自管理与 proficiency 门控之后应用 `domain_selector`，因此只能收窄，不能授权或扩大。
  selector 缺失时保持旧行为；`include_unclassified: true`（推荐默认）保留无 metadata server。
  Path C 的原生 function schema 从这里取得，tail-brace relay 继续复用同一轮已经筛选后的 `tools`
  与 allowed name 集合，不会重建或扩大 MCP 暴露面。Path A 默认类别仍不含 MCP，但管理员可通过
  `tool_exposure.path_a` 明确加入，三端同步生效。
- **暴露面与危险工具排除**：server 级 `allow_tools` 先按 `list_tools` 结果做白名单过滤；调用路径
  还必须在各自 `tool_exposure.path_a/path_c` 的 category、tools 白名单和 exclude_tools 内，并通过
  `mcp_proficiency` 的 schema/执行双重门控。默认 Path C/Path A 都不含 `mcp`。动态 MCP 条目当前统一标记
  `dangerous=False`，不会因外部 description 或 annotation 自动获得本地高危确认语义；显式
  `tool_policy.<tool>.require_confirm: true` 仍会要求确认。需要
  排除的工具必须显式列入 `exclude_tools` 或 `allow_tools` 白名单。外部 server 不能通过工具
  描述改变这些系统权限。
- **执行适配**：`execute()` 走既有的通用分发分支（`func(**tool_args)`），内部转发到
  `session.call_tool()`，默认超时 `tool_timeout_s`（管理面限制为 1–660 秒），可由
  `tool_timeouts_s.<tool_name>` 仅覆盖一项。重试按本地 effect/idempotency 策略执行：`read`
  可重连重试一次，`write` 必须显式 `idempotent: true`，`actuate` 不重试；`emergency` 仅
  显式幂等的 `hardware_stop` 可带同一 request_id 受控重试；`unrestricted` 仅显式幂等且管理员
  本地选定时带同一 request_id 重连重试三次。动作类在超时或断连后返回结构化 `outcome_unknown`，
  提示动作可能已送达且禁止自动重放。结果取 content 里的文本项拼接、截断 2000 字，作为本轮
  bounded ToolResult。**不做后台心跳**，只在调用时才发现断线。
- **结果边界**：普通单次路径将 bounded `ToolResult.safe_summary` 通过现有
  `10_tool_result` prompt layer framing 注入；Path C 保持在当前 loop 的 bounded `role=tool`
  消息中，随后才做最终生成。结果带有“外部/工具数据、可能不可信”的来源标识和边界提示；
  raw data 不进入 prompt 或 memory。MCP 结果不独立写 `short_term`、`event_log` 或长期记忆，
  也不经过 `perceive_event`。
- **action_trace 自动生效**：收口埋点在 `tool_dispatcher.execute()`，MCP 工具零新增记账代
  码；注册条目不声明 `trace_args`，参数不落痕（防外部 server 的敏感入参入盘）。
- **调用观测**：每次 MCP 工具调用额外写入既有 `api_call_log`，caller 固定为
  `mcp__{server}__{tool}`，只记录成功/失败、时长与无敏感的结果提示，不记录 arguments 或
  外部返回正文；管理面按工具展示最近一条调用记录。控制台调用额外携带 `audit_id`，使 UI 返回值能关联
  到同一条总账记录；`request_id` 仍用于动作超时/结果不明的 MCP 侧关联。
- **管理面安全摘要**：`GET /settings/mcp` 为每个工具分别返回已发现、已授权、当前会话可暴露
  三个状态，以及远端/本地/最终 domains、interaction 提示和 metadata 状态/版本。远端分类不是
  授权，interaction 不是本地 effect，确认仍只由本地 policy 控制。响应不返回完整 `_meta`、
  原始 description 或完整参数 schema；控制台只得到有界的参数名/类型摘要，执行时仍在服务端用
  registry 中的完整 JSON Schema 校验。
- **探针默认不覆盖 mcp 类**：默认 Path A 是 info/desktop；若管理员明确把 mcp 放进
  `tool_exposure.path_a`，probe 会只看到经同一 policy/proficiency/allowlist 过滤后的动态 schema。
- **provider 细分**：DS/Claude/GPT 代码层无分支，统一经 OpenAI-compat 网关走 function
  calling，MCP 工具 schema 是标准 JSON Schema 直转，不额外适配。唯一不覆盖场景是原生
  Anthropic API 直连（非网关），当前架构不涉及。
- **风险**：MCP server 是外部进程，描述/结果都是不可信输入，见 `docs/known-issues.md`
  "观察项（Brief 29 · MCP）"。任何描述、参数 schema 或返回文本都不能被当作系统指令，
  不能借工具描述提升角色暴露面、绕过 origin 闸门、危险工具排除、超时或审计规则。

统一路由按路径过滤探针类别，通道不参与决策：
```python
route_pretool(..., categories=None, exposure_path="path_a")
```

快速路径不是 keywords 的通用捷径，只接受 `FAST_PATH_TOOL_ALLOWLIST` 中显式列出的、当前 schema
可见、无必填参数且无副作用的工具；当前仅 `get_time`。其他无参工具（包括 `water_garden`）仍由
普通探针或 Path C 决策。

---

## Brief 171 deployment capability gate

`core/deployment_capabilities.py` is the central policy seam for
`deployment.mode`. In `remote_server`, `device_shutdown`, `device_sleep`,
`exit_yandere`, `fs_list`, and `fs_read` are removed from schema/probe
exposure and fail closed through direct `execute()` as well. The decision is
process configuration only; callers cannot widen it through request fields or
prompt text.

Desktop action tools remain client capabilities. In remote mode they require
an online desktop WebSocket and a successful ack; server-local file fallback
is disabled. `GET /observability/deployment-capabilities` reports the redacted
logical status and recent ack time.

## fs 只读浏览工具（Brief 31）

文件：`core/tools/fs_browse.py`。让角色能"自己翻电脑"——列目录、读文件，范围严格限于
config 声明的允许根目录，**只读**。不新增任何写入入口（唯一写出口仍是
`core/tools/toybox.py` 的 `write_toy_file`）。默认两条路径都不含 `fs`；需要在
`tool_exposure.path_a/path_c` 或对应角色覆盖中明确加入。Path A 开启后 QQ、desktop、mobile
共享同一只读浏览能力。

```yaml
fs_access:
  enabled: false                  # 总开关，默认关
  allow_roots:                    # 只读允许根，绝对路径，用户手填
    - "D:/some/dir"
  deny_names:                     # 命中即拒（对路径任一段做大小写不敏感子串匹配）
    - "secrets"
    - ".env"
    - ".git"
    - "node_modules"
    - "__pycache__"
    - "config.yaml"
    - "token"
  max_read_chars: 10000           # 单次读取截断
  max_list_entries: 100           # 单次列目录条数上限
```

- **deny_names 底线集不可清空**：`_DENY_NAMES_BASELINE` 写死在代码里，与 config 的
  `deny_names` 做集合并集——config 只能追加，永远无法移除底线集里的项（防手滑清空）。
- **`data/` 目录永远隐式拒绝**：即使被 `allow_roots` 包含，`fs_list`/`fs_read` 仍会拒绝
  项目自身沙盒目录（`Path("data").resolve()`），列目录时也不会把它列出来。
- **守卫顺序**（`_resolve_and_guard`，每次调用先过 `enabled` 总开关，再顺序执行）：
  1. `enabled` 为假 → 直接返回"文件浏览未开启"，不碰文件系统。
  2. `Path(path).resolve()` 后必须是某个 `allow_roots` resolve 结果的子路径，否则拒绝
     （`data/` 隐式拒绝在这一步之前先判）。
  3. resolve 前后的路径逐段过 `deny_names`（底线集 ∪ config 追加集），命中拒绝。
  4. 路径本身若是软链直接拒绝——即使软链目标落在允许范围内也拒绝（与 `toybox` 的
     `read_toy_file`/`write_toy_file` 同策略，防 allow 区内放链指向外部）。
  5. `fs_read` 额外校验单文件大小上限 5MB（超过不读，防内存）。
- **fs_list**：`path` 省略时返回 `allow_roots` 列表本身，作为角色的"入口地图"；
  `depth` 只接受 1 或 2（非法值回落 1）。目录/文件条数超过 `max_list_entries` 截断并注明。
- **fs_read**：只读文本类扩展名白名单（txt/md/py/js/ts/json/yaml/toml/csv/log/html/ini
  等），其他扩展名或无法解码的文件返回"这是二进制/不支持的文件类型"提示而不抛错；
  UTF-8 优先，失败尝试 GBK。超 `max_read_chars` 截断并注明字数，v1 不做分页偏移。
- **探针默认不覆盖 fs 类**：默认 Path A 是 info/desktop；若管理员明确把 fs 放进
  `tool_exposure.path_a`，QQ、desktop、mobile 都会收到同一受 allow_roots 约束的只读 schema。
- **不受安全/危险模式闸约束**：`_MODE_RESTRICTED_CATEGORIES` 含 `desktop`/`system`/`phone_control`，
  `fs` 类不在其中——门控完全交给自身的 `enabled`/`allow_roots`/`deny_names`，不需要额外
  切到危险模式。
- **action_trace 自动生效**：`trace_args: ["path"]`（路径本身已在 allowlist 内，不敏感，
  落痕迹方便追问溯源），收口埋点在 `tool_dispatcher.execute()`，无需额外记账代码。
- **风险**：文件内容是不可信输入（与 web_search/MCP 结果同级），可能含提示注入文本，
  v1 接受现状，见 `docs/known-issues.md`。
- **不做什么**：写入/删除/移动（永远不进 `fs` 类）；`fs_search`/grep；分页读取；探针暴露；
  图片/PDF 解析（走既有 `media_processor` 通道，不在此重复）。

---

## 工具注册表

文件：`core/tool_dispatcher.py` → `_TOOL_REGISTRY`

### info 类（探针覆盖）

| 工具名 | 触发描述 | 实现位置 |
|---|---|---|
| `get_time` | 用户问"几点"/"现在时间" | `_get_current_time()` 内联 |
| `weather` | 用户问天气/温度/下雨 | `core/tools/weather.py` |
| `web_search` | 确认信息/帮用户找资料；结果自动沉淀向量库（source="web"） | `core/tools/web_search.py`（DuckDuckGo）|
| `add_reminder` | "提醒我X点做Y"/"帮我记" | `core/tools/reminder.py` |
| `water_garden` | 角色在花园相关对话上下文中决定维护花园 | `core/tools/garden_tools.py` |

### desktop 类（探针覆盖）

| 工具名 | 触发描述 | 执行方式 |
|---|---|---|
| `desktop_minimize` | 最小化窗口 | WS action + ack，失败降级 `agent_actions.json` |
| `desktop_open_url` | 打开网址 | WS action + ack，失败降级 `agent_actions.json` |
| `desktop_play_pause` | 播放/暂停媒体 | WS action + ack，失败降级 `agent_actions.json` |
| `desktop_notify` | 发系统通知 | WS action + ack，失败降级 `agent_actions.json` |
| `play_song` | "放一首xx"/"我要听xx" | 网易云 API 搜索 song_id → WS action / 文件降级 |
| `peek_screen_content` | 叶瑄自主查看当前窗口屏幕文字内容 | 读 `realtime_state` 快照的 `screen.visible_text / clickable_text`（受控出口，见下）|
| `toy_vibrate` | 用户明确要求已连接设备振动 | Intiface Central / Buttplug v3 |
| `toy_stop` | 用户要求立即停止设备 | Intiface Central / Buttplug v3 |
| `toy_pattern` | 用户明确要求预设振动模式 | Intiface Central / Buttplug v3 |
| `toy_job_status` | 查询硬件后台任务状态 | 只读硬件 job 状态 |
| `read_toy_file` | 读取玩具项目白名单文件 | `data/very_formal_project/`，仅接受枚举 `file_key` |
| `write_toy_file` | 覆盖或追加玩具项目白名单文件 | UTF-8 文本，文件总长最多 4000 字，原子写入 |

#### `peek_screen_content` — 屏幕内容受控出口

实现：`core/tools/screen_peek.py`。设计原则：

- **总开关**：`config.screen_peek.enabled`（默认 `false`）。关闭时工具立即返回"未开启"，不读内容。
- **冷却**：同一窗口/文件（key = 规范化后的 `title_hint` 或 `window_title`）在 `screen_peek.cooldown_minutes` 分钟内只触发一次。冷却中返回提示，不刷新计时。冷却表为内存态，重启清零。
- **触发方式**：叶瑄自主决定，不强制。`prompt_builder` 在 Author's Note 末尾注入软提示（`enabled=true` 且有 `title_hint` 时），模型自行决定是否调用。
- **内容边界**：`visible_text`（最多 20 条）+ `clickable_text`（最多 10 条）。敏感窗口已在 `sensor.py` 入口 fail-closed，工具层不重复过滤。
- **唯一合法出口**：`visible_text / clickable_text` 全局只经此工具输出，`_format_realtime_awareness` 只注入 `title_hint`（已服务端截断 80 字），绝不在其他注入层出现。

管理端：`GET/POST /settings/screen-peek`（见 `admin/routers/settings_screen_peek.py`），供前端设置页调用，改后即时生效无需重启。

`toy_vibrate` / `toy_stop` / `toy_pattern` 是 reality-side hardware actuator，`toy_job_status`
是同一 owner 边界内的只读查询；都只能由 `scheduler.owner_id` 对应用户的真实私聊，
经带 origin 闸门的工具调用触发；群聊、scheduler、trigger 和 Dream pipeline 均不能触发。
客户端使用 `aiohttp` 直连本机 Intiface Central，
`trust_env=False` 绕过系统代理。长时动作先写入 `hardware_jobs.json`，由后台 worker 管理
`accepted/started/completed/failed/cancelled/expired` 生命周期；工具调用立即返回受理结果，
worker 在到期、异常、断线、显式取消和进程关闭时尝试停止设备。单个振动任务默认最长 15 分钟，
可由 `hardware.max_job_duration_ms` 限制；pattern 最多 32 步。状态只读注入 prompt，剩余时间由系统计算，
不得由模型或用户输入覆盖。

管理端 `GET /hardware/jobs`、`GET /hardware/jobs/{job_id}` 提供只读观测，
`POST /hardware/jobs/{job_id}/stop`（另有 `/cancel` 兼容别名）执行显式停止，均需 `hardware` scope。

`read_toy_file` / `write_toy_file` 只操作 `get_paths().very_formal_project_dir()` 下的
`diary`（思考笔记）、`wishlist`（愿望清单）、`doodle`（涂鸦板）。LLM 不接触路径；
后端会再次校验解析后的目标和临时文件均未越过玩具箱目录，并拒绝目录或文件软链穿越。

#### toy 自主写入（autogrow）— 系统行为，不走探针

`core/post_process/toy_autogrow.py` 实现「叶瑄自生长」路径：

- **触发**：每轮 `post_process` 在 uid_lock 释放后入慢队列（`toy_autogrow` 任务）。
- **判断**：慢队列 handler 用人格 chat 路由（max_tokens=80，temperature=0.9）判断本轮是否值得记录。返回 `SKIP` 或 1～3 句第一人称随手日记，不写事件摘要。
- **写入**：服务端直接操作文件（`_rollover_append`），绕开 desktop 模式限制——QQ 模式和桌宠模式均可自主写入。
- **限频**：每角色/用户 `toy_autogrow.min_interval_hours`（默认 6 小时）最多写一次。状态存 `data/very_formal_project/.autogrow_state.json`（JSON 字典，key = `{char_id}:{uid}`，value = Unix timestamp）。
- **滚动**：文件超过 4000 字时截去头部（按行对齐），不抛错，始终保留最新内容。
- **开关**：`config.toy_autogrow.enabled: false`（默认关）退回纯手动玩具，原 `read_toy_file`/`write_toy_file` 探针路径不受影响。
- **目标文件**：`config.toy_autogrow.target`（默认 `diary`）。

### memory 类（已注册，但当前未自动接入正式对话）

| 工具名 | 用途 | 备注 |
|---|---|---|
| `read_diary` | 读用户日记 | 用户明确要求时由探针触发（category=info）；主 LLM 无 tools schema，R5 后 Author's Note 不再要求主 LLM 调用 |
| `read_watch` | 读睡眠/心率/运动数据 | |
| `search_diary` | 按关键词搜索最近 30 天日记 | |
| `get_profile` | 获取用户画像 | profile 已由 fetch_context 自动注入，此工具是第二路径 |
| `get_episodic` | 召回情景记忆 | episodic 已由 fetch_context 自动召回，此工具是第二路径 |
| `revise_memory` | 更正指定情景记忆 | 默认仅 Path C；旧条目降强度并保留，更正作为新条目追加；必须给出 episode id 与用户确认的更正 |
| `forget_episodic` | 遗忘指定情景记忆 | 默认仅 Path C；仅在用户明确要求时按 episode id 或 topic 降级，保留审计/叙事归档，不物理删除 |
| `clear_midterm` | 清空近期中期记忆 | 默认仅 Path C；只清空当前用户/角色的 12 小时时间桶，不影响 episodic 或稳定画像 |
| `revise_user_profile` | 更正用户稳定行为画像 | 默认仅 Path C；仅可覆写明确给出的合法 identity 维度，不能凭空推断 |

> 注：`get_profile / get_episodic` 的同类信息已在 `fetch_context` 自动进入 prompt；长期行为模式当前走
> `user_identity` 层。若未来要让他在正式对话中主动再召回 memory 工具，需要在
> `run_llm()` 或对话循环中接入 tools schema 与工具执行回合。
>
> `get_growth` 工具与 `character_growth.load()` 已随 Brief 35 一并删除（确认零其他读者）；
> 磁盘上的历史 `character_growth/` 文件不再被任何工具读取，仅 `core/memory/path_resolver.py`
> 的 `LEGACY_ARTIFACTS` 保留只读路径解析用于审计/迁移兼容。

### 日记工具的三层分工

| 文件 | 职责 |
|---|---|
| `core/tools/diary_reader.py` | 底层读取，从 Obsidian 目录读 .md 文件 |
| `core/tools/diary_tool.py` | `read_diary` 工具实现，按日期读，读完调 `mark_diary_shared()` |
| `core/tools/diary_search.py` | `search_diary` 工具实现，按关键词搜最近30天 |
| `core/memory/diary_context.py` | 存储层，用户日记上下文单独存 txt，只进 prompt 层6d，不参与检索 |

### persist 工具已读指纹（P2 / Brief 82）

`core/memory/tool_read_log.py` 为 `persist=True` 工具（`read_diary` / `read_watch` /
`read_toy_file` / `search_diary`）记录已读指纹（`data/runtime/memory/{char_id}/{uid}/tool_read_log.json`），
同一 uid/char 重复触发同一来源会被 `tool_dispatcher.execute()` 拦下，返回
`（刚读过这个，这次跳过）`。

用户显式要求重读时（显式意图优先于去重优化，DESIGN.md §十一 决策 7）：探针/工具调用点
在同轮用户原始文本里命中 `_BYPASS_PHRASES` 常量表（`再读一遍` / `重新读` / `再看一次` /
`重新看看`，不上 LLM 判断）就给本轮 `execute()` 传 `bypass_read_log=True`。`is_recently_read()`
的 `bypass` 参数只影响"拦不拦"：命中时放行本次调用，但指纹仍照常 `record_read()` 刷新，
不是关掉去重本身。Path A 的 QQ/desktop/mobile 调用统一由 `route_pretool()` 从本轮用户原始文本
探测一次；`core/pipeline.py::run_agentic_loop` 的 Path C 也从同一原始文本计算并在多步调用中复用。

### 花园工具

`water_garden` 是角色内部的 info 类工具，会被探针覆盖。它不接收参数，内部读取当前 `mood_state`，再调用 `garden_manager.force_water()` 给对应情绪花槽浇一次水。Desktop / Mobile 只读展示和刷新花园状态，不暴露这个写操作。

相关关键词来自 `_TOOL_REGISTRY`：`浇花`、`花园`、`浇水`。工具结果只作为层10 `tool_result` 给 LLM 参考，不直接拼进最终回复。

### web 搜索沉淀与自主召回（X3）

`web_search` 在每次执行后，将搜索结果（标题+摘要）以 `source="web"` 异步写入 `vector_store`，去重键为 URL。

**沉淀路径**：`core/tools/web_search.py` → `vector_store.upsert(source="web", source_id=url)`

**召回路径**：`pipeline.fetch_context()` 在每轮拉取 `_query_vec` 后额外做一次 `vector_store.query_with_preview(sources=["web"])`, 结果格式化为 `web_recall_result`，注入 prompt 层 `web_recall`（`_drop_priority=35`，优先于大多数记忆层裁剪）。LLM 看到的框定为"外部事实，不是你的记忆或亲身经历"。

**隔离规则**：
- web 条目只存在于 `vector_store`（`source="web"`），**不进** `mid_term` / `episodic` / `identity` 固化链（这些只从 `short_term` 对话流晋升）。
- prompt 层明确标注"外部资料，非记忆/经历"，防止 LLM 将外部事实误当自身记忆。

**自主触发（web_autosearch，默认关）**：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `web_autosearch.enabled` | `false` | 开启后 author_note 注入软提示，允许叶瑄自行发起搜索 |
| `web_autosearch.min_interval_min` | `30` | 两次自主搜索最短间隔（分钟）；用户明确触发不受限 |

限频实现：`web_autosearch_state.json`（路径：`get_paths().web_autosearch_state()`）记录最近一次 `web_search` 调用的时间戳；`prompt_builder` 在构建 author_note 时检查间隔，未到期则不注入软提示（即关闭自主触发，退回纯反应式）。

### system 类（不走探针）

| 工具名 | 用途 | 备注 |
|---|---|---|
| `device_shutdown` | 关机 | `dangerous=True`，需用户确认，默认关闭 |
| `device_sleep` | 睡眠 | `dangerous=True`，需用户确认，默认关闭 |
| `exit_yandere` | 他从病娇状态平静 | 旧客户端兼容：向 `Emerald-desktop` 写信号文件；PresenceKit-desktop 当前不消费该信号，未配置旧客户端时无可见效果 |

### phone_control 类（默认仅 Path C）

| 工具名 | 用途 | 备注 |
|---|---|---|
| `phone_control_start` | 发起一次手机自动化任务（导航外卖/购物到支付确认页、操作无开放 API 的第三方 App） | `dangerous=True`，需用户确认 + danger-mode 门禁；只负责把任务派给手机（写 `mobile_queue` + `behavior_id=phone_control_task`），真正的截屏/点击循环在设备本地跑，见 `docs/protocols/phone-control-protocol.md`（Emerald-mobile 仓库）。**绝不自动完成支付/提交订单/确认收货**——遇到密码/支付/银行类页面，后端 `core/phone_control/sensitive_filter.py` 和设备本地各自独立拦截，命中任一方即停。默认不在任一路径暴露面内；显式加入 `tool_exposure.path_c` 或角色 `tool_categories_path_c` 后仅进 loop，加入 `path_a` 后三个端都会进入预探针，仍受确认和 danger-mode 闸门约束。能力诊断在角色加载失败时按 fail-closed 处理。 |

新增子系统：`core/phone_control/`（`sensitive_filter.py` 敏感页面拦截、`vision_client.py` 视觉模型调用、`task_state.py` 步数/超时状态）+ 三个端点（`admin/routers/phone_control.py`）：

| 端点 | scope | 用途 |
|---|---|---|
| `POST /phone_control/step` | `chat` | 设备侧循环每步调用，上报观察换回下一步动作 |
| `GET /phone_control/status` | `chat` | 只读诊断：兼容字段 `tool_enabled`（Path C）以及 `path_a_enabled`/`path_c_enabled`，均按角色覆盖后的共享暴露策略解析；另含 `vision_configured` 和 `char_id`，供手机端能力页展示 |
| `POST /phone_control/debug/start` | `chat` | 调试用：跳过 LLM 判断和 chat 内二次确认，直接调 `tool_dispatcher._phone_control_start_wrapper()` 发起任务；**仍然过danger-mode 门禁**（复用 `tool_dispatcher._current_mode()`），不因为是调试端点就放宽 |

视觉模型走 `config.yaml` 的 `vision`（或专用 `phone_control_vision` 覆盖）段，与 `core/perception/vlm_client.py` 共用同一种 OpenAI-compatible 调用方式。角色级手机控制授权使用正式的 `presence_ext.tool_categories_path_a/path_c` 字段；旧 `tool_categories` 只作为 Path C 兼容别名。

### fs 类（默认不暴露）

| 工具名 | 用途 | 备注 |
|---|---|---|
| `fs_list` | 列出允许范围内的目录内容 | `path` 省略返回 `allow_roots` 入口地图；`depth` 1 或 2 |
| `fs_read` | 读取允许范围内的文本文件 | 只读文本白名单扩展名，超限截断，不抛错 |

详见上方「fs 只读浏览工具（Brief 31）」一节。

---

## 探针规则（get_probe_prompt）

文件：`core/tool_dispatcher.py` → `get_probe_prompt()`

探针 prompt 现在从 `_TOOL_REGISTRY` 动态构建，不再硬编码规则列表。
每个 `info` / `desktop` 类工具注册时需提供 `examples` 和 `keywords` 字段：

- `examples`：2-4 条触发例句，拼入探针 prompt 供 LLM 判断
- `keywords`：关键词提示，拼入探针 prompt 帮助模型判断；不自动获得快速执行权限

**快速路径**（`core.pretool_router.fast_path_match()`）：只匹配
`FAST_PATH_TOOL_ALLOWLIST` 的显式规则，且工具必须在本轮 schema 可见、无必填参数、无副作用。
当前仅 `get_time`；QQ、desktop、mobile 共用同一实现。

**严禁推断**规则保留不变：消息里有"现在""今天""热""冷"等词，但没有明确问天气或时间，不调工具。

---

## 桌面动作执行机制（SubAgent）

### 流程

```
1. 工具调用或意图解析触发动作
2. _is_desktop_active()：优先检查桌宠 WebSocket；未连接时检查 `data/runtime/channel_queue.json` 修改时间是否在 5 分钟内
   └─ 离线 → 直接返回失败；如果来自意图解析路径，失败信息会写入 pending_perception
3. _push_desktop_action()：WS 在线时推送 action 并等 ack；失败时降级追加到 `data/runtime/agent_actions.json`
4. 桌宠端通过 WS 或轮询 `data/runtime/agent_actions.json` 执行动作
5. 意图解析路径执行失败时最多重试 2 次，间隔 0.5s
6. 仍失败：_write_pending_perception() → 下轮注入 perception_block
```

### pending_perception 机制

失败感知文件目录：`data/runtime/pending_perception/`
- 文件名为时间戳（防止多次失败覆盖）
- 两阶段提交，消除并发竞态：
  1. `read_and_mark()`：`os.rename` 原子抢占，把文件移到 `processing/` 子目录
     并发时只有一个 task 能成功，FileNotFoundError 说明被抢走，直接跳过
  2. `confirm_delivered()`：删除 `processing/` 下的文件
  3. `cleanup_stale()`：根目录扫超24h文件；processing 目录扫 mtime 超1h的文件
- 时间前缀自动计算：`[刚刚]` / `[N秒前]` / `[N分钟前]`

## execute() origin 闸门

`tool_dispatcher.execute()` 新增**必填**关键字参数 `origin: str`（无默认值）。

| 情形 | 行为 |
|---|---|
| 漏传（调用方未写 `origin=`） | `TypeError`，调用即崩，杜绝静默绕过 |
| 传入值不在白名单 | `(None, None)` + `logger.warning`，零副作用（fail-closed） |
| `origin="user_live"` | Path A 正常执行 |
| `origin="assistant_loop"` | Path C（Brief 28 tool loop）自主多步调用，`Pipeline.run_agentic_loop()` 专用 |
| `origin="autonomy_loop"` | autonomy runner 受限工具调用 |
| `origin="admin_console"` | 管理面 MCP Tool-call Console |
| `origin="assistant_self_management"` | Path C 原生 tool call 的 self-management gateway；不能执行普通业务工具 |
| `origin="autonomy_self_management"` | autonomy 的 self-management gateway；不能执行普通业务工具 |

白名单还包括 `autonomy_loop`、`admin_console` 与两个 self-management 专用 origin；所有 origin 均只是在
统一 dispatcher 内标记调用来源，不改变权限或类别边界。`manage_self_capability` 只有在全局 Self
Capability 开启、当前角色存在用户已授权且 `mutable_by_agent` 的未锁定能力时才加入 Path C 或 autonomy
schema；Path C 的 `exclude_tools`、调用方白名单和 chat tool preset 仍可将它排除。
Path A 的 pending confirmation、missing input、快速路径和普通探针均由
`core.pretool_router.route_pretool()` 收口，并显式传入 `origin="user_live"`；旧入口只保留兼容薄封装。

---

## 动作痕迹（Brief 27 · action_trace）

工具结果只在执行当轮注入 prompt（层 `10_tool_result`），下一轮就"失忆"——用户追问
"你刚才查到什么/你干了什么"无从溯源。`core/memory/action_trace.py` 给每次工具执行落一条
精简痕迹，供层 `10.5_action_trace` 注入"你最近做过的操作"（见 `docs/prompt-layers.md`）。该层明确
标注为历史参考，不得替代本轮结果。

**埋点位置：**

- `tool_dispatcher.execute()` 每条 return 前都调 `action_trace.record(...)`，**只有 origin
  闸门拒绝（fail-closed 那支）不记**——那不是角色做过的事。其余分支（工具不存在/模式闸/
  未启用/权限拒绝/高危待确认/persist 去重跳过/成功/异常）全部落痕迹，`status` 分别对应
  `failed` / `pending_confirm` / `ok`。

**存储：** `data/runtime/memory/{char_id}/{uid}/action_trace.json`，JSON 数组，环形上限
30 条，原子写（`core/safe_write.py`）。单条 schema：

```json
{"ts": 1789000000.0, "tool": "web_search", "origin": "user_live",
 "args_digest": "query=明天北京天气", "result_digest": "北京明天多云,18-26度…",
 "status": "ok"}
```

**脱敏规则：**

- `args_digest`：只拼接工具在 `_TOOL_REGISTRY` 里声明的 `trace_args: [...]` 白名单字段
  （截断 60 字）；未声明 `trace_args` 的工具只记工具名，不记参数——防 secrets/长文本入痕迹。
  已声明字段的工具：`add_reminder`(`remind_at`)、`weather`(`city`)、`web_search`(`query`)、
  `read_diary`(`date`)、`read_watch`(`query`)、`search_diary`(`query`)、
  `desktop_minimize`(`window`)、`desktop_open_url`(`url`)、`play_song`(`song_name`)、
  `get_episodic`(`topic`)、`toy_pattern`(`pattern_name`)、`read_toy_file`(`file_key`)。
- `result_digest`：取 `to_tool_result().safe_summary` 前 80 字（复用 `core/tools/tool_result.py`
  的脱敏出口，不碰 `raw_data`）。`peek_screen_content` 特判：只记"看了一眼屏幕：{title_hint}"，
  不记 `visible_text`/`clickable_text`，不绕过该工具本身的受控出口约束。

**注入（层 10.5）：** `fetch_context()` 读 `action_trace.recent(max_items, window_hours)`
（默认 5 条 / 24 小时，可配），`build_prompt()` 透传给 `prompt_builder.build(action_trace_entries=)`。
当轮去重：本轮已有 `tool_result` 时跳过与之同源（工具名相同）的最新一条，避免层 10 / 层 10.5
把同一件事说两遍。不进 `_drop_priority` 裁剪链，全层预算截断 400 字。

**可选回流 event_log：** `status=ok` 且 `action_trace.event_log_echo` 开启时，经
`fixation_pipeline.capture_turn(trigger_name="action_trace")` 回流一条，让动作进入角色
日记 / event_search 的记忆固化链。**不得**直接调用 event_log 的底层写入函数——
`tests/test_r6b_reality_scrub_contract.py` C2 强制所有生产代码的事件日志写入只能经
`capture_turn`。回流文案刻意不整行包在中文括号里（如"（做了一件事…）"），否则会被
`capture_turn` 内的 `scrub_reality_output_text` 当整行动作旁白丢弃，写了等于没写。

**配置（`action_trace` 节点，`config.example.yaml` / `config.yaml`）：**

```yaml
action_trace:
  enabled: true              # 关闭后不记录、不注入，零行为变化（回滚开关）
  inject_max_items: 5
  inject_window_hours: 24
  event_log_echo: true
```

---

## 工具开关

`config.yaml` 的 `tools:` 节点，危险工具默认关闭：

```yaml
tools:
  device_shutdown:
    enabled: false
  device_sleep:
    enabled: false
  weather:
    enabled: true
  # 其他工具默认 enabled: true
```

工具执行还受全局安全模式约束：

- 默认 `safe`：`desktop` / `system` 类工具在 `execute()` 入口被友好拒绝，`info` / `memory` 类不受影响
- 临时 `danger`：通过受 Bearer 鉴权的 `PATCH /system/meta-mode` 开启，默认有效 7200 秒
- 当前状态：`GET /system/meta-mode` 返回 `{mode, expires_at}`；过期或状态文件损坏时 fail-closed 为 `safe`
- 状态文件：`data/runtime/meta_mode.json`，路径通过 `get_paths().meta_mode()` 获取
- 单工具 `config.tools.<name>.enabled` 仍保留；`device_shutdown` / `device_sleep` 在 danger 模式下仍需确认

---

## ToolResult v0 契约

文件：`core/tools/tool_result.py`

所有工具裸输出在进入 prompt 之前必须经过此收口。

### 数据类

```python
@dataclass
class ToolResult:
    raw_data: str          # 原始未过滤；仅供 debug 日志，永不进 prompt/memory
    safe_summary: str      # 唯一允许进 prompt 的字段（截断后）
    memory_candidate: str | None = None  # v0 预留，未接线
    meta: dict = field(default_factory=dict)   # 预留 tool_name / trust_level 等
```

**不变量**：`raw_data` 永不进 prompt 或 memory。将来任何 tool→memory 路径只能消费 `safe_summary` 或 `memory_candidate`。

### 适配器与截断

- `to_tool_result(x) -> ToolResult`：幂等适配器，已是 `ToolResult` 则原样返回；`str` 则包装；其他先 `str()` 再包装。旧工具返回裸字符串自动适配，无需改动工具实现。
- `sanitize_for_prompt(s)`：截断到 `TOOL_RESULT_CHAR_CAP = 2000` 字符，超长追加 `…（工具结果已截断）`。
- `frame_tool_result(safe_summary)`：用定界标记 `<<<TOOL_DATA_START>>>` / `<<<TOOL_DATA_END>>>` 加反注入指令包裹，产出注入 layer 10 的最终文本。

### 安全收口位置

唯一注入点：`core/prompt_builder.py` layer 10（`10_tool_result`）。所有 4 个 `tool_dispatcher.execute()` 调用方均经 `build_prompt(tool_result=)` 参数汇聚于此，无其他注入路径。

---

## 新增工具的规范

1. 在 `core/tools/` 下创建独立实现文件
2. 在 `tool_dispatcher.py` 顶部写 wrapper 函数（async）
3. 在 `_TOOL_REGISTRY` 里注册，填写 `func / description / dangerous / category / parameters`
4. 如果需要探针覆盖（info/desktop 类），在注册条目里补充 `examples`（触发例句）和 `keywords`（快速路径关键词），`get_probe_prompt()` 会自动同步，无需手动改探针规则
5. 如果是高危工具，设 `dangerous: True`，并在 `execute()` 的确认逻辑里补充描述文案
6. 在 `config.yaml` 的 `tools:` 节点决定默认开关状态
7. 在此文档的注册表里补充说明

---

## 当前未注册的旧网易云 wrapper

当前 `core/tool_dispatcher.py` 中未发现 `_desktop_launch_netease_wrapper` / `_desktop_play_netease_wrapper` 这类旧 wrapper。
网易云播放只保留 `play_song`：搜索歌曲 ID 后推送 `{"type": "play_netease", "song_id": ...}`。

## Brief 237 Agent Runtime 接入

`add_reminder` 已统一进入 scheduler capability/Task Manager；runtime 写入失败不会静默回落到
legacy reminder 文件。workspace/process/browser 工具的 task receipt 仍由 Reality Task Manager
统一持有，visible delivery 必须经过新的 Reality ingress/turn，不能在后台调用 `capture_turn()`。
# Memory Event source boundary (Brief 214)

Owner/Path C event-read tools retain their existing origin and scope gates.
Their default queries exclude `web`, `dream_echo`, `coplay`, and conservative
`legacy_unknown` evidence. Explicit source selection remains an authenticated
admin-only forensic capability; role tools cannot request isolated sources.
The repair adds no tool, desktop/mobile protocol, or prompt injection path.

## Brief 228 character knowledge recall

`search_documents`, `read_document`, and `search_character_notes` are explicit,
bounded, frozen-`uid + char_id` recall tools. They do not inject results into the
prompt by default and do not write short-term, event-log, episodic, identity, or
storyline memory. Upload ingestion stores bounded derived text; raw bytes require
the explicit `character_document_library.retain_raw_uploads` flag. The admin-only
`GET /observability/character-library` route exposes aggregate counts and failures
without content or filesystem paths. `search_character_notes` also includes
only toybox mirror records from the same `uid + char_id` bucket; `read_diary`
and `search_diary` use the active character diary path. All are bounded reads.
