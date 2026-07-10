# Brief 52 · Stage 仲裁信号升级：呼格/第三人称区分 + 问句邻接对 + 关键词自动派生

> 依赖：**50**（改打分必须有 trace 对照）。与 53 同碰 `arbiter.py`，串行执行。
> 参考：Who speaks next?（Frontiers 2025）邻接对——被点名提问者优先接话是人类会话最强规律；
> AutoGen speaker selection 的分层信号做法。
> 现状问题（core/stage/arbiter.py 实读）：只看最后一条消息；`_addressed` 名字 substring
> 命中即 0.9（"叶瑄昨天说的"这类第三人称提及与"叶瑄，你觉得呢"同权重）；
> `_keyword_relevance` 依赖手工维护的 `settings.keywords`，角色卡/lore/identity 里现成的
> 话题信号全没用上；提问无加权。

## 1. 呼格 vs 第三人称提及（_addressed 拆分）

`_addressed` 返回值从 bool 改为枚举：

- `vocative`（呼格，0.9）：`@char_id` / `@名字` / 名字出现在句首且后跟逗号、顿号、空格，
  或名字后紧跟第二人称疑问（"名字你…"）。
- `mention`（第三人称提及，0.3）：名字出现在句中其他位置。
- `none`（0.0）。

`addressed_exclusive` 收窄逻辑只对 `vocative` 生效（被议论≠被点名，不该垄断发言权）。

## 2. 问句邻接对加分

最后一条消息含疑问信号（句尾 ？/吗/呢/么，或"谁/什么/怎么/为什么/多少"疑问词）：

- 且该角色为 `vocative` → 额外 `+0.3`（被提问必答，邻接对）。
- 无人被点名的开放提问 → 全体候选 `+0.1`（提问轮总体更值得回应，配合 53 的沉默阈值）。

## 3. 关键词自动派生（settings.keywords 增强而非替换）

`StageCharacterView` 构建时（已有缓存点）派生该角色的话题词集：

1. 角色卡 tags / description 内的结构化关键词字段（cc 核对角色卡 schema 现有字段取用，不新增字段）。
2. 该角色 LoreEngine 各条目的触发 keys。
3. 该角色对 owner 的 `identity.yaml` 中 `topic_preference` 维度文本切词（n-gram 复用 `text_match`）。

派生集与手工 `settings.keywords` 取**并集**，手工词保留；派生集上限 30 词（防 lore 大库淹没打分）。
缓存随 view 缓存失效（roster 变更时重建）。

## 4. 拍板

- 分值（0.9/0.3/+0.3/+0.1）与派生上限 30 拍死初值，全部命名常量。
- 不引入 LLM want-to-speak 探针：先把规则信号做满，用 50 的 trace 观察两周，
  仍不够再立项混合仲裁（Think-Before-Speak 式廉价内评是那一单的方向）。
- clamp 上限 1.5 不变。

## 5. 测试

表驱动 ≥ 20 条，覆盖：

1. "X，你觉得呢" → X vocative + 问句加分，垄断（exclusive）生效。
2. "X 昨天说的对" → X mention 0.3，不垄断，其他角色仍可参选。
3. 开放提问无点名 → 全体 +0.1。
4. 派生关键词命中与手工关键词命中同权；并集去重；上限 30 截断。
5. 现有 `tests/test_stage*` 仲裁相关断言更新后全绿；trace parts 含新分项名。

## 6. 不做什么

- 不做 LLM 选人 / 内评探针（观察后另立项）。
- 不看倒数第二条之前的历史（recency_penalty 已覆盖近 6 条，够）。
- 不动 talkativeness / peer_reply 权重。
