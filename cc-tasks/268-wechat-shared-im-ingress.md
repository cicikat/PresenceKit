# 268 个人微信接入与最小 shared IM ingress

状态：工单已编写；功能施工未开始。用户已要求先写工单后施工。接入类型已确认个人微信，用户已确认桥接项目尚未选定；不以空 adapter 或模拟收发冒充微信接入完成。

## 目标与现状

增加个人微信 transport adapter 与 output channel，抽取 QQ/微信共用的最小现实私聊 IM ingress。保持 Pipeline、turn_sink、memory、tool loop 的核心算法和写入顺序不变；QQ 的私聊、群聊、媒体、工具及主动下行行为作为兼容验收基线。

源码检查：`core/qq_adapter.py` 已封装 NapCat/OneBot 事件解析和收发，但 `main.handle_message` 仍混合 QQ 路由、现实 guard、媒体处理、pretool、Pipeline 调用与回复；`core/output/text_output.py` 直接调用 QQ；`channels/qq.py` 承接主动广播。已有 `core/pretool_router.py`、`core/conversation_gate.py`、`core/turn_sink.py` 必须复用，避免再建业务入口。

## 范围与合同

- 首期微信仅显式绑定的 owner 文本私聊。群聊、图片、语音、文件、朋友圈及历史导入不在本单范围；不支持的类型不得当文本伪造进入 Pipeline。
- transport 负责协议鉴权、事件解析、过滤、连接/重连、回调确认和真实发送结果；shared ingress 不识别 OneBot/CQ 或微信原始字段。
- 标准输入包含 channel、external_sender_id、canonical_uid、conversation_id、message_id、timestamp、text、trusted_user_text、reply_address；角色 scope 在执行前冻结。媒体字段仅保留既有 QQ 证据合同，不扩展微信媒体支持。
- canonical_uid 来自服务端显式 owner 绑定，使用现有 owner UID；不得用昵称、客户端自报 UID 或数字相似性自动合并身份。外部微信地址只用于回信，不进入记忆桶键。未绑定发送者不得触发模型、工具、owner 活跃状态或记忆写入。
- transport 会话/去重键带 channel 与连接账号命名空间；跨端业务串行继续使用 canonical_uid 的 conversation_lock。QQ legacy session_key 与群聊处理保持兼容，不做全仓队列迁移。
- ingress 接受注入的分段发送接口，用于普通回复及 guard/工具确认短文本；QQ 保持 `text_output.send` 的切分、停顿和行为。不得通过修改全局 QQ send callback 把微信回复路由过去；审计会直接发送 QQ 的工具，微信不支持的发送能力应在暴露面排除或明确拒绝。
- 回复继续 record-before-send，沿用 `record_assistant_turn`、`fanout=[]`、现有 scrub 与 provenance；微信来源显式记录为 wechat，禁止 stamp_qq。必要的来源枚举/工厂扩展属于合同接线，不改 sink/记忆算法。
- 微信普通回复只发送到本次 reply_address；主动广播经 WeChatChannel 独立路由到绑定 owner。主动下行默认关闭，避免一启用就把全部跨端消息复制到微信；开启条件、活跃状态及平台限制须按真实 bridge 能力确定。
- bridge 重投在进 Pipeline 前去重；缓存有界且有 TTL。入站重投不重复生成/写记忆；发送超时或结果未知不得盲目重发。首期不建持久补发队列，不承诺 exactly-once。回调 ack 必须依真实协议明确代表接收还是完成。
- 新运营配置必须同单提供管理面热更新、校验与 effective state；连接、最后错误、接收/拒绝/去重/发送结果复用或增加脱敏只读观测。不在桌面/手机新建设置 UI。
- 启停与连接资源纳入既有生命周期；无连接时不得假报在线/发送成功。密钥、地址及绑定身份存本地配置，不写入 tracked 文件。

## 施工清单与独立提交

### 268-A：QQ 基线与最小抽取

- [x] 检查入口、队列、输出与现有通道文档，确认混合边界。
- [ ] 先运行既有 QQ/guard/pretool/turn_sink 相关回归，记录基线；复查 dirty files 与换行。
- [ ] 设计 `core/im_ingress.py` 的最小消息/发送合同，只抽取现实私聊编排；QQ 解析、媒体转换、群聊及特殊协议留在 QQ facade。
- [ ] `main.handle_message` 与 `_qq_reality_reply_adapter` 保留兼容入口；既有测试 monkeypatch 入口不因搬函数静默失效。避免双重 conversation_lock、双重 record 或双重发送。
- [ ] 补最小必要合同测试：来源与 UID 分离、短文本回信路由、QQ memory-before-send、一次写入/发送、scope 冻结、QQ/微信/HTTP 同 owner 串行。
- [ ] 相关测试与 diff 检查通过，立即独立 commit，再施工 B。

### 268-B：真实微信 transport 与 output

- [ ] 确定 bridge 项目、版本、协议文档、样例事件与发送结果；明确登录、回调/轮询、鉴权、重连及可用部署方式。此项是依赖，不猜测 API。
- [ ] 实现 `integrations/wechat/` transport 与 `channels/wechat.py`，接入已抽取 ingress，验证绑定、拒绝、重投去重、结果未知及连接恢复。
- [ ] 完成启动注册、独立开关、热更新控制面、脱敏观测和 no_outbound 防线；启用微信不要求启动 NapCat，不改变 QQ 的 standalone_mode 语义。
- [ ] 检查 pretool、tool loop 中 channel/target 使用及 QQ 专属发送工具，明确微信能力暴露与拒绝策略，保持核心执行算法不变。
- [ ] 更新 `config.example.yaml`、`docs/channels.md`、架构入口说明、`docs/feature-control-surface.md` 与实际受影响的三仓总账条目；不改桌面/手机协议。
- [ ] 静态管理面按 AGENTS.md 更新版本并浏览器验收；相关测试与 diff 检查通过后独立 commit。

### 268-C：真实收发验收与交付

- [ ] 真实绑定 owner 微信发送文本：正确角色回复，回到同一私聊；记忆写入现有 UID，下一轮能延续。
- [ ] 未绑定微信不进入业务链；重复事件仅一次生成；断连/超时有可观测结果，且不误投 QQ。
- [ ] QQ 私聊、群聊 @、媒体、guard、工具确认、tool loop、分段和主动下行回归；同时输入 QQ/微信/desktop/mobile 不并行写同 owner 的关键轮次。
- [ ] 微信默认关闭时系统维持现状；关闭/重新开启不遗留连接，主动下行未开启不广播。
- [ ] 真实 bridge、微信收发、管理面浏览器分别记录 pass / partial / not-run；fixture 与静态测试不能替代真微信验收。实际缺口才记 known-issues。

## 验证入口

复用 `tests/test_r1d_qq_reality_reply_adapter.py`、`tests/test_r1b_qq_convergence_audit.py`、`tests/test_qq_tool_reply_chain.py`、`tests/test_qq_fast_path_tool_loop.py`、`tests/test_qq_dream_guard.py`、`tests/test_fix08_qq_trigger_segmented_send.py`、`tests/test_fix09_group_at_detection_and_isolation.py`、`tests/test_mark_user_active_owner_guard.py`、`tests/test_pretool_router.py`、`tests/test_turn_sink.py`。按实际影响补 envelope、工具 schema、安全与生命周期既有测试，不默认跑全量。

每单提交前核对 `git diff --check`，对所有暂存目标比较普通 stat 与 `--ignore-cr-at-eol` stat。只暂存本单文件。当前仅新增工单，未运行功能测试，未连接微信或修改生产数据。

## 删除 brief 候选（本次不执行）

两种 IM 的真实回归稳定后，评估删除重复的 QQ 私聊编排 wrapper 与失效旧注释；连同仅为旧实现存在的守卫、测试和文档条目一起处理。保留协议 facade 与必要兼容入口，不在本单顺手清理。
