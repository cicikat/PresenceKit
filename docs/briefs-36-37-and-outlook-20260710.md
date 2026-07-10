# Brief 36/37 工单 + 聊天质量长期展望

> 来源：`critique-followup-20260710.md`。遗留 1（口令轮换/docs 脱敏）用户自行处理，不在本单。
> 本文档两部分：①供 CC 直接执行的工单；②全局视角的聊天质量发展分析。
> 工单内 file:line 均已于 2026-07-10 复核。

---

## 第一部分 · 工单

**并行性**：Brief 36 与 37 都改 `core/pipeline.py` 但函数不相交（36 改 `fetch_context`，37 改 `post_process`），**可并行**；若同一工作区顺序跑，**建议先跑 37**（用户体感收益最大）。

---

### Brief 36 —— executor 化收尾：episodic retrieve 里的同步 `_vs.query`

**背景（复核结果，与报告有一处出入）**：

- ✅ 属实：`core/memory/episodic_memory.py:376` 的 `_vs.query(...)` 是同步调用，经 `retrieve()` 在 `pipeline.py:334` 被同步调用，sqlite 读跑在事件循环线程上。
- ❌ 已不成立：报告称 `event_log.search` 内也有同步 `_vs.query`——复核发现 `event_log.py:352` 已是 `await _vs.query_async(...)`。**event_log 不需要动。**
- 全仓 grep `_vs.query(`（非 async 形式）仅剩 episodic 这一处。

**方案（二选一，推荐 A）**：

- **A（推荐，改动最小、不破坏约定）**：`fetch_context` 在 `pipeline.py:281` 已经用 `query_async` 拿过一次全源 top-8。再补一次 `await _vs.query_async(..., k=10, sources=["episodic"])`，把结果作为新参数（如 `sem_hits: list | None`）传入 `retrieve()`；`retrieve()` 内 `query_vec is not None` 分支改为消费传入的 hits，不再自己查库。`retrieve()` 保持同步签名，所有 sqlite IO 仍走 vector_store 的单 worker executor。
- **B**：把 `pipeline.py:334` 的 `retrieve()` 调用整体 `run_in_executor`。缺点：`_vs.query` 会绕开 vector_store 自己的单 worker executor，破坏"所有 sqlite IO 串行化"的既定约定（`vector_store.py:22-24` 注释），并需逐一检查 retrieve 内部的线程安全。**除非 A 有意外阻碍，不选 B。**

**验收**：

1. 请求热路径上 grep `_vs.query(`（同步形式）为 0 处。
2. `pytest --testmon` 或指定 `tests/test_*episodic*` 相关测试绿。
3. commit message 明确写"executor 化收尾（episodic）"，不要再写笼统的 "executor-ize vector_store IO"——避免复制"已全部解决"的假象。
4. 同步更新 `docs/memory.md` 相应段落（doc sync hook 会拦）。

---

### Brief 37 —— send 前关键路径剥离 detect_emotion（延迟回归修复）

**背景**：`main.py:824` `record_assistant_turn`（默认 `await_critical_post_process=True`，`turn_sink.py:166/224`）→ 完整 `pipeline.post_process`（含最长 8s 的 `detect_emotion`，`pipeline.py:905-912`，`_DETECT_EMOTION_TIMEOUT=8.0`）→ 才走到 `main.py:854` `text_output.send`。用户每条消息多等一次 LLM 往返。

**目标**：保持裁定书"落盘先于 send"的顺序不变，但 send 前只做毫秒级文件写；`detect_emotion` / mood_state / avatar 推送 / profile 检查 / slow_queue 全部挪到 send 之后异步执行。

**设计要点（CC 核实后自行拍板，结论写进 commit）**：

1. 拆分方式：把 `post_process` 拆成 `critical`（capture_turn + 必要的 uid_lock 内写）与 `slow`（detect_emotion → mood → avatar → profile → slow_queue）两段；或加参数 `emotion_mode="deferred"`。倾向前者，接口更诚实。
2. **emotion 依赖排查（本单核心风险点）**：`capture_turn` 的 docstring 写死"必须在 detect_emotion 完成后调用"（`fixation_pipeline.py:538`），emotion 默认 `"neutral"`。需先查 emotion 的全部读者（event_log 行标注、mid_term 摘要、episodic 提升链）：
   - 若只是 event_log 标注 → critical 段直接用 `"neutral"` 占位，可接受；
   - 若 mid_term/episodic 消费它 → slow 段完成 detect 后再进 uid_lock 消费（slow 段本就要进锁写 mood）。
3. mood_state 挪到 send 后**语义无损**：本轮 prompt 早已 build 完，mood 只影响下一轮。
4. 通道范围：turn_sink 是统一入口（QQ/desktop/scheduler 都走它）。推荐全通道统一改（desktop 同样受益）；scheduler 主动消息无人在等，可保持原样但要在代码注释里写明为什么。
5. 顺手在 `AGENTS.md`「改代码前的强制规则」加一条：**任何要 await 进 send/关键路径的调用，先问它是不是 LLM/网络往返**（对应报告"一句话给 Fable"的 checklist 建议）。

**验收**：

1. QQ 路径 send 前的 await 链中不含任何 LLM 调用（日志时间戳或测试断言"capture 完成即 send"）。
2. 落盘仍先于 send：测试模拟 `text_output.send` 抛异常，short_term/event_log 已有本轮记录。
3. `detect_emotion` 超时/失败不再影响 send 延迟，mood 照常降级 neutral。
4. `pytest -n auto` 相关测试绿；同步更新 `docs/memory.md`（顺序/并发章节）及 `docs/channels.md`（如涉及）。

---

## 第二部分 · 全局展望：聊天质量的长期风险与方向

核心判断先摆出来：架构方向（五层记忆 + tag 门控 + prune + 单 pipeline）是对的。未来质量风险不在"缺功能"，而在三条**慢变量**：摘要腔回灌、identity 刻板化、prompt 配额侵蚀文风。三条都有一个共同解法前提——**度量先行**（见 §5）。

### 1. 降速（延迟）

关键路径 = pre-pipeline probe LLM + get_tags + embedding + 检索 + 主 LLM +（Brief 37 修复前的）detect_emotion。Brief 37 修掉最后一段后，剩余结构性开销是 **probe 与 tags 这两次前置 LLM 往返**。

方向：给每阶段立延迟预算，在 `/system` health 暴露各阶段 p50/p95（现在只有 silent_failure 计数，没有延迟可见性）。probe 的优化不要急着做——先埋点看关键词 fast path 的命中率和 probe 实际触发率/耗时占比，再决定是否值得上小模型或纯规则。**不确定处：probe 的真实延迟占比，无数据前不动刀。**

### 2. 风格坍缩

系统里有三条 assistant 输出反哺自身的回路，坍缩风险从低到高：

- **short_term 回灌**：已有 `_sanitize_assistant_message` 防护，是三条里防得最好的。
- **摘要腔回灌（最慢、最难察觉）**：mid_term / episodic / identity 全是 LLM 写的摘要，"她表达了……的情绪"这类转述腔长期回灌 prompt，角色会逐渐开始**转述自己**，语言同质化。建议：摘要 prompt 强制保留**原话引语与具体名词**（episodic 条目加 raw quote 字段），注入时用"事实 + 引语"体裁而非情绪转述；每 30/60/90 天抽样 episodic 语料做一次腔调审计。**不确定处：污染的实际速率——这正是要抽样的原因。**
- **主动消息（scheduler）**：无用户新输入，全靠记忆自组织，是坍缩风险最高的路径。RC6 的 `recall_policy="none"` 方向正确，值得延伸：主动消息的种子 prompt 应该始终比被动回复更"窄"。

### 3. 生硬 & prompt 过长 → 回答不够生动

12+ 层、20k 字符硬限。两个机制在压平回答：**指令稀释**（层越多，人设/文风指令在总 token 里占比越低，模型注意力被"资料"挤占）和**约束堆叠**（每层都是隐性的"要记得 X"，模型倾向安全、面面俱到、生硬）。

方向（按性价比排序）：

1. **A/B 先行**：用已有的 `prompt_ablation` 做对照——同一批输入，全层 vs 精简层，LLM-as-judge 评生动度。把"prompt 长度 vs 生动度"从直觉变成数据，再决定砍什么。这是本节唯一的前置依赖。
2. **记忆注入体裁改造**：mood_text / afterglow soft hint 已经是"一两句内心闪念"体裁，效果模型消化得动；episodic 注入可以向这个体裁靠拢，从"资料清单"改为软提示。
3. **层预算从截断防线改为主动配额**：现在 20k 是被动 prune；改为记忆层默认小配额、按对话需要（如用户明确在回忆往事）再放开。
4. identity 层从条目列表蒸馏成短叙述。

**不确定处**：DeepSeek 对 20k 字符中段的注意力衰减程度（lost-in-the-middle 效应）没有实测；层排序是否把人设/文风放在了首尾有利位置也值得实验确认。这两点都可以并入 A/B 实验一起做。

### 4. 长期记忆的长期后果

这是单用户陪伴系统特有的问题域——记忆库只增不换用户，错误会复利。

- **错误固化**：identity.yaml 是唯一的主动长期写入者。一次错误观察 → 被后续轮次当事实引用 → 引用又强化它。需要：矛盾检测（新证据与旧条目冲突时降权/标记待复核，而非直接覆盖）、条目带 confidence（provenance_log 已有溯源，缺置信度）。
- **无遗忘的层**：episodic 有 strength decay + 200 上限，mid_term 12h 过期，但 identity 疑似只增改不衰减。若无 aging，三个月后它会变成"角色对用户的刻板印象"，跟不上真人变化。建议 identity 条目加 `last_reinforced` 时间戳 + 长期未强化自动降权/进入复核。**不确定处：identity 当前是否已有删除/降权路径，需查 fixation_pipeline 的 consolidation 逻辑再立单。**
- **检索噪声随库增长**：episodic 200 条 + 30 天 event_log + web 沉淀，库越大语义检索 precision 越低，固定 `MIN_SCORE` 会放进更多边缘命中。建议阈值随库规模自适应，或 top-k 后加一道廉价 rerank（规则即可：时间近 + strength 高优先）。
- **召回错误比不召回伤害大**：角色言之凿凿说错一件事，比"没想起来"对陪伴体验的破坏大一个量级（恐怖谷效应）。episodic 注释里"宁可不说也不强行关联"的哲学是对的，建议把它升格写进 `DESIGN.md`，作为所有新记忆层的准入标准。

### 5. 度量基建（元问题）

现在唯一的质量回归工具是 `run_eval`（只测层激活）。**聊天质量本身没有回归检测**——每次记忆/prompt 改动对生动度的影响全靠体感，这就是 §2-§4 所有"不确定处"的共同根源。

建议单独立一个小 brief：固定 20-30 条真实对话种子，每次大改后重跑生成，LLM-as-judge 按"生动 / 贴人设 / 记忆使用自然度"三轴打分，分数入库画曲线。不求绝对准，求**趋势可比**。有了它，§3 的层配额实验、§2 的腔调审计才有靠山。

### 优先级建议

| 顺位 | 事项 | 理由 |
|---|---|---|
| 1 | Brief 37 | 现行体验退化，每条消息都在付费 |
| 2 | Brief 36 | 收尾债，改动小 |
| 3 | §5 度量基建 | 后续所有质量决策的前提 |
| 4 | §3 prompt A/B + 体裁改造 | 依赖 §5 |
| 5 | §4 identity aging / 矛盾检测 | 慢变量，但越晚做错误复利越多 |
