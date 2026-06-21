# 工单：管理面板 round4 —— 运行时内部态观测 + Prompt 层检视器 + debug 架子清理

> 仓库 `D:\ai\qq-st-bot`，面板 `admin/static/index.html`。先看 `AGENTS.md`。
> 接 round1-3。本轮把"后端内部态"做成可观测——这是面板最不可替代的价值（config/前端都给不了）。
> 含一处**产品决策已下**：debug 架子怎么清、live 配置是否现在做。

---

## 决策摘要（先读）

1. **删 `debug.py`**：`/debug/ws-segments-test` 是 message_segments 集成期的一次性测试桩，文件头 TODO 写明"稳定后可移除"。segment 早已在桌面端日常使用 → 条件满足，删。
2. **`hidden_state_debug.py` 保留但去 DEV-ONLY**：它支撑隐性状态观测页，是真观测，不是临时架。摘掉"DEV-ONLY"标签，归入「观测」而非「Debug」。
3. **分期**：Phase 1 = 只读检视器（运行时内部态 + Prompt 层），现在做；Phase 2 = GUI 实时改每层 token 预算/位置，**压后**——它要把 `prompt_builder` 改成配置驱动、动生成热路径，风险高，等 Phase 1 的检视器证明确有调参需求再单开工单。本工单只做 Phase 1 + 清理。

---

## A. 清理 debug 架子

- [ ] 删除 `admin/routers/debug.py`，并移除 `admin/admin_server.py:72` 的 `app.include_router(debug.router, ...)  # DEV-ONLY` 注册行 + 顶部 import。确认前端无 `/debug/ws-segments-test` 调用（应无）。
- [ ] `hidden_state_debug.py` 升格：
  - 文件头 docstring 去掉 `[DEV-ONLY]` 措辞，改为"隐性状态只读观测"。
  - `admin_server.py:73` 注册行 tag 从 `["Debug"]` 改为 `["观测"]`，去掉 `# DEV-ONLY` 注释。
  - `index.html:1013` 页标题里的 `DEV-ONLY · 只读` 改为 `只读`。
  - 路径可保持 `/debug/user-hidden-state` 不变（避免改前端引用），仅去标签；若想更干净可另起 `/observe/hidden-state` 并改前端调用，二选一。
- [ ] 顺手扫一遍是否还有其它孤儿 debug 端点/开关（grep `DEV-ONLY` / `ws-test` / 无引用的 `load*` JS）。`debug_token_log`、`gating_shadow`、`forensic_logs` 这些是**有用的取证设施，保留**，不在清理范围。

**验收**：`/debug/ws-segments-test` 不复存在；隐性状态页不再标 DEV-ONLY；面板加载无悬空引用。

---

## B. 「运行时内部态」观测页（Phase 1）

在「🔍 观测」组新增一页「运行时内部态」，**只读**，集中显示后端跑动时的内部状态。数据源都已存在，需各加一个小的只读 accessor + 一个聚合端点（建议 `GET /observe/runtime`）：

| 展示项 | 来源 | 取什么 |
|---|---|---|
| slow_queue 积压 | `core/post_process/slow_queue.py` | 队列长度、当前正在处理的 task_type、单 worker 是否存活 |
| DLQ（死信） | `get_paths().dead_letter_queue()`（`data/logs/dead_letter_queue/`，count-cap 200） | 文件数、最近 N 条文件名+task_type+时间 |
| pending_perception | `get_paths().pending_perception_dir()`（两阶段提交暂存） | 未提交感知条数、最旧时间 |
| 锁状态 | `core/memory/locks.py`（`uid_lock`/`global_lock`）、`core/conversation_gate.py`（`conversation_lock`） | 当前哪些 uid_lock / global_lock 被持有（`Lock.locked()`） |
| active channels | `channels.registry.get_active()`（见 `core/turn_sink.py:98`） | 活跃通道名列表（qq/desktop/mobile…） |
| 当前 mood | 已有 `/mood` | 直接复用，放一行 |

实现要点：
- 后端聚合端点 `GET /observe/runtime` 一次返回上述全部，路径走 `core/sandbox.get_paths()`，**纯读不写**，任一子项失败只让该项显示"读取失败"，不整页崩。
- 前端一页多张只读卡 + 一个刷新按钮（可选 5s 自动轮询，但默认手动，别给后端加负担）。
- 锁/队列这类瞬时态标注"快照，刷新查看"，不要让用户误以为是实时流。

**验收**：页面能看到 slow_queue 长度、DLQ 计数、pending 数、当前持锁 uid、活跃通道、当前 mood；制造一次积压（如塞个慢任务）能在刷新后看到变化。

---

## C. 「Prompt 层检视器」观测页（Phase 1，本轮重点）

**目的**：回答"为什么模型没看到这条信息"。一条信息没进 prompt 通常是三种原因之一，检视器要能一眼区分：
1. **没被构建**：该层 tag 没命中（topic tag 未激活）或内容为空，`build_prompt` 里的 `if` 跳过了 append。
2. **被裁剪**：token 估算 > 20000，按 `_drop_priority` 整批丢弃（裁到 ≤18000），落在 `removed_layers`。
3. **在但靠后/被淹没**：内容在，但位置/占比问题。

**数据已现成**（无需重算）：`core/prompt_builder.py` 的 `build_prompt()` 返回 `(messages, meta)`：
- 每个 `message`：`_layer`(层名)、`content`、`len(content)`(字符数)、可选 `_drop_priority`、在列表中的 index(=位置/注入顺序)。
- `meta`：`layers_activated`、`token_estimate`、`tags`(本轮激活的 topic tags = 决定哪些层被构建)、`removed_layers`(被裁的层)。
- 阈值：软警戒 15000、裁剪触发 20000、裁剪目标 ≤18000（字符数估算，注释里 1 token≈1.5~2 汉字）。

**后端**：
- 加一个**最近 N 轮的环形缓冲**（内存即可，按 uid 存最近 3-5 次 build 的 `(messages, meta)` 快照；注意脱敏——这是给 owner 自己看的本地面板，可含原文，但别落盘）。在 `build_prompt` 返回处或 pipeline 调用处挂钩捕获。
- 端点 `GET /observe/prompt-layers/{uid}` → 返回最近一轮（或带 `?n=` 选第几轮）的层级明细：每层 `{layer, position, chars, est_tokens, drop_priority, gated_in:bool, pruned:bool, content}` + 顶层 `{token_estimate, soft/hard 阈值, active_tags, removed_layers}`。
- `est_tokens` 用 `chars / 1.7` 之类的粗估即可（与现注释一致），并在 UI 标注"估算"。
- 可选「dry-run」：给定一段输入文本，跑一次 `build_prompt` 但不进 LLM、不写存储，返回层级明细——方便复现"这句话会不会进 prompt"。**这是加分项，Phase 1 可只做"看最近真实一轮"。**

**前端**：
- 层级堆叠视图：按 position 列出每层，显示层名 + 字符/估算 token + 占总量百分比（小进度条）+ 状态徽标（`已注入`/`被裁`/`未构建(tag未命中)`）。
- 顶部总览：当前估算 token vs 15k/18k/20k 三条线（进度条标出在哪个区间）、本轮激活 tags、被裁层列表。
- 每层可展开看 `content` 原文。
- 一个 uid 选择器 + 轮次选择（最近 3-5 轮）。

**验收**：
- 发一条消息后，检视器能列出本轮所有层、各自 token 占比、哪些被裁/未构建；点开能看层内容。
- 故意造一条 >20k 的超长上下文 → 检视器显示触发裁剪、`removed_layers` 与现实一致。
- 能借它定位"某条往事没进 prompt"是因为 tag 没命中 还是 被裁。

---

## D. （Phase 2，本轮不做，仅登记方向）GUI 实时改每层预算/位置

用户想要"实时调每层 token 预算、位置"。这需要把 `prompt_builder` 现在**硬编码**的层顺序 + `_drop_priority` + 裁剪阈值改成**配置驱动**（如新增 `prompt_layers` config 块：每层 enabled/order/drop_priority/budget），再加面板编辑器。

**为什么压后（决策）**：这动的是每轮生成的热路径，改错直接影响所有对话输出；且在没有 Phase 1 检视器之前，无法判断到底哪层需要调。**先上 C 的只读检视器，用它观察一两周真实数据，再决定是否值得为可调性承担热路径风险。** 届时单开工单，且必须带回归测试（`python tests/run_eval.py` 验层激活）。

记入 `docs/known-issues.md` 或 `docs/prompt-layers.md` 一条"待评估：prompt 层配置化 + 面板调参（依赖 round4 检视器先行）"。

---

## 执行顺序
1. **A**（清理，最小）。
2. **B**（运行时内部态，独立）。
3. **C**（Prompt 层检视器，重点；后端捕获 + 前端堆叠视图）。
4. **D** 只写一条 TODO，不施工。

> 自测：删 debug 后面板正常加载；运行时页能反映真实队列/锁/通道；发消息后 prompt 检视器层级与 `prompt_builder.token` 日志对得上。改了 prompt 观测但**没动构建逻辑** → 无需跑 run_eval；若 C 的捕获钩子改到了 `build_prompt` 签名，则需跑 `python tests/run_eval.py` 确认层激活未受影响，并同步 `docs/prompt-layers.md`。
