# Spec #5 — 梦境预构 Activity（Dream Seed）

> 状态：待实现  
> 难度：中  
> 改动范围：`core/activity/registry.py`、新增 `core/activity/dream_seed.py`、`admin/routers/dream_seed.py`、`admin/admin_server.py`（路由注册）、`core/dream/dream_context.py`（读取 seed 注入）、前端新 tab

---

## 目标行为

睡前，用户和角色在"梦境预构"活动里共同写下今晚梦的场景设定（地点、氛围、我们在做什么）。活动结束时，这份"梦境种子"写入文件，下次入梦时作为 `entry_reason` 注入梦境 context，让梦境有延续感。

---

## 后端实现步骤

### Step 1：注册 ActivityMeta

在 `core/activity/registry.py` 的 `ACTIVITY_REGISTRY` 元组里追加：

```python
ActivityMeta(
    id="dream_seed",
    label="梦境预构",
    enabled=True,
    route_prefix="/activity/dream_seed",
    session_store="activity_store",
    session_dir_layout="{char_id}/{uid}/dream_seed/{session_id}",
    frontend_key="dream_seed",
    tauri_command_prefix="activity_dream_seed_",
    tauri_commands=(
        "activity_dream_seed_start",
        "activity_dream_seed_state",
        "activity_dream_seed_chat",
        "activity_dream_seed_close",
    ),
    memory_policy=MemoryPolicy(
        transcript="activity_local",
        summary_threshold=6,       # 6 轮对话后就可以提炼种子
        main_memory="deferred",    # 活动结束后的种子写入 dream_seed.json
    ),
    has_companion_chat=True,
    docs_path="docs/dream-seed-activity.md",
),
```

---

### Step 2：新建 `core/activity/dream_seed.py`

这个文件管理梦境种子的读写和活动会话逻辑。

```python
"""
梦境预构 (Dream Seed) — 活动后端。

session 生命周期：
  start  → 创建 session record，返回 session_id
  chat   → 附加一条对话，可选调 LLM 生成角色回复
  close  → 从对话中提炼梦境种子，写入 dream_seed.json，结束 session

dream_seed.json 存储位置：
  data/runtime/memory/{char_id}/{uid}/dream_seed.json
  通过 sandbox.get_paths().resolve_memory_path("dream_seed", uid, char_id) 读写
"""

import time
import uuid
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DREAM_SEED_TTL_HOURS = 12.0   # 种子超过 12h 未入梦则过期，不再注入


# ── Session 管理 ──────────────────────────────────────────────────────────────

def start_session(uid: str, *, char_id: str = "yexuan") -> str:
    """创建新会话，返回 session_id。"""
    from core.activity.store import ActivityStore
    store = ActivityStore(char_id=char_id)
    session_id = str(uuid.uuid4())[:8]
    store.create(uid, "dream_seed", session_id, metadata={"started_at": time.time()})
    return session_id


def get_session(uid: str, session_id: str, *, char_id: str = "yexuan") -> Optional[dict]:
    from core.activity.store import ActivityStore
    return ActivityStore(char_id=char_id).get(uid, "dream_seed", session_id)


def append_turn(uid: str, session_id: str, role: str, content: str, *, char_id: str = "yexuan") -> bool:
    """追加一条对话记录（role='user' 或 'assistant'）。"""
    from core.activity.store import ActivityStore
    store = ActivityStore(char_id=char_id)
    session = store.get(uid, "dream_seed", session_id)
    if not session:
        return False
    transcript = session.get("transcript", [])
    transcript.append({"role": role, "content": content, "ts": time.time()})
    return store.update(uid, "dream_seed", session_id, {"transcript": transcript})


async def generate_reply(uid: str, session_id: str, user_msg: str, *, char_id: str = "yexuan") -> str:
    """用 LLM 生成角色在梦境预构活动里的回复。"""
    from core import llm_client
    session = get_session(uid, session_id, char_id=char_id)
    history = session.get("transcript", []) if session else []

    system_prompt = (
        "你和用户正在一起构建今晚的梦境场景。\n"
        "活动目标：共同决定今晚梦的地点、氛围、你们会在梦里做什么。\n"
        "规则：\n"
        "- 用自然的对话语气和用户一起设想，问一些具体的问题（比如天气、时间、你们在做什么）\n"
        "- 不要直接宣布'梦境设定完成'，让对话自然推进\n"
        "- 回复简短，50字以内\n"
        "- 不要写旁白或括号动作描写"
    )
    messages = [{"role": "system", "content": system_prompt}]
    for turn in history[-6:]:    # 只取最近 6 条，避免 context 过长
        messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": user_msg})

    reply = await llm_client.chat(messages, call_category="activity_dream_seed") or ""
    return reply


async def close_session(uid: str, session_id: str, *, char_id: str = "yexuan") -> Optional[str]:
    """
    结束会话，从对话中提炼梦境种子，写入 dream_seed.json。
    返回提炼出的 seed_text，失败返回 None。
    """
    from core import llm_client
    session = get_session(uid, session_id, char_id=char_id)
    if not session:
        return None

    transcript = session.get("transcript", [])
    if len(transcript) < 2:
        return None     # 对话太短，不提炼

    # LLM 提炼种子
    dialogue = "\n".join(f"{t['role']}: {t['content']}" for t in transcript[-10:])
    seed_prompt = (
        f"以下是你和用户为今晚梦境做的预构对话：\n\n{dialogue}\n\n"
        "请把你们商量好的梦境设定总结成一段自然的描述（60字以内），"
        "像一个梦境入口的描述：包含地点、氛围、你们会做什么。"
        "只输出描述本身，不要任何前缀或解释。"
    )
    seed_text = await llm_client.chat(
        [{"role": "user", "content": seed_prompt}],
        call_category="dream_seed_distill",
        max_tokens_override=120,
    ) or ""
    seed_text = seed_text.strip()

    if not seed_text:
        return None

    # 写入 dream_seed.json
    _save_seed(uid, seed_text, session_id=session_id, char_id=char_id)

    # 标记 session 为已关闭
    from core.activity.store import ActivityStore
    ActivityStore(char_id=char_id).update(
        uid, "dream_seed", session_id,
        {"status": "closed", "seed_text": seed_text, "closed_at": time.time()}
    )

    return seed_text


def _save_seed(uid: str, seed_text: str, *, session_id: str = "", char_id: str = "yexuan") -> None:
    """原子写入 dream_seed.json。"""
    from core.sandbox import get_paths
    from core.memory.scope import MemoryScope
    from core.memory.path_resolver import resolve_path
    from core.safe_write import safe_write_json
    from core.sandbox import safe_user_id

    scope = MemoryScope.reality_scope(safe_user_id(uid), char_id)
    path = resolve_path(scope, "dream_seed")
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write_json(path, {
        "seed_text": seed_text,
        "created_at": time.time(),
        "session_id": session_id,
        "uid": uid,
    })
    logger.info("[dream_seed] saved uid=%s session=%s len=%d", uid, session_id, len(seed_text))


def load_seed(uid: str, *, char_id: str = "yexuan") -> Optional[str]:
    """
    读取有效的梦境种子。超过 TTL 返回 None。
    供 dream_context.py 在入梦时调用。
    """
    import json
    from core.sandbox import get_paths
    from core.memory.scope import MemoryScope
    from core.memory.path_resolver import resolve_path
    from core.sandbox import safe_user_id

    scope = MemoryScope.reality_scope(safe_user_id(uid), char_id)
    path = resolve_path(scope, "dream_seed")
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        age_hours = (time.time() - float(data.get("created_at", 0))) / 3600.0
        if age_hours > DREAM_SEED_TTL_HOURS:
            return None
        return data.get("seed_text") or None
    except Exception as e:
        logger.warning("[dream_seed] load_seed failed uid=%s: %s", uid, e)
        return None


def consume_seed(uid: str, *, char_id: str = "yexuan") -> Optional[str]:
    """
    读取种子并删除（一次性消费）。入梦时调用。
    返回 seed_text 或 None。
    """
    seed = load_seed(uid, char_id=char_id)
    if seed:
        try:
            from core.memory.scope import MemoryScope
            from core.memory.path_resolver import resolve_path
            from core.sandbox import safe_user_id
            scope = MemoryScope.reality_scope(safe_user_id(uid), char_id)
            path = resolve_path(scope, "dream_seed")
            if path.exists():
                path.unlink()
        except Exception:
            pass
    return seed
```

**注意**：`resolve_path(scope, "dream_seed")` 需要在 `core/memory/path_resolver.py` 里注册 `"dream_seed"` 键名，对应文件路径 `dream_seed.json`。检查 `path_resolver.py` 里的 `_SCOPE_TO_FILE` 或类似映射表，按格式追加：`"dream_seed": "dream_seed.json"`。

---

### Step 3：新建 `admin/routers/dream_seed.py`

HTTP API，供前端 Tauri invoke 调用：

```python
from fastapi import APIRouter, Depends, HTTPException
from admin.auth import verify_token
from core.activity import dream_seed as _ds

router = APIRouter()


@router.post("/start")
async def start(body: dict, auth=Depends(verify_token)):
    from core.config_loader import get_config
    uid = str(get_config().get("scheduler", {}).get("owner_id", "owner"))
    session_id = _ds.start_session(uid)
    return {"session_id": session_id}


@router.post("/chat")
async def chat(body: dict, auth=Depends(verify_token)):
    from core.config_loader import get_config
    uid = str(get_config().get("scheduler", {}).get("owner_id", "owner"))
    session_id = body.get("session_id", "")
    user_msg = body.get("message", "")
    if not session_id or not user_msg:
        raise HTTPException(status_code=400, detail="session_id and message required")
    _ds.append_turn(uid, session_id, "user", user_msg)
    reply = await _ds.generate_reply(uid, session_id, user_msg)
    if reply:
        _ds.append_turn(uid, session_id, "assistant", reply)
    return {"reply": reply}


@router.post("/close")
async def close(body: dict, auth=Depends(verify_token)):
    from core.config_loader import get_config
    uid = str(get_config().get("scheduler", {}).get("owner_id", "owner"))
    session_id = body.get("session_id", "")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id required")
    seed = await _ds.close_session(uid, session_id)
    return {"seed_text": seed or "", "success": bool(seed)}


@router.get("/state")
async def state(auth=Depends(verify_token)):
    from core.config_loader import get_config
    uid = str(get_config().get("scheduler", {}).get("owner_id", "owner"))
    seed = _ds.load_seed(uid)
    return {"has_seed": bool(seed), "seed_preview": (seed or "")[:40]}
```

在 `admin/admin_server.py` 注册路由：

```python
from admin.routers import dream_seed as _dream_seed_router
app.include_router(_dream_seed_router.router, prefix="/activity/dream_seed", tags=["dream_seed"])
```

---

### Step 4：`core/dream/dream_context.py` — 入梦时注入种子

在 `build_snapshot()` 里，在 `entry_reason` 处理完之后，尝试读取 dream seed 作为补充：

```python
# 入梦时读取梦境预构种子（如有），拼接到 entry_reason
from core.activity.dream_seed import consume_seed as _consume_seed
try:
    _seed = _consume_seed(user_id, char_id=char_id)
    if _seed:
        seed_prefix = f"今晚的梦境设定：{_seed}\n"
        snapshot["entry_reason"] = seed_prefix + (snapshot.get("entry_reason") or "")
except Exception as _seed_err:
    logger.warning("[dream_context] dream_seed inject failed: %s", _seed_err)
```

---

### Step 5：注册 `"dream_seed"` 路径键

在 `core/memory/path_resolver.py`（或类似的路径映射文件）里追加映射，确保 `resolve_path(scope, "dream_seed")` 解析到正确路径。

**具体方式**：找到文件里 `"history"` / `"mid_term"` 等映射的定义位置，按格式添加 `"dream_seed": "dream_seed.json"`。

---

## 前端实现步骤（Emerald-client）

1. 在 `ActivityRibbon`（或现有 activity tab 容器）里加一个 `dream_seed` tab
2. 新建 `windows/activity/components/DreamSeedPanel.tsx`：
   - Start 时调 `activity_dream_seed_start` Tauri command
   - 对话界面类似 GomokuCompanion，收发消息
   - Close 按钮调 `activity_dream_seed_close`，成功后显示种子预览
3. 在 `src-tauri/src/lib.rs` 里注册对应的 Tauri commands（转发 HTTP 请求到 backend）

---

## 注意事项

- `consume_seed` 是一次性的——入梦时消费，再次入梦不会重复注入同一颗种子
- TTL 12h：如果用户构建了种子但没入梦，第二天的种子会过期，避免陈旧内容注入
- `dream_seed_distill` 的 LLM 调用不走主 pipeline，直接调 llm_client，不写记忆
- activity store 依赖现有的 `ActivityStore`，检查 `core/activity/store.py` 确认接口兼容
