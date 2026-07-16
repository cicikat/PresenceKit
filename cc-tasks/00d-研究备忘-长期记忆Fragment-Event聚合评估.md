# 研究备忘 · 长期记忆 Fragment → Event Aggregation 可行性评估（2026-07-16）

> 触发：评估一个「底层碎片记忆（fragment）+ 后台周期聚合成长期叙事事件（event/storyline）」的
> 长期记忆设计在 PresenceKit 当前架构中的可行性。结论先行，附学界定位与落地建议。**本备忘不含工单**，
> 决策后再拆单。
>
> **修订 2026-07-16（代码对照复核）**：修正三处与实际架构不符的判断（挂载点、
> memory_digest 重叠、provenance_log 用途），补两个拆单前必须裁决的前置问题
> （增量/快照策略、event_log 来源污染——后者已拆 Brief 79 作为前置单）。
> 全部 arxiv 引用已逐一点开验证存在、标题相符。

## 一句话结论

方向对、也是主流；但**「Fragment 层」在 PresenceKit 里已经存在两份**（`event_log` + `episodic`），
且 **Event 侧也已有雏形**（`digest_evicted_episodes` 产出的 memory_digest「时期摘要」，Brief 46，
v1 只归档不进 prompt）。真正的缺口是**「主动的、按弧线组织的 storyline 聚合 + 召回」**。所以它应
作为**新增的一层**（不是替代 mid_term/episodic/identity），且必须**严格只加一层**、复用现有碎片
底座、挂 scheduler 闲时触发器（不是逐轮慢队列，见 §三.2），并**吸收或合并 memory_digest**（见
§三.1a），否则正中「记忆层过度复杂」的担忧。

## 一、学界定位（这套设计不土，踩在主流上）

「底层碎片 + 定期聚合成高层叙事」就是 Generative Agents（Park 2023）的 **observation → reflection**
经典结构：原始记忆流之上定期跑 reflection，把碎片综合成高层洞察。近两年演进都在这条线：

- **episodic → semantic 巩固**：把时间戳事件流固化成语义知识是公认路径；有专门工作研究怎么固化而
  **不漂移身份**（directly 关系到 `identity.yaml`）。
- **事件分割（Event Segmentation）**：ES-Mem / SeCom 指出——**用固定对话轮数当存储单元会切碎语义**，
  应按语义/事件边界分段。对 PresenceKit 尤其相关（event_log 目前逐轮落）。
- **聚合与遗忘要有筛选**：树状聚合是常见做法，但多篇警告「无差别聚合会传播错误、拖垮长期表现」，
  必须**基于效用（utility-based）的保留/删除**。
- **类型化记忆防坍缩**：*Provenance-Role Collapse* 一文指出长期 agent 里不同来源/角色的记忆混在
  一起会坍缩——PresenceKit 已有 `provenance_log`，可顺势做类型隔离。

## 二、映射到 PresenceKit 现有五层

| 提案概念 | PresenceKit 现状 | 判断 |
|---|---|---|
| **Fragment（发生了什么）** | `event_log`（每轮事实、日度 .md、30 天窗）+ `episodic`（离散记忆、强度衰减、上限 200） | **已存在，甚至两份**。别再建第三个碎片库 |
| 工作缓冲 | `mid_term`（每轮 LLM 压缩、12h、3 时段桶） | 功能是「近期压缩」，非叙事，**保留** |
| 稳定语义 | `identity.yaml`（fixation 固化） | 「用户是谁」的稳定属性，**保留** |
| **Event/storyline（长期形成了什么故事/模式）** | **雏形已存在**：`memory_digest.md`（episodic 淘汰批次 → LLM 压缩「时期摘要」，Brief 46 §1，被动触发、v1 不进 prompt、不可召回） | **半缺口**：缺主动聚合与召回，不缺「压缩成叙事」这个动作本身 |

关键洞察：提案里 **Fragment 那一半已经实现**（event_log + episodic 就是碎片底座）。若照搬提案再建
一个 fragment store，就是重复建设——这恰是「过度复杂」的来源。而 `identity.yaml` 存的是「用户是个
什么样的人」（稳定属性），**不是**「职业方向转变」这种**有时间弧线的叙事**。

**但 Event 侧不是全空白**：`digest_evicted_episodes` 已经在把 episodic 淘汰批次压缩成「时期摘要」。
它与 storyline 的差异只在触发方式（被动淘汰 vs 主动周期）与可见性（纯归档 vs tagged 召回）。
storyline 立项时必须**二选一**：把 memory_digest 吸收为 storyline 的输入之一，或把 digest 逻辑
合并进 storyline aggregator 并废弃独立产物。不裁决就落地 = 两个互不知情的长期叙事产物并存，
正是本备忘警告的重复建设。

另注：碎片/半长期库的真实基数**不止两份**——user_hidden_state、user_facts、trait、diary、
vector_store（web 沉淀）、memory_digest 都在。按工作惯例（每若干功能 brief 排一个删除 brief），
storyline 立项前建议先排一轮记忆面的删除/合并盘点。

## 三、落地建议：加一层，不替换，挂 scheduler 闲时触发器

1. **只加 `storyline`（叙事事件）一层**，坐落于 episodic **之上**，由后台周期 reflection 生成。
   数据源直接读现有 episodic/event_log 碎片，**不新建碎片库**。

   1a. **前置裁决：与 memory_digest 的关系**（见 §二）。推荐：storyline aggregator 吸收
   `digest_evicted_episodes` 的职责——淘汰批次作为 storyline 的一种输入事件，`memory_digest.md`
   随之退役（连同其测试与文档条目，按删除 brief 审计原则）。

2. **挂 `core/scheduler/triggers/` 闲时触发器，不挂固化慢队列**。
   ~~原稿建议「在慢队列末端并一个每周步骤」~~——**这是错的**：slow_queue 是**逐轮驱动**的
   （capture_turn → mid_term → episodic → identity 全是 per-turn handler），周频节奏在这条链上
   没有触发器。正确落点是 scheduler trigger，先例现成：`memory_janitor`（闲时整合 pass）、
   `hidden_state_decay`（7d consolidate tick、stamp_trigger、不发言）。`aggregate_storyline`
   照 `hidden_state_decay` 的模式做周频 stamp_trigger 即可。频率必须远低于逐轮，否则重复
   re-summarize 引入漂移与成本。

3. **和 identity 划清类型边界**（最关键）。identity = 「他是谁」（属性），storyline = 「他在经历
   什么弧线」（时序叙事）。否则「职业方向转变」会在两层各写一份、互相打架（即 Provenance-Role
   Collapse）。**实现手段修正**：~~原稿说「借 provenance_log 做类型隔离」~~——provenance_log 是
   append-only 审计 JSONL（fire-and-forget、吞异常），只能事后追溯、**做不了运行时隔离**。真正的
   隔离在**写入路由层**实现：storyline writer 与 `consolidate_to_identity` 各自的 prompt/schema
   明确排除对方的内容类型（identity 固化 prompt 里显式排除「时间弧线叙事」，storyline 聚合
   prompt 里显式排除「稳定属性断言」）。provenance_log 照 Hard Rule 6 正常 append 留痕即可，
   它是审计不是闸门。

4. **前置裁决：增量式 vs 快照式**（拆单前必须定，这是设计核心不是细节）。碎片底座是**易失的**：
   event_log 30 天窗、episodic 衰减 + 上限 200 淘汰。周频聚合想覆盖「职业方向转变」这种数月弧线，
   只有两条路——
   - **增量式**：每次聚合读「旧 storyline + 新碎片」再写回。能跨月，但这就是反复 re-summarize，
     正是上文警告的漂移源；需要配套约束（如：已固化的弧线条目只允许追加新节点、不允许改写旧节点）。
   - **快照式**：只聚合窗口内碎片。无漂移，但只能看见 ≤30 天的弧，「storyline」名不副实。
   推荐增量式 + 「旧节点只读、只追加」约束；无论选哪个，工单里必须写明。

5. **前置单：event_log 来源污染（Brief 79，独立于 storyline 也该修）**。web_recall / dream_echo
   轮次被 `handler_summarize_to_midterm` 刻意跳过 mid_term/episodic/identity 固化，但
   `capture_turn` 已把这些轮写进 event_log，且 event_log 条目**无任何来源标记**、
   `event_log_salvage` 也**无过滤**——被来源隔离设计刻意排除的外部信息，今天就有一条经
   salvage → important_facts 重新固化的通路。storyline 若直接读 event_log 会把这个洞放大成
   长期叙事污染。先落 Brief 79（event_log 来源标记 + salvage/未来聚合器统一过滤），storyline
   复用同一标记。

6. **注入侧照本项目已确立的共识：relevance 门控，不常态**（同 dream 印象 / watch / growth 的
   tagged 模式）。storyline 只在对话触及该弧线时召回相关那条，别每轮刷。要进 `build_prompt` 的
   `_layer` + `_drop_priority` + 20k 裁剪链。

7. **碎片粒度顺便升级**：event_log 现逐轮落（固定轮数单元），按 ES-Mem/SeCom 结论会切碎语义。
   storyline 聚合时别按时间桶，按**事件边界**聚。属「顺手做对」。

## 四、过度复杂风险：真实但可控

现状已是 5 层记忆 + dream 余韵 + coplay + growth（外加 user_hidden_state / user_facts / trait /
memory_digest 等半长期库），无脑加 fragment+event 两层会失控。**控制阀**：只加一层 storyline、
碎片复用现有、吸收 memory_digest（净层数不增）、聚合挂 scheduler 周频 trigger、写入路由层类型
隔离、relevance 注入、来源过滤复用 Brief 79 标记。做到这几条，净增复杂度 ≈ 一个新 store + 一个
scheduler trigger + 一个 tagged 注入层，与近期 dream/growth 的改造模式完全同构，不是新范式。

**拆单顺序**：Brief 79（event_log 来源标记，独立价值）→ 增量/快照裁决 + memory_digest 归并裁决
（决策，不写码）→ storyline 主单。

## 五、裁决结果（2026-07-16，Brief 79 落地后拍板）

Brief 79（event_log 来源标记 + salvage 过滤）已实现并提交（`56d4c28` / `b9c7e35` / `e789f1e`），
storyline 主单的前置条件已满足。以下两项裁决按 §三 建议直接采纳，理由不变，不再展开论证：

1. **增量式 + 旧节点只读只追加**。快照式会让「storyline」名不副实（≤30 天窗盖不住数月弧线），
   代价是漂移风险，用「已固化节点只允许追加新节点、不允许改写旧节点」这条硬约束吃住。
   storyline 主单必须把这条约束写成可测的验收项（例如：对同一 arc_id 的旧节点做写入尝试应被拒绝
   /忽略，只有 append 新节点这条路径可写）。
2. **memory_digest 归并：storyline aggregator 吸收 `digest_evicted_episodes` 职责，
   `memory_digest.md` 退役**。具体落地形状（storyline 主单需覆盖）：
   - episodic 淘汰批次（`episodic_memory.py:225` 的 `slow_queue.enqueue("digest_evicted_episodes", ...)`）
     改为喂给 storyline aggregator 作为一种输入事件，而不是继续写 `memory_digest.md`。
   - `fixation_pipeline.py` 里的 `digest_evicted_episodes` / `handler_digest_evicted_episodes`
     （§1026-1113）随之下线；`pipeline.py:1731/1738` 的 handler 注册一并移除。
   - 存量 `memory_digest.md` 文件按删除 brief 惯例处理（迁移或只读归档，不强行删数据），不在
     storyline 主单范围内新增读取逻辑。

拆单顺序更新为：~~Brief 79~~（已完成）→ storyline 主单（可直接开工，两项前置裁决已定）。

## 六、推荐先读

- Park et al., *Generative Agents*（reflection 原型）
- *Episodic-to-Semantic Consolidation Without Identity Drift*（几乎正面拆解 `identity.yaml` 会遇到的坑）
- ES-Mem / SeCom（事件边界分割，修正逐轮 event_log 的语义切碎）
- *Mitigating Provenance-Role Collapse via Typed Memory Representation*（类型隔离，配合已有 provenance_log）

## 参考来源

> 2026-07-16 复核：以下链接已逐一访问验证，全部真实存在且标题相符（ES-Mem / HiMem 摘要页可读，
> 其余 PDF 可达）。

- Generative Agents / Memory Mechanisms in LLM Agents — https://www.emergentmind.com/topics/memory-mechanisms-in-llm-based-agents
- Episodic-to-Semantic Consolidation Without Identity Drift — https://arxiv.org/pdf/2607.01988
- Episodic-Semantic Memory Architecture for Long-Horizon Agents — https://arxiv.org/pdf/2605.17625
- ES-Mem: Event Segmentation-Based Memory — https://arxiv.org/pdf/2601.07582
- Mandol: Agglomerative Agent Memory System — https://arxiv.org/html/2606.29778
- Mitigating Provenance-Role Collapse via Typed Memory Representation — https://arxiv.org/pdf/2605.25869
- HiMem: Hierarchical Long-Term Memory — https://arxiv.org/pdf/2601.06377
- Agent-Memory-Paper-List（survey）— https://github.com/Shichun-Liu/Agent-Memory-Paper-List
