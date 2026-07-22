# 私人内容备份清单

> 当前口径：C1 已将用户 authored 资产的主路径统一到 Git 忽略的 `userdata/`。本文只列
> 「丢失后无法从仓库恢复」的内容；运行缓存、索引、队列和测试沙盒不在备份集内。路径
> 分类的代码真值见 `core/data_paths.py`，完整运行时树见 [data-taxonomy.md](data-taxonomy.md)。

## 一、最高优先级：`userdata/`

这是本机私有内容的主根目录，应整体备份：

```text
userdata/
├── assets/stickers/{emotion}/
└── characters/
    ├── cards/{char_id}.{json,txt,md}
    ├── authored/{char_id}/
    │   ├── activity_pool.yaml
    │   ├── author_notes.json
    │   ├── traits.yaml
    │   ├── letter_samples/
    │   └── knowledge/
    ├── reality/
    └── dream/{worlds,presets}/
```

其中贴纸、角色卡、角色 authored 配置、现实素材和梦境世界/预设均不进 Git；丢失后只能从外部备份
恢复。不要把仓库内的 `defaults/`、`examples/` 或公开默认角色/梦世界模板误当作私人唯一副本。

## 二、同样应备份的本机配置

| 路径 | 原因 |
|---|---|
| `config.yaml` | 本机服务与渠道配置，不入库。 |
| `secrets.local.yaml`（如存在） | 本机密钥配置，不入库；备份应放在受保护的位置。 |
| 渠道或客户端各自的本地配置 | 属于对应应用的私有配置；按其仓库说明备份，勿复制到本仓 Git 工作树。 |

备份内容不得提交、粘贴到工单或日志中；密钥应使用加密介质或受控密码库保存。

## 三、按需要备份的运行积累

以下文件不属于 C1 authored 资产，但通常重建代价高。需要保留对话连续性、梦境偏好或本地日记时，
一并备份相应目录：

| 路径模式 | 内容 |
|---|---|
| `data/runtime/memory/{char_id}/{uid}/` | 对话历史、identity、episodic/mid-term 和记忆索引。 |
| `data/runtime/dreams/{char_id}/{settings,state,summaries,impressions}/` | dream 偏好、状态和可回顾的梦境积累。 |
| `data/diary_fallback/` | 未配置外部日记根目录时的本地日记。 |
| `data/relations.yaml`、`data/blacklist.yaml` | 本地关系/屏蔽定制（如已填写）。 |

这类数据由 `get_paths()` 管理，测试模式会写到独立的 `data/test_sandbox/`；不要把测试沙盒纳入
生产备份，也不要把生产 `data/` 复制进新 clone 来做开箱测试。

## 四、迁移与旧目录

读取时，`DataPaths` / `AssetRegistry` 会优先使用 `userdata/`；仅为旧安装保留
`assets/stickers/`、`characters/`、`content/characters/` 等历史位置的只读 fallback。确认
`userdata/` 已包含所需资产并完成备份前，不要删除旧目录。新建或修改用户 authored 资产应落在
`userdata/`，不要重新把私有内容写回旧根目录。

## 五、不必作为私人唯一副本备份

- `defaults/`、`examples/` 和其他已跟踪的公共种子：从 Git 可恢复。
- `data/cache/`、`data/inbox/`、`data/runtime/*queue*`、`data/test_sandbox/`：缓存、上传原件、
  IPC 或测试临时文件。
- `data/logs/`、`data/debug/`：排障材料可按需要另存，但不是业务恢复的必需真值。
- `data/runtime/observability/api_calls-*.jsonl`：仅调用元数据的短期观测账本，保留策略为最近 7 天。

## 备份顺序

1. 每次编辑私有资产后立即备份整个 `userdata/`。
2. 变更配置或密钥后更新受保护的配置备份。
3. 需要长期连续性时，按用户/角色选择性备份 §三中的运行积累。
4. 迁移或清理旧目录前，先完成第 1 步并验证主路径可被程序读取。
