# T3.1：领域依赖解耦与 Pipeline Registry 收口

当前状态（2026-11-26 校准）：T3.1 / T3.1.1 已由 PR #37 合并，合并提交
`d2fe511200b90f55e6dbead72e2a62608b238829`。T3.2 的五项架构遗留仍未完成，
不属于本轮 DIARY-01 / T4 范围；T5–T9 与 HINT 未开始。

以下为历史施工证据：PR #37 初版 Head `72545bae10085be7b1040748e5091f9cd84affbb`；
main `9c72ee01fcf02c4f08918e6d345d22fc6950dcd5`。历史待审查措辞不是当前合并指令。

## T3.1.1 补丁：流式角色 ID 冻结

补丁基线为 `829da9c57c8d84ccce56825ec85fe22e9d835c35`。
生成组件仍保留旧 Pipeline 已有的无效 fallback：未定义 DEFAULT_CHAR_ID 的异常
被 fail-open 捕获，导致无显式 char_id 的 stream-collapse 信号静默缺失。
这是遗留缺陷的收口，不将其描述为 T3.1 新引入的业务回归。

CharacterSession 增加只读取已加载角色的 current_character_id；ReplyGeneration
构造必须接收该 bound method。只有 char_id 为 None 且 user_id 非空时才解析，
在模型流请求之前冻结；同一局部 ID 贯穿模型请求、检测和信号写入。
显式角色与无 user_id 路径不调用 provider；provider 空值或异常在模型降级处理
之外明确抛错，不猜默认角色，不重新解析活动角色。

定向行为测试使用真实沙盒信号文件，并以事件屏障在流式期间切换真实 CharacterSession，
验证旧角色桶有信号、新角色桶无信号。用基线两个流式方法做内存故障注入时，
默认角色用例在“目标角色桶应有信号”断言失败，日志记录 DEFAULT_CHAR_ID NameError。
构造及架构测试拒绝可选 provider、隐式字段、默认 ID、registry 与 Pipeline bound method；
临时真实 Git 仓库中的同款 CI 断言拒绝 fallback / Pipeline 方法注入，撤销后恢复通过。

只修改角色能力、生成组合、该流式路径、两份对应测试及本报告。
不修改 pipeline_registry 或 31 项保留清单，不批量改造生成调用者，不改流式算法、
控制标记、重试和同质判定。当前补丁 CI 以 PR #37 最新 Head 的检查和说明为准。

本地 Python 3.12.13：上述相关回归初跑 336 passed / 2 failed，两个失败均在新增
故障注入测试的临时仓库骨架与错误文本显示；补齐空 Pipeline 文件并显式输出完整
组合依赖错误后，故障注入、角色依赖门禁及新正负向测试重跑 16 passed。
生产行为回归没有失败，基线缺陷复现的预期失败单独记录，不计为通过。
最终以 CI 同款无 conftest 入口重跑完整 Architecture：
`python -m pytest --noconftest -c pytest.ini -q tests/test_ar02_dependency_directions.py`，
197 passed、0 failed，146.60 秒；包含精确 fallback 与 Pipeline bound method 故障拒绝及恢复。

## 唯一所有者与依赖

| 组件 | 改造前 | 当前必需依赖 |
|---|---|---|
| RealityContextReader | 完整 Pipeline | CharacterSession 的 scope 构造、不可变引用快照读取两个能力 |
| ToolTurnRunner | 完整 Pipeline，名为 generator | ReplyGeneration 的模型调用、流式调用、工具协议治理、完成声明治理、反坍缩五个能力 |
| TurnEffects | 完整 Pipeline | CharacterSession 的 scope 构造与活动角色判断两个能力 |
| consistency handler | 可选 runtime，缺失时静默跳过 | 读取被检查角色与按角色条件写入 author note 两个必需能力 |
| turn_sink | 未传 Pipeline 时全局 get | 调用者必须提供 effects；char_id / frozen_scope 显式传入 |

`core/character_session.py` 是角色、世界书和临时 author note 的唯一所有者，
不保存 Pipeline 引用。快照只含这三个检索所需字段，不包含执行器或回指。
`core/owner_chat/reply_generation.py` 是模型生成与输出治理的实际实现，
没有角色状态或 Pipeline 引用。T3.1 迁移时七个生成方法与基线对应方法的 AST 相同；
T3.1.1 仅增加上述显式 ID 解析能力与流式冻结，未重写生成算法。
Pipeline 只组合这些实例并执行 prompt 组装；四个既有字段属性直接读写同一
CharacterSession，不保存第二份角色状态，也不提供旧操作的转发方法。

三个领域组件保存的 bound methods 的 owner 都是上述窄所有者；没有 lambda
捕获完整 Pipeline，也没有 runtime / generator 容器、第二个注册表或缓存。
构造时缺少能力或传入非 callable 会立即失败。

| 直接修改的生产调用者 | 原因 |
|---|---|
| `main.py` | 唯一启动组合，绑定 handler / HTTP 能力，QQ 路径传 effects，生成调用改用实际所有者 |
| `core/owner_chat/service.py` | scope 构造改用唯一 CharacterSession |
| `core/owner_chat/generation.py`、`core/stage/views.py` | 流式/非流式生成改用 ReplyGeneration |
| `core/owner_chat/delivery.py`、`core/autonomy/talk_gate.py` | sink 显式传 effects |
| `core/scheduler/loop.py` | scope、生成和 effects 调用正式所有者，名称经正式 provider |
| `core/companion/service.py` | 已有跨入口 orchestrator 的 scope 调用唯一所有者 |
| `core/dream/reality_continuation.py` | 延后执行的生成及 sink 显式能力传递 |
| `core/character_loader.py` | 热重载委托唯一 CharacterSession，移除重复字段/世界书更新实现 |
| `core/autonomy/runner.py`、`core/dream/dream_pipeline.py`、`channels/qq.py` | 已有角色 loader / name provider 取代直接 registry 查找 |
| `admin/activity_dialogue.py` | HTTP activity route 注入 effects 并传给 sink |
| `admin/routers/chat.py`、`coplay.py`、`lorebook.py`、`observability.py`、`settings_mcp.py`、`settings_prompt_assets.py` | 角色读写 / 世界书重载从启动组合显式传入 |
| `admin/routers/dream_common.py`、`dream_sessions.py`、`dream_rpg.py` | Dream 入口和公共 helper 显式接受角色 reader |
| `admin/routers/dream_archives.py`、`dream_observability.py`、`dream_presets.py`、`dream_worlds.py` | 沿公共 helper 的真实调用链逐层传 reader，避免 helper 内重新全局定位 |

相应既有测试只迁移正式操作所有者、显式依赖和替身签名，保留原业务结果断言。
绕过 main 的老测试使用仅位于 tests 的显式绑定辅助函数，生产模块不导入它。

`main._init_modules` 仍只构造并注册一个生产 Pipeline；慢任务显式绑定
CharacterSession。HTTP 使用既有 FastAPI dependency_overrides 在同一启动点绑定
角色读取、角色重载与 activity effects，闭包只捕获 CharacterSession 或 TurnEffects。
独立 Admin 无运行会话时保持原先不可用/不触发实时重载的语义，未新增可变全局绑定。

## Handler 与角色切换

注册缺依赖、None 或非 callable 时，在任何 handler 注册副作用前抛 TypeError。
初版旧代码对缺失依赖用例出现 DID NOT RAISE；当前通过。

consistency 队列 payload 由 TurnEffects 写入生成时的 char_id。处理前校验其仍为
当前角色，模型返回后再由 CharacterSession.write_author_note 校验同一角色。
队列尚未消费就切换角色，以及模型等待中切换角色，两种情况均不写新角色。
没有 char_id 的旧 consistency payload 会明确失败进入队列既有失败处理，
不会猜测当前角色写纠偏；其他慢任务 payload / DLQ 兼容策略不变。

新增测试走真实 slow_queue 注册、入队、worker、drain 和公开角色热切换；
只替换外部模型边界。断言实际 author note，旧角色任务不得影响新角色。

## Registry 全量审查

使用扩展后的既有架构 AST 检查器重新读取基线全部 698 个生产 Python 文件，
并扫描当前全部 Git 可见生产 Python（包括新增文件、入口脚本、integrations、tools
与 scripts，不以 core/admin 目录列表代替全量）。排除 tests 和 test_ 前缀测试文件、
Git ignored 运行数据与临时产物。

基线 **49** 个调用点，切断 **18** 个，精确保留 **31** 个（30 get、1 register）。
逐点文件、行、函数、操作、调用链、所需能力、分类、决定、理由及退出条件见
[机器可校验清单](t31-pipeline-registry-sites.json)。这不是目录白名单。

切断内容：HTTP 上传、Dream、公用 Dream helpers、陪玩、世界书、MCP 暴露面、
观测和 prompt assets 读取/重载改为显式依赖；QQ、scheduler 名称及 Dream 进入名称
使用既有正式名称所有者；Autonomy 显式 char_id 的卡片读取使用正式 loader；
turn_sink 删除默认 registry 分支。部分名称所有者自身仍有精确保留的无 scope 查找，
这里消除的是业务侧直接全局定位，未把间接调用写成完全无 registry。

保留点主要是尚无显式 scope 的旧 API、后台执行/恢复入口、设备权威检查，以及
必须区别“磁盘已选角色”和“实际已加载角色”的安全或可用性判断。
例如控制面 tool_loop override 必须与实际 policy 使用同一已加载卡片，不能仅为降低
计数改读磁盘选中卡；character deletion 也不能把新选择当成运行实例已退出。
`owner_media_scope` 的 Session 兼容删除属于 T4，本单未提前执行。
Dream retention 的真实入口是 session_control 的唤醒状态机，仍须验证失败硬退出后才能迁移。

## 静态门禁与边界

扩展 `tests/test_ar02_dependency_directions.py`，保留 T0/T1/T2/T2.1/T3 的全部断言。
新增必需构造能力、禁止完整对象和回指、必需 handler 参数、精确 registry 清单一致性、
已清零域不得恢复查找等门禁。覆盖本地导入、导入别名、简单赋值别名、
getattr 字面调用及可识别 importlib / __import__ / sys.modules 动态加载。
窄域拒绝模块动态加载、namespace 反查及已识别回指；组合点拒绝完整对象、
绑定到 Pipeline 的方法、直接或命名闭包捕获。

故障注入在临时真实 Git 仓库中调用 CI 同一个 pytest 断言入口：
新增 integrations 别名 registry.get 后因未审查调用点断言失败；
给 Reader 注入捕获 Pipeline 的 lambda 后因组合依赖断言失败。
分别撤销故障后，同一入口通过。不是仅测试一个孤立匹配函数。

静态分析不执行任意字符串、反射元编程或完整跨函数数据流，不证明不存在一切
动态访问；运行时 owner 检查与实际队列/HTTP 回归补充它，不能相互替代。

## 验证与交付（T3.1 基线记录）

环境：Windows、项目 Python 3.12.13，公开测试资产及函数级可写沙盒。

- 定向检索、工具循环、顺序等回归：99 passed；sink / effects 组：44 passed。
- 首轮全量：81 failed、8846 passed、13 subtests；暴露旧替身/旧绑定与显式参数迁移问题。
  保留业务断言并迁移实际依赖目标，未新增 skip/xfail 或吞异常。
- 失败项所在文件重跑：484 passed；角色队列及 fail-loud 新覆盖：7 passed。
- 架构门禁：`python -m pytest -q tests/test_ar02_dependency_directions.py`，184 passed。
- 最终全量：`python -m pytest -n auto -q --tb=short --junitxml=.tmp/t31-final-full.xml`，
  8955 passed、13 subtests passed、0 failed、0 skipped，498.65 秒。
  TEMP/TMP 指向仓库临时目录；自动 worker 数受本机环境限制为 8。
- 当前 Head 的 Python 3.10/3.12 全量、Smoke、Architecture、Browser、Offline
  验证入口为 [PR #37 checks](https://github.com/cicikat/PresenceKit/pull/37/checks)，
  精确运行链接、计数和最终结果在 PR 说明中记录，旧 Head 检查不作为准入证据。
- 真实 main 初始化、uvicorn Admin 启停、HTTP OpenAPI 200、未鉴权 Session 401 通过；
  OpenAPI 全量和 614 条路由与切换前快照一致（501 paths）。
- 未改静态资源，不触发静态缓存版本变更；`python -m tests.run_admin_browser`
  七组真实浏览器合同通过，覆盖 67 页面且无溢出；API 使用合成边界，实际 HTTP 另行验证。
- 差异检查、逐文件 LF 敏感/忽略 CR 统计一致性检查，生成算法 AST 比较通过。

本单不启动 T4–T9 / HINT，未修改客户端、生产数据、配置字段、默认值、scope 权限或外部协议。
因此不扩展客户端施工或控制面字段文档。未做真实设备或新一轮付费模型验收，
不以自动化测试代替这些验收。31 个暂存 registry 点及其退出条件是明确保留边界，
不宣称已删除 registry。

## T3.2 C1：TurnSink 冻结身份检查（2026-11-27）

原入口先 mark_request_side_effects_started，再 drain artifacts；显式角色、owner、domain
以及 EventContext 与 frozen_scope 冲突均可进入 capture。五种业务负例在旧实现全部失败。
现在入口先核验 Reality owner/character，事件 scope 也必须一致，再开始原有
critical → fanout → deferred 链路。没有从活跃角色、registry 或产物推导身份。

真实 caller：owner_chat.delivery、scheduler.loop、admin.activity_dialogue、
dream.reality_continuation 均已传当轮冻结 scope；后者是出梦后的 Reality 续接。
autonomy.talk_gate 由其已冻结 uid/char 和正式 EventContext 授权，保留既有无 frozen_scope
契约，事件身份仍受入口检查。本项无 caller 签名迁移、无新增 facade/fallback/配置或协议变化。

`tests/test_turn_sink.py` 新负例断言无 side-effect 标记、pending artifacts 不被 drain、
critical/deferred 不被调用且无 channel 发送；原正式 effects 集成断言继续验证角色切换后
旧 turn 写冻结角色桶、另一角色桶为空及 pending 清理。没有放宽旧断言。
Windows / Python 3.12.13：`python -m pytest -n 4 tests/test_turn_sink.py
tests/test_session_scope_cutover.py tests/test_ar02_dependency_directions.py -q --tb=short`
298 passed；`tests/test_event_context_propagation.py tests/test_event_context_identity_boundaries.py
tests/test_event_context_contract.py` 同参数 10 passed。远端 CI 以本项 PR 当前 head 为准。
C2–C5 未完成，T3.2 不标 complete；无客户端协议或配置影响，未部署生产。
