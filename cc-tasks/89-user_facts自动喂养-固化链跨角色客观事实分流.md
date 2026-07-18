# Brief 89 · user_facts 自动喂养：固化链跨角色客观事实分流（00e A2）

> 背景：DESIGN.md 决策 2 四库宪章给了 `user_facts`（global scope，跨角色客观 KV）席位，
> 但反查调用方发现只有 admin 面板手写（`admin/routers/users.py`），主链零自动写入者——
> 宪章有座位、桌上没饭。本单给两个既有 LLM 固化点加分流输出，不新增 LLM 调用。

## 1. 🟡 两个写入点（复用既有调用，加输出段）

- **consolidate_to_identity**：prompt 增加可选输出段 `global_facts`，格式
  `[{key, value}]`，key 严格限定 `user_facts.py` docstring 的 ALLOWED 集
  （preferred_language / timezone / device_os / project_paths /
  writing_style_preferences / stable_preferences / known_projects /
  tool_usage_preferences），prompt 里给出该清单并声明「角色主观印象/关系/情绪
  一律不属于此段」（与 DENIED 名单对齐——防线双置：prompt 排除 + 代码拒绝）。
- **event_log_salvage**：同样加 `global_facts` 可选输出段（同一 key 白名单）。
  salvage 本来就在提取「仍然为真的持久事实」，其中跨角色客观的那部分顺路分流。

两处均：每次 run 落盘 ≤3 条（超出截断）；LLM 未输出该段 → 零动作（完全可选，
坏 JSON 不影响主产物落盘）。

## 2. 🟡 落盘路径

- 统一走 `update_user_facts(uid, patch)`——模块自带 DENIED 拒绝逻辑，
  `rejected_keys` 非空时 logger.warning（观测 prompt 排除是否漏）。
- 每次成功写入 `provenance_log.append(artifact="user_facts", ...)`（Hard Rule 6；
  注意 user_facts 是 global scope，provenance 落 uid 维度、char_id 用发起固化的角色）。
- 值语义：同 key 覆盖（user_facts 是 KV 不是列表）；LLM 给出与现值相同的 → 跳过不写
  不留 provenance（防噪音）。

## 3. 🟢 注入与观测（已有，确认即可）

- 注入：`5.1_user_facts` 层已存在（prompt-layers 不裁剪表），`format_for_prompt`
  已接——本单零注入工作，验收里确认新写入的 key 出现在层内即可。
- 观测：`admin/routers/users.py` 已可读写——确认 GET 返回完整 facts；缺则补只读字段。

## 验收

- 固化/salvage 输出含合法 global_facts → 落盘 + provenance；含 DENIED key
  （如 nickname/impression 类）→ 被拒 + warning；无该段 → 零动作。
- 同值重写跳过；>3 条截断。
- identity/salvage 主产物行为零回归（global_facts 段解析失败不影响主链）。
- `pytest -n auto`；文档：`docs/memory.md` user_facts 段补自动写入者；
  DESIGN.md 决策 2 表格「唯一写者」列更新（admin 手写 + 固化链分流）。

## Commit 划分

1（identity 分流）→ 2（salvage 分流）→ 3（文档）。1、2 可并行（不同 prompt/handler）。
