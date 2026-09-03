# Brief 233：Workspace 文件能力

> 状态：implemented；前置：230、229；本工单只实现受控工作区读写，不提供全盘访问。

## 现有代码事实

`core/tools/fs_browse.py` 的 `fs_list/fs_read` 是只读能力，要求 `fs_access.allow_roots`，拒绝项目 data、
软链接和敏感名称；现有通用写入入口不存在，toybox 只接受固定枚举文件。

## 目标

新增明确的 workspace capability，默认指向用户显式配置的一个或多个工作区，例如桌面下专用目录，
而不是整个用户目录。

能力必须定义：

- read/list/create/update/delete 的分开权限
- 根目录、路径规范化、软链接和敏感文件策略
- 单文件/总容量/并发任务限制
- 删除和覆盖的确认策略
- 原子写入、版本/撤销、任务 receipt
- Reality-only 默认；Dream 无此能力

## 与现有 fs 能力的关系

先建立 capability adapter，兼容读取现有 `fs_list/fs_read`；不要直接扩大 `allow_roots` 或绕过
`execute(origin=...)`。迁移完成前旧工具继续保持只读语义。

## 验收

- 角色可以在授权 workspace 创建文档并读取自己的结果。
- workspace 外路径、项目 data、软链接、敏感文件和未授权删除均 fail-closed。
- 所有写入可通过 task receipt 和只读观测追踪，但观测不泄露正文和绝对路径。
- 运行在 remote_server 时 capability 明确不可用。
