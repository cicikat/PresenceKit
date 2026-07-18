# 研究备忘 · Prompt 对模型输出的影响(支撑 Brief 72/73)

写于 2026-07-15。这份是 Brief 72(换行硬兜底)、73(合规率 eval)背后的文献依据,
按三条研究方向整理,附引用。给之后接手的人和茶茶留个索引,免得结论被单一来源带偏。

---

## 0. 一句要先纠的偏

早期口径"DeepSeek 指令遵循比 Claude 弱"来自厂商对比博客,是单一来源、过度结论。
学术图景更微妙,两个反证:

- **格式敏感性是全模型现象**。FormatSpread(Sclar et al., arXiv 2310.11324)证明:仅
  语义无关的格式扰动(分隔符/空格/大小写),就能让同一模型在同一任务上摆动**最高 76 个
  准确率点**;且"哪种格式好"在模型间只有弱相关。→ 用单一固定 prompt 判"A 比 B 听话"
  方法论上站不住,正确表述是"在这批格式分布下 A 的合规率区间低于 B"。
- **指令层级失效也是全模型现象**。"Control Illusion"(arXiv 2502.15851)测下来,主流模型
  都无法可靠执行"高优先级指令压过低优先级",不是某一家的短板。

结论:换行不生效,不该归因于"DeepSeek 笨",而是格式约束在当前 LLM 上普遍是弱约束。
→ 支持 72 号"prompt 侧已榨干,上生成后兜底"的路线。

---

## 1. 指令遵循 / instruction hierarchy

- **官方层级规范**:OpenAI 2024 Model Spec 定义 system > developer > user > 工具/第三方内容,
  并新增 developer 角色夹在 system 与 user 之间([OpenAI](https://openai.com/index/the-instruction-hierarchy/))。
  但如上,实际执行不可靠,别指望"写进 system 就压得住 history/user"。
- **位置偏置(直接支撑 Brief 72 §3 指令后移)**:"Lost in the Middle"(Liu et al., TACL,
  [MIT Press](https://direct.mit.edu/tacl/article/doi/10.1162/tacl_a_00638/119630/))证明上下文
  利用呈 **U 形**——开头(primacy)、结尾(recency)记得牢,中间几乎等于没给。**recency 在
  生成/摘要任务上更强,primacy 在分类/选择题上更强**。我们是生成任务 → "换行指令后移吃
  recency"这条路对味。当前换行句在层11 author_note,后面还压着 post_history/pinned/
  time_hint/user_message,落在相对弱的"次末"位置。
- **格式敏感性方法论(支撑 Brief 73)**:FormatSpread 建议评测 prompt 时**报告跨格式的性能
  区间**而非单点。73 号 eval 采纳此姿势。

## 2. 长期记忆 / persona 一致性

- **Persona drift(直接命中我们的痛点)**:研究识别激活空间中的 "Assistant Axis",模型在长
  对话中沿它漂移,合成多轮对话 **10–15 轮即掉 20–40%** 人格投影
  ([persona drift 综述](https://www.emergentmind.com/topics/persona-drift);
  [arXiv 2512.12775](https://arxiv.org/pdf/2512.12775))。**关键**:目标/任务型指令会**加速**
  漂移(把模型从 persona 上拽走),纯 persona 导向对话漂移小,脚本化交互几乎不漂移。
  → 我们 prompt 里堆的大量格式/工具/约束指令(【输出格式】【词级强调】【工具结果】【表达
  规则】…)本身在和"人味"抢注意力、加速漂移。这给 #4"层瘦身"提供了第二个理由:砍冗余
  不只省 token,是给 persona 让出注意力。
- **RAG vs 长上下文**:非谁碾压谁。强长上下文模型能吃满完整信息流、常优于 RAG(RAG 有上下文
  碎片化/信息丢失);但超长语料下高 top-k 的 RAG 又能靠压缩召回捞回被截断证据
  ([ChatQA2](https://arxiv.org/pdf/2407.14482))。我们的五层记忆=结构化 RAG+工作记忆,踩在
  正确折中点。
- **个性化基准**:[LaMP](https://github.com/LaMP-Benchmark/LaMP)(7 任务)、
  [LongLaMP](https://arxiv.org/html/2407.11016v1)(个性化长文本生成)。数据点:RAG 式个性化
  在 LaMP 上比无个性化提升约 15%,叠加 PEFT 到约 16%。

## 3. Prompt 影响的实验验证

- **自动指标(客观约束)**:IFEval(arXiv 2311.07911)——可验证指令,strict/loose 四档。换行属
  客观可验证约束,73 号 eval 走此路,scorer 的 loose 复刻生产 S4 判据。
- **LLM-as-judge(主观质量,如 voice)**:好判官与人类 **>80% 一致**,但有四个系统性偏置必须
  治理——**位置、冗长、自我偏好、权威**([Galileo](https://galileo.ai/blog/llm-as-a-judge-vs-human-evaluation))。
  实操:pairwise 比 pointwise 可靠但需跑两个方向消序偏置;抽查 5–10% 判官结论对齐人工。
  → 评"换 few-shot 后 voice 崩没崩"用得上。
- **A/B + 报区间**:结合 FormatSpread 教训,任何 prompt 改动结论都应跨多 seed/格式给区间,
  别单点下结论。

---

## 与本仓库的落点对照

| 研究结论 | 对应动作 |
|---|---|
| 格式约束是全模型弱约束、prompt 侧边际递减 | Brief 72 §1 生成后硬兜底(默认关) |
| 位置偏置 U 形,生成任务吃 recency | Brief 72 §3 换行指令后移实验 |
| few-shot "禁止复用"可能被泛化成"连格式也别学" | Brief 72 §2 few-shot 修正 |
| 任务指令加速 persona drift | #4 冗余层瘦身(用检视器+消融量化) |
| 评测应报区间、客观约束用可验证指标 | Brief 73 合规率 eval(strict/loose) |

## 主要引用

- FormatSpread / 格式敏感性:arXiv 2310.11324
- Control Illusion / 指令层级失效:arXiv 2502.15851
- OpenAI Instruction Hierarchy / Model Spec:openai.com/index/the-instruction-hierarchy
- Lost in the Middle / 位置偏置:TACL(MIT Press)
- Persona drift:emergentmind persona-drift;arXiv 2512.12775
- RAG vs 长上下文:ChatQA2 arXiv 2407.14482
- 个性化基准:LaMP;LongLaMP arXiv 2407.11016
- IFEval:arXiv 2311.07911
- LLM-as-judge 偏置:Galileo blog
