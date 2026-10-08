"""
Activity Registry P0-Lite — 静态元信息表。

这是所有已知 reality-side activity 的唯一权威声明点。

用途：
  1. 发现（/activity/list endpoint）
  2. contract smoke tests（route / Tauri command / frontend key / memory policy 断言）
  3. memory policy 声明（所有 activity 默认不写 short_term / hidden_state / event_log）

不做：
  - router 自动注册（admin_server.py 手工维护）
  - dynamic import / 插件系统
  - MCP bridge
  - 前端 component schema / hot reload
  - LLM tool dispatch
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass(frozen=True)
class MemoryPolicy:
    writes_short_term: bool = False
    writes_hidden_state: bool = False
    writes_event_log: bool = False
    transcript: Literal["activity_local", "none"] = "activity_local"
    # None = 该 activity 不生成结束摘要
    summary_threshold: Optional[int] = None
    # "episodic" = 结束摘要回流 episodic 记忆（close + threshold 满足时触发）
    # "deferred" = 预留但尚未接入（dream_seed 有自己的种子链，不复用本流程）
    # "none"     = 不写主记忆
    main_memory: Literal["deferred", "none", "episodic"] = "none"


@dataclass(frozen=True)
class ActivityMeta:
    id: str
    label: str
    kind: Literal["activity"] = "activity"
    enabled: bool = True
    # 完整 route prefix，含 /activity 前缀，例如 "/activity/gomoku"
    route_prefix: str = ""
    # 存储后端标识："reading_store" (activity_store.py) | "activity_store" (store.py)
    session_store: str = ""
    # 相对于 data/runtime/activity/ 的路径模板，用于 smoke test 断言
    session_dir_layout: str = ""
    # 前端 tab key，与 ActivityRibbon.tsx ActivityTab 对齐
    frontend_key: str = ""
    # Tauri command 函数名前缀，例如 "activity_reading_"
    tauri_command_prefix: str = ""
    # 完整 Tauri command 名列表（与 lib.rs async fn 名称一一对应）
    tauri_commands: tuple[str, ...] = field(default_factory=tuple)
    memory_policy: MemoryPolicy = field(default_factory=MemoryPolicy)
    has_companion_chat: bool = False
    # 相对于项目根目录的 docs 文件路径
    docs_path: str = ""
    # Idle TTL from updated_at; hard max from created_at. 0 disables that bound.
    # find_active_session lazy-expires stale actives so autonomy/UI share one truth.
    idle_ttl_seconds: int = 0
    max_age_seconds: int = 0


ACTIVITY_REGISTRY: tuple[ActivityMeta, ...] = (
    ActivityMeta(
        id="minecraft", label="Minecraft 共玩", enabled=False,
        route_prefix="/activity/minecraft", session_store="activity_store",
        session_dir_layout="{char_id}/{uid}/minecraft/{session_id}",
        memory_policy=MemoryPolicy(main_memory="none"), has_companion_chat=True,
        docs_path="docs/minecraft-activity.md", idle_ttl_seconds=60, max_age_seconds=4 * 3600,
    ),
    ActivityMeta(
        id="reading",
        label="一起看书",
        enabled=True,
        route_prefix="/activity/reading",
        session_store="reading_store",
        session_dir_layout="reading/{char_id}/{uid}/{session_id}",
        frontend_key="reading",
        tauri_command_prefix="activity_reading_",
        tauri_commands=(
            "activity_reading_start",
            "activity_reading_state",
            "activity_reading_page",
            "activity_reading_turn_page",
            "activity_reading_close",
            "activity_reading_chat",
            "activity_reading_library",
            "activity_reading_add_book",
            "activity_reading_start_from_library",
            "activity_reading_delete_book",
            "activity_reading_rename_book",
            "activity_reading_categorize_book",
        ),
        memory_policy=MemoryPolicy(
            transcript="activity_local",
            summary_threshold=2,  # current_page 阈值，≤ 视为翻了开头就关
            main_memory="episodic",
        ),
        has_companion_chat=True,
        docs_path="docs/reading-activity.md",
        idle_ttl_seconds=7 * 24 * 3600,
        max_age_seconds=14 * 24 * 3600,
    ),
    ActivityMeta(
        id="gomoku",
        label="五子棋",
        enabled=True,
        route_prefix="/activity/gomoku",
        session_store="activity_store",
        session_dir_layout="{char_id}/{uid}/gomoku/{session_id}",
        frontend_key="gomoku",
        tauri_command_prefix="activity_gomoku_",
        tauri_commands=(
            "activity_gomoku_start",
            "activity_gomoku_state",
            "activity_gomoku_move",
            "activity_gomoku_close",
            "activity_gomoku_chat",
            "activity_gomoku_ai_move",
            "activity_gomoku_comment",
        ),
        memory_policy=MemoryPolicy(
            transcript="activity_local",
            summary_threshold=12,
            main_memory="episodic",
        ),
        has_companion_chat=True,
        docs_path="docs/gomoku-activity.md",
        idle_ttl_seconds=72 * 3600,
        max_age_seconds=7 * 24 * 3600,
    ),
    ActivityMeta(
        id="chess",
        label="国际象棋",
        enabled=True,
        route_prefix="/activity/chess",
        session_store="activity_store",
        session_dir_layout="{char_id}/{uid}/chess/{session_id}",
        frontend_key="chess",
        tauri_command_prefix="activity_chess_",
        tauri_commands=(
            "activity_chess_start",
            "activity_chess_state",
            "activity_chess_move",
            "activity_chess_legal_moves",
            "activity_chess_close",
            "activity_chess_chat",
            "activity_chess_ai_move",
            "activity_chess_comment",
        ),
        memory_policy=MemoryPolicy(
            transcript="activity_local",
            summary_threshold=10,  # move_history 长度阈值，≤ 视为试棋噪声
            main_memory="episodic",
        ),
        has_companion_chat=True,
        docs_path="docs/chess-activity.md",
        idle_ttl_seconds=72 * 3600,
        max_age_seconds=7 * 24 * 3600,
    ),
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
            summary_threshold=6,
            main_memory="deferred",
        ),
        has_companion_chat=True,
        docs_path="docs/dream-seed-activity.md",
        idle_ttl_seconds=12 * 3600,
        max_age_seconds=24 * 3600,
    ),
)


def get_activity_meta(activity_id: str) -> ActivityMeta | None:
    """Return ActivityMeta for the given id, or None if not registered."""
    for meta in ACTIVITY_REGISTRY:
        if meta.id == activity_id:
            return meta
    return None


def list_enabled_activities() -> list[ActivityMeta]:
    """Return all enabled activities in registration order."""
    return [m for m in ACTIVITY_REGISTRY if m.enabled]
