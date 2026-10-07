# 268 个人微信接入与最小 shared IM ingress

状态：A/B 代码施工及隔离验收完成；C 实桥验收 not-run。用户已确定个人微信 WeChatPadPro REST/WebSocket 为 reference transport；通过独立 `wechat_transport` abstraction 隔离第三方 API/字段，不进入 shared ingress、Pipeline、memory 或 turn_sink。第三方下载放在仓库外指定目录。

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
- [x] QQ 基线：124 passed，1 个既有启动白名单失败（未列 STT/HDS）；核实均为启动任务后更新守卫。工作树最初干净，目标文件 LF。
- [x] `core/im_ingress.py` 提供中立消息/发送合同、限额 TTL 去重及 canonical UID 锁；现有 main 私聊编排通过注入来源/发送/工具集合共用，不另复制 Pipeline。QQ 媒体及群聊留在现有 facade。
- [x] `main.handle_message` 与 `_qq_reality_reply_adapter` 保留兼容入口及 monkeypatch；微信 scope 在共享锁内冻结，避免双重锁、双重记录/发送。后处理 target_id 属于 QQ 媒体地址，其他 IM 不传。
- [x] 补 admission 去重、canonical lock、地址隔离及 record-before-send 测试；现有 QQ guard/工具/分段回归复用。
- [x] A 定向回归 128 passed；差异与换行检查后独立提交，进入 B。

### 268-B：真实微信 transport 与 output

- [x] 固定公开 849 API family 的 reference REST/WS 合同与上游 commit；真实部署版本及同步/发送响应样例仍待 C 核对。登录交由桥接，普通 key 来自进程环境。
- [x] 实现独立 `integrations/wechat_transport.py`、`integrations/wechat/` reference implementation 和 `channels/wechat.py`；本地真实 WS/HTTP fixture 验证协议收发、绑定拒绝、去重、unknown 不重发、supervisor 启停。实桥重连 observe。
- [x] 启动注册、独立开关、管理面热更新/effective state、脱敏观测和 no_outbound 接线；微信不依赖 NapCat。仅微信新任务参与新增关闭清理，QQ 生命周期不扩改。
- [x] 复用已有统一 pretool/tool loop 许可合同；审计确认注册工具未直接依赖 qq_adapter，全局 QQ send callback 当前无执行消费者。QQ TTS/图片路径的 target_id 对微信为空；未增加微信媒体能力。Pipeline 仅将 wechat 纳入已有私聊 continuity channel 集合，未改核心算法；memory/turn_sink/tool loop 实现未改。
- [x] 更新配置示例、通道、架构、控制面、生命周期、known-issues 和三仓总账；桌面/手机代码和协议未改。
- [x] 静态版本 wechat-268-1；隔离管理面浏览器验收通过（保存、启用回读 runtime_not_started、停用 disabled、凭据缺失、中文/英文），未连接生产服务。相关测试 187 passed；差异/换行核对后独立提交。

### 268-C：真实收发验收与交付

- [ ] 真实绑定 owner 微信发送文本：正确角色回复，回到同一私聊；记忆写入现有 UID，下一轮能延续。
- [ ] 未绑定微信不进入业务链；重复事件仅一次生成；断连/超时有可观测结果，且不误投 QQ。
- [ ] QQ 私聊、群聊 @、媒体、guard、工具确认、tool loop、分段和主动下行回归；同时输入 QQ/微信/desktop/mobile 不并行写同 owner 的关键轮次。
- [ ] 微信默认关闭时系统维持现状；关闭/重新开启不遗留连接，主动下行未开启不广播。
- [x] 真实 bridge/微信收发 not-run；隔离管理面浏览器 pass；QQ/微信 business fixture pass；真实四端并发 observe。未配置或部署桥接，没有扫码、外发、生产配置/数据变更。

## 验证入口

复用 `tests/test_r1d_qq_reality_reply_adapter.py`、`tests/test_r1b_qq_convergence_audit.py`、`tests/test_qq_tool_reply_chain.py`、`tests/test_qq_fast_path_tool_loop.py`、`tests/test_qq_dream_guard.py`、`tests/test_fix08_qq_trigger_segmented_send.py`、`tests/test_fix09_group_at_detection_and_isolation.py`、`tests/test_mark_user_active_owner_guard.py`、`tests/test_pretool_router.py`、`tests/test_turn_sink.py`。按实际影响补 envelope、工具 schema、安全与生命周期既有测试，不默认跑全量。

每单提交前核对 `git diff --check`，对所有暂存目标比较普通 stat 与 `--ignore-cr-at-eol` stat。只暂存本单文件。A 提交 `9b2288e`；B 187 项定向测试通过。额外静态拆分页检查有 2 个既有失败（HDS inline style 与 admin-inline-103/131 孤立 CSS，均由 HEAD 证实原已存在），未顺手修改；该批为 191 passed / 2 failed，后续新增测试另完成定向复验。浏览器截图存于 ignored 测试产物目录。

## 删除 brief 候选（本次不执行）

两种 IM 的真实回归稳定后，评估删除重复的 QQ 私聊编排 wrapper 与失效旧注释；连同仅为旧实现存在的守卫、测试和文档条目一起处理。保留协议 facade 与必要兼容入口，不在本单顺手清理。
