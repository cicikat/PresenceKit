# 挑错报告 · Emerald-Presence

> 审查人：Claude（Fable）
> 日期：2026-07-07
> 视角：对家（GPT / Codex）写的这套系统，本轮只挑错、不动手。
> 原则：从底层 → 架构 → 模块，只说真问题，不留情面。爱之深，责之切。🐱

---

## 0. 一句话结论

**这是一个用「打补丁」的方式长出来的系统。** 每个具体问题都被认真地、局部地解决了——补丁写得很讲究，注释很详细，守卫叠了一层又一层。但没有人退后一步问：*为什么一个单用户聊天机器人需要 13 万行代码、261 个测试文件、38 份设计文档？* 代码的质量（局部）很高，架构的质量（整体）在崩塌。技术债不在某个模块里，技术债就是这套架构本身。

下面从最底层的机制问题往上说，越往后越是「架构级」和「哲学级」的问题。

---

## 一、底层机制：会真正咬人的 bug

### 1.1 【严重】同步 sqlite 查询在热路径里阻塞整个事件循环

`core/pipeline.py::fetch_context`（第 281 行）：

```python
_semantic_hits = _vs.query(uid, char_id, _query_vec, k=8)
```

`core/memory/vector_store.py::query` 是**同步函数**，内部 `sqlite3.connect` + `execute` 全是阻塞 IO，而且**没有** `run_in_executor`。它被直接 `await` 不了——它就是同步调用，卡在协程里执行。

这是单线程 asyncio。这一句一卡，**当前用户以外的所有会话全部排队等它**。`upsert`（第 110 行起）虽然是 `async def`，但 sqlite 那段同样是内联阻塞，`asyncio.create_task` 只是把阻塞推迟到下一个 tick，照样卡循环。

你们花了大力气做 per-session 队列（`message_queue.py`）来让不同用户**并行**，然后又在最热的 `fetch_context` 里放了一个同步 sqlite 阻塞点，把并行性又收了回去。单用户系统现在感觉不到，多用户或压测立刻头对头阻塞（head-of-line blocking）。

**对家的盲区**：async 正确性靠肉眼看不出来，`query()` 看着像个普通函数调用就放进去了。这类问题只有在「谁在阻塞事件循环」这个问题上系统性排查才能发现，而没人做过这件事。

---

### 1.2 【严重】`detect_emotion` 把一次网络 LLM 调用关在 `uid_lock` 里最长 8 秒

`core/pipeline.py::post_process`（第 883–904 行）：

```python
async with _locks.uid_lock(user_id):
    ...
    _emotion = await asyncio.wait_for(
        llm_client.detect_emotion(reply), timeout=_DETECT_EMOTION_TIMEOUT  # 8.0s
    )
```

`uid_lock` 的语义是「同一用户读-改-写记忆的临界区」。你们把一个**最长 8 秒的网络往返**放进了这个临界区。这意味着：

- 该用户的记忆写入临界区被一个情绪分类 LLM 调用占用最多 8 秒；
- 任何等这把锁的路径（另一个通道的同用户消息、慢队列回写）全程干等；
- 注释信誓旦旦写着「detect_emotion（带超时，绝不拖死 uid_lock）」——**8 秒就是「拖死」**。超时只保证不是无限，不代表不阻塞。

`detect_emotion` 的结果（emotion）确实要写进 mood/event_log，所以想在锁里拿。但正确做法是**锁外算，锁内写**：先在锁外 `await detect_emotion`，拿到结果再进 `uid_lock` 做纯内存/文件的快速写入。现在的写法把「慢」和「临界」焊死在一起。

---

### 1.3 【严重】每一条 owner 消息触发 5–8 次 LLM 调用，其中一次是「猜模型想干嘛」

顺着一条普通 owner 私聊消息数一遍 LLM 调用：

1. **probe**（`main.py:467`）——探测要不要调工具；
2. **主生成**（`run_llm` 或 `run_agentic_loop`，后者可能是多次）；
3. **detect_emotion**（`post_process`，锁内，见 1.2）；
4. **consistency_check**（慢队列，一次 LLM）；
5. **summarize_to_midterm**（慢队列，一次 LLM）；
6. **reflect_to_episodic**（情绪显著时 eager，又一次 LLM）；
7. **user_profile_update**（每 N 轮，一次 LLM）；
8. **`_parse_and_execute_intent`**（`pipeline.py:1142`）——**再拿一次 LLM 去读刚生成的回复，猜角色"是不是想做某个桌面操作"**。

第 8 项（Path B）是最刺眼的设计。你们已经有 probe（第 1 项）和 tool loop（`run_agentic_loop`）两条正经的工具触发通道，然后又加了第三条：**生成完之后，用另一个 LLM 反向解析自己刚说的话，把"我去把游戏关掉"这句台词翻译成真实的 `minimize_window` 执行。**

这条链路的脆弱性写在它自己的守卫里——看看 `_parse_and_execute_intent` 需要多少护栏才敢运行：guard (a) trigger_name 空、(b) user_content 非空、(c) 危险动作黑名单、(c2) 120 秒同动作幂等窗口、(d) loop_executed 跳过，外加 `send_notification` 还要额外做 time-word × action-word 的关键词组合校验。**一个功能需要五道守卫加一套关键词正则才敢落地，这个功能的设计就是错的。** 它在拿「模型的表演台词」当「用户的操作指令」执行，本质是在赌 confab 不会伤人，然后用一堆 if 去堵赌输的情况。

**成本与延迟**：单条消息 5–8 次 LLM 往返。DeepSeek 再便宜，这个放大系数也离谱，而且用户要为第 3 项（锁内）多等一个往返。

---

### 1.4 【中】进程内全局字典无限增长（内存泄漏）

- `core/memory/locks.py`：`_uid_locks` / `_global_locks` 是 `defaultdict(asyncio.Lock)`，**只增不删**。每见到一个新 uid 就多一把锁，永不回收。
- `core/pipeline.py`：`_INTENT_LAST_ACTION`（幂等窗口）、`_AVATAR_DIRECTIVE_LAST`（表情节流）同样只写不清。
- `core/message_queue.py`：`_queues` / `_tasks` 按 session_key 建，任务结束后 queue 对象也不回收。

单用户跑一年没事，但这是「设计成不回收」，不是「暂时够用」。任何把它当多用户/群聊网关用的场景，这些字典就是缓慢上涨的内存曲线。没有一处 TTL 或 LRU。

---

### 1.5 【中】`uid_lock` 与 `message_queue` 的串行保证在语义上重叠又不完全重叠

`message_queue._process_session` 已经保证「同一 session 严格串行」。`post_process` 里的 `uid_lock` 又保证「同一 uid 串行」。两者大部分时候在做同一件事，但边界对不齐：

- session_key 私聊是 `user_{uid}`，uid_lock 是 `uid`——同一个人，两套 key；
- 慢队列 handler（`_handler_user_profile_update` 等）也抢 `uid_lock`，它跑在 message_queue 之外。

结果是：并发模型有**两个真相来源**，读代码的人得同时在脑子里维护「队列串行」和「锁串行」两张图，还得判断某条路径到底受哪个保护。这不是 bug，是**认知负债**——下一个改并发的人极可能在两套机制的缝里塞进一个竞态。

---

## 二、架构：正确但过度

### 2.1 【架构级·最重要】accidental complexity 已经淹没 essential complexity

事实清单（都是从仓库里数出来的）：

- **133,808 行 Python**，261 个测试文件，38 份 `docs/*.md`，用于**一个单用户陪伴机器人**；
- `main.py::handle_message` 单函数约 **420 行**；`pipeline.py` 1484 行、`prompt_builder.py` 1662 行、`tool_dispatcher.py` 1190 行；
- 核心概念栈：Pipeline / MemoryScope / WriteEnvelope / frozen_scope / recall_policy / tag gating / 五层记忆 / fixation_pipeline / 慢队列 + DLQ / dream guard / stage session / garden / embodiment / hidden_state ……新人要理解一条消息怎么变成一句回复，得先装下这十几个概念。

一个概念本身都合理。合在一起，**理解成本已经超过了这个产品要解决的问题的复杂度**。这是典型的对家（GPT/Codex）式增长模式：每个 Brief、每个 RC、每个 CC 任务都在「正确地」加东西，没有一次是在「删东西」。代码库在做加法游戏，而好架构的标志是敢做减法。

判断依据很简单：**注释里到处是「R8-E2」「Brief 28 · Path C」「N2-A」「S6 migration」「CC 任务 19」这类内部工单编号。** 当代码需要靠工单号来解释「为什么长这样」，说明它的形状是历史施工顺序的化石，不是问题结构的映射。半年后没人记得 N7-B 是什么，但那个 allowlist 还在。

### 2.2 【架构级】迁移脚手架从不拆除，变成永久地层

代码里同时活着「旧布局」和「新布局」，且旧的从不删：

- `data_paths.py`：`_LAYOUT_CHARACTER_INNER` / `_LAYOUT_REALITY` / `_LAYOUT_DREAM` 三个布局开关 + `_TRANSITION_*` 镜像写开关。全部已翻到 `v1`，但 `legacy` 分支和 `_p("diary_context")` 这类旧路径分支还留在函数体里。
- `slow_queue.LEGACY_TASK_TYPES`：`mid_term_append` / `episodic_compress` 已被取代，但 handler（`_handler_mid_term_append` 等）还注册着，「供 DLQ 里残留任务重试用」。
- `character_growth`：R8-E2 后「write path retired，now read-only legacy surface」——一个只读的死器官还挂在身上。
- `scheduler.set_pipeline()`：注释明说 deprecated shim，delegate 到 `pipeline_registry.register()`，却还在 `main.py:150` 被调用。
- CLAUDE.md 自己写：「Legacy paths … are **migrated / historical** and must not be used in new code」——**你在文档里请求开发者"假装这些代码不存在"，这就是它该被删除的信号。**

每一处「保留兼容」单独看都稳妥。加起来，代码库背着一整层考古地层跑步，新人无法区分「活代码」和「化石」，只能全读。

### 2.3 【架构级】fail-open 无处不在，等于系统性地把错误变成静默数据丢失

统计：`core/ admin/ channels/ main.py` 里 **627 处 `except Exception`，其中约 97 处直接 `except ...: pass`**。

抽样看后果：

- `main.py:512` probe_capture、`main.py:537` 同款、`pipeline.py:604` prompt capture——全是 `try/except: pass`。观测数据丢了你永远不知道。
- `main.py:832` turn_sink 失败只 `log_error`，注释自己写「記憶寫入可能丟失」——**记忆写入丢失是这个产品的核心失败**，却只是记一行 warning 就放过。
- 大量 `except Exception: pass` 把「配置错了」「路径穿越了」「JSON 坏了」和「网络抖了」压成同一种「无事发生」。

fail-open 在陪伴机器人里是合理的默认姿态（不能因为召回失败就不回话）。但**无差别 fail-open** 会让真正的 bug 潜伏数月：某层记忆一直没写进去，表现只是「她好像记性不太好」，没有任何异常冒头。1.1 提到的阻塞、H1 提到的 hidden_state 未接线，都是这类「功能设计好了但静默没生效」——而 fail-open 文化正是让它们能长期隐身的土壤。

### 2.4 【中】函数级 import 被当成常规写法（约 939 处）

`core/` + `main.py` 里有约 **939 个函数体内的 `import`**。`handle_message` 一个函数里 `from core import ...` 出现十几次。

理由通常是「避免循环导入」。但当循环导入多到要用函数级 import 系统性规避，这说明**模块依赖图本身是一团意大利面**——模块边界没划对，才需要靠延迟 import 来骗过 Python 的导入顺序。函数级 import 是症状，循环依赖是病。而且它有真实代价：每次调用都重新进一次 import 机制（虽有缓存但非零），且 IDE / 静态分析 / 依赖审计全部失效。

---

## 三、安全与运维

### 3.1 【严重·部署】默认绑 `0.0.0.0` + 弱/占位 secret + 启动不阻断

- `config.example.yaml`：`admin.host: 0.0.0.0`、`secret_key: YOUR_ADMIN_SECRET`；你们本机 `config.yaml` 实际是 `host: 0.0.0.0` + `secret_key: <redacted>`（弱口令）。
- `admin/auth.py::resolve_token`：只要 `secret` 非空就 `hmac.compare_digest(raw, secret)`。也就是说**占位符 `YOUR_ADMIN_SECRET` 本身就是一个能用的 admin 全权 token**——谁读过 example 谁就有你的后台。
- `main.py:82-84`：检测到占位 secret 且无 token 时，只 `logger.info("首次使用请运行 …")`——**不阻断启动**。对比同文件里 `gating_shadow.enabled=false` 会直接 `sys.exit(1)`：你们愿意为一个内部功能门禁硬阻断启动，却不愿为「后台裸奔在公网默认口令」阻断。安全门禁的优先级排反了。

后台路由包含 `device_shutdown`、桌面控制、记忆读写、角色卡编辑。`0.0.0.0` + 已知默认口令 + 不阻断 = 一台在局域网/公网上敞开的远程控制台。**这是本报告里唯一一个「今天就该改」的问题**：默认 host 改 `127.0.0.1`，占位 secret 拒绝作为有效 token，启动时占位口令直接阻断。

### 3.2 【中】真实关机命令由 LLM 工具链驱动

`tool_dispatcher._device_shutdown` 真的 `subprocess.Popen(["shutdown", "/s", ...])`。它 `dangerous=True` 要确认、Path B 黑名单排除——护栏是有的。但「让一个语言模型的工具决策能触发真实关机」这件事本身，风险收益比就不对。probe 分类器一旦误判 + 确认流程一旦有 bug，代价是关用户的机器。这种能力应该默认关闭、config 显式开、且独立于普通工具面。

### 3.3 【小】日志/文案简繁混用、内部编号外泄

`main.py:834` 一句话里「異常」（繁）和「记忆写入」（简）混排；日志里 `event=qq_fast_path_match`、`N7-B`、`Brief 28` 直接打给运维看。对开源项目而言，这些内部工单号和简繁混用是「没做最后一道收尾」的观感。

---

## 四、模块级零碎（按文件）

- **`prompt_builder.py`（1662 行）+ 20000 字符硬裁剪**：token 预算用 `token_estimate > 20000` 这种字符数估算 + 固定顺序裁剪（`event_search → mid_term → diary → episodic → lore`）。字符数不是 token，中英混排误差很大；固定裁剪顺序意味着「lore 永远最后被砍」是写死的策略，无法按本轮相关性调整。1662 行的 builder 本身也该拆。
- **`config_loader.get_config()`（113 处调用）**：每次调用 `stat()` 一次磁盘做 mtime 热加载。单次便宜，但热路径上每轮几十次 stat。更该有的是「一轮之内冻结一次 config」，就像你们已经对 character scope 做了 freeze——同样的问题，config 没享受同样的待遇。
- **`_qq_reality_reply_adapter` 先发送后写记忆**：`text_output.send` 成功后才 `record_assistant_turn`。如果发送成功但 turn_sink 抛异常（3.x 的 fail-open），**用户看到了回复，但这轮对话没进记忆**——下一轮她「忘了」刚说过的话。发送与记忆之间没有事务性，且失败只记 warning。
- **`_do_compress_episode` / 各 handler 的 3 次重试都是整段 LLM 重放**：JSON 解析失败就把整个压缩 prompt 再发一遍，最多 3 次。失败模式（模型不吐 JSON）重试 3 次通常还是同样结果，纯浪费 token。该用 structured output / function calling 约束，而不是「不行就再求一次」。
- **`_sanitize_assistant_message` 80 字阈值 + 正则删括号**：用 `len(content) <= 80` 和一串正则判断「是不是小说腔」。这类风格净化是启发式叠启发式（第三人称检测、括号长度、填充词前缀……），每条规则都在和模型的输出分布打地鼠。CLAUDE.md 甚至专门警告「改这里会导致 style feedback collapse」——**一段没人敢碰的代码，是最需要重写的代码。**
- **`get_probe_prompt` / intent prompt 里塞满角色名和中文规则**：工具决策靠自然语言 prompt 描述规则（「严格规则：只在…命中」），而非结构化的工具 schema 约束。自然语言规则 = 不可测、易漂移。

---

## 五、测试与工程实践（值得肯定 + 隐患）

**值得肯定**：261 个测试文件、`pytest -n auto`、testmon 增量、test sandbox 数据隔离、DLQ、审计脚本、`docs/` 齐全——工程纪律在同类个人项目里是顶配。对家在「局部严谨」上做得非常好。

**隐患**：

- 测试数量掩盖架构问题。261 个测试大多在测「补丁行为是否符合预期」（`test_r2d_defer_queue_dnd`、`test_r6_reality_scrub_audit`……全是工单级测试）。它们锁死了当前的复杂度——**任何一次架构简化都会踩碎几十个测试**，于是没人敢简化。测试从「安全网」变成了「防腐层」，反过来保护技术债不被清理。
- `run_test.py` 会**改写你的 `config.yaml`**（写入再删除 `data_prefix` 行）。用运行时脚本去修改一个受版本控制语义的配置文件，是危险的副作用——测试中断、并发跑两个 session、或写入时崩溃，都可能给你留下一个指向 `test_sandbox` 的 production 配置（你们自己在注释里都标了这是「P0-1 排查过的阻断项」，说明真踩过）。
- `docs/known-issues.md` 维护得很好（H1、ACT-1 等都有据可查），但它证明了一件事：**你们清楚地知道系统里有「设计好了但没接线」的死链路**（hidden_state 现实写入零调用），却让它们带病运行并记录在案，而不是删掉或修好。已知问题清单越详细，越说明团队在用「记录债务」代替「偿还债务」。

---

## 六、如果只做三件事

1. **今天**：`admin.host` 默认 `127.0.0.1`；占位/弱 secret 拒绝作为有效 token 并在启动时阻断（对齐你们已有的 `gating_shadow` 阻断先例）。（§3.1）
2. **本周**：把 `detect_emotion` 挪出 `uid_lock`（锁外算、锁内写），把 `vector_store.query/upsert` 的 sqlite 段丢进 `run_in_executor`。两处都是「不改功能、直接还并发性」的净收益。（§1.1、§1.2）
3. **本季度，且这是真正重要的一件**：砍掉 Path B（`_parse_and_execute_intent`）。工具触发已经有 probe + tool loop 两条路，第三条「反向解析台词去执行」是净负债——它带来的每轮一次额外 LLM、五道守卫、confab 执行风险，都在为一个不该存在的功能付费。删掉它，顺带删掉它的守卫和测试，是你们能做的第一笔「减法」。（§1.3）

---

## 七、给对家（GPT / Codex）的一句话

你把每一个具体问题都解得很漂亮——补丁精准、注释诚实、测试充分、文档周全。你唯一没做的事，是**停下来删掉一些东西**。这套系统不缺聪明，缺的是「敢承认某个抽象是多余的」的那种克制。下一个 Brief，试试让它是一个「删除 Brief」。🐱
