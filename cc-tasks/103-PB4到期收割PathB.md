# Brief 103 · PB4 到期收割：删除 Path B

> 前置条件：**2026-08-10 之后**执行，且 known-issues PB4 条目零缺口记录。
> 有记录则不执行本单，转为按记录出修复单。
>
> ⚠️ 零记录的有效性要求：观察期内必须**实际触发过**桌面动作场景
> （危险模式窗口内，让角色执行至少各一次 desktop 类动作 + toy_invite），
> 且均由 tool loop 正常执行。没测过 ≠ 没缺口；到期前请茶茶做一轮
> 主动冒烟（对话里自然引导即可，记录日期到 known-issues PB4 条目）。

## 范围

1. 删 `core/pipeline.py::_parse_and_execute_intent` 及调用点。
2. 删 `config.intent_reflex` 配置项（config.example.yaml 同步）、三道守卫、120s 幂等窗口。
3. `toy_invite` 当前沿用 Path B 守卫与幂等窗口（docs/tools.md:451）——迁移到 tool loop / `_push_desktop_action` 正路，不得随删失效。**这是本单最大风险点，先核对 toy_invite 触发链再动手。**
4. 删 `origin="assistant_intent"` 预留分支（docs/tools.md:462）。
5. 删对应测试；docs/tools.md、known-issues 同步更新。

## 验收

- 全仓 grep `intent_reflex`、`_parse_and_execute_intent` 零残留。
- toy_invite 真机验证仍可触发 ToyWindow。
- `pytest -n auto` 通过。
