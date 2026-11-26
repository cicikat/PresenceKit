# T4 媒体与生成图引用的冻结作用域修补

日期：2026-11-26；隔离回归发现并修补；不涉及生产部署。

## 实际修改与依赖

- `core/chat_media.py`：全局 inbox/cache 只负责存储，不授予读取权。读取须有同 uid、char_id、reality
  的未墓碑事件媒体引用，或当前角色正式上传资料索引。保留原有角色资料库 raw blob 路径。
- 新上传在回复/事件落盘之前已有本角色上传索引，因此即使 retain_raw_uploads=false，也仍能
  在本轮作为图片参考；没有添加无 Session 或 active/default fallback。
- `core/image_gen.py::image_reference_bytes`：生成图 artifact ID 只在调用者 uid/char_id 的正式索引中
  读取，删除以全局 ID lookup 返回的 owner 代替调用者身份的逻辑。
- 未授权的 inbox 媒体与不存在媒体均返回原有 410 响应，不暴露文件是否全局存在。
  生成图引用不匹配抛出原有 ValueError；不改变工具 schema、HTTP 字段、设置或数据布局。

真实消费者：`admin.routers.chat.download_chat_media`、`core.image_gen.image_reference_bytes`、
`core.tools.image_gen` 的 pin_character_image_reference。调用方已传入 uid/char_id，无须增加
facade、Registry 或另一份权限状态；源字节仍由既有资料/产物存储所有。

## 回归证据

修补前的挂载 HTTP 用例：A 有媒体引用、B 没有引用但持有 SHA 时，B 错误收到 200 和图片字节，
而同 scope 不存在 SHA 返回 410。修补后 A=200，B 与不存在媒体响应相同；A 的唯一事件墓碑后
A 也不可继续读全局 inbox 字节。资料库正式 raw 保留行为由既有回归继续覆盖。

生成图引用新增三种拒绝：其他角色、其他 owner、缺失调用者 scope。旧实现均错误读取成功，
修补后全部抛错；原同角色图片引用仍通过。另有新上传索引尚无 turn 的正向用例，防止收紧隔离
误伤本轮工具读取。

命令：`python -m pytest tests/test_session_scope_cutover.py tests/test_chat_media.py
tests/test_chat_artifacts.py tests/test_image_gen_reference.py -q --tb=short`。
Windows / Python 3.12.13：103 passed（50.88s）。浏览器和真机结果单列在 T4 后续记录中，
不能把该回归结果写成真实设备 PASS。

依赖方向、记忆路径和可移植工具 schema 守门：
`python -m pytest tests/test_ar02_dependency_directions.py tests/test_memory_direct_path_lint.py tests/test_tool_schema_portable.py -q --tb=short`，229 passed（144.22s）。

## 架构与限制

没有新增 facade/adapter/bridge/wrapper、全局业务状态、静默降级、白名单例外或旧测试专用
兼容实现；没有删除文件。授权只读取现有本角色资料索引和证据，墓碑语义不变。
T3.2、T5–T9 不在本修补范围；依照用户默认提交合并指令交付，不据此宣称整体验收 verified。
