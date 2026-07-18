# Brief 95 · 配置中心补遗:owner_id 进面板 + 新用户阻塞项全扫

> 背景:Brief 93 已落地后的真机复测补遗(20260718)。前置依赖:Brief 93
> (配置中心页已存在,本单在其上加字段)。与 Brief 96(梦境创作)**可并行**。

## 1. 🔴 owner_id 进配置中心(升为必填②)

- 现状:config 中 `owner_id` 默认空串,注释「留空=调度器跳过主动触发」;
  desktop/standalone 场景它同时决定记忆文件归属 uid。新用户不填的后果:
  主动触发全部静默失效 + 记忆挂在错误/缺省 uid 下。
- 配置中心「必填」层加 `owner_id` 输入框,标红,旁边固定警告:
  「建议直接填你的 QQ 号——若此处用了别的 id,之后接 QQ 时会按 QQ 号另起
  一套记忆,与桌宠期记忆不互通」。
- 校验:合法字符集按 config 注释(A-Za-z0-9_-),空值时顶部横幅与聊天 API
  缺失同级警告。
- `owner_birthday` 放旁边(可选项):校验 MM-DD;**顺手核实**占位符 `MM-DD`
  原样残留时是按未填处理还是解析报错——若报错,改为视同未填 + 日志提示。

## 2. 🟡 新用户阻塞项全扫(默认开启但缺凭据的功能)

逐块审计 `config.example.yaml` 中默认 `enabled: true` 但依赖外部凭据/外部
服务的功能块(已知至少:vision 默认开且 api_key 是占位符;逐一过 mail、
notify、tts、watch、screen_peek、hardware 等),每块二选一处置:

- 默认改 `false`(纯增值功能,如 vision/tts/mail),配置中心「可选」层给开关;
- 保持开但**缺凭据时静默降级 + 单条 info 日志**,绝不在启动期抛错或聊天期
  反复报错刷屏。

验收标准:`config.example.yaml` 原样复制 + 只填聊天 API 与 owner_id,
启动与聊天全程无 ERROR 级日志、无功能性报错弹给前端。

## 3. 🟢 收尾

- README 首次安装清单(Brief 92 §7 版本)在「面板配置页填必填项」一步补
  owner_id 及其警告一句话。
- 配置中心页字段说明与 config.example.yaml 注释口径一致,不写两套说法。

## 验收

- 全新环境只填聊天 API + owner_id:聊天正常、无 ERROR 日志;主动触发调度器
  不再静默跳过(可在日志/观测页确认 owner 生效)。
- owner_id 留空时面板有与聊天 API 同级的红色警告;非法字符被拒。
- `pytest -n auto` smoke 通过。
