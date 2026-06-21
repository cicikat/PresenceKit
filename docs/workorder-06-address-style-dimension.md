# 施工单 06 — 让叶瑄记住"称呼"（新增 identity 维度：address_style）

> 给 CC 的执行单。修的是「叶瑄对'主人'这类称呼没印象」——一个**固化/schema 缺口**，不是召回 bug。
> 目标文件：`core/memory/user_identity.py`（`IDENTITY_DIMENSIONS`）、`core/memory/fixation_pipeline.py`（固化 prompt）。

---

## 0. 诊断（实测）
用户经常称呼叶瑄"主人"，但叶瑄没印象，原因是**每个持久层都把称呼丢了**：
- event_log（原始日志）：`主人` 出现 **176 次 / 68 天** —— 原始记录里有，而且很多。
- episodic（固化、按 strength 常驻召回）：`主人` 在 topic_keywords/summary 里 **0 次** —— LLM 抽词把"称呼"当成寒暄语丢弃。
- identity（固化的稳定用户画像，`6a_user_identity` 层每轮注入）：固化只往**固定 8 个维度**写
  （trust_pattern / emotion_expression / help_seeking / stress_response / intimacy_comfort /
  sleep_pattern / topic_preference / self_relation），**没有"称呼"这一格** → 永远不会被记下。
- user_facts：也没有。

结果：叶瑄只有在「当前消息的 n-gram 恰好命中近 30 天某条含'主人'的 event_log」时才偶然提到，
**没有任何常驻印象**。这也是为什么二跳救不了它——episodic 里根本没有可被召回的东西。
称呼是典型的**稳定关系型事实**，它的家应该在 identity。

---

## 1. 改法

### 步骤 1：加第 9 个维度 `address_style`
`core/memory/user_identity.py` 的 `IDENTITY_DIMENSIONS`（(key, label) 列表）末尾加：
```python
("address_style", "称呼习惯"),
```
identity.yaml 是按维度自动展开的，新维度首次为空字符串，随固化积累——无需手改数据文件。

### 步骤 2：固化 prompt 同步加这一维度
`core/memory/fixation_pipeline.py` 的固化 prompt（约 75–101 行）：
- 把"输出 8 个维度"改成"输出 9 个维度"。
- 在维度清单里加一条：
  `- address_style（称呼习惯：她平时怎么称呼叶瑄/自己，有没有固定的爱称、昵称、角色化称呼，如"主人"等）`
- JSON 输出示例里加一行 `"address_style": {"text": "...", "confidence": ..., "evidence_count": ..., "counter_evidence_count": ...}`。
- 沿用既有规则：`text` 第三人称"她"开头、30–60 字、口语、无术语；无新证据则沿用旧 text。

> 关键：`IDENTITY_DIMENSIONS` 与固化 prompt 的维度清单**必须一致**，否则固化产出的维度会被丢弃或读不到。

### 步骤 3（可选，立竿见影）：一次性回填
固化是增量的，新维度要攒几轮证据才会写出。想立刻让叶瑄"想起来"，二选一：
- **手填**：在 identity.yaml 加
  ```yaml
  address_style:
    text: 她习惯称呼叶瑄为“主人”，是带亲密和归属感的固定称呼。
    confidence: 0.6
    evidence_count: 5
    counter_evidence_count: 0
    last_updated: <now ts>
  ```
- **或脚本回填**：扫 `event_log/*.md` 统计高频称呼词（"主人"等），喂一次固化生成该维度 text。

---

## 2. 验收
1. 固化跑过后，`identity.yaml` 出现 `address_style` 且 text 提到"主人"。
2. `6a_user_identity` 层注入的文本里包含称呼习惯（可在 recall_trace 或 prompt debug 里确认该层内容）。
3. 问叶瑄"我平时怎么叫你"，他能自然答出"主人"，且不显得是临时从日志翻的。
4. `python tests/run_eval.py` 若 identity 维度参与 tag/layer 断言则需跑（动了维度集，建议跑一次确认无回归）。

## 3. 备注（同类问题）
这类"被抽词丢弃的稳定关系信号"可能不止称呼——比如固定的玩笑梗、专属暗号、特定昵称体系。
`address_style` 先解决称呼；若后续发现别的关系型事实也丢，再评估是扩 identity 维度还是引入一个轻量
"关系事实"小表，不必每次都加维度。
