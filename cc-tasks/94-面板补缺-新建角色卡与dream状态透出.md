# Brief 94 · 面板补缺:新建角色卡入口 + dream 状态精确透出

> 背景:异机真机实测(20260718)。与 Brief 93 **可并行**;§2 的状态字段是
> desktop 仓 Brief 34 §3 的前置。

## 1. 🟡 角色卡创作页加「新建角色卡」

- 现状:character 页只能编辑已有角色卡,无从零新建入口。
- 加「新建角色卡」按钮:输入角色 id/名称 → 以 `examples/character_template.json`
  为模板生成 `characters/{id}.json` → 进入现有编辑视图。
- 落盘走 character.py 路由既有的写路径/校验(含 id 冲突检查、非法文件名拒绝);
  新建的卡默认不激活,由用户在现有激活机制里切换(如无激活机制则新建即列出,
  不自动切当前角色)。
- 注意 `characters/*.json` 在 .gitignore 内(默认卡除外),新建卡属用户数据,
  行为正确,无需改 ignore。

## 2. 🟡 dream 状态精确透出(修「正在做梦无法聊天」误导)

- 现状:desktop 把 dream 期间的不可聊状态统一简化成「角色正在做梦因此无法聊天」,
  但 dream guard 的真实状态无法确认,用户不知道等多久、也不知道是不是卡死。
- 施工前先读 `docs/dream.md` 厘清状态机,然后:状态端点(status 路由或 dream
  路由,取现有前端已轮询的那个,避免新增轮询)返回结构化字段:
  `dream_state: idle | dreaming | cooldown`(命名以代码实际状态机为准)、
  `since`、`expected_end`(可给则给,不可预估返回 null)、`blocks_chat: bool`。
- 关键核实点:dream 期间聊天到底是**阻塞**还是**排队/延迟回复**——按代码实际
  行为填 `blocks_chat` 与文案语义,不许沿用想当然的「无法聊天」。若实际是
  排队,字段语义改为「回复会延迟到梦醒」。
- 异常兜底:dream 卡死/超时的情况,端点要能体现(如 `since` 距今远超正常时长),
  desktop 侧才有依据提示「状态异常,可重启后端」。

## 3. 🟢 文档同步

- `docs/dream.md` 补状态透出契约;`docs/backend-integration.md`(如为前端对接文档)
  同步字段定义,desktop 仓 Brief 34 按此实现。

## 验收

- 面板可从零新建角色卡并立即编辑;非法 id 被拒且有提示。
- 手动触发一次 dream:端点状态流转 idle→dreaming→(cooldown→)idle 可观测,
  字段与实际聊天可用性一致;smoke 测试通过。
