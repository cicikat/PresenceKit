# Spec #4 — 梦境邀请链接（Dream Invite）

> 状态：待实现  
> 难度：小  
> 改动范围：`core/pipeline.py`、`Emerald-client/src/shared/api/types.ts`、`ws.ts`、`ChatWindow.tsx`

---

## 目标行为

用户和角色在现实对话里聊到"一起做梦"、"去梦里"等语境时，角色的回复中角色意图解析器（Path B）会检测到 `dream_invite` 意图，然后：

1. 后端通过 WebSocket 推送一个 `dream_invite` action 到前端
2. 前端收到后，在聊天界面的角色消息下方展示一个"进入梦境"的邀请按钮
3. 用户点击按钮，梦境窗口打开（等同于点击 Ribbon 的月亮按钮）

---

## 实现步骤

### 后端：`core/pipeline.py` — 向 intent_prompt 添加 `dream_invite` 动作

在 `_parse_and_execute_intent()` 的 `intent_prompt` 里，在现有操作类型列表最后追加一条：

```python
f"- dream_invite: 邀请用户进入梦境，仅当{_char}明确表达「一起去梦里/想和你做梦/来梦里找我」等直接邀请语义时才触发，"
f"params: {{}}\n\n"
```

同时在 action_payload 组装处，确保 `dream_invite` 不会被 `_INTENT_DANGEROUS_ACTIONS` 拦截（目前只有 `device_shutdown` / `device_sleep`，不影响）。

**守卫说明**：Path B 已有三道守卫（trigger_name 为空、user_content 非空、非危险动作），dream_invite 自然继承，不需要额外守卫。即触发器/定时消息里不会误发邀请。

在 `tool_dispatcher.py` `_push_desktop_action` 不需要任何修改——已有 WS 推送和文件降级两条路，dream_invite action 原样推出去即可。

---

### 前端 Step 1：`Emerald-client/src/shared/api/types.ts`

把 `dream_invite` 加入 `DesktopActionType` 联合类型：

```typescript
// 找到现有的类型定义，类似：
export type DesktopActionType =
  | 'minimize_window'
  | 'open_url'
  | 'show_notify'
  | 'media_play_pause'
  | 'dream_invite';   // ← 新增
```

---

### 前端 Step 2：`Emerald-client/src/shared/api/ws.ts`

**2a. 在 `EventMap` 里加一个事件：**

```typescript
type EventMap = {
  state: ConnectionState;
  channel_message: { content: string; msg_id: string; source?: string };
  message_segments: { content: string; segments: NarrativeSegment[]; msg_id: string; source?: string };
  action: DesktopActionPayload;
  dream_invite: Record<string, never>;   // ← 新增
};
```

**2b. 在 `_dispatchAction` 的 switch 里加一个 case：**

```typescript
case 'dream_invite':
  this.emit('dream_invite', {});
  return;
```

放在 `default: throw new Error(...)` 之前。

---

### 前端 Step 3：`Emerald-client/src/windows/chat/ChatWindow.tsx`

在组件 mount 时订阅 `dream_invite` 事件，收到时打开梦境窗口：

找到现有的 `useEffect` 中 wsClient 订阅的位置（靠近 `toggleDreamWindow` 定义处），加：

```typescript
useEffect(() => {
  const unsub = wsClient.on('dream_invite', () => {
    // 只在梦境窗口未打开时触发，避免重复弹出
    setDreamWindowOpen(prev => {
      if (!prev) {
        // 可选：先弹一个系统通知提示"角色邀请你进入梦境"
      }
      return true;
    });
  });
  return unsub;
}, []);
```

**注意**：这段 useEffect 的依赖数组为空，mount 时注册一次。用 `setDreamWindowOpen(prev => ...)` 函数式更新，避免闭包捕获过期值。

---

### 可选：inline 邀请卡片（更显眼的 UX）

如果希望邀请按钮直接出现在角色的消息气泡里而不是全局通知，可以在角色的文字回复中嵌入一个约定标记，让前端识别后渲染为内联按钮。

**后端**：在 `prompt_builder.py` 的系统提示或角色卡里加一条规则：
> "当你主动邀请用户进入梦境时，在回复末尾附上 `[DREAM_INVITE]`"

**前端** `ChatPanel.tsx`：在渲染消息内容时，检测 `[DREAM_INVITE]` 标记，将其替换为一个 `<DreamInviteChip />` 组件（一个小的 onClick 按钮）：

```tsx
// ChatPanel.tsx 消息渲染处
const renderContent = (text: string) => {
  if (text.includes('[DREAM_INVITE]')) {
    const clean = text.replace('[DREAM_INVITE]', '').trim();
    return (
      <>
        {clean}
        <DreamInviteChip onAccept={() => onDreamToggle()} />
      </>
    );
  }
  return text;
};
```

`DreamInviteChip` 是一个新的小组件，可以放在 `features/dream/` 下：
```tsx
// features/dream/DreamInviteChip.tsx
export function DreamInviteChip({ onAccept }: { onAccept: () => void }) {
  return (
    <button className="dream-invite-chip" onClick={onAccept}>
      ✦ 进入梦境
    </button>
  );
}
```

**但注意**：如果同时用 inline 按钮 + action 触发，会重复打开，需要去重（比如只留一种机制，建议 action-based 为主，inline 为可选增强）。

如果不做 inline，只用 action，角色消息里不需要任何特殊标记，`[DREAM_INVITE]` 标记也不需要加到提示里，体验简洁。

---

## 验证方式

1. 启动 bot + 桌宠，在聊天里说"我们一起去梦里吧"
2. 观察后端日志有没有 `[pipeline.intent] action=dream_invite` 记录
3. 前端梦境窗口应该自动弹出

如果 Path B 没触发（用户没有打字只是在等触发器），说明是正常行为——dream_invite 只在真实对话 owner turn 里触发。

---

## 注意事项

- Path B 的 intent 解析是 LLM 判断，不是规则匹配。触发率取决于 LLM 对"邀请语义"的理解。如果触发率偏低，可以在 intent_prompt 里补充触发例句。
- `dream_invite` 在 `_INTENT_COOLDOWN_SEC`（2min）内相同动作不重复执行——这是好的，避免用户同一轮被多次弹窗。
- 不要把 `dream_invite` 加入 `_INTENT_DANGEROUS_ACTIONS`（它不是危险动作）。
