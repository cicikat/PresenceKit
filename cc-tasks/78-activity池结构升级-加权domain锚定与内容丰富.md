# Brief 78 · activity_pool 结构升级：加权 + domain 锚定 + 内容丰富

> 前置：Brief 77 已落地（`fa27d67` growth 聊天出口、`fb085fe` presence 改名、`a881e1e` growth
> practice 混入 presence）。但 77 的 Part 3 只做了「近 20h 有 practice → 概率直注『在练X』」
> （`activity_manager._pick_recent_growth_activity`），**没动池 schema**。
> 本单补齐池本身——它目前约等于占位符结构：每条只有 `id/text/arcs`，无权重、无 domain，
> `_pick_activity`（`core/activity_manager.py:129`）是纯 `random.choice` 均匀抽。

## 现状核实

- 池文件 `content/characters/{char}/activity_pool.yaml`：字段仅 `id/text/arcs`（+ reading 的 `books`、
  一条 `watching_you` 的 `thinking_about_eligible`）。yexuan 池内容有角色味（博尔赫斯、校准感知层、
  时空漫游），但**结构撑不起加权和 growth 对齐**。
- `_pick_activity`：arc 过滤后 `random.choice(eligible)`，**均匀随机，无法强调/压制**。
- `_pick_recent_growth_activity`（77 已加）：只覆盖「真练过 → 概率直注」，**不覆盖**「池里带
  writing 活动、但角色 growth 无 writing 兴趣 → 应压低以免自相矛盾」这一半。

## 1. 🟡 池 schema 升级（向后兼容，新字段全可选）

给 activity 条目加：
- `weight`: float，默认 1.0——arc 内**加权随机**，取代当前均匀 `random.choice`。
- `domain`: `writing|music|drawing`（可选）——声明该活动属哪个 growth 域。不填 = 纯 flavor 活动
  （漫游/喂鸟/校准），不参与 growth 对齐。**池 schema 不收 `other`**：`interest_state._normalise`
  会把所有未知 domain 归一成 `"other"`，池条目若写 `domain: other` 会在角色恰好有任意「other」
  兴趣时被无关上调——两者毫无内容关联。想表达「非成长活动」就留空。
- `thinking_pool`: list[str]（可选）——该活动专属的「想着：…」小观察池，扩展现在只有
  `watching_you` 一条 `thinking_about_eligible` 的贫瘠状态。

**`thinking_pool` 与现有 `thinking_about` 的关系（规格，落地者别猜）**：
- 两者都落到 `state["thinking_about"]`，同一条 activity 同时声明 `thinking_pool` 和
  `thinking_about_eligible` 时，**episodic 来源优先**（`_load_thinking_about` 返回非空用它，
  空了才从 `thinking_pool` 均匀抽一条）——真实记忆 > 静态文案。
- `_PATTERN_WORDS` 的「好像」前缀逻辑是给 episodic 记忆文本设计的，**静态池文本不套**。
  实现上给 state 加 `thinking_source: "episodic"|"pool"` 标记，`get_prompt_fragment` 按来源
  决定是否走前缀分支。
- `get_prompt_fragment` 有 50 字约定，`thinking_pool` 单条建议 ≤15 字，example 里注明。

同步更新 `activity_pool.example.yaml` 字段说明。老池不填新字段 → 行为完全不变（回归保证）。

## 2. 🟡 `_pick_activity` 改加权 + growth domain 对齐

- **加权随机**：把 `random.choice(eligible)` 换成按 `weight` 的加权抽样（复用文件里已有的
  `random.uniform(0,total)` 累加式，见 `activity_manager.py:100-108` thinking 抽样的写法，保持一致）。
  补 `total <= 0` 守卫（全部条目 weight 为 0 时退回均匀抽样），避免除零/空抽。
- **growth domain 对齐**：读 `growth.interest_state.active_interests(char_id)`——
  - 池条目 `domain` 命中角色**真实 active 兴趣** → 权重上调（×1.5）；
  - 池条目 `domain` 存在但 active 兴趣中**无该 domain** → 权重下调（×0.3），避免「此刻在写作」
    但 growth 根本没有 writing 的矛盾；
  - `domain` 留空 → 权重不变。
  - **边界裁决（原稿两条规则打架，以此为准）**：
    - `active_interests` 返回**空列表**（角色没有任何兴趣，含 growth 数据文件缺失）→ 按「无该
      domain」处理，**所有 domain 条目 ×0.3**。理由：防矛盾优先——没有成长数据支撑的练习活动
      本身就是矛盾源，角色禁用 growth（`presence_ext`）的场景也落在这里。
    - 只有**读取抛异常**才 fail-open 退回纯 `weight` 加权（不硬依赖 growth 模块可 import）。
- **`suppress_growth` 覆盖面扩展（本单必做，否则开洞）**：`get_prompt_fragment(suppress_growth=True)`
  目前只抑制 `source == "growth"` 的直注活动（`activity_manager.py:227`），目的是 tags 命中
  `_GROWTH_SELF_TRIGGERS` 时避免与 3.8_growth_self 层双重陈述。domain 锚定的池条目功能上就是
  growth 活动、`source` 却是 `"pool"`，会绕过抑制——聊写作时 3.8 层 + presence 层同时说「在写
  东西」。修法：`switch_activity` 把选中条目的 `domain` 写进 state（如 `state["domain"]`），
  `get_prompt_fragment` 的抑制条件改为 `source == "growth" or state.get("domain")`。
- 与 `_pick_recent_growth_activity` 的关系不变：仍是 `_pick_recent_growth_activity() or _pick_activity()`
  （`activity_manager.py:186`），growth 直注优先，未命中才走加权池。

## 3. 🟢 yexuan 池内容丰富（保留角色味，补 domain 锚点 + thinking_pool）

在现有诗意池基础上**补几条 domain 锚定的练习类活动**（不替换漫游/喂鸟，是给 growth 对齐落点），
并给内省类活动挂 `thinking_pool`。示例（最终措辞可由落地者按角色语气微调）：

```yaml
  - id: writing_practice
    text: "在写一段东西，不确定成不成"
    domain: writing
    weight: 1.0
    arcs: [deep_night, afternoon, evening]
    thinking_pool: ["刚才那句还是太满了", "想换一种更轻的说法"]
  - id: sketching
    text: "在纸上涂一个形状"
    domain: drawing
    arcs: [late_morning, afternoon]
  - id: humming
    text: "在哼一个还没成形的调子"
    domain: music
    arcs: [afternoon, evening]
  - id: deducing        # 注意：yexuan 池已有此 id——这条是给现有条目补 thinking_pool，
    text: "在推演一个可能性"  # 不是新增。落地时确保池内 id 不重复。
    arcs: [afternoon, evening]
    thinking_pool: ["如果那样的话……", "还有一条没试过的路"]
```

诗意条目（reading/roaming/planet/calibrating/watching_sunlight…）保留，`domain` 留空即可。
默认角色（`characters/default`）池同步补最小 `weight`/字段示例，保持开箱可跑。

## 验收

- 池带 `weight` → 加权分布符合预期（统计断言**固定 random seed**，否则容差再宽也偶发红）；
  老池无新字段 → 行为不变（回归）。
- growth 有 writing active 兴趣 → `domain:writing` 条目命中率上升；active 兴趣无 writing →
  `writing_practice` 被压低（×0.3）。
- `active_interests` 为空列表 → 所有 domain 条目 ×0.3（防矛盾断言）；**读取抛异常** → 退回纯
  `weight` 加权，不抛错（fail-open 断言）。两个 case 分开测，行为不同。
- tags 命中 `_GROWTH_SELF_TRIGGERS` 时，`get_prompt_fragment(suppress_growth=True)` 对
  `domain` 非空的池活动同样返回 ""（suppress 旁路回归测试）。
- `thinking_pool` 文本不被加「好像」前缀；episodic 来源优先于 `thinking_pool`。
- 全池 weight=0 → 退回均匀抽样不抛错。
- `_pick_recent_growth_activity` 行为不回归（77 的测试 `test_activity_manager_char_scope.py` 仍绿）。
- 独立 commit：1（schema+example）→ 2（选择逻辑+suppress 覆盖）→ 3（内容），顺序有前置依赖。
