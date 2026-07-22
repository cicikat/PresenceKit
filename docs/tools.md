# docs/tools.md — 工具系统

---

## 工具触发路径

### 媒介 MCP 熟练度门控（Brief 61）

`mcp_proficiency` 按 MCP server 配置成长域与等级 tiers。连接层仍注册全量工具；
tool-loop schema 暴露层根据角色级 `interest_state` 的同域最高 level 过滤，`execute()`
再做一次防御性校验。未列入配置的 server 以及 tiers 从未列出的工具视为器官类，行为不变。
未解锁调用只返回中性失败文本，不记录动作痕迹，也不暴露等级或配置细节。

当前真正接入主流程的触发路径有两类：

```
路径A：pre-pipeline 探针
  QQ 用户消息 → trusted_user_text（media merge 前捕获，下同）
              → 关键词快速路径（仅 QQ 入口，只匹配 trusted_user_text）
              → 未命中时走 get_probe_prompt + function schema
              → 探针 user message 只含 trusted_user_text，不含 history / media span
              → 只判断 info + desktop 类
              → execute(origin="user_live") → 结果写入 tool_result → prompt 层10

  /desktop/chat 或 /mobile/chat → trusted_user_text（body 原始字段，media 端点在拼接前捕获）
                               → get_probe_prompt + function schema
                               → execute(origin="user_live")
                               → 工具结果包装成"刚刚执行了操作..."提示 → prompt 层10

  /chat 管理面板冻结入口 → 不走工具探针

路径B：意图解析（pipeline 之后，受限旁路；Brief 35 起两步降级中，第一步）
  他的回复 → _parse_and_execute_intent()
             → 入口闸：config.intent_reflex.enabled（默认 false）→ 关闭时直接 return
             → 守卫全部满足才执行：
               (a) trigger_name 为空 → 真实 owner turn（非 scheduler/sensor/watch）
               (b) user_content 非空 → 本轮有真实用户输入
               (c) 意图非 dangerous（device_shutdown/device_sleep 永不经此路径）
               (d) 本轮未走 tool loop（loop_executed=False，Brief 28）
             → c1: LLM 只在「第一人称、当下要做」时命中；承认/复述/过去式/吐槽回应一律不命中
             → c2: per-uid 同动作幂等窗口 120s（key = uid:action:关键参数）
             → 通过后调 _push_desktop_action，失败写 pending_perception
```

> 两步降级节奏见 `docs/known-issues.md` PB4：第一步只加 config 默认关，守卫/测试本步保留；
> 观察一个月无缺口后第二步整删本路径。

**memory 类工具默认不走探针，路径C（tool loop）激活时才对主 LLM 可见。**
`read_diary/read_watch/search_diary/get_profile/get_episodic` 已注册且 `execute()` 能执行，
但路径A/B 都没有把 memory 类喂给探针或主生成。Fable R5 已修复与 Author's Note 工具承诺的落差：
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
    - 跳过路径A 探针（main.py:441-451、chat.py 内 _probe_and_execute_tools 调用点），
      工具决策权整体移交主模型；QQ 关键词快速路径不受影响，先于一切判断
    - 主生成改走 Pipeline.run_agentic_loop()：
        chat_turn(messages, tools) → 有 tool_calls 就 execute(origin="assistant_loop") 回填
        role="tool" 消息（tool_call_id 对齐）→ 继续下一步，直到自然终止 / max_steps 耗尽 /
        总墙钟 total_timeout_s 超时
    - 用过 ≥1 个工具后，最终生成前注入 voice_reanchor system 提示，收尾出口改走
      run_llm()/run_llm_stream()（复用既有反坍缩重试），不再带 tools 参数
    - 高危工具触发 ask_confirm → 立即强制收尾，直接把询问文字作为本轮回复，下一步必须是问用户
    - 本轮结束后 pipeline.post_process(loop_executed=True) → 跳过路径B
      （_parse_and_execute_intent 守卫 (d)），避免同一意图被路径C和路径B各执行一次
    - 暴露面：categories（默认 info/desktop/memory）减去 exclude_tools
      （默认排除 toy_vibrate/toy_stop/toy_pattern/write_toy_file），前端设置页可调

  与路径A/B互斥表：
    | 场景                                  | 路径A探针 | 路径B意图解析 | 路径C loop |
    |---------------------------------------|-----------|---------------|------------|
    | 有效 tool_loop 关 / preset 非 FC / 非owner  | 正常执行  | 正常执行      | 不激活     |
    | 有效 tool_loop 开 + owner + FC preset       | 跳过      | 跳过（loop_executed）| 激活 |

  默认不排斥的角落：QQ 关键词快速路径命中后走 tool_result 注入，loop 里的模型能在层10
  看到这次执行结果，不会重复调用；两者理论上仍可能对同一意图各执行一次，见
  `docs/known-issues.md` 的已知边角登记。

  工具意愿软提示（`tool_loop.nudge_hint`，默认 true，Brief 29 · 5）：loop 首步在
  messages 尾部、用户消息之前插入一条 system 提示"需要外部信息或操作时，直接调用可用
  工具，不要凭记忆编造。"（`_layer: "11.5_tool_nudge"`），利用 recency 位置缓解弱代理
  模型不主动调工具的问题。只在 loop 首次组装 messages 时注入一次，只存在于本轮
  `loop_msgs` 副本里，不进 short_term history，也不经过 prompt_builder 的层级消融机制
  （那套只覆盖 `build()` 组装出的 messages，与 loop 的一次性 messages 是两条链路）。
```

---

## per-char 兼容钩子（Brief 29 · "本我"模式）

角色卡 JSON 顶层可选块 `presence_ext`，缺失 = 全默认 = 现有角色零行为变化：

```json
"presence_ext": {
  "disabled_layers": ["0_jailbreak", "2_jailbreak", "11_jailbreak"],
  "model_routing": "claude-main",
  "tool_categories": ["info", "desktop", "memory", "mcp"],
  "proactive": "off",
  "tool_loop": "on"
}
```

- `tool_categories`：`run_agentic_loop()` 取工具暴露面时，活跃角色卡声明了这个字段就用它，
  否则回落全局 `tool_loop.categories`。`exclude_tools` 始终读全局配置，per-char 不能绕过
  硬件写类等排除项。示例卡 `examples/benwo.example.json` 把 `mcp` 类加入暴露面（`characters/`
  根目录不放模板/示例文件，见 `tests/test_authored_assets.py::test_no_template_files_in_characters_root`；
  要实际加载体验这张卡，复制到 `characters/` 下改名去掉 `.example` 再改 `active_character`）。
- `tool_loop`：仅接受 `"on"` / `"off"`。`"on"` 允许这张卡在全局默认关闭时启用 Path C；
  `"off"` 关闭 Path C；字段缺失或非法值回落全局 `tool_loop.enabled`。它不会绕过 owner 私聊
  或 `function_calling` preset 两道硬闸。`examples/assistant.example.json` 是人机直连组合示例。
- 另外四个钩子（`disabled_layers` / `model_routing` / `proactive` / `tool_loop`）分别见
  `docs/prompt-layers.md`、`docs/model-presets.md`、`docs/scheduler.md`。

---

## MCP（Model Context Protocol）外部工具客户端（Brief 29 · 4）

文件：`core/mcp_client.py`。**只接外部工具，不接 resources/prompts、不接外部记忆库**——
外接记忆库会绕过 prompt 层注入与固化链，裂成两套真相；MCP 在这套架构里只承担"给主 LLM
多几个可调用的外部工具"这一件事。

```yaml
mcp_servers:
  enabled: false
  servers:
    - name: filesystem
      transport: stdio            # stdio | http（streamable http）
      command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "D:/some/dir"]
      # http 时用: url: https://your-mcp-server.example/mcp
      # headers: {Authorization: "Bearer ${MCP_SERVER_TOKEN}"}  # 可选；stdio 忽略
      enabled: true                 # 单 server 开关（默认 true）
      tool_timeout_s: 30
      allow_tools: []              # 空 = 全部；非空 = 白名单
```

- **生命周期**：`main.py` 启动时调 `mcp_client.init_mcp_servers()`，对每个已启用 server 建
  `ClientSession` + `list_tools()`；单 server 初始化失败只跳过它（log + 继续），不影响其他
  server 或主流程。进程退出时 `main.py` 的 `finally` 块调 `shutdown_mcp_servers()` 清理全部
  session。
- **管理面**（Brief 110）：admin token 可在 MCP 页测试 Streamable HTTP URL（`initialize +
  list_tools`）、导入 server、切换总/单 server 开关和勾选 `allow_tools` 白名单。导入前的测试
  不注册工具也不写配置；保存后总开关走 `sync_mcp_servers()`，单 server 走定点热重载。HTTP
  `headers` 的 `${ENV_VAR}` 会在连接时展开，缺失环境变量即连接失败；管理面仅显示环境变量
  占位符或“已配置”，不回显字面 token。
- **工具注册**：转成 `_TOOL_REGISTRY` 动态条目，命名 `mcp__{server}__{tool}`，
  `category="mcp"`，description/inputSchema 直接映射为 OpenAI function schema。与静态注册表
  同名冲突时 MCP 侧让位（记 warning，不覆盖）。
- **执行适配**：`execute()` 走既有的通用分发分支（`func(**tool_args)`），内部转发到
  `session.call_tool()`，超时 `tool_timeout_s`；结果取 content 里的文本项拼接、截断 2000
  字。调用失败（连接已死等）尝试重连一次，再失败异常上抛，走 `tool_dispatcher.execute()`
  既有的失败兜底文案（不是 mcp_client 自己造文案）。**不做后台心跳**，只在调用时才发现断线。
- **action_trace 自动生效**：收口埋点在 `tool_dispatcher.execute()`，MCP 工具零新增记账代
  码；注册条目不声明 `trace_args`，参数不落痕（防外部 server 的敏感入参入盘）。
- **调用观测**：每次 MCP 工具调用额外写入既有 `api_call_log`，caller 固定为
  `mcp__{server}__{tool}`，只记录成功/失败、时长与无敏感的结果提示，不记录 arguments 或
  外部返回正文；管理面按工具展示最近一条调用记录。
- **探针不覆盖 mcp 类**：`get_probe_prompt()` 只拼 info/desktop 两类，MCP 工具只经 tool
  loop（Path C）暴露——角色卡 `presence_ext.tool_categories` 不含 `"mcp"` 就永远看不到这
  些工具，这是"本我接 MCP、角色扮演不受影响"的实现方式。
- **provider 细分**：DS/Claude/GPT 代码层无分支，统一经 OpenAI-compat 网关走 function
  calling，MCP 工具 schema 是标准 JSON Schema 直转，不额外适配。唯一不覆盖场景是原生
  Anthropic API 直连（非网关），当前架构不涉及。
- **风险**：MCP server 是外部进程，描述/结果都是不可信输入，见 `docs/known-issues.md`
  "观察项（Brief 29 · MCP）"。

探针调用时明确过滤：
```python
get_tools_schema(categories=["info", "desktop"])
```

QQ 入口的关键词快速路径直接构造 `{"name": tool, "arguments": {}}`，适合 `get_time` /
`water_garden` 这类无参工具；需要参数的工具仍主要依赖 LLM function_calling 填参。

---

## fs 只读浏览工具（Brief 31）

文件：`core/tools/fs_browse.py`。让角色能"自己翻电脑"——列目录、读文件，范围严格限于
config 声明的允许根目录，**只读**。不新增任何写入入口（唯一写出口仍是
`core/tools/toybox.py` 的 `write_toy_file`）。暴露方式与 MCP 同策略：只经 tool loop
（Path C）暴露，角色卡 `presence_ext.tool_categories` 含 `"fs"` 才可见。

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
  max_read_chars: 4000            # 单次读取截断
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
- **探针不覆盖 fs 类**：`get_probe_prompt()` 只拼 info/desktop 两类，`fs` 类工具只经
  tool loop（Path C）暴露，理由同 MCP——多步浏览本来就是 loop 行为。
- **不受安全/危险模式闸约束**：`_MODE_RESTRICTED_CATEGORIES` 只含 `desktop`/`system`，
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
| `water_garden` | 用户催他浇花/问花园状态并暗示该浇水 | `core/tools/garden_tools.py` |

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

`toy_*` 是 reality-side hardware actuator，只能由 `scheduler.owner_id` 对应用户的真实私聊，
经带 origin 闸门的工具调用触发；群聊、scheduler、trigger 和 Dream pipeline 均不能触发。
客户端使用 `aiohttp` 直连本机 Intiface Central，
`trust_env=False` 绕过系统代理；动作串行执行，并在正常结束、异常和取消时 best-effort 停止设备。
强度限制为 `0.0~1.0`，单步时长上限 30 秒，pattern 最多 32 步。

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
| `revise_memory` | 更正指定情景记忆 | 仅 Path C；旧条目降强度并保留，更正作为新条目追加；必须给出 episode id 与用户确认的更正 |
| `revise_user_profile` | 更正用户稳定行为画像 | 仅 Path C；仅可覆写明确给出的合法 identity 维度，不能凭空推断 |

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
不是关掉去重本身。三个调用点（`main.py` QQ 探针/快速路径、`admin/routers/chat.py`
`owner_chat`、`core/pipeline.py::run_agentic_loop` Path C）各自从本轮用户原始文本探测一次，
Path C 多步调用复用同一次探测结果。

### 花园工具

`water_garden` 是 info 类工具，会被探针覆盖。它不接收参数，内部读取当前 `mood_state`，再调用 `garden_manager.force_water()` 给对应情绪花槽浇一次水。

触发关键词来自 `_TOOL_REGISTRY`：`浇花`、`花园`、`浇水`。工具结果只作为层10 `tool_result` 给 LLM 参考，不直接拼进最终回复。

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

### fs 类（不走探针，只经 tool loop）

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
- `keywords`：关键词列表，命中时走快速路径直接调工具，跳过 LLM

**快速路径**（`_fast_path_match`，在 `main.py` 探针入口）：
关键词命中 → 直接构造 tool_calls，不调 LLM 探针。

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

### send_notification 防误触发

二次校验：意图解析为 `send_notification` 后，他回复必须同时包含时间词和动作词才真正触发通知：
```
时间词：等下 / 待会 / 一会 / 明天 / 后天 / 点 / 分钟后 / 小时后 / 到时 / 之后 / 时候
动作词：提醒你 / 通知 / 告诉你 / 帮你记 / 记着 / 别忘 / 不要忘
```

---

## 意图解析（_parse_and_execute_intent）

在 `post_process` 里异步执行。不同于探针（在用户消息上判断），意图解析是在**他的回复**上判断。

他说"我去把游戏关掉" → 真的执行 `minimize_window`

**守卫（全部满足才执行）：**

| 守卫 | 实现 | 说明 |
|---|---|---|
| (a) 真实 owner turn | `trigger_name == ""` | scheduler/sensor/watch turn 的 trigger_name 非空，直接跳过 |
| (b) 有真实用户输入 | `user_content.strip()` 非空 | 防止空 span 触发 |
| (c) 非危险动作 | action not in `{device_shutdown, device_sleep}` | 永不经 Path B 自动触发 |
| c1 收紧 prompt | 只在第一人称当下主动意图时命中 | 承认/复述/过去式/吐槽回应/睡眠关机语义均不命中 |
| c2 幂等窗口 | `_INTENT_LAST_ACTION[uid:action:key]` 120s 内跳过 | 防止他复述导致重复执行（吐槽+复述链路可能跨 60s） |

**支持的意图类型：**
- `minimize_window`：最小化窗口（不匹配睡眠/关机语义）
- `play_song`：播放歌曲
- `open_url`：打开网址
- `play_pause`：播放/暂停
- `send_notification`：发通知（有额外时间词+动作词组合校验）
- `dream_invite`：角色明确邀请用户进入梦境；向桌面客户端推送邀请动作
- `toy_invite`：角色明确邀请进入玩耍模式；向桌面客户端推送 `{type: toy_invite}` UI 动作，前端在玩耍模式开启时打开 ToyWindow（非危险动作，沿用 Path B 三道守卫与 120s 幂等窗口）

**execute() origin 闸门（Path A）：**

`tool_dispatcher.execute()` 新增**必填**关键字参数 `origin: str`（无默认值）。

| 情形 | 行为 |
|---|---|
| 漏传（调用方未写 `origin=`） | `TypeError`，调用即崩，杜绝静默绕过 |
| 传入值不在白名单 | `(None, None)` + `logger.warning`，零副作用（fail-closed） |
| `origin="user_live"` | Path A 正常执行 |
| `origin="assistant_intent"` | 保留供 Path B 未来接线；当前 Path B 直接调 `_push_desktop_action` |
| `origin="assistant_loop"` | Path C（Brief 28 tool loop）自主多步调用，`Pipeline.run_agentic_loop()` 专用 |

白名单 = `_EXECUTE_ALLOWED_ORIGINS = {"user_live", "assistant_intent", "assistant_loop"}`。
Path A 的 4 个调用方（`main.py` WAITING_CONFIRM / WAITING_INPUT / 探针结果 + `chat.py` `_probe_and_execute_tools`）均已显式传入 `origin="user_live"`。

---

## 动作痕迹（Brief 27 · action_trace）

工具结果只在执行当轮注入 prompt（层 `10_tool_result`），下一轮就"失忆"——用户追问
"你刚才查到什么/你干了什么"无从溯源。`core/memory/action_trace.py` 给每次工具执行落一条
精简痕迹，供层 `10.5_action_trace` 注入"你最近做过的操作"（见 `docs/prompt-layers.md`）。

**埋点位置：**

- `tool_dispatcher.execute()` 每条 return 前都调 `action_trace.record(...)`，**只有 origin
  闸门拒绝（fail-closed 那支）不记**——那不是角色做过的事。其余分支（工具不存在/模式闸/
  未启用/权限拒绝/高危待确认/persist 去重跳过/成功/异常）全部落痕迹，`status` 分别对应
  `failed` / `pending_confirm` / `ok`。
- Path B（`pipeline._parse_and_execute_intent`）不经 `execute()`，直接调
  `_push_desktop_action`，在拿到 `last_result` 后单独补记一条（`origin="assistant_intent"`）。

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
