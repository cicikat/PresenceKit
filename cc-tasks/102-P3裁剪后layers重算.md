# Brief 102 · P3：裁剪后 layers_activated 重算

> 来源：known-issues P3。独立小单，无依赖，可并行。

## 问题

`core/prompt_builder.py` token 强制裁剪后，`layers_activated` 仍包含已被裁掉的层，观测数据失真（影响 run_eval 与调参判断）。

## 改法

1. 裁剪完成后，从**最终 messages** 重算 effective layers（按 `_layer` 字段收集），作为 `layers_activated`。
2. 新增 `layers_before_trim` 保留裁剪前全集，便于对比。
3. 回归测试：构造超 20k 场景，断言被 drop 层不出现在 `layers_activated`、出现在 `layers_before_trim`；未触发裁剪时两者相等。

## 验收

- `pytest -n auto` 通过；`python tests/run_eval.py` 无异常。
- known-issues P3 条目移入已关闭。
