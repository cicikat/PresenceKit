# T2：切断 Scheduler 通用活动与身份读取依赖

日期：2026-11-23。基线：`6e3701559bb6c023c4056a72f2adda379d64fbea`。
本单仅处理 T2，交付后等待用户协调的独立审查；不合并、不部署，不启动 T3–T9 或 HINT。

## 唯一所有者和调用图

| 旧入口 / 调用者 | 当前所有者与处理 |
|---|---|
| `scheduler.loop.mark_user_active`；main、owner_chat.service | 直接调用 `conversation_activity.mark_user_active`；原转发函数删除 |
| `scheduler.loop._user_active_recently`；gating | 直接调用 `conversation_activity.user_active_recently`；原转发函数删除 |
| `scheduler.loop.last_user_message_time` | 无生产调用者，删除；autonomy 和 gating 原有读取仍直连活动所有者 |
| `scheduler.loop._active_char_id_or_none`；audio、owner_turn、wake_bridge、IME、autonomy/control-center 与相关 HTTP scope | 将原资产读取实现迁到 `character_context.selected_character_id_or_none`；跨业务调用全部直连，无配置 fallback |
| `scheduler.loop._owner_id`；autonomy/control-center、IME 与相关 HTTP scope | 将原配置读取实现迁到 `owner_config.configured_owner_id`；跨业务调用全部直连，保留字符串转换和去空格语义 |
| `scheduler.loop._cfg`；autonomy.effective_state | 直接读 `config_loader.get_config().get("scheduler", {})`，删除为旧测试保留的反向依赖 |
| life_records.settings 中未使用的角色读取导入 | 直接删除，不增加替代调用 |

```text
owner 有效输入（main / owner_chat） → conversation_activity 唯一进程内时间戳
gating / autonomy / consolidation → conversation_activity 读取活动窗口
跨业务 scope → character_context / owner_config → 资产文件 / config_loader
autonomy 开关解析 → config_loader
Scheduler → 消费上述所有者与已有调度状态
```

活动时钟没有新增状态或存储；原本就只有一份时间戳，本单删除的是三个兼容转发入口。
默认窗口仍为 120 秒，比较仍为严格小于，`now` 和自定义窗口契约不变。
Scheduler 内部保留角色与 owner 函数的同对象导入引用，继续服务内部触发器；
没有保留第二份实现、包装函数或状态。跨业务调用这些 Scheduler 私有引用由门禁禁止。
选中角色读取失败仍返回 None，不混用严格活动准入、编辑配置回退和模型路由诊断的不同合同。
`DataPaths.active_prompt_assets()` 既有首次访问初始化仍然有效；文件不可读后的处理没有新增配置回退。

## 删除与门禁

- 删除三个活动转发函数，以及 Scheduler 中 owner/选中角色的原函数实现。
- 将原函数实现迁到领域所有者；测试补丁迁到实际调用的正式所有者。
- 机器清单 `migration-cutover-dependencies.json` 的 26 条精确债务全部联删，现为 0。
- T2 零容忍断言同时检查源码与 JSON；即使旧基线允许历史债务，也不能恢复。
- 子进程故障注入分别恢复活动/owner 依赖和旧清单，验证 CI 断言失败。
- 未增加 facade、fallback、动态加载、白名单例外或持久 trace。

## 验证与影响边界

Python 3.12.13 / Windows；测试使用公共配置和隔离可写沙盒，不使用生产数据。
本地验证及当前提交远端 CI 的最终结果见审查 PR。

- 新增门禁先在旧源码上得到业务边界断言失败，再执行切断。
- 初始活动切断相关回归：612 passed；Smoke：385 passed。
- 扩展到全部 T2 依赖后的相关组：632 passed；新增缺失文件用例最初未隔离
  DataPaths 首次初始化，修正其文件不可用前置条件后，与两项旧补丁回归同组 32 passed。
- 独立架构门禁：133 passed；债务 CLI 对 origin/main 验证 26→0。
- 施工中并行全量：8891 passed、3 failed、13 subtests passed。两项为已迁移的旧配置/角色补丁，
  修复后包含在上述 32 项通过组；另一项为未修改的 character_self 相对路径用例在 Windows
  临时目录跨盘失败，指定仓库内 `--basetemp` 后原用例 1 passed，未改业务或断言。
- 实际时钟写入到 gating 验证：119.999 秒阻断普通候选，120 秒放行；
  另覆盖自定义窗口和 owner 对话真实入口写入。
- 新的角色读取覆盖文件缺失、坏 JSON、空字段、异常字段类型及去空格，禁止回退配置角色。
- 隔离 Admin 实际启动、HTTP 及关闭通过；`/openapi.json` 返回 200，501 个 path，
  未授权 `POST /v1/sessions` 返回 401；无循环导入或启动警告。

本单不改变 HTTP 路由、鉴权、设置字段、默认值、effective state、客户端协议和静态资源。
无需更改管理面缓存版本、控制面文档或客户端仓库；客户端构建和真实设备/模型验收未执行。
没有部署、用户数据迁移或备份。零依赖债务只证明 T1/T2 的已登记边界清零，不代表整轮架构迁移完成。
