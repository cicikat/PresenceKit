# 工单批次：管理面自主设置 / 兜底回复 / TTS 卡死 / 工具 schema 兼容

> 状态：待施工。四张工单互不依赖，可并行；每张验收后独立提交（见 AGENTS.md）。
> 行号为写单时快照，施工前先 Grep 复核。

---

## 工单 B1：自主设置页可编辑每轮上限 + 保存按钮与布局重整

### 现象
管理面看得到「每次最多步数 / 每次最多工具次数 / 其中写类工具」，但改不了；卡片不整齐；「保存全部设置」藏在某张折叠卡片的底部，看不出它保存什么、属于哪里。

### 根因
1. 后端已经支持这三个字段：`admin/routers/autonomy.py:105-160` 的 PATCH `/admin/autonomy/config` 接受 `max_steps`(1-8)、`max_tools`(0-8)、`max_write_tools`(0-8)，并在 :158 校验 `max_write_tools ≤ max_tools`。
2. 前端缺字段：
   - `admin/static/pages/autonomy-settings.html` 里没有这三个 input。
   - `admin/static/js/observability.js` 的 `loadAutonomySettings()`（约 :2283）不回填，`saveAutonomyConfig()`（约 :684）也不提交。
   - `total_timeout_seconds`、`tool_timeout_seconds` 同样缺失，施工时一并核对后端可接受字段，前端一起补齐。
3. 保存按钮的问题：
   - 按钮在 `autonomy-settings.html:4`，i18n key 是 `autonomy.save`。它实际只保存「总控与预算 / Overflow / 定时唤醒 / 间隔唤醒」四张卡；视频通话卡有自己的保存按钮。
   - `admin/static/js/admin-design.js:4-72` 的 `decorateSettingsPanels` 会把含 2 个及以上字段的卡包进折叠的 `<details>`，并把 header 按钮挪进这张卡的 footer。于是页级按钮看起来只属于这一张卡。
   - :56-58 还会把函数名 `saveAutonomyConfig` 作为原文显示在摘要里。
4. 卡片不齐：`admin/static/style.css:528-548` 的 `.autonomy-source-grid` 规则是按未包裹的卡片写的。包裹后有的卡被折叠、有的没被折叠，两种卡混排，高宽就对不齐了。

### 施工要求
1. 在「总控与预算」卡里，把三项上限放进同一个「每轮执行上限」分组。
   - 用 number input，范围按后端约束；写类工具上限的 max 跟随工具次数联动。
   - 前端先做 `写类 ≤ 工具次数` 校验，并就地给出提示。
   - 在 load 中回填，在 save body 中带上。
   - 两个超时字段按后端可接受字段同样补齐。
2. 保存交互改成每张卡保存自己。
   - 每张可编辑卡都有自己的「保存」按钮，位置固定在卡底部，只提交这张卡的字段。仍然走同一个 PATCH，提交部分字段即可，先确认后端是 partial update；如果不是，就合并当前已加载的配置后再提交。
   - 删除页级「保存全部设置」，或者把它改成页面顶部固定栏里的「保存所有修改」：有未保存修改时高亮，并列出脏卡名称。二选一，推荐前者（每卡自保存）。
   - 有未保存修改的卡要显示「未保存」标记。离开页面前不强制弹窗，标记清楚即可。
3. `admin-design.js`：
   - 对 `autonomy-settings` 页，不再把页级按钮挪进某张卡的 footer。
   - 摘要里不再显示函数名原文。
   - 「总控与预算」卡默认展开，不折叠。
4. 布局：
   - 统一卡片结构，所有卡要么都包裹，要么都不包裹。
   - 修正 `.autonomy-source-grid` 的选择器，让所有卡在宽屏下两列等宽、顶部对齐，窄屏下单列。
   - 删除 :548 这条冗余规则。
5. 文案全部走 i18n（中英两份），不写死中文。
6. 按 AGENTS.md「Admin Static Asset Cache」：
   - 更新 `core.js:23` 的 `ADMIN_UI_FRAGMENT_VERSION`。
   - 更新 `index.html` 里 `core.js?v=`；改到的其他直接加载的 JS/CSS 也要更新对应的 `?v=`。
   - 在浏览器里硬刷新实测。

### 验收
- 三个上限可修改，保存后刷新页面值仍然保留；`max_write_tools > max_tools` 时前端拦截，后端同样拒绝。
- 每张卡的保存只影响本卡；页面上没有归属不清的保存按钮。
- 宽屏和窄屏下卡片都对齐。
- 浏览器实测截图或文字记录写进提交说明；如果环境无法打开浏览器，写明「浏览器实测未完成」。

---

## 工单 B2：去掉硬编码的「我还没能实际完成这一步…」整段替换

### 现象
角色没拿到工具结果时，或者某些场合下，整条回复突然变成一句生硬的：「我还没能实际完成这一步，刚才没有拿到可确认的成功结果。」

### 根因
`core/tool_grounding.py:122-140` 的 `guard_completion_claim()` 同时满足下面三个条件时，会把整条回复替换成这句硬编码字面量：
- (a) 本轮 prompt 有 `11_tool_grounding` 层且 `required=True`；
- (b) 判定为未成功；
- (c) 回复匹配宽泛正则 `_COMPLETION_CLAIM_RE`（:55-61）。

这句话不是角色口吻，违反 AGENTS.md 规则 9 的精神。测试 `tests/test_tool_grounding.py:47` 把这个字面量锁住了。

调用点在 `core/pipeline.py`：:912（普通 `run_llm`）、:1752（工具循环在没有调用任何工具的情况下自然结束）、:1778（流式）、:1789（非流式收尾）。

### 误判来源（都要修）
1. 成功判定只认 `role=="tool"` 的消息，并且要求内容里同时出现 `工具已执行：` 和 `<<<TOOL_DATA_START>>>`（:111-119）。
   - 非工具循环路径下，工具结果是以 system 层 `10_tool_result` 注入的，所以 :912 永远识别不到成功。
2. `prompt_builder.py:1436-1437` 把 `tool_executed` 以外的所有 status 都映射为 `execution_failed`，只读类或信息类的 status 也会被当成失败。
3. 工具循环里只有带 `工具已执行：` 前缀（`pipeline.py:1474/1652/1690`）的结果才算本轮成功，前缀唯一来源是 `tool_dispatcher.py:3131`。下面这些结果都不带前缀，都会被判失败：
   - discovery 的 `load_tools_*` 回执；
   - ask_confirm；
   - MCP 结果；
   - `self_db_*` / `self_tool_*` 的早退返回；
   - 中转站分支的结果。
4. 正则不识别否定句式，也不区分主语。「我刚才看到了你发的…」「你完成了吗」这类句子都会命中。

### 施工要求
1. 成功判定改为结构化信号，不再匹配文本前缀。
   - 给 `execute_structured()` 及以上所有非标准返回路径（discovery、ask_confirm、MCP、self_*、中转站分支）统一产出一个 `ToolOutcome`（`ok` / `failed` / `pending_confirm` / `info`）。
   - pipeline 用这个结构判定成功，不再看 `工具已执行：` 前缀。
   - `info`（discovery 回执等）不算失败；需要另一次真实工具调用才能确认成功的情况，记为「本轮尚未执行」。
2. 修正 status 映射：只读或信息类 status 不得映射成 `execution_failed`。
3. 不再整段替换回复，改成以下两档之一，推荐 (a)：
   - (a) 检测到未经证实的完成声明时，不改写文本。改为在同一轮内追加一个 nudge，让模型在知道「工具未成功」的前提下用角色口吻重写一次。最多重写 1 次；重写后仍然命中，就原样放行并记录 trace。
   - (b) 只删掉命中的那一句，保留回复其余部分。
   - 无论哪档，都不允许输出固定字面量。如果确实需要兜底文本，经 `char_name` 插值的 prompt 让模型生成。
4. 收紧正则：排除否定和疑问句式、主语是「你」的句子、以及引用用户内容的句子。补反例测试。
5. 加观测：每次触发都写 trace，记录命中片段、判定依据和采取的动作。复用现有工具 trace 或 observability 端点即可，满足 AGENTS.md 规则 7。
6. 更新 `tests/test_tool_grounding.py`：删掉对字面量的断言，补齐上面各误判场景的回归用例（MCP 成功、self_db 成功、discovery 回执、非循环路径下 system 层注入的工具结果、否定句）。
7. `docs/tools.md` 约 :204 的 grounding 说明同步为新行为。

### 验收
- 上面的误判场景都不再触发；真正的「没调用工具却声称完成」会被拦下，最终输出是角色口吻的重写。
- `pytest tests/test_tool_grounding.py` 以及 pipeline 工具循环相关测试通过。

---

## 工单 B3：TTS 卡死与长回复堵塞

### 现象
TTS 报错一次之后就再也连不上，只有重启后端才能恢复；一次性回复很长、分段很多时也会卡住。

### 根因（按可能性排序）
1. 本机 GPT-SoVITS 调用没有超时（主因）。
   - `core/output/voice_adapter.py` 的 `GsvProvider.synthesize`（约 :483-606）每次调用都新建 `gradio_client.Client`（:524），切换模型时调用 `predict`（:533/543），再调用 `/get_tts_wav`（:564），全程没有超时。
   - 合成过程中持有两把模块级锁：`_GSV_SYNTHESIS_LOCK`（:48/:592）和 `core/video_call.py:63,300` 的 `local_resource()`（与视觉推理共享）。
   - GSV 一旦卡住（OOM、重启、gradio 队列卡死），两把锁就永久不释放，之后所有 TTS 请求无限排队。
   - `synthesize()` 的文档字符串（:711）写着「超时 15 秒」，但代码里没有实现。
2. `_yield_shared_local_resource`（:457-474）在分段之间让出锁时，先 release 再 acquire。
   - 如果在 acquire 处被取消，外层 `async with` 会再 release 一次：可能把视觉推理持有的锁错误释放，也可能抛出被 :750 吞掉的 RuntimeError，锁的归属就乱了。
   - 这个问题只在分段数达到 2 及以上时出现，与「长回复卡」吻合。
3. 并发没有上限。
   - 自动 TTS 是 fire-and-forget 的 `create_task`（`core/pipeline.py:2295`）。
   - `/tts/synthesize`（`admin/routers/settings_misc.py:348`）收到的每个请求都在同一把锁上无限等待，不会丢弃过期请求。
4. `_GSV_ACTIVE_MODELS`（:53）缓存了已加载的模型，失败时不失效。GSV 重启后，后端会跳过 `change_*_weights`，持续用错音色，或者持续失败。

### 施工要求
1. 每次 GSV 调用都要有硬超时。
   - 给 `Client` 和每次 `predict` 都设置超时：优先用 gradio_client 或 httpx 自带的 timeout 参数。参数不可用时，用专用的单线程 executor 加 `asyncio.wait_for`。
   - 超时后必须确保锁被释放。
   - 超时后线程可能仍在运行，因此要把这次调用标记为「GSV 不健康」，后续请求先做健康探测，探测通过后再进锁。
   - 超时时长做成配置项（单段默认 30s，模型切换默认 60s）；新增字段同步 `docs/feature-control-surface.md`。
2. 修正锁让出逻辑：追踪当前是否真正持有锁，没有重新拿到锁就不在外层 release；或者改成显式 acquire/release 并在每一步做持有判断。补一条取消场景的测试。
3. 给 TTS 请求加一个有界队列：单 worker 消费。
   - 队列满时，或者同一 turn 已有更新的请求时，丢弃旧请求并返回明确错误，不挂起调用方。
   - 同一 turn 的多段文本合并成一个任务。
4. 任何失败都清掉 `_GSV_ACTIVE_MODELS[api_url]`，下一次调用重新切换模型。
5. 恢复能力：失败后下一次请求必须能重新连上，不依赖重启后端。新增一个只读观测入口，或者复用 TTS 状态接口，暴露以下内容：
   - 当前锁持有者；
   - 队列长度；
   - 最近一次错误或超时；
   - 健康状态。
   满足 AGENTS.md 规则 7。
6. 顺手修 `send_voice` 里的裸 `except: pass`（:779-788），改成记录日志。
7. 测试：mock 一个会卡住的 GSV，断言超时后锁被释放、下一次请求能成功；再测多段文本过程中取消的场景，以及队列满时的丢弃行为。复用 `tests/test_tts_provider_adapter.py` 和 `tests/test_video_call.py`。
8. 在 `docs/known-issues.md` 记录本问题及修复；如果对 `docs/feature-control-surface.md:264-266` 里队列和锁的描述有改动，同步更新。

### 验收
- 模拟 GSV 卡住或抛错后，不重启后端也能自动恢复。
- 长回复（10 段以上）不阻塞后续 TTS 和视觉推理。
- 相关测试通过。

---

## 工单 B4：工具 schema 统一走兼容层 + 文档补规则

### 现象
之前为了兼容中转站的格式要求统一做过一次工具 schema 兼容，最近新增工具后，角色调用工具更容易报错。

### 根因
1. 现有兼容层是 `core/llm_protocol.py:633-705`：`_portable_tool_schema` 把 type 联合类型转成 anyOf，并去掉只有 required 的 anyOf/oneOf 分支。
   - 只有 chat completions 路径（:770）用到了它。
   - responses 路径（:328-344）和 anthropic 路径（:408）直接把原始 schema 透传出去。
2. 兼容层本身没有处理以下情况：
   - 工具名字符集和长度；
   - 空 description；
   - `additionalProperties`；
   - 没有 properties 的 `type:object`；
   - 空的 `properties: {}`；
   - `$ref` / `format` / `default`。
3. 最近新增的工具踩中了这些坑：

| 来源 | 位置 | 问题 |
|---|---|---|
| `self_db_insert` / `self_db_query` / `self_db_update` / `self_db_delete`（T2） | `core/tools/character_self.py:212,230,250-251,267` | `rows`/`where`/`set` 是没有 properties 的 object |
| `self_tool_*`（T3） | `character_self.py:314,345` | `args` 是没有 properties 的 object |
| `self_tool_*` 名称和描述 | `core/self_tool_recipes.py:120,132,201` | 由角色自定义，名称没有字符集校验 |
| 无参工具 | `character_self.py:165,331` | `properties: {}` 为空 |
| 听歌工具 | `core/listening_tools.py:293-373` | `additionalProperties: False` |
| discovery stub | `core/tool_discovery.py:86` | `additionalProperties: False` |
| dispatcher 工具 | `core/tool_dispatcher.py:1118,2025-2057` | `additionalProperties: False` |
| MCP 工具 | `core/mcp_client.py:934,990` | 名称和 inputSchema 原样透传 |

### 施工要求
1. 在 `llm_protocol.py` 里做唯一的出口清洗函数 `portable_tool_spec(name, description, schema)`，三条协议路径（chat completions / responses / anthropic）全部经过它。它负责：
   - 名称：规范化为 `^[A-Za-z0-9_-]{1,64}$`，非法字符替换为 `_`，超长就截断并加 hash 后缀。同时维护「出口名 → 内部名」映射表，在 tool_call 回来时反查。MCP 名称和 self_tool 名称都走这套映射。
   - 空 description：补成工具名或一句通用描述。
   - 递归移除 `additionalProperties`、`$ref`（能内联就内联，否则降级为 string 加说明）、`format`、`default`、`examples`、`$schema`。
   - 没有 properties 的 object 参数（如 `where`、`set`、`args`、`rows.items`）降级为 `type:string`，并在描述里写明「JSON 对象字符串」。dispatcher 侧负责把这个字符串 `json.loads` 回 dict，兼容模型直接传 dict 的情况。
   - 空 `properties: {}`：顶层保持 `{"type":"object","properties":{}}`，并按 provider 配置决定是否注入一个可选的占位参数。默认不注入，留一个 preset 级开关。
   - 保留原有的 type 联合转换和 required-only 分支清理。
2. 不改各工具源码里的 schema 声明（便于阅读）。所有兼容处理只在出口统一做。
3. 新增守门测试 `tests/test_tool_schema_portable.py`：遍历 `_TOOL_REGISTRY`、discovery stub、listening 工具，再加上一个 MCP 模拟工具和一个 self_tool 模拟配方，经过三条出口后断言以下全部成立：
   - 名称合法；
   - description 非空；
   - 不含任何被禁关键字；
   - 不存在没有 properties 的 object。
   以后新加工具时，这条测试会自动覆盖到。
4. 补一条测试：回程 tool_call 的出口名能正确映射回内部名并执行。self_db 的 `where` 以字符串形式传入时也要能执行成功。
5. 文档规则：
   - `docs/tools.md`：在约 :246 的注册约定处新增「Provider 兼容层」小节，写明：
     - 所有工具 schema 只经由 `portable_tool_spec` 出口；
     - 新增或修改工具（含 self_tool、MCP、discovery）不得绕过；
     - 列出禁用的 schema 写法；
     - 列出守门测试名。
     - 修改约 :390 的 MCP「直接映射」描述。
   - `docs/model-presets.md`：增加中转站兼容说明（三条协议路径都会清洗，以及占位参数开关）。
   - `AGENTS.md`「改代码前的强制规则」新增一条：**新增或修改工具时，schema 必须能通过 `tests/test_tool_schema_portable.py`，不得在工具侧为单个 provider 打补丁；兼容问题统一在 `core/llm_protocol.py` 出口处理。**

### 验收
- 守门测试通过；`pytest tests/ -k "tool or llm_protocol or mcp or self_db or self_tool"` 通过。
- 用一个严格的中转站 preset 实测 `self_db_query`、`self_tool` 和一个 MCP 工具，都能调用成功；条件不具备时写明「实测未完成」。
