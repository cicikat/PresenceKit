"""
数据路径治理元数据注册表

每条 DataPaths 公开路径方法对应一个 PathMeta，记录四个治理属性：
  durability  : canonical | derived | runtime | forensic | archive | authored
  domain      : reality | dream | shared | character_inner
  scope       : global | per_char | per_user | per_char_user | per_group
  git_policy  : track | ignore | seed

自检测试 tests/test_data_registry.py 遍历 DataPaths 所有公开路径方法，
断言每个都在 REGISTRY 中；新增方法忘记登记会使 CI fail。
"""

from dataclasses import dataclass
from typing import Literal

Durability = Literal["canonical", "derived", "runtime", "forensic", "archive", "authored"]
Domain     = Literal["reality", "dream", "shared", "character_inner"]
Scope      = Literal["global", "per_char", "per_user", "per_char_user", "per_group"]
GitPolicy  = Literal["track", "ignore", "seed", "ignore-but-authored"]
# ignore-but-authored: 私人手写本体 — 不进 git，但唯一副本在磁盘，丢失不可重建，须磁盘外备份。
# 与 ignore 的区别：ignore 是"可丢/可重建"，ignore-but-authored 是"不可重建、必须备份"。


@dataclass(frozen=True)
class PathMeta:
    durability: Durability
    domain:     Domain
    scope:      Scope
    git_policy: GitPolicy


# ── 注册表 ─────────────────────────────────────────────────────────────────────
# 键 = DataPaths 方法名（不含括号）。
REGISTRY: dict[str, PathMeta] = {

    # ── runtime: IPC / 队列 / 快照 / 临时态，重启可清 ─────────────────────────
    "channel_queue":          PathMeta("runtime",   "shared",          "global",        "ignore"),
    "mobile_queue":           PathMeta("runtime",   "shared",          "global",        "ignore"),
    "mobile_queue_seq":       PathMeta("runtime",   "shared",          "global",        "ignore"),
    "agent_actions":          PathMeta("runtime",   "shared",          "global",        "ignore"),
    "pending_perception_dir": PathMeta("runtime",   "shared",          "global",        "ignore"),
    "activity_snapshot":      PathMeta("runtime",   "character_inner", "per_char",      "ignore"),
    "dreams_tmp_dir":         PathMeta("runtime",   "dream",           "per_char_user", "ignore"),
    "scheduler_cooldowns":    PathMeta("canonical", "shared",          "global",        "ignore"),
    "scheduler_user_state":   PathMeta("runtime",   "shared",          "global",        "ignore"),
    "proactive_recent":       PathMeta("runtime",   "shared",          "global",        "ignore"),
    "proactive_ledger":       PathMeta("runtime",   "shared",          "global",        "ignore"),
    "wake_delivery_ledger":   PathMeta("canonical", "shared",          "per_user",      "ignore"),

    # ── forensic: 日志 / 观测 / DLQ，业务可丢 ────────────────────────────────
    "error_log":              PathMeta("forensic",  "shared",          "global",        "ignore"),
    "dead_letter_queue":      PathMeta("forensic",  "shared",          "global",        "ignore"),
    "fixation_log":           PathMeta("forensic",  "shared",          "global",        "ignore"),
    "trigger_state_log":      PathMeta("forensic",  "shared",          "global",        "ignore"),
    "gating_shadow_log":      PathMeta("forensic",  "shared",          "global",        "ignore"),
    "execute_dryrun_log":     PathMeta("forensic",  "shared",          "global",        "ignore"),
    "debug_llm_output_dir":   PathMeta("forensic",  "shared",          "global",        "ignore"),

    # ── derived: 可由 canonical 重建的缓存 / 索引 ─────────────────────────────
    "memory_index":           PathMeta("derived",   "reality",         "per_char_user", "ignore"),
    "image_cache_dir":        PathMeta("derived",   "shared",          "global",        "ignore"),
    "inbox_dir":              PathMeta("derived",   "shared",          "global",        "ignore"),

    # ── S6: per-user memory 新布局 ──────────────────────────────────────────────
    # user_memory_root: 每用户在 memory/{char_id}/{uid}/ 下的根目录（写入目标）
    "user_memory_root":       PathMeta("canonical", "reality",         "per_char_user", "ignore"),
    # memory_char_root: memory/{char_id}/ 扫描根（v1 用户枚举入口）
    "memory_char_root":       PathMeta("derived",   "reality",         "per_char",      "ignore"),

    # ── canonical · reality: 用户维度真值，丢 = 失忆 ──────────────────────────
    "history":                PathMeta("canonical", "reality",         "per_char_user", "ignore"),
    "mid_term":               PathMeta("canonical", "reality",         "per_char_user", "ignore"),
    "episodic_memory":        PathMeta("canonical", "reality",         "per_char_user", "ignore"),
    "profiles":               PathMeta("canonical", "reality",         "per_char_user", "ignore"),
    "user_identity_dir":      PathMeta("canonical", "reality",         "per_char_user", "ignore"),
    # diary_context/ 下文件为 {uid}.txt（core/memory/diary_context.py:21 确认，非 .json）
    "diary_context":          PathMeta("canonical", "reality",         "per_char_user", "ignore"),
    # diary_fallback/ 是 obsidian_path 未配置时的本地日记兜底目录（含人工写入的 .md）
    "diary_fallback":         PathMeta("canonical", "reality",         "global",        "ignore"),
    "reminders":              PathMeta("canonical", "reality",         "per_char_user", "ignore"),
    # event_log/ 30 天窗口内 canonical，窗口外同物理位置视为 archive；
    # 单目录双身份按主用途标 canonical，无需拆目录
    "event_log":              PathMeta("canonical", "reality",         "per_char_user", "ignore"),
    "group_context":          PathMeta("canonical", "reality",         "per_group",     "ignore"),
    "fixation_state_dir":     PathMeta("canonical", "reality",         "per_char_user", "ignore"),

    # ── canonical · character_inner: 角色状态真值 ─────────────────────────────
    # S5: global → per_char（路径迁至 characters/{char_id}/inner/）
    "mood_state":             PathMeta("canonical", "character_inner", "per_char",      "ignore"),
    "activity_state":         PathMeta("canonical", "character_inner", "per_char",      "ignore"),
    "trait_state":            PathMeta("canonical", "character_inner", "per_char",      "ignore"),
    "author_note_state":      PathMeta("canonical", "character_inner", "per_char",      "ignore"),
    "presence":               PathMeta("canonical", "character_inner", "per_char",      "ignore"),
    "yexuan_inner_diary":     PathMeta("canonical", "character_inner", "per_char",      "ignore"),
    "pet_file":               PathMeta("canonical", "character_inner", "per_char",      "ignore"),
    "garden":                 PathMeta("canonical", "character_inner", "per_char",      "ignore"),
    "character_growth":       PathMeta("canonical", "character_inner", "per_char_user", "ignore"),
    # observations.jsonl 由离线脚本写入，但被 prompt_builder.py:60 读作提示词层输入；
    # 丢失永久降低输出质量且无自动重建路径，判定 canonical
    "observations":           PathMeta("canonical", "character_inner", "per_char",      "ignore"),

    # ── canonical · dream: 梦域真值 ───────────────────────────────────────────
    "dreams_summaries_dir":   PathMeta("canonical", "dream",           "per_char_user", "ignore"),
    "dreams_impressions_dir": PathMeta("canonical", "dream",           "per_char_user", "ignore"),
    "dream_state_path":       PathMeta("canonical", "dream",           "per_user",      "ignore"),
    "dream_settings_path":    PathMeta("canonical", "dream",           "per_user",      "ignore"),

    # ── archive: 只追加，仅供人工复盘 ─────────────────────────────────────────
    "dreams_archive_dir":     PathMeta("archive",   "dream",           "per_char_user", "ignore"),

    # ── authored: 手工维护的静态配置 ──────────────────────────────────────────
    # activity_pool.yaml 私人手写活动池；target: content/characters/{char_id}/activity_pool.yaml
    # accessor 已有 new-primary/old-fallback；物理文件待 S8 迁移
    "activity_pool":          PathMeta("authored",  "character_inner", "per_char",      "ignore-but-authored"),
    # author_notes_pool: characters/{char_id}_author_notes.json — 私人，不可重建
    # target: content/characters/{char_id}/{char_id}_author_notes.json
    "author_notes_pool":      PathMeta("authored",  "character_inner", "per_char",      "ignore-but-authored"),
    # yexuan_traits.yaml 私人 traits；target: content/characters/{char_id}/traits.yaml
    # accessor 已有 new-primary/old-fallback；物理文件待 S8 迁移
    "yexuan_traits":          PathMeta("authored",  "character_inner", "per_char",      "ignore-but-authored"),
    # jailbreak_presets/ 目前无 reader（确认死代码），仅含 .example 模板，accessor 备将来使用
    "jailbreak_presets_dir":  PathMeta("authored",  "shared",          "global",        "track"),

    # ── admin 运行时可写（沙盒偏移，seed = 随仓库发默认值，运行时副本 ignore）──
    "jailbreak_entries":      PathMeta("canonical", "shared",          "global",        "seed"),
    "lorebook":               PathMeta("canonical", "shared",          "global",        "seed"),
    "relations":              PathMeta("canonical", "shared",          "global",        "seed"),
    "blacklist":              PathMeta("canonical", "shared",          "global",        "seed"),

    # ── runtime config: active prompt asset selection ─────────────────────────
    "active_prompt_assets":   PathMeta("runtime",   "shared",          "global",        "ignore"),

    # ── runtime: global safe/danger mode flag ─────────────────────────────────
    "meta_mode":              PathMeta("runtime",   "shared",          "global",        "ignore"),

    # ── runtime: prompt 层级消融开关（CC 任务 23 · B，fail-open 重建）─────────
    "prompt_layer_ablation":  PathMeta("runtime",   "shared",          "global",        "ignore"),

    # ── canonical: shared toy-project files, writable only through whitelist ─
    "very_formal_project_dir": PathMeta("canonical", "shared",         "global",        "ignore"),
    "root_dir":               PathMeta("runtime",   "shared",          "global",        "ignore"),

    # ── Stage: multi-character session truth ─────────────────────────────────
    # Stage may run in reality or dream; the persisted meta carries the domain.
    "stage_group_dir":        PathMeta("canonical", "shared",          "per_group",     "ignore"),
    "stage_meta":             PathMeta("canonical", "shared",          "per_group",     "ignore"),
    "stage_transcript":       PathMeta("canonical", "shared",          "per_group",     "ignore"),

    # ── authored: lorebooks / jailbreaks (characters/reality/ 目录，不走 data/) ─
    "lorebooks_dir":          PathMeta("authored",  "shared",          "global",        "ignore-but-authored"),
    "jailbreaks_dir":         PathMeta("authored",  "shared",          "global",        "ignore-but-authored"),

    # ── dream: HUD state ────────────────────────────────────────────────────────
    "dream_hud_state_path":   PathMeta("runtime",   "dream",           "per_user",      "ignore"),

    # ── runtime: per-character runtime assets dir ─────────────────────────────
    "runtime_character_dir":  PathMeta("runtime",   "character_inner", "per_char",      "ignore"),

    # ── Activity: reading activity paths ─────────────────────────────────────
    "reading_char_root":         PathMeta("runtime",    "reality", "per_char",      "ignore"),
    "reading_sessions_root":     PathMeta("runtime",    "reality", "per_char_user", "ignore"),
    "reading_session_dir":       PathMeta("runtime",    "reality", "per_char_user", "ignore"),
    # ── Library (shared book shelf) ───────────────────────────────────────────
    "reading_library_root":      PathMeta("authored",   "shared",  "global",        "ignore-but-authored"),
    "reading_library_books_dir": PathMeta("authored",   "shared",  "global",        "ignore-but-authored"),
    "reading_library_insights_dir": PathMeta("derived", "shared",  "global",        "ignore"),

    # ── Activity: generic session (char_id-first layout) ─────────────────────
    "activity_char_root":     PathMeta("runtime",   "reality",         "per_char",      "ignore"),
    "activity_sessions_root": PathMeta("runtime",   "reality",         "per_char_user", "ignore"),
    "activity_session_dir":   PathMeta("runtime",   "reality",         "per_char_user", "ignore"),

    # ── Library: manifest (shared, derived from physical book files) ──────────
    "reading_library_manifest": PathMeta("derived", "shared",          "global",        "ignore"),

    # ── Authored content: letter samples + knowledge base (per-char static) ───
    "letter_samples_dir":     PathMeta("authored",  "character_inner", "per_char",      "ignore-but-authored"),
    "letter_knowledge_dir":   PathMeta("authored",  "character_inner", "per_char",      "ignore-but-authored"),

    # ── Canonical: sent letter archive (per-char-user) ────────────────────────
    "sent_letters":           PathMeta("canonical", "reality",         "per_char_user", "ignore"),

    # ── SEC-AUTH-2: scoped token registry + audit (global, single-owner system) ─
    "auth_dir":               PathMeta("runtime",   "shared",          "global",        "ignore"),
    # tokens.yaml 只存 label+hash+scopes，丢失 = 边缘设备集体失联（需重签发），非记忆丢失，但非可随意重建
    "auth_tokens_file":       PathMeta("canonical", "shared",          "global",        "ignore"),
    "auth_audit_log":         PathMeta("forensic",  "shared",          "global",        "ignore"),

    # ── authored: dream worlds/presets (characters/dream_worlds/、characters/dream_presets/，不走 data/ 沙盒偏移) ─
    "dream_worlds_dir":       PathMeta("authored",  "dream",           "global",        "ignore-but-authored"),
    "dream_presets_dir":      PathMeta("authored",  "dream",           "global",        "ignore-but-authored"),

    # ── runtime: X3 自主联网搜索限流状态 ────────────────────────────────────────
    "web_autosearch_state":   PathMeta("runtime",   "shared",          "global",        "ignore"),

    # ── Coplay（陪玩模式，Brief 38-42）──────────────────────────────────────────
    "coplay_state_path":      PathMeta("canonical", "reality",         "per_char_user", "ignore"),
    "coplay_games_root":      PathMeta("runtime",   "reality",         "per_char_user", "ignore"),
    "coplay_game_dir":        PathMeta("runtime",   "reality",         "per_char_user", "ignore"),
    "coplay_game_state_path": PathMeta("canonical", "reality",         "per_char_user", "ignore"),
    # log.md 是收尾链（Brief 42）追加式浓缩记录，唯一权威来源，无自动重建路径
    "coplay_game_log_path":   PathMeta("canonical", "reality",         "per_char_user", "ignore"),
    # afterglow 是纯 TTL 软提示残留，过期即无意义，等价于 dream_hud_state 的定位
    "coplay_afterglow_path":  PathMeta("runtime",   "reality",         "per_char_user", "ignore"),
}

# ── retention 策略（由 scheduler.log_maintenance 每 24 小时执行，参数见 config.yaml retention.*）
# 键 = DataPaths 方法名；值 = GC 语义描述。
RETENTION_POLICY: dict[str, str] = {
    # derived — 可重建，无业务语义
    "inbox_dir":         "age-gc     max_age_days=7      原始上传裸文件，视觉/解析完成后即可删",
    "image_cache_dir":   "age+lru    max_age_days=30, max_files=500   sha256 视觉缓存，条数+龄双重 GC",
    # forensic — 可丢，不影响业务
    "dead_letter_queue": "count-cap  max_files=200       超出时删最旧；保持可监控但不无限增长",
    # canonical — 仅删 done 项，活跃数据不受影响
    "reminders":         "done-prune prune_done_days=30  done=True 且 remind_at 过期 N 天后清理",
    # archive — loader 不读，可无损删旧
    "dreams_archive_dir":"count-cap  max_files=200       最旧先删；distill/summary 仅在 close 时读一次",
    # canonical · compaction（非 forensic rotation）— 保留语义，不删业务数据
    # 保留最近 max_raw 条原始观察；超出条目按文本去重合并（weight 累加），独特 text 全部保留。
    # 区别于 forensic rotation：本策略不删除任何唯一语义条目，只消除文本重复冗余。
    "observations":      "compaction  max_raw=100  保留最近 N 条原始观察；旧条目文本去重后 weight 累加，唯一 text 全量保留",
}

# ── 孤立路径（未经 DataPaths 方法路由，由调用方直接构造，仅文档记录）─────────────
# 这些路径不受自检测试约束，需后续补充 DataPaths 方法并登记。
#
#   yexuan_inner/presence.json  ← core/presence.py 和 core/prompt_builder.py
#                                 直接调用 get_paths()._p("yexuan_inner","presence.json")
