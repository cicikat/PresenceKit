# 工单 68：跨世界不变量观测（纯观测侧）+ DESIGN.md 哲学根据章节

> 与 66 / 67 **无依赖，可并行**；任务 3（明信片暗线）**软依赖工单 65**（65 未完成则跳过任务 3）。
> 改动前必读：`docs/dream.md` 合同段 + 设计原则、`core/dream/distill_impression.py`。

## 定位（最重要的一段，违反即打回）

理论出发点：Hoel「过拟合大脑假说」——世界包切换 = 对身份的扰动测试（data
augmentation），跨世界反复出现的行为模式 = 验证过的身份不变量。

**但本单是纯观测/测量机制，不是回流机制**：

- 不变量产物**一个字节都不进任何 prompt**（现实栈和梦境 D 栈都不进）
- 不写 impression_store（否则会延长 `has_active_impressions` 窗口，与工单 66 打架）
- 唯二消费者：管理面板（人看）+ 明信片生成器（工单 65 的信里偶尔一句，仍是出站方向）
- 理由（已与用户对齐）：现实侧 identity 固化已经偏重、LLM 会"对着答案说话"；
  把"他总是X"注入 prompt 是制造刻板化，方向性错误。

## 任务 1：不变量提炼与收敛聚合

- `distill_impression()` 末尾追加一步（独立 LLM 调用或合并进现有调用均可，fail-open）：
  从梦境日志提炼 0–2 条**「情境类型 → 反应模式」**二元组，两段都必须世界无关：
  - 情境类型：世界无关处境（"你退缩躲开的时候" / "被第三方误解的时候"）
  - 反应模式：具体行为（"他没有追问，先退半步等你"）
  - **禁词表**：爱 / 永远 / 深爱 / 命中注定 等抽象情感词（常量维护），逼输出落在行为层
- 独立存储：`data/runtime/dreams/{char_id}/invariants/{uid}.json`
  （走 `core/sandbox.get_paths()`；**不是** impressions/）。
- 收敛聚合：写入前与已有条目做语义相近合并（初版可用 LLM 判同；条目少，成本可忽略），
  命中则 `count+1`、`worlds_seen` 追加本场 world_id、更新 `last_seen`；
  未命中则新建（`count=1`）。字段：
  `situation / response / count / worlds_seen[] / first_seen / last_seen / contradicted_by[]`。
- **矛盾检测**：新梦提炼出与某高收敛条目（count≥3）同情境但反应相悖的行为时，
  不覆盖，追加进该条目 `contradicted_by[]`（记 dream_id + 简述）——这是漂移信号，
  留给人工复盘。

## 任务 2：管理面板「跨世界身份稳定性」页

- `GET /dream/invariants`（admin 鉴权，只读），返回按 count 降序的条目列表。
- admin 面板加一页：收敛条目（count / worlds_seen 徽标）+ 矛盾告警区
  （contradicted_by 非空的条目置顶标红）。
- 价值：把 docs/dream.md 第十节"身份稳定性测试是弱代理，真验证靠实际游玩"
  升级为跨梦自动观测。在 known-issues 对应条目下补一行引用本机制。

## 任务 3：明信片暗线（软依赖工单 65）

- 明信片生成器**可以**读 invariants store：当存在 count≥3 且 worlds_seen≥2 的条目时，
  允许 prompt 模板引用一条，以他的口吻在信里出现**一次**
  （风味参考："不管梦里是什么世界，好像我总是先等你"）。
- 方向仍是出站（梦→用户眼睛），与任务 1 的"不进 prompt"不冲突——
  这里的"prompt"禁令指对话生成栈，明信片生成是离线单向产物。

## 任务 4：DESIGN.md 新章节「梦境系统的哲学根据」

收编四个框架，每个 2–4 句 + 指向对应工程不变式，不展开论文综述：

1. **Winnicott 过渡空间**：梦境=potential space；hard_exit 铁律 + afterglow TTL =
   健康过渡客体必需的"缺席/会醒"结构（回应"AI 模拟容纳却绕过缺席"的批评）。
2. **Huizinga 魔环 / Foucault 异托邦**：入梦/出梦状态机 + 现实窗硬锁 = 工程化魔环；
   "隔离靠没接线不靠过滤"的人文根据。
3. **Evan Thompson**：自我是跨状态过程，非静态实体 → lucid_shared/non_lucid 轴、
   "两层是同一个他"的现象学版本。
4. **Hoel 过拟合大脑**：世界包 = 身份的扰动测试集；跨世界不变量 = 泛化验证——
   注明本系统将其落地为**测量**（本工单）而非回流，并写明理由（prompt 层过拟合悖论）。

## 测试

- 提炼禁词表命中即重试/丢弃的正反例
- 聚合合并 / 新建 / 矛盾追加三分支
- **合同测试（反假绿）**：静态断言 `core/prompt_builder.py`、`core/dream/dream_prompt*.py`
  不出现 `invariants` 读取路径（参考 `test_dream_isolation_guard.py` 的写法），
  配正样本断言 admin router 确实读它。

## 文档

`docs/dream.md`：FUTURE 段落地一条、数据目录补 invariants/、known-issues 弱代理条目补引用。
