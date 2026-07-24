# C1 根目录资产盘点与迁移记录

> 盘点与迁移完成：2026-07-22。本文保留 C1 的资产边界、迁移目标和遗留清理清单，描述的是
> **当前实现**，不再是待执行的迁移计划。
>
> 2026-07-24 复查（Brief 116 §2）：`characters/`、`examples/`、`content/` 根目录逐文件核对，
> 结论是**不需要再合并/搬动**——三个目录里的每个文件都已经是这里判定过的"保留"项，均有
> 测试或文档硬引用（`tests/test_authored_assets.py`、`tests/test_no_template_files_in_characters_root`、
> `docs/tools.md` 的 `examples/*.example.json` 路径、`core/activity_manager.py` 与
> `core/data_paths.py` 的 `content/characters/{char_id}/` 硬编码路径）。唯一发现的缺口是本表
> 漏记了 `content/characters/yexuan/*.example.yaml`（已跟踪但当时没写进表格），已在下方补上；
> 没有发现需要清理或搬移的新增游离文件。

## 决策与目标结构

`userdata/` 是仅容纳用户私有 authored 资产的根目录。它不承载
`data/` 的运行时状态；后者继续由 `core.sandbox.get_paths()` 管理，并保持测试沙箱偏移。

```text
userdata/                         # 用户可写/私有 authored 资产（Git ignored）
├── assets/stickers/
└── characters/
    ├── cards/
    ├── authored/{char_id}/
    ├── reality/
    └── dream/{presets,worlds}/
```

`DataPaths` / `AssetRegistry` 已优先读取 `userdata/`，并为旧安装目录保留只读 fallback。
新建角色和其他可写 authored 资产进入 `userdata/`；运行时 `data/` 路径不参与这次迁移。

## 保留在根目录

| 路径 | 判定 | 依据 |
|---|---|---|
| `admin/`、`channels/`、`core/`、`scripts/`、`tests/`、`tools/`、`firmware/` | 保留 | 源码、测试与构建入口。 |
| `data/` | 保留 | 运行时 canonical 状态根；必须经 `get_paths()` 并在 test mode 偏移。不是可随意迁移的用户素材目录。 |
| `defaults/` | 保留 | 8 个已跟踪 seed 文件；`DataPaths` 使用它们初始化空运行时状态。 |
| `examples/` | 保留 | 已跟踪的公开角色卡示例/模板。 |
| `content/characters/default/`、`content/jailbreak_presets/示例.example.json` | 保留 | 已跟踪的公开默认 authored 资产。 |
| `content/characters/yexuan/{activity_pool,traits}.example.yaml` | 保留 | 已跟踪的公开中性示例（`.gitignore` `content/characters/*/*.yaml` 的 `!*.example.yaml` 例外），供其他角色卡作者参照结构；上次盘点漏记，Brief 116 §2 复查时补上。 |
| `characters/default.json`、`characters/default_author_notes.json` | 保留 | 已跟踪的公开默认角色卡与作者注池。 |
| `characters/dream_postcards/templates/` | 保留 | 已跟踪的 Dream 明信片模板；`core/dream/postcard.py` 直接读取。 |
| `config.example.yaml`、`*.example.yaml`、`secrets.example.yaml`、`README*`、`ARCHITECTURE.md`、`AGENTS.md`、`DESIGN.md`、启动/安装脚本 | 保留 | 项目文档、模板和启动入口；即使某些本地文档当前未跟踪，也不是缓存。 |
| `config.yaml`、`secrets.local.yaml` | 保留在根目录且继续忽略 | 本机运行配置/凭据；不能提交、移动或删除。 |

## 已迁入 `userdata/`

| 原路径（旧安装可 fallback） | 当前主路径 | 消费者/原因 |
|---|---|---|
| `assets/stickers/` | `userdata/assets/stickers/` | 私有贴纸库；当前由 `core/output/sticker.py` 读取。 |
| `characters/*.json`（排除 `default.json` 与 `default_author_notes.json`） | `userdata/characters/cards/` | 私有角色卡；当前由 `core/character_loader.py`、`core/asset_registry.py` 和角色管理 API 扫描。 |
| `characters/{char_id}_author_notes.json`（非 default） | `userdata/characters/authored/{char_id}/author_notes.json` | 私有作者注池；当前有 `DataPaths.author_notes_pool()` 兼容读取。 |
| `content/characters/{char_id}/`（排除 `default/` 与 `*.example.*`） | `userdata/characters/authored/{char_id}/` | 私有 traits、activity pool、信件、知识库、参考音频；当前由 `DataPaths` authored accessor 使用。 |
| `characters/reality/` | `userdata/characters/reality/` | 私有 reality lorebook、jailbreak 与头像资产；当前由 `DataPaths`、asset registry、prompt builder 使用。 |
| `characters/dream_presets/`、`characters/dream_worlds/` | `userdata/characters/dream/{presets,worlds}/` | 私有 Dream 世界/预设；当前由 Dream loaders 与 asset registry 使用。 |

迁移已同步更新 `core/data_paths.py` / `core/asset_registry.py` / `core/character_loader.py` / Dream
loaders / `core/output/sticker.py`、`.gitignore`、`docs/data-taxonomy.md` 与回归测试。不得把任何
`data/` 路径迁到本目录。

## 需由用户手动删除

这些路径均未被 Git 跟踪，且没有业务代码将其作为资产根读取。请在没有 pytest、服务或固件构建正在运行时手动删除；删除后告诉我即可。

| 路径 | 原因 |
|---|---|
| `MagicMock/` | 测试 mock 对象误写到仓库根目录的残留。 |
| `__pycache__/` 及所有 `**/__pycache__/` | Python 字节码缓存。 |
| `.pytest_cache/` | pytest 缓存。 |
| `.tmp/` | Codex/pytest 临时目录；仅在确认没有活跃测试后删除。 |
| `.claude/.cache/` | 本地编辑历史缓存。 |
| `firmware/presence-device/.pio/` | PlatformIO 构建产物。 |

`firmware/presence-device/.vscode/` 为本机编辑器配置，不列为必删项；是否删除由用户自行决定。

## 已完成的迁移约束

1. 用户已确认清单，并手动清理要求由用户处理的空目录占位文件；
2. 主路径、旧路径 fallback 与公开 seed 均有测试覆盖；`tests/test_authored_assets.py` 只校验随仓库
   发布的 `defaults/` / `examples/` 资产，不把用户私有 `userdata/` 误当成 tracked 文件；
3. `config.yaml`、`secrets.local.yaml`、`data/` 和公开 default/example 资产没有被移动或纳入 Git；
4. 后续若删除旧安装目录，只能在确认该用户的 `userdata/` 迁移完整后手动执行；代码 fallback 保留以兼容
   尚未迁移的旧安装。
