"""
DataPaths：路径定义与沙盒类（实现层）。
胶水层保留于 core/sandbox.py；迁移辅助函数位于 core/migration.py。
"""

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
_DEFAULTS_ROOT = Path(__file__).parent.parent / "defaults"
_SAFE_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _read_default_char_id() -> str:
    """Read character.default from config.yaml once at import time.

    Callers that omit char_id will use this value, so changing config.yaml
    character.default naturally propagates to all path defaults — no more
    silent fallback to 'yexuan' on multi-character deployments.
    Falls back to 'yexuan' only if config is unreadable (startup edge case).
    """
    try:
        import yaml as _yaml
        cfg = _yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        default = (cfg.get("character", {}).get("default") or "").strip()
        return default if default else "yexuan"
    except Exception:
        return "yexuan"


_DEFAULT_CHAR_ID: str = _read_default_char_id()

# 公开导出别名（Brief 25 P1）：外部模块的 `char_id: str = ...` 默认参数统一 import 这个，
# 而不是各自硬编码字面量 "yexuan"。语义不变，值在 import 时冻结自 character.default。
DEFAULT_CHAR_ID: str = _DEFAULT_CHAR_ID

# ── 多角色布局开关（三者均已翻至 v1，legacy 分支已删除，见下方断言）───────────────
# S5 将 character_inner 类翻至 v1（global → per_char）；
# S6 将 reality 类翻至 v1（per_user → per_char_user）：
#     新布局 data/memory/{char_id}/{uid}/ 内存放各类型文件；dream 类另定。
_LAYOUT_CHARACTER_INNER: str = "v1"   # S5: global → characters/{char_id}/inner/
_LAYOUT_REALITY: str         = "v1"   # S6: per_user → memory/{char_id}/{uid}/
_LAYOUT_DREAM: str           = "v1"

# Brief 35：三个开关的 legacy 分支已删除（全部长期跑在 v1），开关常量本身保留但收窄为
# 启动断言——下个大版本再删常量本体。若看到这个 AssertionError，说明有人把值改回了
# "legacy"，但对应的 legacy 路径分支已经不存在了。
assert _LAYOUT_CHARACTER_INNER == "v1", "_LAYOUT_CHARACTER_INNER legacy 分支已删除，只支持 v1"
assert _LAYOUT_REALITY == "v1", "_LAYOUT_REALITY legacy 分支已删除，只支持 v1"
assert _LAYOUT_DREAM == "v1", "_LAYOUT_DREAM legacy 分支已删除，只支持 v1"


def safe_user_id(value: str | int) -> str:
    """Return a user id safe for use as a filename stem or directory name."""
    safe = str(value)
    if not safe or not _SAFE_USER_ID_RE.fullmatch(safe):
        raise ValueError(f"unsafe user_id: {value!r}")
    return safe


def _read_config_mode() -> str:
    try:
        import yaml
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("mode", "production")
    except Exception:
        return "production"


class DataPaths:
    def __init__(self, mode: str | None = None, test_session_id: str | None = None):
        if mode is None:
            mode = _read_config_mode()
        self.mode = mode

        if mode == "test":
            if test_session_id is None:
                test_session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.test_session_id = test_session_id
            self._base = Path("data") / "test_sandbox" / test_session_id
        else:
            self.test_session_id = None
            self._base = Path("data")

    def _p(self, *parts: str | Path) -> Path:
        clean_parts = []
        for part in parts:
            path = Path(part)
            if path.is_absolute() or path.anchor:
                raise ValueError(f"unsafe data path part: {part!r}")
            if any(segment == ".." for segment in path.parts):
                raise ValueError(f"unsafe data path part: {part!r}")
            clean_parts.append(path)

        target = self._base.joinpath(*clean_parts)
        base_resolved = self._base.resolve()
        target_resolved = target.resolve()
        try:
            target_resolved.relative_to(base_resolved)
        except ValueError as e:
            raise ValueError(f"data path escapes sandbox: {target}") from e
        return target

    def root_dir(self) -> Path:
        """沙盒数据根目录（生产模式 data/，测试模式 data/test_sandbox/{session}/）。
        供需要判断"是否落在沙盒内"的调用方使用（如 fs_browse 的隐式 deny 判断），
        不作为业务写入路径的起点——业务路径一律走本类其他具名方法。
        """
        return self._base

    # ── 桌宠端轮询文件（方案A：前缀同步到 config.yaml 的 data_prefix 字段）──────
    def channel_queue(self) -> Path:
        return self._p("runtime", "channel_queue.json")

    def mobile_queue(self) -> Path:
        return self._p("runtime", "mobile_queue.json")

    def mobile_queue_seq(self) -> Path:
        return self._p("runtime", "mobile_queue_seq")

    def agent_actions(self) -> Path:
        return self._p("runtime", "agent_actions.json")

    # ── 日志 / 状态 ────────────────────────────────────────────────────────────
    def error_log(self) -> Path:
        return self._p("logs", "error.log")

    def scheduler_cooldowns(self) -> Path:
        return self._p("scheduler_cooldowns.json")

    def scheduler_user_state(self) -> Path:
        return self._p("runtime", "scheduler_user_state.json")

    def proactive_recent(self) -> Path:
        return self._p("runtime", "proactive_recent.json")

    def proactive_ledger(self) -> Path:
        return self._p("runtime", "proactive_ledger.json")

    def wake_delivery_ledger(self, user_id: str | int) -> Path:
        return self._p("wake_delivery", f"{safe_user_id(user_id)}.json")

    # ── 记忆根目录 ─────────────────────────────────────────────────────────────
    def character_growth(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        # legacy/dead registered artifact（core/memory/path_resolver.py LEGACY_ARTIFACTS）；
        # get_growth 工具与 character_growth.py 模块已随 Brief 35 删除，本方法只为
        # path_resolver 的 legacy 兼容解析与一次性迁移脚本（scripts/migrate_data_v1.py）保留。
        return self._p("runtime", "characters", char_id, "character_growth")

    def diary_context(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("chars", char_id, "diary_context")

    def pet_file(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "characters", char_id, "pet.json")

    def episodic_memory(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("chars", char_id, "episodic_memory")

    def memory_index(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("chars", char_id, "memory_index")

    def event_log(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("chars", char_id, "event_log")

    def group_context(self) -> Path:
        return self._p("group_context")

    def yexuan_inner_diary(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "characters", char_id, "inner", "diary")

    def history(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("chars", char_id, "history")

    def profiles(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("chars", char_id, "profiles")

    def reminders(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("chars", char_id, "reminders")

    def diary_fallback(self) -> Path:
        return self._p("diary_fallback")

    def pending_perception_dir(self) -> Path:
        p = self._p("runtime", "pending_perception")
        p.mkdir(parents=True, exist_ok=True)
        return p

    def activity_snapshot(self, *, char_id: str) -> Path:
        return self._p("runtime", "characters", char_id, "inner", "activity_snapshot.json")

    def presence(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "characters", char_id, "inner", "presence.json")

    def inbox_dir(self) -> Path:
        p = self._p("inbox")
        p.mkdir(parents=True, exist_ok=True)
        return p

    def image_cache_dir(self) -> Path:
        p = self._p("cache", "image_cache")
        p.mkdir(parents=True, exist_ok=True)
        return p

    def mood_state(self, *, char_id: str) -> Path:
        return self._p("runtime", "characters", char_id, "inner", "mood_state.json")

    def activity_pool(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        # S8 will physically move authored files; fall back to legacy yexuan path if new not yet present
        new = Path(f"content/characters/{char_id}/activity_pool.yaml")
        return new if new.exists() else Path("data/yexuan_inner/activity_pool.yaml")

    def activity_state(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "characters", char_id, "inner", "activity_state.json")

    def observations(self, *, char_id: str) -> Path:
        return self._p("runtime", "characters", char_id, "inner", "observations.jsonl")

    def mid_term(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("chars", char_id, "mid_term")

    def dreams_tmp_dir(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "dreams", char_id, "tmp")

    def dreams_archive_dir(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "dreams", char_id, "archive")

    def dreams_summaries_dir(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "dreams", char_id, "summaries")

    def dreams_impressions_dir(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "dreams", char_id, "impressions")

    def dream_state_path(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "dreams", char_id, "state", safe_user_id(user_id), "dream_state.json")

    def dream_settings_path(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "dreams", char_id, "settings", safe_user_id(user_id) + ".json")

    def dream_hud_state_path(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "dreams", char_id, "state", safe_user_id(user_id), "dream_hud_state.json")

    def coplay_state_path(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "coplay", char_id, "state", safe_user_id(user_id), "coplay_state.json")

    def coplay_games_root(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """data/runtime/coplay/{char_id}/games/{uid}/ — parent of all per-game dirs (Brief 42 listing)."""
        return self._p("runtime", "coplay", char_id, "games", safe_user_id(user_id))

    def coplay_game_dir(self, user_id: str | int, game_id: str, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """data/runtime/coplay/{char_id}/games/{uid}/{game_id}/ — game_state.json + log.md (Brief 41/42).

        game_id can contain ':' (e.g. "steam:123", from core.coplay.watcher) which
        is illegal in a Windows path segment — sanitize before it ever reaches _p().
        """
        safe_game_id = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(game_id)).strip(". ") or "unknown"
        return self.coplay_games_root(user_id, char_id=char_id) / safe_game_id

    def coplay_game_state_path(self, user_id: str | int, game_id: str, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self.coplay_game_dir(user_id, game_id, char_id=char_id) / "state.json"

    def coplay_afterglow_path(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """Brief 42 — session 结束后的软提示残留（纯文本 TTL，不挂 hidden_state）。"""
        return self._p("runtime", "coplay", char_id, "afterglow", f"{safe_user_id(user_id)}.json")

    def coplay_game_log_path(self, user_id: str | int, game_id: str, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self.coplay_game_dir(user_id, game_id, char_id=char_id) / "log.md"

    def garden(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "characters", char_id, "garden")

    def author_notes_pool(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        # S8 will physically move authored files; fall back to default pool if new not yet present
        new = Path(f"content/characters/{char_id}/{char_id}_author_notes.json")
        legacy = Path(f"characters/{char_id}_author_notes.json")
        if new.exists():
            return new
        if legacy.exists():
            return legacy
        return Path("characters/default_author_notes.json")

    def _seed_if_missing(self, runtime_path: Path, defaults_name: str) -> Path:
        """运行时文件不存在时从 defaults/ 复制种子，然后返回运行时路径。"""
        if not runtime_path.exists():
            src = _DEFAULTS_ROOT / defaults_name
            if src.exists():
                runtime_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, runtime_path)
                logger.info(f"[sandbox] seeded {runtime_path} from {src}")
            else:
                logger.warning(f"[sandbox] defaults seed not found: {src}")
        return runtime_path

    def _reality_p(self, filename: str) -> Path:
        """Authored reality prompt assets 路径。
        production → characters/reality/{filename}
        test       → data/test_sandbox/{id}/reality/{filename}（沙盒隔离）
        不 fallback 到 data/。
        """
        if self.mode == "test":
            return self._base / "reality" / filename
        return Path("characters") / "reality" / filename

    # ── Runtime prompt asset selection config ────────────────────────────────
    def active_prompt_assets(self) -> Path:
        """Runtime config: data/runtime/active_prompt_assets.json

        First-run init: if the file doesn't exist, reads config.yaml character.default
        (via absolute _CONFIG_PATH) to seed active_character.
        Raises RuntimeError if character.default is not configured.

        Runtime reads: returns path directly; callers validate active_character content.
        No silent fallback to any hardcoded character id.
        """
        import json as _json
        p = self._p("runtime", "active_prompt_assets.json")
        if not p.exists():
            import yaml as _yaml
            try:
                cfg = _yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            except (FileNotFoundError, OSError, _yaml.YAMLError):
                cfg = {}
            raw_default = (cfg.get("character", {}).get("default") or "").strip()
            if not raw_default:
                raise RuntimeError(
                    "[data_paths] active_prompt_assets.json 不存在，"
                    "且 config.yaml character.default 未配置，无法初始化 active_character。"
                    "请在 config.yaml 中设置 character.default，或手动创建 active_prompt_assets.json。"
                )
            # Strip .json extension if config.default is a legacy filename
            char_id = raw_default[:-5] if raw_default.endswith(".json") else raw_default
            p.parent.mkdir(parents=True, exist_ok=True)
            default = {
                "active_character": char_id,
                "enabled_lorebooks": ["base"],
                "enabled_jailbreaks": ["base"],
            }
            p.write_text(_json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(
                f"[data_paths] 首次初始化 active_prompt_assets.json "
                f"(active_character={char_id!r} from config.default): {p}"
            )
        return p

    def lorebooks_dir(self) -> Path:
        """characters/reality/lorebooks/ 目录（authored，不走 data/ 沙盒偏移）"""
        if self.mode == "test":
            return self._base / "reality" / "lorebooks"
        return Path("characters") / "reality" / "lorebooks"

    def dream_worlds_dir(self) -> Path:
        """characters/dream_worlds/ 目录（authored，不走 data/ 沙盒偏移）"""
        if self.mode == "test":
            return self._base / "dream_worlds"
        return Path("characters") / "dream_worlds"

    def dream_presets_dir(self) -> Path:
        """characters/dream_presets/ 目录（authored，不走 data/ 沙盒偏移）"""
        if self.mode == "test":
            return self._base / "dream_presets"
        return Path("characters") / "dream_presets"

    def jailbreaks_dir(self) -> Path:
        """characters/reality/jailbreaks/ 目录（authored，不走 data/ 沙盒偏移）"""
        if self.mode == "test":
            return self._base / "reality" / "jailbreaks"
        return Path("characters") / "reality" / "jailbreaks"

    # ── authored reality prompt assets（characters/reality/，不走 data/ 沙盒偏移）
    def jailbreak_entries(self) -> Path:
        """主路径：characters/reality/jailbreak_entries.json（无 data/ fallback）。

        首次运行缺失时从 defaults/ 播种一个空壳（entries: []，不含任何私人条目），
        production/test 两种模式都播种——保证 fresh clone 不改配置就能直接启动；
        用户可随时用自己的真实内容覆盖播种出的文件。
        """
        p = self._reality_p("jailbreak_entries.json")
        if not p.exists():
            src = _DEFAULTS_ROOT / "jailbreak_entries.json"
            if src.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, p)
                logger.info(f"[sandbox] seeded {p} from {src}")
            else:
                logger.error(
                    f"[data_paths] authored asset missing: {p}  "
                    f"— 请从版本库恢复或从备份拷贝；运行时不自动生成。"
                )
        return p

    def lorebook(self) -> Path:
        """主路径：characters/reality/lorebook.yaml（无 data/ fallback）。

        首次运行缺失时从 defaults/ 播种一个空壳（entries: []），
        production/test 两种模式都播种，理由同 jailbreak_entries()。
        """
        p = self._reality_p("lorebook.yaml")
        if not p.exists():
            src = _DEFAULTS_ROOT / "lorebook.yaml"
            if src.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, p)
                logger.info(f"[sandbox] seeded {p} from {src}")
            else:
                logger.error(
                    f"[data_paths] authored asset missing: {p}  "
                    f"— 请从版本库恢复或从备份拷贝；运行时不自动生成。"
                )
        return p

    def relations(self) -> Path:
        return self._seed_if_missing(self._p("relations.yaml"), "relations.yaml")

    def blacklist(self) -> Path:
        return self._seed_if_missing(self._p("blacklist.yaml"), "blacklist.yaml")

    # ── 只读静态（不偏移，test 与 prod 共享原文件）───────────────────────────
    def yexuan_traits(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        # S8 will physically move authored files; fall back to legacy if new not yet present
        new = Path(f"content/characters/{char_id}/traits.yaml")
        return new if new.exists() else Path("data/yexuan_traits.yaml")

    def jailbreak_presets_dir(self) -> Path:
        new = Path("content/jailbreak_presets")
        return new if new.exists() else Path("data/jailbreak_presets")

    def author_note_state(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "characters", char_id, "inner", "author_note_state.json")

    def trait_state(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "characters", char_id, "inner", "trait_state.json")

    def dead_letter_queue(self) -> Path:
        return self._p("logs", "dead_letter_queue")

    def fixation_state_dir(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("chars", char_id, "fixation_state")

    def fixation_log(self) -> Path:
        return self._p("logs", "fixation.jsonl")

    def trigger_state_log(self) -> Path:
        return self._p("logs", "trigger_state.jsonl")

    def gating_shadow_log(self) -> Path:
        return self._p("logs", "gating_shadow.jsonl")

    def execute_dryrun_log(self) -> Path:
        return self._p("logs", "execute_dryrun.jsonl")

    def debug_llm_output_dir(self) -> Path:
        return self._p("debug", "llm_output")

    def user_identity_dir(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("chars", char_id, "user_identity")

    # ── S6: per-user memory 新布局 ────────────────────────────────────────────
    def user_memory_root(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """S6: per-user memory 根目录: data/runtime/memory/{char_id}/{uid}/
        写入前调用方负责 .mkdir(parents=True, exist_ok=True)。"""
        return self._p("runtime", "memory", char_id, safe_user_id(user_id))

    # ── 信件内容资产（authored static content）────────────────────────────────
    def letter_samples_dir(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """示范信件库目录（静态内容）: content/characters/{char_id}/letter_samples/"""
        return Path(f"content/characters/{char_id}/letter_samples")

    def letter_knowledge_dir(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """知识库目录（静态内容）: content/characters/{char_id}/knowledge/"""
        return Path(f"content/characters/{char_id}/knowledge")

    def sent_letters(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """已发送信件归档: data/runtime/memory/{char_id}/{uid}/sent_letters.json"""
        return self.user_memory_root(user_id, char_id=char_id) / "sent_letters.json"

    def memory_char_root(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """S6: per-char memory 根目录: data/runtime/memory/{char_id}/
        用于 v1 模式下枚举所有用户（各 uid 是其直接子目录）。"""
        return self._p("runtime", "memory", char_id)

    def runtime_character_dir(self, *, char_id: str) -> Path:
        """Per-character runtime override dir: data/runtime/characters/{char_id}/
        Used for runtime-uploaded assets (e.g. avatar overrides)."""
        return self._p("runtime", "characters", char_id)

    # ── Global runtime meta flags ────────────────────────────────────────────
    def meta_mode(self) -> Path:
        """data/runtime/meta_mode.json — global safe/danger mode switch."""
        return self._p("runtime", "meta_mode.json")

    # ── SEC-AUTH-2: scoped token registry + audit ────────────────────────────
    def auth_dir(self) -> Path:
        """data/runtime/auth/ — token registry + audit log directory."""
        return self._p("runtime", "auth")

    def auth_tokens_file(self) -> Path:
        """data/runtime/auth/tokens.yaml — token registry (label/hash/scopes)."""
        return self.auth_dir() / "tokens.yaml"

    def auth_audit_log(self) -> Path:
        """data/runtime/auth/audit.jsonl — token lifecycle + auth failure audit trail."""
        return self.auth_dir() / "audit.jsonl"

    def web_autosearch_state(self) -> Path:
        """data/runtime/web_autosearch_state.json — rate-limit state for autonomous web search (X3)."""
        return self._p("runtime", "web_autosearch_state.json")

    def prompt_layer_ablation(self) -> Path:
        """Runtime config: data/runtime/prompt_layer_ablation.json — layer ablation switches (CC 任务 23)."""
        return self._p("runtime", "prompt_layer_ablation.json")

    def very_formal_project_dir(self) -> Path:
        """data/very_formal_project/ — whitelisted toy files only."""
        return self._p("very_formal_project")

    # ── Stage / multi-character group session ───────────────────────────────
    def stage_group_dir(self, *, group_id: str) -> Path:
        """data/runtime/groups/{group_id}/ — shared Stage session state."""
        return self._p("runtime", "groups", safe_user_id(group_id))

    def stage_meta(self, *, group_id: str) -> Path:
        return self.stage_group_dir(group_id=group_id) / "meta.json"

    def stage_transcript(self, *, group_id: str) -> Path:
        return self.stage_group_dir(group_id=group_id) / "transcript.json"

    def stage_arbiter_trace(self, *, group_id: str) -> Path:
        """Append-only Stage arbiter decision trace for one group."""
        return self.stage_group_dir(group_id=group_id) / "arbiter_trace.jsonl"

    # ── Activity: reading ─────────────────────────────────────────────────────
    def reading_char_root(self, *, char_id: str) -> Path:
        """data/runtime/activity/reading/{char_id}/  — enumerate all uid subdirs."""
        return self._p("runtime", "activity", "reading", char_id)

    def reading_sessions_root(self, *, char_id: str, uid: str) -> Path:
        """data/runtime/activity/reading/{char_id}/{uid}/"""
        return self._p("runtime", "activity", "reading", char_id, safe_user_id(uid))

    def reading_session_dir(self, *, char_id: str, uid: str, session_id: str) -> Path:
        """data/runtime/activity/reading/{char_id}/{uid}/{session_id}/"""
        return self._p(
            "runtime", "activity", "reading",
            char_id, safe_user_id(uid), safe_user_id(session_id),
        )

    # ── Library (shared book shelf across chars) ──────────────────────────────
    def reading_library_root(self) -> Path:
        """data/library/  — shared book library root."""
        return self._p("library")

    def reading_library_books_dir(self) -> Path:
        """data/library/books/  — user-placed PDF files."""
        return self._p("library", "books")

    def reading_library_insights_dir(self, *, book_id: str) -> Path:
        """data/library/insights/{book_id}/  — Yexuan's reading insights per book."""
        return self._p("library", "insights", safe_user_id(book_id))

    def reading_library_manifest(self) -> Path:
        """data/library/manifest.json — book metadata registry (id, title, category, filename)."""
        return self._p("library", "manifest.json")

    # ── Activity: generic session (char_id-first layout) ─────────────────────
    def activity_char_root(self, *, char_id: str) -> Path:
        """data/runtime/activity/{char_id}/  — enumerate uid subdirs."""
        return self._p("runtime", "activity", char_id)

    def activity_sessions_root(self, *, char_id: str, uid: str, activity_type: str) -> Path:
        """data/runtime/activity/{char_id}/{uid}/{activity_type}/"""
        return self._p("runtime", "activity", char_id, safe_user_id(uid), activity_type)

    def activity_session_dir(self, *, char_id: str, uid: str, activity_type: str, session_id: str) -> Path:
        """data/runtime/activity/{char_id}/{uid}/{activity_type}/{session_id}/"""
        return self._p(
            "runtime", "activity",
            char_id, safe_user_id(uid), activity_type, safe_user_id(session_id),
        )

    def cleanup(self):
        if self.mode != "test":
            raise RuntimeError("只有 test 模式才能执行 cleanup()")
        sandbox_dir = Path("data") / "test_sandbox" / self.test_session_id
        if sandbox_dir.exists():
            shutil.rmtree(sandbox_dir)
            logger.info(f"[sandbox] 已清理沙盒目录: {sandbox_dir}")
        else:
            logger.info(f"[sandbox] 沙盒目录不存在，无需清理: {sandbox_dir}")
