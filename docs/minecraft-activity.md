# Minecraft Activity 与 Docker 身体

## 275：陪伴与快速判断

`approach` 接近后停下，`accompany` 在用户授权会话内持续陪伴，`protect` 跟随并近距离保护，武器/耐久不足拒绝战斗。陪伴和保护动作每90秒由会话监督器续租，单次身体指令仍120秒；本地停止/危险后不恢复。切角色、入梦、关闭、配置变化或租约失效立即撤销。普通 follow 仍是单次动作。

快速判断走既有 `minecraft_reaction` 路由，3秒总预算、零SDK重试、并发1、默认关闭；主模型保留当前角色的人设和表达。owner聊天可先判断动作后生成回复，动作无需等待主模型说完；环境判断仅用于已授权陪伴/保护模式及局部状态变化，不能接管采矿或建造、扩大战斗授权。最小间隔默认5秒，每会话默认120次；失败回到本地规则，紧急停车从不等待模型。

模型连接与分工页分配 category；共玩页通过 `PUT /settings/minecraft/routing`（admin）修改同一生效profile，严格校验preset、角色覆盖与profile变化。关闭快层保留原本地规则与主模型角色聊天。观测提供快层次数、延迟、失败和陪伴目标，不暴露账号凭据。

## 运行边界

PresenceKit Python 后端负责角色与 Activity 会话；`integrations/minecraft/body`
是自有 Node 执行器，通过锁版本 npm 库调用 Mineflayer。第三方源码不 vendor，
确需克隆时只放维护者指定的仓库外工作区。执行器与桥合在 Docker 容器内，
不需要常驻 CMD；玩家客户端与 Minecraft 服务端不随桥自动启动。

## Docker 启动

在 ignored 的本地文件生成至少 32 字符随机桥接 token，不使用 admin token。
默认文件为配置目录的 `.env.minecraft.bridge-token`，Git 忽略 `.env.*`；Compose 默认读仓库根目录同名文件，正常后端启动直接读取，无需每次另开 CMD 设置环境变量。不同部署目录可分别用 `MINECRAFT_BRIDGE_TOKEN_FILE` 与 `PRESENCEKIT_MINECRAFT_TOKEN_FILE` 覆盖到同一个本地凭据文件。然后运行：

```powershell
docker compose -f integrations/minecraft/compose.yaml up -d --build
docker compose -f integrations/minecraft/compose.yaml ps
```

桥只发布到宿主 loopback 3210。宿主游戏服务在容器中通常使用 `host.docker.internal`；
实际地址必须按部署验证。Docker 管进程，管理面管游戏连接/会话；后端不挂 Docker socket。
认证缓存保存到独立 Docker volume，镜像只读、非 root、无附加 capabilities。
在线认证需账号交互登录；初版不在管理面回显认证缓存。容器重启不自动进服。

## 桥接 v1

除无内容 `/health` 外使用独立 Bearer token；8 KiB 请求上限，禁止未知字段。
连接必须显式传版本、服务器、账号认证方式、owner UUID 与 session_id。
一次只允许一个绑定。spawn 才算 connected；新连接生成 connection_epoch。

- `POST /v1/connect`：显式连接；重复绑定返回冲突。
- `GET /v1/state`：有界局部快照、当前动作与最近 30 回执；无原始服务器错误内容。
- `POST /v1/heartbeat`：绑定验证并续租，15 秒无续租就停止并断开。
- `POST /v1/disconnect`：撤销动作与连接。
- `POST /v1/commands`：session_id、connection_epoch、command_id、action、expires_at、params。
- `GET /v1/events?after=N`：仅已绑定 owner UUID 的聊天，内存最多 100 条，每次最多 20 条。

动作白名单 stop/follow/return/pickup/defend/say；有效期最多 120 秒，
另有默认关闭的 collect_iron（count/radius 均为1–8整数）。其阶段为装备、接近、挖掘、拾取核对、返回；只采当前可见铁矿，要求石镐或更高级非金镐、足够耐久、无附近威胁，不移除脚下或头顶支撑、不接近水/岩浆/砂砾。回执数量以实际背包增量核对，挖掘开始后中断为 outcome_unknown，不重放。它是会话内短任务，不持久续做；跨重启采集需另行 Agent Runtime capability。
同 id 同参数返回原回执、不同参数拒绝。单动作运行；stop 可抢占。
每连接最多 256 命令，达上限需显式重新开会话，不自动驱逐去重凭据。
回执包括 running/succeeded/failed/canceled/outcome_unknown；say 成功只表示提交给游戏连接，
不声称对方读到。pickup 目标消失不能证明拾取成功，标记 outcome_unknown。

默认寻路禁止挖块、搭桥、跑酷及大落差；跟随范围 32 格，低血/owner 不可见停止。
PVE 只限定白名单敌对生物及 6 格范围，不攻击玩家/宠物，不追击 creeper。
owner 游戏输入 `停下`、`停止`、`!pk stop` 直接本地停车，无需模型响应。

## Activity 与记忆

Minecraft 是显式 Reality 共玩会话，不使用 ambient activity_manager 或统一 EventBus。
领域编排只接桥协议，不依赖 Mineflayer 对象；游戏状态、聊天与回执留活动内。
游戏受伤/死亡不写现实 hidden state 或普通聊天历史；初版不做长期摘要回流。
第三方聊天/告示牌内容不能成为授权指令。重启/失联不回放旧任务。

## 验收边界

Node fake-body 与 HTTP 测试验证仲裁、去重、认证和失联安全态。
Docker 健康验证不证明进服、皮肤、寻路或真实战斗；这些需要指定 Java 服务端实测。
Microsoft 登录、独立 Bot 账号、玩家皮肤可见效果分别记录，不能以离线测试替代。

## 后端控制面与角色接入

`GET/PUT /settings/minecraft` 需要 admin；保存配置变化先撤销旧会话，再原子落盘并热加载。
`GET /observability/minecraft` 需要 state.read，只返回连接/动作回执、快照年龄、预算和错误码，
不返回游戏坐标、聊天、账号和凭据。
附加 `?session_id=<32位会话ID>` 可读取当前 owner/角色的已保存会话元数据、最后回执与关闭原因，不包含活动聊天。
`/activity/minecraft/start|close|command|chat|state` 需要 activity scope；owner 和当前角色
由后端解析，不接受客户端提供 uid、char_id 或服务器地址。start 必须启用、配置完整、
token 文件就位且 startup worker 活着。管理面入口是“Minecraft 共玩”。

角色使用当前角色资产、近期主聊天只读参考、活动 transcript 与局部快照，
通过既有 LLM 路由做有界规划。模型每次最多 512 输出 token/20 秒、并发 1，
默认每会话 120 次且间隔 10 秒，超限拒绝而不排无限队列；游戏聊天内存最多 100 条。
手动动作会使已开始的模型计划失效，停止始终可用。关闭 model_enabled 后仍能用固定
`!pk follow / !pk return / !pk stop`。拾取和防守默认关闭，管理面单独授权。

Minecraft registry 的 enabled=False 是客户端静态发现的默认值，并非运行时 effective state；
不虚构桌面/手机入口。持久文件只保存 Activity 会话与有预算上限的活动 transcript；
连续游戏状态不持久化。空闲 TTL 60 秒、会话硬上限 4 小时，运行期间每 30 秒刷新存储。
