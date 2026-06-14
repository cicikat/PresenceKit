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

# ── 多角色布局开关（默认 legacy = 维持旧单角色路径）──────────────────────────────
# S5 将 character_inner 类翻至 v1（global → per_char）；
# S6 将 reality 类翻至 v1（per_user → per_char_user）：
#     新布局 data/memory/{char_id}/{uid}/ 内存放各类型文件；dream 类另定。
# 各开关独立，可逐类翻转，不影响其他类。
_LAYOUT_CHARACTER_INNER: str = "v1"   # S5: global → characters/{char_id}/inner/
_LAYOUT_REALITY: str         = "v1"   # S6: per_user → memory/{char_id}/{uid}/
_LAYOUT_DREAM: str           = "v1"

# 迁移过渡标志：True 时写新路径的同时镜像写旧路径，默认不镜像。
_TRANSITION_CHARACTER_INNER: bool = False
_TRANSITION_REALITY: bool = False  # 无外部 caller，备用


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

    def wake_delivery_ledger(self, user_id: str | int) -> Path:
        return self._p("wake_delivery", f"{safe_user_id(user_id)}.json")

    # ── 记忆根目录 ─────────────────────────────────────────────────────────────
    def character_growth(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_CHARACTER_INNER == "legacy":
            return self._p("character_growth")
        return self._p("runtime", "characters", char_id, "character_growth")

    def diary_context(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_REALITY == "legacy":
            return self._p("diary_context")
        return self._p("chars", char_id, "diary_context")

    def pet_file(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_CHARACTER_INNER == "legacy":
            return self._p("pet.json")
        return self._p("runtime", "characters", char_id, "pet.json")

    def episodic_memory(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_REALITY == "legacy":
            return self._p("episodic_memory")
        return self._p("chars", char_id, "episodic_memory")

    def memory_index(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_REALITY == "legacy":
            return self._p("memory_index")
        return self._p("chars", char_id, "memory_index")

    def event_log(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_REALITY == "legacy":
            return self._p("event_log")
        return self._p("chars", char_id, "event_log")

    def group_context(self) -> Path:
        return self._p("group_context")

    def yexuan_inner_diary(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_CHARACTER_INNER == "legacy":
            return self._p("yexuan_inner", "diary")
        return self._p("runtime", "characters", char_id, "inner", "diary")

    def history(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_REALITY == "legacy":
            return self._p("history")
        return self._p("chars", char_id, "history")

    def profiles(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_REALITY == "legacy":
            return self._p("profiles")
        return self._p("chars", char_id, "profiles")

    def reminders(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_REALITY == "legacy":
            return self._p("reminders")
        return self._p("chars", char_id, "reminders")

    def diary_fallback(self) -> Path:
        return self._p("diary_fallback")

    def pending_perception_dir(self) -> Path:
        p = self._p("runtime", "pending_perception")
        p.mkdir(parents=True, exist_ok=True)
        return p

    def activity_snapshot(self, *, char_id: str) -> Path:
        if _LAYOUT_CHARACTER_INNER == "legacy":
            return self._p("activity_snapshot.json")
        return self._p("runtime", "characters", char_id, "inner", "activity_snapshot.json")

    def presence(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_CHARACTER_INNER == "legacy":
            return self._p("yexuan_inner", "presence.json")
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
        if _LAYOUT_CHARACTER_INNER == "legacy":
            return self._p("yexuan_inner", "mood_state.json")
        return self._p("runtime", "characters", char_id, "inner", "mood_state.json")

    def activity_pool(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_CHARACTER_INNER == "legacy":
            return Path("data/yexuan_inner/activity_pool.yaml")
        # S8 will physically move authored files; fall back to legacy if new not yet present
        new = Path(f"content/characters/{char_id}/activity_pool.yaml")
        return new if new.exists() else Path("data/yexuan_inner/activity_pool.yaml")

    def activity_state(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_CHARACTER_INNER == "legacy":
            return self._p("yexuan_inner", "activity_state.json")
        return self._p("runtime", "characters", char_id, "inner", "activity_state.json")

    def observations(self, *, char_id: str) -> Path:
        if _LAYOUT_CHARACTER_INNER == "legacy":
            return self._p("yexuan_inner", "observations.jsonl")
        return self._p("runtime", "characters", char_id, "inner", "observations.jsonl")

    def mid_term(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_REALITY == "legacy":
            return self._p("mid_term")
        return self._p("chars", char_id, "mid_term")

    def dreams_tmp_dir(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_DREAM == "legacy":
            return self._p("dreams", "tmp")
        return self._p("runtime", "dreams", char_id, "tmp")

    def dreams_archive_dir(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_DREAM == "legacy":
            return self._p("dreams", "archive")
        return self._p("runtime", "dreams", char_id, "archive")

    def dreams_summaries_dir(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_DREAM == "legacy":
            return self._p("dreams", "summaries")
        return self._p("runtime", "dreams", char_id, "summaries")

    def dreams_impressions_dir(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_DREAM == "legacy":
            return self._p("dreams", "impressions")
        return self._p("runtime", "dreams", char_id, "impressions")

    def dream_state_path(self, user_id: str | int, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_DREAM == "legacy":
            return self._p("dreams", "state", safe_user_id(user_id), "dream_state.json")
        return self._p("runtime", "dreams", char_id, "state", safe_user_id(user_id), "dream_state.json")

    def dream_settings_path(self, user_id: str | int, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_DREAM == "legacy":
            return self._p("dreams", "settings", safe_user_id(user_id) + ".json")
        return self._p("runtime", "dreams", char_id, "settings", safe_user_id(user_id) + ".json")

    def dream_hud_state_path(self, user_id: str | int, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_DREAM == "legacy":
            return self._p("dreams", "state", safe_user_id(user_id), "dream_hud_state.json")
        return self._p("runtime", "dreams", char_id, "state", safe_user_id(user_id), "dream_hud_state.json")

    def garden(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_CHARACTER_INNER == "legacy":
            return self._p("garden")
        return self._p("runtime", "characters", char_id, "garden")

    def author_notes_pool(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_CHARACTER_INNER == "legacy":
            return Path("characters/yexuan_author_notes.json")
        # S8 will physically move authored files; fall back to legacy if new not yet present
        new = Path(f"content/characters/{char_id}/{char_id}_author_notes.json")
        return new if new.exists() else Path(f"characters/{char_id}_author_notes.json")

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

    def jailbreaks_dir(self) -> Path:
        """characters/reality/jailbreaks/ 目录（authored，不走 data/ 沙盒偏移）"""
        if self.mode == "test":
            return self._base / "reality" / "jailbreaks"
        return Path("characters") / "reality" / "jailbreaks"

    # ── authored reality prompt assets（characters/reality/，不走 data/ 沙盒偏移）
    def jailbreak_entries(self) -> Path:
        """主路径：characters/reality/jailbreak_entries.json（无 data/ fallback）"""
        p = self._reality_p("jailbreak_entries.json")
        if not p.exists():
            if self.mode == "test":
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
        """主路径：characters/reality/lorebook.yaml（无 data/ fallback）"""
        p = self._reality_p("lorebook.yaml")
        if not p.exists():
            if self.mode == "test":
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
    def yexuan_traits(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_CHARACTER_INNER == "legacy":
            return Path("data/yexuan_traits.yaml")
        # S8 will physically move authored files; fall back to legacy if new not yet present
        new = Path(f"content/characters/{char_id}/traits.yaml")
        return new if new.exists() else Path("data/yexuan_traits.yaml")

    def jailbreak_presets_dir(self) -> Path:
        new = Path("content/jailbreak_presets")
        return new if new.exists() else Path("data/jailbreak_presets")

    def author_note_state(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_CHARACTER_INNER == "legacy":
            return self._p("yexuan_inner", "author_note_state.json")
        return self._p("runtime", "characters", char_id, "inner", "author_note_state.json")

    def trait_state(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_CHARACTER_INNER == "legacy":
            return self._p("yexuan_inner", "trait_state.json")
        return self._p("runtime", "characters", char_id, "inner", "trait_state.json")

    def dead_letter_queue(self) -> Path:
        return self._p("logs", "dead_letter_queue")

    def fixation_state_dir(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_REALITY == "legacy":
            return self._p("fixation_state")
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

    def user_identity_dir(self, *, char_id: str = "yexuan") -> Path:
        if _LAYOUT_REALITY == "legacy":
            return self._p("user_identity")
        return self._p("chars", char_id, "user_identity")

    # ── S6: per-user memory 新布局 ────────────────────────────────────────────
    def user_memory_root(self, user_id: str | int, *, char_id: str = "yexuan") -> Path:
        """S6: per-user memory 根目录: data/runtime/memory/{char_id}/{uid}/
        写入前调用方负责 .mkdir(parents=True, exist_ok=True)。"""
        return self._p("runtime", "memory", char_id, safe_user_id(user_id))

    def memory_char_root(self, *, char_id: str = "yexuan") -> Path:
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

    # ── Stage / multi-character group session ───────────────────────────────
    def stage_group_dir(self, *, group_id: str) -> Path:
        """data/runtime/groups/{group_id}/ — shared Stage session state."""
        return self._p("runtime", "groups", safe_user_id(group_id))

    def stage_meta(self, *, group_id: str) -> Path:
        return self.stage_group_dir(group_id=group_id) / "meta.json"

    def stage_transcript(self, *, group_id: str) -> Path:
        return self.stage_group_dir(group_id=group_id) / "transcript.json"

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
