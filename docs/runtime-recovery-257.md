# 257 运行时回归验收记录（2026-09-19）

> 本页是工单 257 R8 的验收与缺口记录，不是“其余功能全部正常”的证明。
> 证据来自复用既有测试、源码核对和运行日志形态；未做完整真机 soak，也不把
> 天气失败或模型超时并入架构回归结论。

## 结论

R1–R7 已按工单独立提交。R8 复用后端与手机既有测试，核对跨控制器/跨接口缺口，
并单独诊断天气网络失败与模型超时。自动化覆盖了冷启动时序、会话失效重绑、
角色归属、历史/附件、生活记录、主动消息落盘、日记/花园/Dream 查询与发送重试
身份；**不能**代替真机冷启动、前后台、重连、撤权或桌面消费者联调。

固定 SHA 三仓 matrix 仍保持上一轮快照。桌面 session-scope 消费者、真机重连/
后台/撤权、手机 owner 共享 `seq` 仍 `open`。

## 本轮复用命令与结果

解释器：项目 `.venv`（Python 3.12）。临时目录指向仓库 `.tmp`。未使用遗留 3.14
pytest。

| 仓 | 命令范围 | 结果 |
|---|---|---|
| 后端 | `pytest` 复用下列文件 | 183 passed，13 subtests passed |
| 手机 | `flutter test` 复用下列文件 | All tests passed（94） |

后端复用文件：`tests/test_weather_proxy.py`、`tests/test_settings_tools.py`、
`tests/test_trigger_boundary_p0_5.py`、`tests/test_session_scope.py`、
`tests/test_session_scope_proposed_fixtures.py`、`tests/test_diary_feeling_api.py`、
`tests/test_dream_ui_endpoints.py`、`tests/test_garden_mood_char_scope.py`、
`tests/test_garden_active_char_fail_loud.py`、`tests/test_admin_mood_active_char_fail_loud.py`、
`tests/test_activity_manager_char_scope.py`、`tests/test_media_ingest_errors.py`、
`tests/test_chat_media.py`、`tests/test_life_records.py`、
`tests/test_observability_endpoints.py`、`tests/test_dream_settings_ownership.py`。

手机复用文件：`test/chat_recovery_upload_test.dart`、`test/session_scope_test.dart`、
`test/profile_appearance_controller_test.dart`、`test/life_records_test.dart`、
`test/dream_regression_test.dart`、`test/chat_history_reconciliation_test.dart`、
`test/mobile_poll_lifecycle_test.dart`、`test/app_shell_structure_test.dart`、
`test/backend_client_error_test.dart`。

R8 验收时发现 `GET/PUT /settings/tools` 投影 `file_access` 会读 `fs_browse`；
天气工单后未给两条旧 settings 用例打桩，导致它们在复用套件里失败。本单只给
`tests/test_settings_tools.py` 补了 `effective_state` stub，不改生产路径。

## 场景对照

| 场景 | 自动化覆盖 | 本轮未做 |
|---|---|---|
| 手机冷启动 | 连接身份先于角色偏好；角色资料后再绑会话与历史；历史失败不标初始化完成；缺角色 `character_unavailable` | 真机冷启动、慢网络、通知启动 |
| 前后台 | poll ack 持久化失败不推进 cursor；同批重投去重 | 真机前后台、Doze、后台通知点击 |
| 历史/附件 | 历史对账、canonical 媒体、失败气泡保留附件并重试 | 换设备、重装、权限失效 |
| 生活记录 | 操作/同步/识别分表；缺 Pillow 为 `dependency_unavailable`；失败可重试 | 真机断网/重启 outbox soak |
| 主动消息 | `capture_turn` 转发 `trigger_source`/`run_id`/`correlation_id` 等审计字段，未知 extras 不打断落盘；poll 生命周期 | 真实 scheduler 主动开口 soak |
| 角色切换 | 本机所选角色不跟 live active；未知/隐藏 `422 character_unavailable`；删除/隐藏保留原选择并报错 | 真机多角色来回切、服务端 active 对打 |
| 日记/花园/Dream | 日记/花园/mood/activity 显式 `char_id`；Dream 独立域，不套 Reality session；入梦/设置 body `char_id` | 真机入梦软挽留、归档失败重试 |

## 天气失败（独立诊断，非架构回归）

257 审计日志只能确认天气请求失败，不能证明由这次整理引入。

已确认路径：

- 工具仍是角色可读的 info 工具 `weather`；`tools.weather.use_proxy` 默认 `false`，
  不继承配置里的全局 `proxy`。管理面「工具」页可打开代理。
- 请求目标是 wttr.in（`format=3` / `format=j1`），aiohttp 总超时 10 秒。
- 历史 `data/logs/error.log` 的 `tool.weather.detail` 主要是
  `ClientConnectorError: Cannot connect to host wttr.in:443` 与 `TimeoutError`。
  形态是出站连接/超时，不是 257 的会话、角色或依赖回归。
- 旧问题 PROF-1（`user_profile.location` 反抖动锁死导致天气查旧城）已于 2026-07-25
  关闭，与本次梯子失败不是同一条链。

仍 `observe`：

- `aiohttp.ClientSession()` 默认 `trust_env=True`。配置层直连已生效，但进程环境里的
  `HTTP_PROXY` / `HTTPS_PROXY` 仍可能把 wttr.in 送进梯子。这与 LLM client /
  MCP / 硬件 session 的 `trust_env=False` 不一致，未在本单改代码。
- 10 秒超时、HTTP 非 200、出站闸门 `assert_outbound_allowed("weather")` 仍可让工具
  返回失败文案或空详情。实网 wttr.in 未在本轮验收。

## 模型超时（独立诊断，非架构回归）

调用分类超时在 `core/llm_client.py`：probe 15s，intent / detect_emotion 10s，
summary / consolidation 30s，chat 90s，vision 30s，perform / monologue 10s，
scenario_reconcile 8s，event_edge_proposer / rpg_kp 30s；缺省 90s。httpx 连接
超时 10s。无显式 proxy 时 LLM HTTP client 使用 `trust_env=False`。

运行日志里的超时主要落在记忆慢队列，而不是主聊天回合：

- dead-letter：`consolidate_to_identity`、`reflect_to_episodic`、`practice_session`
  出现 `httpx.ReadTimeout` → `openai.APITimeoutError`。
- `error.log` 另有 `detect_affection` 同类超时（走 detect_emotion 10s 预算）。
- 较早样本的 traceback 指向遗留 3.14 解释器；较新样本已落在项目 `.venv` 3.12。
  两者都是上游读超时，不能用来断言 257 引入了新的超时逻辑。

结论：这是既有 LLM / 网络延迟与分类超时预算下的运行失败。本单不改超时数字，
也不把它写成架构回归。记忆固化 soak 与主聊天 90s 超时的真机表现仍 `observe`。

## 仍未完成

| 项 | 状态 | 说明 |
|---|---|---|
| 桌面 session-scope 消费者 | `open` | 后端 `v1` 已签发；桌面接入未在本轮施工 |
| 固定 SHA 三仓 matrix | `open` | `.github/protocol-matrix.json` 保持上一轮快照 |
| 真机冷启动 / 前后台 / 重连 / 后台 / 撤权 | `open` | 自动化不能代替设备 soak |
| 手机 `seq` | `open` | 仍是 owner 共享游标，不能按角色跳过后 ack |
| 天气实网 | `observe` | 直连开关已测；wttr.in 与环境代理未实网验收 |
| 模型超时 soak | `observe` | 分类超时已核对；记忆固化与主聊天超时未 soak |

接口总账见 [three-repo-interface-catalog.md](three-repo-interface-catalog.md)。
控制面天气开关见 [feature-control-surface.md](feature-control-surface.md)。
会话合同见 [session-scope-contract.md](session-scope-contract.md)。
