# 复审补遗 · Brief 33/34 落地后的遗留与回归

> 审查人：Claude（Fable，第三个）。日期：2026-07-10。
> 前情：`critique-fable-20260707.md`（初审）→ `critique-triage-20260708.md`（裁定，Brief 33/34/35）。
> 本轮：核实 Brief 33/34 的实际落地情况，只报**新问题**——初审/裁定已覆盖的不重复。
> 三条全部 file:line 核实过。🐱

---

## 先说做对的（不是客套，是校准基线）

Brief 33/34 大部分到位，值得记账：

- `detect_emotion` 已挪到 `uid_lock` **之外**（`pipeline.py:905` 在 `914` 取锁之前，锁外算锁内写，§1.2 修对）。
- `vector_store` 已 executor 化，且用 `max_workers=1` 单写线程（`vector_store.py:24`）——注释明确点了 `database is locked`，正好堵住了 executor 化最容易踩的并发写坑。这一手很干净。
- `silent_failure` 计数器已接进 `/system` health（`system.py:145-148`），§2.3 的"静默失败要可见"落地了。
- write-before-send 已改（`main.py:818` 先 `record_assistant_turn` 后 `text_output.send`）。

以上是好的。下面三条是这些修复**之后**仍然存在的问题——其中一条正是某个修复自己引入的。

---

## 遗留 1 【严重·部署】P0 只完成了一半：占位符挡了，弱口令没换

**裁定书 §Brief 33 原话**：占位 secret「需脱敏 + 轮换口令」。**脱敏做了，轮换没做。**

核实：

- `admin/auth.py:71`：`if secret in ("", PLACEHOLDER_ADMIN_SECRET): return ""`——只拒绝**占位符**。
- 但真实部署口令是一个弱口令（8 位、可字典命中），它既不是占位符、也没被轮换，因此**仍是一个有效的 admin 全权 token**。auth 挡的是"没改过"，挡不住"改了但改得很弱"。
- 更关键：这个明文口令串**还留在本仓 4 个 tracked 文档**里——
  - `docs/配置改进候选.md:57`
  - `docs/interaction_issues_dedup.md:275`
  - `docs/opensource-v0.1-checklist.md:59` / `:154`

也就是说：后台口令没轮换，而它在你自己仓库的工作区里 `grep` 一下就能捞到明文。`opensource-v0.1-checklist.md` 自己都记着"旧 token 存在于 git 历史中"，却把同一个串又明文写进了当前工作区的 docs。

**这条为什么算新问题**：初审只说了"默认绑 0.0.0.0 + 占位 secret 不阻断"，Brief 33 把那部分修了（host→127.0.0.1、占位符拒绝、启动阻断）。但"轮换 + 从 tracked docs 脱敏"这半件裁定书自己列了却没进任一 Brief 的执行清单，漏在了缝里。

> **注**：用户已在本轮手动改了 `config.yaml` 的口令。剩下的动作是：(a) 确认新口令有足够熵；(b) 把上述 4 个 docs 里的明文串替换成 `<redacted>` 或占位描述；(c) 如果这些 docs 曾进过本仓 git 历史，开源前按 checklist 同款做历史清理。

---

## 遗留 2 【严重】§1.1 executor 化只做了表层，热路径里层仍在同步阻塞

Brief 34 把 `fetch_context` 里**直接**那句 `_vs.query` 换成了 `query_async`（`pipeline.py:281`，✓）。但**同一条热路径、更深一层**的同步 sqlite 调用没动：

- `core/memory/episodic_memory.py:376`：`_vs.query(...)` 是同步调用，它经由 `retrieve()` 在 `pipeline.py:334` **被同步调用**（这一句没有 `run_in_executor`，也没 `await`）。
- `core/memory/event_log.py::search` 内的 `_vs.query`：经 `pipeline.py:297` 的 `event_search_task`（`create_task` 只是换个 tick 执行，sqlite 那段照样在事件循环线程里跑）。

也就是说，§1.1 描述的"同步 sqlite 阻塞事件循环"这个**根问题没有消失**——它只是从你一眼能看到的 `fetch_context` 表层，退到了 `retrieve()` / `search()` 里层。

**为什么"修一半"比"没修"更危险**：commit message 写着 `executor-ize vector_store IO`，下一个人看到这句会认为向量库 IO 已经全部脱离事件循环，不会再去查 `retrieve`/`search` 这两条路径。修复制造了"已解决"的假象，把剩下的阻塞点藏进了没人会再看的地方。

**建议**：要么把 `retrieve()` / `event_log.search()` 整个丢进 `run_in_executor`，要么让它们内部所有 `_vs.query` 走 `query_async`。挑一种,别留半条。

---

## 遗留 3 【严重·今天就中招】write-before-send 的接线方式引入了一个延迟回归

这条直接回应裁定书对初审的核心反驳——「把多用户压测视角套在单用户上，严重度普遍标高一档」。**这条不是压测才暴露的潜伏问题，是唯一那个用户每条消息都在承受的现行体验退化。**

核实链路（`main.py:818` → `turn_sink.py:224` → `pipeline.post_process`）：

1. 裁定书 §四拍板"先写记忆后发送"，方向对（宁可"她没看到但我记得"）。
2. 但落地方式是：adapter 先 `await record_assistant_turn(...)`，而 `record_assistant_turn` 默认 `await_critical_post_process=True`（`turn_sink.py:166/224`），于是它会**完整跑一遍 `pipeline.post_process`**——里面包含那次挪到锁外、但仍最长 8s 的 `detect_emotion` LLM 调用 + `capture_turn`。
3. **然后**才轮到 `text_output.send`（`main.py:854`）。

后果：回复文本早在第 570 行的 `response_processor.process` 就生成好了，却被压在"情绪检测 + 记忆落盘"整条链后面才发出去。**你的唯一用户，现在每条消息都要多等一个 `detect_emotion` LLM 往返（外加落盘）才看到回复。** correctness 修对了，latency 回归了，而且没人在 commit 或裁定里标注这个 tradeoff。

**最刺的一点**：这正是初审 §1.2 批评过的**同一种错误**——把"贵且非必需于当前动作"的东西，焊死在"必须先完成"的关键路径上。§1.2 是把慢 LLM 焊进 `uid_lock`；这次是把慢 LLM 焊进 send 前的必经链。换了个位置，同一个病又犯了一次。

**正确姿势**：send 前只做一次**廉价的同步落盘**——把 raw turn 直接写进 `short_term` / `event_log`（纯文件 IO，毫秒级），这就足以保证"我记得"。`detect_emotion` / `mood_state` / 向量索引这些慢活，全部挪到 send **之后**再异步做。数据持久性靠廉价落盘保证，不需要拿一次 LLM 往返当发消息的门票。

具体到代码：给这条 QQ reality 路径传 `await_critical_post_process=False`，另外在 send **之前**插一个只写 short_term/event_log 的同步 `capture_turn_lite`（或复用现有 capture 但剥掉 detect_emotion）。安全性不降（落盘先于 send），延迟回到修复前。

---

## 一句话给 Fable

Brief 33/34 的执行质量本身是高的——`max_workers=1` 那个细节说明你预判到了并发写坑。但这三条暴露同一个元问题：**"修了"和"修透了"之间有条缝**。P0 漏了轮换那一半、executor 化漏了里层那一半、write-before-send 顺手把一个 LLM 往返塞进了用户的等待路径。每条单独看都是收尾没做满，合起来是同一种手感：补丁在正确的方向上停得太早了一步。遗留 3 尤其值得你回看——它证明初审 §1.2 那类"慢活焊在关键路径上"不是一次性 bug，是一个会换着地方复发的**模式**，值得单独立一条 checklist：*任何要 await 的东西进关键路径前，先问它是不是 LLM/网络调用。*
