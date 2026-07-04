# CC 任务 22 · pytest 遗留失败修复决策

> 逐条排查了 CC 汇报的剩余失败项，给出根因 + 判定（改代码 / 改测试）+ 具体修法。
> 全部已对照当前源码核实过行号与逻辑，个别标注了"待现场确认"。
> 改完请全量 `pytest` 验证；本任务不涉及 tag_rules，无需跑 run_eval.py。

---

## A. test_dream_impression — I1 结构违规【改代码】

**根因**：`core/pipeline.py:778` 为计算 `_dream_echo` 直接 `from core.dream.impression_store import get_active_impressions`。I1 契约（`test_reality_pipeline_has_no_impression_imports`）禁止 pipeline.py 出现 `impression_store` / `dreams_impressions` / `impressions/` 这三个字符串，唯一合法接口是 `impression_loader`。

**修法**：
1. `core/dream/impression_loader.py` 新增只读 helper：
   ```python
   def has_active_impressions(uid: str, *, char_id: str = "yexuan") -> bool:
       """D2 隔离用：本轮是否存在活跃梦境印象（fail-closed 返回 False）。"""
       try:
           from core.dream.impression_store import get_active_impressions
           return bool(get_active_impressions(uid, char_id=char_id))
       except Exception:
           return False
   ```
2. `core/pipeline.py:776-781` 改为调用 `impression_loader.has_active_impressions(user_id, char_id=char_id)`。
3. ⚠️ 新代码（含注释）里不得出现 `impression_store`、`dreams_impressions`、`impressions/` 字面量——注释写"经 impression_loader 只读检查"即可。

---

## B. r3 scope lint / cleanup contract — 硬编码 char_id 默认值【改代码】

**根因**：三处新增 `char_id: str = "yexuan"` 函数参数默认值，且文件不在 allowlist：
- `admin/routers/chat.py:256` `_probe_and_execute_tools`（触发 `test_admin_no_char_id_defaults`）
- `core/tool_dispatcher.py:972` `execute()`、`1114` `ToolDispatcher.execute()`（触发 `test_no_new_char_id_yexuan_defaults`）

**修法**（三处默认值全部删掉，改为必填 keyword-only；生产调用点已核实全部显式传参）：
1. `admin/routers/chat.py:256`：`char_id: str = "yexuan"` → `char_id: str`。唯一调用点 chat.py:89 已传 `char_id=_frozen_scope.character_id`，无需改。
2. `core/tool_dispatcher.py` 两处签名：`char_id: str = "yexuan"` → `char_id: str`（与 `origin` 同样必填，漏传 TypeError fail-loud，符合该函数 docstring 精神）。
3. 生产调用点（main.py:282/305/474、admin/routers/chat.py:321）已全部显式传 `char_id`，无需改。
4. **测试调用点需补 call-site kwarg**（AST 检测器不管调用点，允许）：以下文件里所有 `tool_dispatcher.execute(...)` / `execute(...)` 调用补 `char_id="yexuan"`：
   - `tests/test_buttplug_integration.py`（2 处）
   - `tests/test_meta_mode.py`（5 处）
   - `tests/test_toybox.py`（3 处）
   - `tests/test_intent_grounding.py`（`_run` helper 内 1 处 + 265 行附近 origin 白名单循环 1 处；注意该文件 283 行的"漏传 origin"用例故意不传 origin，保留）
5. 不要把这三个文件加进 allowlist——那是给存量欠账用的，这里是新增回归，应直接修掉。

---

## D1. test_episodic_temporal · 到期未来事件返回空【改代码 + 改测试各一半】

**根因（两层）**，位于 `core/memory/episodic_memory.py::retrieve`：
1. **小语料 DF 特异性过滤退化**：候选规则要求命中词是 specific（df ≤ max(1, int(0.10*N))）或 kw 命中≥2。测试里 N=2 条记忆都含关键词"考试"→ df=2 > cap=1，非 specific；`ngram_tokens("考试")` 只产 1 个 gram → 两条都进不了候选集 → 走"无词面证据返回空"分支。生产小语料同样会踩。
2. **MIN_SCORE 在到期降权之后过滤**：`score *= 0.3`（expires_at 已过）发生在 MIN_SCORE=0.15 过滤之前。即使进了候选，ep_expired（0.6 强度）算完≈0.072、ep_normal（0.3 强度）≈0.150 临界，全被滤掉。测试意图是"到期事件**降权仍可召回**并渲染为已过去"，当前实现等价于"直接排除"。

**修法**（`retrieve` 内两处小改）：
1. 候选阶段：查询 gram 与 `mem["topic_keywords"]` 中某个条目**完全相等**时视为主证据，直接入候选（策划过的 tag 精确命中不该受 DF 特异性挡；不影响大语料行为）。同时给这类精确命中一个 relevance 下限：`relevance_norm = max(relevance_norm, 0.5)`。
2. 评分阶段：MIN_SCORE 用**降权前**的分数过滤，到期 ×0.3 只用于排序：
   ```python
   base_score = _score_recall(...) + emotion_bonus
   rank_score = base_score * 0.3 if (过期) else base_score
   if base_score >= MIN_SCORE: scored.append((rank_score, mem))
   ```
   验算：ep_normal base≈0.15✓ rank≈0.15；ep_expired base≈0.33✓ rank≈0.099 → 排序 [ep_normal, ep_expired]，与断言一致。
3. **测试也要改一处**（与 pronoun 功能冲突的陈旧断言）：`format_for_prompt` 现在默认把"用户"替换成"她"（见 test_user_pronoun U7/U8），所以 64-66 行断言改为：
   ```python
   assert "她那天要考试" in rendered
   assert "明天要考试" not in rendered
   ```
4. 改完先单跑本文件确认 `test_unexpired_future_episode_remains_normal` 等其余用例不回归。

---

## D2. test_group_router_cc04（2个）+ test_stage_cc05 · hongcha 未注册【改测试基建，代码不动】

**根因**：`characters/hongcha.json` 在仓库里**存在且有效**，注册表本身没问题。真正的问题是 `core/asset_registry.py` 的模块级单例 `_registry` 被其他测试**永久污染**：
- `tests/test_character_avatar_binding.py:80`：`_reg_mod._registry = AssetRegistry()`（直接赋值，在 `monkeypatch.chdir(tmp)` 之后构建、teardown 不还原）
- `tests/test_asset_registry.py:123/135/144/154`：`_mod._registry = registry`（同样直接赋值不还原）

这些 fixture 树恰好只有 yexuan / character_b / yexuanJ-5412 —— 与 CC 观察到的"注册表里只有这三个"完全吻合。全量跑时污染残留 → `group.py` PATCH roster 对 hongcha `resolve` 失败 422；`stage/store.py::_validate_roster` 对 hongcha raise。单跑这些文件应该是绿的（可先验证以确认诊断）。

**修法**：
1. `tests/conftest.py` 加 autouse fixture，每个测试前后重置单例（懒加载，代价极小）：
   ```python
   @pytest.fixture(autouse=True)
   def reset_asset_registry():
       import core.asset_registry as _reg
       _reg._registry = None
       yield
       _reg._registry = None
   ```
2. 顺手把上述两个文件里的 `_mod._registry = xxx` 直接赋值改为 `monkeypatch.setattr(_mod, "_registry", xxx)`（防御性，非必需）。

---

## D3. test_intent_grounding · read_diary【改测试；附产品决策点】【待现场确认】

CC 汇报的"翻上一篇日记"在当前测试文件里不存在（全仓 grep 无此串），疑似转述失真。静态排查最可能失败的是 `test_read_diary_path_a_explicit_requests`：

**根因**：read_diary 是 `persist=True` 工具，P2 已读指纹（`core/memory/tool_read_log.py`，指纹 `diary:{今天日期}`）会在 `execute()` 里去重。该测试用同一个 uid="u1" 连续执行两次 read_diary：第一次成功并记指纹，第二次被 `is_recently_read` 拦截返回"（刚读过这个，这次跳过）"，`_fake_read_diary` 不被调用 → 第二个 `len(read_diary_calls) == 1` 断言失败。测试写于 P2 之前，没考虑指纹。

**修法（推荐，改测试）**：三个子用例各用独立 uid（如 `u_case1` / `u_case2` / `u_case3`），或每次 `_run` 前删除 sandbox 下的 tool_read_log.json。
**产品决策点（可选，留给茶茶定）**：用户明确再次要求"再读一遍日记"时是否应绕过指纹？若要支持，需在 execute() 为 user_live origin 加 bypass 参数——本次不做，先记入 docs/known-issues.md。
**执行前**：先单跑该文件拿到真实失败断言，若与上述不符，把 pytest 输出贴回来再定。

---

## D4. test_prompt_no_internal_leak（3个）· 步数/电量/睡眠【改测试】

**根因**：数据注入**没有被删**，只是措辞在去内部术语重写时改了（正是这个测试文件推动的重写）。现状 `core/prompt_builder.py:595-598/566-573`：
- 步数：`f"{_sensor['steps']}步"`（旧："今日步数"）
- 电量：`f"电量{_sensor['battery']}%"`（旧："手机电量"）
- 睡眠：`f"…共{_h}时{_m}分。…"`（旧：`{_h}小时{_m}分钟`）

L9 回归断言仍在找旧字面量 → 3 个失败。CC"疑似被误删"的判断不成立。

**修法**：更新 `TestRegressionDataNotDeleted` 三个断言为"数据仍在注入"而非旧措辞：
```python
assert "_sensor['steps']" in src          # test_37_steps_still_injected
assert "_sensor['battery']" in src        # test_37_battery_still_injected
assert "{_h}时{_m}分" in src              # test_36_sleep_hours_still_injected
```

---

## D5. test_sensor_sidecar_contract · 窗口标题泄漏【改代码】

**根因**：`core/prompt_builder.py::_format_realtime_awareness`（157-159 行）把 `title_hint` 拼进了 summary（`在看「{title_hint}」`）。这违反 3.9 层的隐私契约（多处测试与层注释都规定只注 app/输入行为摘要）；窗口标题正是 "private-chat-with-alice" 这类隐私泄漏源。全测试套里没有任何用例断言标题应该注入——是单方面的行为变更。

**修法**：删掉 title_hint 注入块（155-159 行连同注释），并把函数 docstring 里 "including window title when available" 改回摘要口径。删除后两个断言用例（`大致在写代码，正在认真输入` / `在用 Code.exe`）经核对拼接逻辑均能通过。

---

## D6. test_slow_queue_scope_payload · 缺 scope【改代码，一行】

**根因**：`core/pipeline.py:821-826` `toy_autogrow` 的 enqueue payload 带了 `char_id` 但没带 `scope`，是唯一漏网的（capture_turn_retry / summarize_to_midterm / user_profile_update / trait_tracker_update 都有）。

**修法**：payload 里补 `"scope": scope_payload,`。handler_toy_autogrow 不读 scope 也没关系，字段向后兼容。

---

## D7. test_topic_freshness · 文案对不上【改测试，一字】

**根因**：`core/scheduler/triggers/time_based.py:875` 文案是 `你忽然想到一件事`；测试断言 `"想到了一件事"`（多一个"了"）。纯措辞漂移。

**修法**：`test_random_message_hint_returns_string_and_marks_topic` 的断言改为 `assert "想到一件事" in result`。

---

## D8. test_trigger_boundary_p0 · trigger 写 short_term【改测试】

**根因**：这是**有意的设计变更**，不是 bug。`core/memory/fixation_pipeline.py::capture_turn`（565-581 行 + docstring）现在区分：
- 会话型触发（`CONVERSATIONAL_TRIGGERS`，含 morning_greeting）：写 **assistant 行**进 short_term 维持上下文连续，user 侧仍不写；
- 非会话型触发：完全不写 short_term。

测试 T2 用 morning_greeting 断言"完全不写"，是旧契约。

**修法**：改 `test_trigger_skips_short_term_append` 为两段：
1. 非会话型触发（用任意不在 `CONVERSATIONAL_TRIGGERS` 里的名字，如 `"hidden_state_decay"`）→ 断言 `short_term_calls == []`；
2. 会话型触发（morning_greeting）→ 断言只有 assistant 行、没有 user 行：
   ```python
   assert all(c[0][1] == "assistant" for c in short_term_calls)
   assert len(short_term_calls) == 1
   ```
   同时保留 event_log assistant 行断言。测试文件头部注释 T2/T7 描述同步更新。

---

## D9. test_user_pronoun（3个）· 渲染空串【改测试】

**根因**：日期腐烂。`TestEventLogRenderCard` 的假日志硬编码 `# 2026-06-21`，`event_log.search` 有规则"7 天外仅保留 intensity>=1 的块"（event_log.py:384）——今天离 2026-06-21 已超 7 天，假日志无 intensity 标记 → 整块被滤掉 → 返回空。该测试写完 7 天后必然变红。

**修法**：`_call_search_with_mock_log` 里改用动态日期：
```python
from datetime import datetime
fake_log = f"# {datetime.now().strftime('%Y-%m-%d')}\n**用户**：天气好好\n"
```

---

## D10. test_recall_trace · trace 应至少 1 条【改测试】

**根因**：失败的是 `test_trace_items_have_required_fields`，它用 `topic=""` 调 retrieve。当前设计里空 topic 不构建词面候选、语义候选也没有（无 query_vec）→ 候选集为空 → trace 为空。生产 fetch_context 永远传非空 topic，空 topic 场景由 `retrieve_fallback` 负责——retrieve 空 topic 返回空是合理设计，测试假设过时。

**修法**：该用例改传 `topic="西瓜"`（与 `_episode` 的 topic_keywords 匹配；验算 base_score≈0.30 > MIN_SCORE 0.15，能出 1 条 trace）。同 class 其他空 topic 用例断言的是"空结果不炸"，不用动。

> 注意：本项与 D1 的 retrieve 改动有交互——D1 落地后单条记忆 + 精确关键词命中会走"主证据"路径，分数只会更高，不冲突。

---

## 建议执行顺序

1. D2（conftest 重置单例）——先消掉污染源，其他文件的全量结果才可信
2. B（char_id 默认值）+ A（I1）——纯机械改动
3. D6 / D7 / D9 / D10 / D4 / D8 ——小改
4. D5（title_hint 移除）
5. D1（retrieve 逻辑）——最需要小心，改完单跑 test_episodic_temporal + test_recall_trace + test_dream_impression 三个文件
6. D3 —— 先单跑拿真实报错，按文档修或回报

全部完成后全量 pytest，把仍红的项连同断言输出贴回来。
