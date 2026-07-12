# 工单 67：Scenario 短测试剧本 + 张力弧控制器（对照组）

> 与 65 / 66 / 68 **无依赖，可并行**。**单内任务 2 依赖任务 1**（没有短剧本无法验收张力弧体感）。
> 改动前必读：`docs/dream.md` Scenario 段（v0–v0.8）、`data/dream/scenarios/prison_demo.yaml`。

## 背景

Scenario 模式落地至今用户从未实际玩过——`prison_demo.yaml` 流程太长，没有能
十分钟跑完的短剧本。本单先补一个可玩的短剧本，再在其上做"线性推进 vs 张力弧"
的对照实验（理论依据：Façade drama manager，beat 按目标张力曲线动态选择，
而非线性达标推进）。

## 任务 1：`test_short.yaml` 短测试剧本（前置：无）

- `data/dream/scenarios/test_short.yaml`，**2 个 stage，每个 stage 设计成 2–3 轮可满足**。
- 题材（可微调）：深夜天台偶遇 →
  - stage 1 `rooftop_meet`：他先到，你上来。dramatic_task=自然搭话让气氛落定；
    exit_signs 宽松（比如"用户回应了他的话并停留"级别）。
  - stage 2 `unsaid_words`：他说出一件白天没说出口的事。dramatic_task=完成这次坦白；
    含一个 `drift_pressure`（after_turns: 3，压他主动开口）用于验证机制。
- exit_signs 写得**宽**，目的是让 satisfied 容易达成——这是测试剧本不是内容剧本。
- 验收：真实走一遍 `POST /dream/enter (scenario, test_short)` → 若干轮 → 阶段推进 →
  completed，全程 ≤ 10 轮。

## 任务 2：张力弧控制器（前置：任务 1）

**对照组定位**：不替换现有线性推进，加配置开关做 A/B。

- script YAML 新增可选字段 `arc`：每 stage 一个目标张力档
  （复用 D7 的四档语义：低位/上升中/高位/临界），如：
  ```yaml
  arc:
    rooftop_meet: rising      # 上升中
    unsaid_words: high        # 高位
  ```
- 新增设置 `scenario_arc_mode: linear | arc`（默认 linear，保持现状零风险）。
- `arc` 模式下，DS 层追加"张力导演"块：比较当前 `emotional_tension` 分桶
  （复用 `_bucket_tension()`，**不暴露数值**，遵守设计原则 9）与该 stage 目标档——
  - 低于目标 → 注入升压指令（收紧节奏/推进冲突/靠近）
  - 高于目标 → 注入降压指令（放缓/给喘息/退半步）
  - 达标 → 不注入
- 阶段推进条件在 arc 模式下叠加一条：`satisfied_streak >= 2` **且** 当前张力档
  达到或越过目标档，才推进（防"话说到了但情绪没到"的干瘪推进）。
- ScenarioCore 不加新持久字段（张力本就 dream-local）；只注入当前 stage 的 arc 目标，
  后续 stage 不泄漏（对齐 drift_pressure 的不泄漏原则）。
- 验收：用 test_short.yaml 分别以 linear / arc 各跑一遍，对照体感 + 断言 arc 模式下
  低张力轮 DS 层含升压块、达标轮不含。

## 测试

- arc 字段缺失时 arc 模式退化为 linear 行为（fail-soft）
- 升压/降压/达标三分支的 DS 层注入正反例
- 回归 `pytest tests/test_dream_*.py -n auto`

## 文档

`docs/dream.md` Scenario 段补 arc 字段、`scenario_arc_mode` 设置、推进条件差异。
