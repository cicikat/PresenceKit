# Brief 44 · 记忆质量评测基线（memeval）

> 依赖：无。**建议最先跑**——45/46/47/48/49 都是记忆行为改动，没有这个基线全是盲调。
> 参考：LongMemEval 五能力框架（信息抽取 / 跨会话 / 时间推理 / 知识更新 / abstention），
> 但**不引入 LLM-as-judge**，全部确定性断言，离线可跑。

## 1. 定位

现有 `tests/run_eval.py` 只测 tag→层激活。本 brief 新增记忆召回质量评测：
种子记忆 → 跑 `fetch_context()` / `retrieve()` → 断言"该召回的召回了、不该的没混进来"。

## 2. 结构

```
tests/memeval/
├── cases/*.yaml        # 评测用例（数据即用例，加 case 不改代码）
├── conftest.py         # 复用 run_test.py 的 sandbox 隔离（数据只落 test_sandbox）
└── test_memeval.py     # runner，pytest 收集，也可 python tests/run_memeval.py 单独跑
```

case schema：

```yaml
id: ku-01
category: knowledge_update   # extraction / multi_session / temporal / knowledge_update / abstention
seed:
  episodic: [...]            # 直接写 episodic.json 格式条目（含 occurred_at/strength/topic_keywords）
  profile_facts: [...]       # important_facts dict 条目
  event_log: [...]           # {date, lines}
query: "我上周说的那件事怎么样了"
expect:
  episodic_must_hit: [ep_a]      # id 必须在 retrieve 结果里
  episodic_must_not_hit: [ep_b]  # 不得混入（陈旧事实/已 resolved/无关情绪记忆）
  layers_present: [6c_episodic]
  layers_absent: [6d_diary_context]
```

## 3. 用例覆盖（每类 5–8 条起步，共 ~30 条）

1. **extraction**：跨 30 天 event_log + episodic 的具体事实召回。
2. **multi_session**：同一事实散在多条 mid_term/episodic，聚合命中。
3. **temporal**："上次/之前/那天" 类查询——48 落地前这类 case 允许 xfail 标记，48 落地后翻转。
4. **knowledge_update**：新旧矛盾事实并存时，注入层里不得出现已失效旧值——45 落地前 xfail。
5. **abstention**：种子里没有的事，`MIN_SCORE` 过滤后 episodic 层为空（不瞎召回凑数）。

## 4. 拍板

- 只断言召回/注入行为，不断言 LLM 回复文本；embedding 不可达时 fail-open 走关键词路径也必须过（两种模式各跑一遍，`sem` 权重置零模拟）。
- xfail 用例是 45/48 的验收翻转点：那两张工单完成的定义包含"把对应 xfail 摘掉且通过"。
- 用例数据里不写字面角色名/用户名（Hard Rule 8），uid 用测试专用值。

## 5. 测试

- `pytest tests/memeval -n auto` 全绿（xfail 除外）。
- sandbox 校验：跑完后生产 `data/` 无任何新文件（复用现有 sandbox fixture 断言）。

## 6. 不做什么

- 不做 LLM-as-judge、不接在线 API、不进 CI 必跑集（本地/改记忆前手动跑）。
- 不造新召回接口，只调用现有 `fetch_context` / `retrieve` / `build_prompt`。
