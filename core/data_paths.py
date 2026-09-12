"""
DataPaths：路径定义与沙盒类（实现层）。
胶水层保留于 core/sandbox.py；迁移辅助函数位于 core/migration.py。
"""

import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from core.test_data_guard import assert_production_identity_allowed

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(
    os.environ.get("PRESENCEKIT_CONFIG_PATH", Path(__file__).parent.parent / "config.yaml")
)
_BUNDLED_ROOT = Path("bundled")
_USERDATA_ROOT = Path("userdata")
_SAFE_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _read_default_char_id() -> str:
    """Read character.default from config.yaml once at import time.

    Callers that omit char_id will use this value, so changing config.yaml
    character.default naturally propagates to all path defaults — no more
    silent fallback to a private deployment id on multi-character deployments.
    Falls back to 'default' only if config is empty or unreadable (startup edge case).
    """
    try:
        import yaml as _yaml
        cfg = _yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        default = (cfg.get("character", {}).get("default") or "").strip()
        return default if default else "default"
    except Exception:
        return "default"


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


def _safe_authored_component(value: str) -> str:
    """Validate one user-authored filename component without ASCII-only policy."""
    component = str(value)
    if not component or component in {".", ".."} or Path(component).name != component:
        raise ValueError(f"unsafe authored asset component: {value!r}")
    return component


def _read_config_mode() -> str:
    try:
        import yaml
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("mode", "production")
    except Exception:
        return "production"


class DataPaths:
    def __init__(
        self,
        mode: str | None = None,
        test_session_id: str | None = None,
        project_root: str | Path | None = None,
    ):
        if mode is None:
            mode = _read_config_mode()
        self.mode = mode
        self._project_root = (
            Path(project_root).expanduser().resolve()
            if project_root is not None
            else None
        )

        if mode == "test":
            if test_session_id is None:
                test_session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.test_session_id = safe_user_id(test_session_id)
            relative_base = Path("data") / "test_sandbox" / self.test_session_id
        else:
            self.test_session_id = None
            relative_base = Path("data")
        self._base = self._project_path(relative_base)

    def _project_path(self, relative: Path) -> Path:
        return (
            self._project_root / relative
            if self._project_root is not None
            else relative
        )

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

    def test_data_archive_root(self) -> Path:
        """Archive root for confirmed test-only runtime remnants."""
        return self._p("_archive", "test_data")

    def layout_version(self) -> Path:
        """data/layout_version.json — v1 installation/data compatibility baseline."""
        return self._p("layout_version.json")

    # ── User-authored assets (never part of the runtime data sandbox) ───────
    # ``data/`` is canonical runtime state and deliberately remains separate.
    # These accessors centralize the C1 migration from several root-level
    # private-asset directories to one user-owned root.
    def userdata_root(self) -> Path:
        return self._project_path(_USERDATA_ROOT)

    # ── Packaged public assets (read-only; never a writer target) ──────────
    def bundled_root(self) -> Path:
        """Root for release-owned public assets shipped with the program."""
        return self._project_path(_BUNDLED_ROOT)

    def bundled_default_character_dir(self) -> Path:
        return self.bundled_root() / "characters" / "default"

    def bundled_default_character_card(self) -> Path:
        return self.bundled_default_character_dir() / "card.json"

    def bundled_reality_seed_dir(self) -> Path:
        return self.bundled_root() / "seeds" / "reality"

    def bundled_dream_seed_dir(self) -> Path:
        return self.bundled_root() / "seeds" / "dream"

    def bundled_dream_preset_seed_dir(self) -> Path:
        return self.bundled_dream_seed_dir() / "presets"

    def bundled_templates_dir(self) -> Path:
        return self.bundled_root() / "templates"

    def bundled_examples_dir(self) -> Path:
        return self.bundled_root() / "examples"

    def user_stickers_dir(self) -> Path:
        return self.userdata_root() / "assets" / "stickers"

    def legacy_stickers_dir(self) -> Path:
        return Path("assets") / "stickers"

    def stickers_dir(self) -> Path:
        primary = self.user_stickers_dir()
        return primary if primary.exists() else self.legacy_stickers_dir()

    def sticker_packs_root(self) -> Path:
        """角色专属表情包池根目录（角色资产路由：presence_ext.sticker_pack）。

        每个 pack 是 sticker_packs_root()/<pack_name>/<emotion>/ 的子目录，结构与
        通用池 stickers_dir()/<emotion>/ 一致；某个情绪在专属包里没有图片时，
        core/output/sticker.py 会回落读通用池，专属包不需要覆盖全部六种情绪。
        """
        return self.userdata_root() / "assets" / "stickers_packs"

    def sticker_pack_dir(self, pack_name: str) -> Path:
        return self.sticker_packs_root() / pack_name

    def user_character_voice_dir(self, *, char_id: str) -> Path:
        """Canonical authored voice assets for one character.

        This is a writer target.  Consumers that support legacy installations
        should use ``character_voice_dirs`` and keep the effective source in
        their own metadata rather than exposing a filesystem path.
        """
        return self.user_authored_character_dir(char_id=char_id) / "voice"

    def legacy_character_voice_dir(self, *, char_id: str) -> Path:
        return self.legacy_authored_character_dir(char_id=char_id) / "voice"

    def character_voice_dirs(self, *, char_id: str) -> tuple[Path, Path]:
        return (
            self.user_character_voice_dir(char_id=char_id),
            self.legacy_character_voice_dir(char_id=char_id),
        )

    def user_live2d_root(self) -> Path:
        """Canonical authored Live2D package root (backend-only in v1)."""
        return self.userdata_root() / "assets" / "live2d"

    def user_model3d_root(self) -> Path:
        """Canonical authored 3D package root (backend-only in v1)."""
        return self.userdata_root() / "assets" / "model3d"

    def user_character_cards_dir(self) -> Path:
        return self.userdata_root() / "characters" / "cards"

    def character_card_write_path(self, char_id: str) -> Path:
        """Canonical writer target for a JSON character card.

        This is intentionally separate from ``character_card_dirs()``: callers
        resolving a legacy fallback must never reuse that read path for writes.
        Character cards are authored assets rather than runtime state, so tests
        isolate this relative root by changing into ``tmp_path``.
        """
        safe_id = _safe_authored_component(char_id)
        return self.user_character_cards_dir() / f"{safe_id}.json"

    def character_card_write_file(self, filename: str) -> Path:
        """Canonical writer target for a validated card filename (.json/.txt/.md)."""
        name = Path(filename)
        if name.name != filename or name.suffix.lower() not in {".json", ".txt", ".md"}:
            raise ValueError(f"unsafe character card filename: {filename!r}")
        _safe_authored_component(name.stem)
        return self.user_character_cards_dir() / name.name

    def legacy_character_cards_dir(self) -> Path:
        return Path("characters")

    def character_card_dirs(self) -> tuple[Path, Path, Path]:
        """Card read layers: user-authored, packaged public, then legacy."""
        return (
            self.user_character_cards_dir(),
            self.bundled_default_character_dir(),
            self.legacy_character_cards_dir(),
        )

    def user_authored_character_dir(self, *, char_id: str) -> Path:
        return self.userdata_root() / "characters" / "authored" / safe_user_id(char_id)

    def legacy_authored_character_dir(self, *, char_id: str) -> Path:
        return Path("content") / "characters" / safe_user_id(char_id)

    def authored_character_dir(self, *, char_id: str) -> Path:
        """Compatibility single-directory resolver.

        Resource-level consumers must use ``authored_character_dirs`` so an
        empty or partial userdata directory cannot shadow legacy files.
        """
        primary = self.user_authored_character_dir(char_id=char_id)
        return primary if primary.exists() else self.legacy_authored_character_dir(char_id=char_id)

    def authored_character_dirs(self, *, char_id: str) -> tuple[Path, Path | None]:
        """Return (user, legacy) read layers for one character's authored files."""
        return (
            self.user_authored_character_dir(char_id=char_id),
            self.legacy_authored_character_dir(char_id=char_id),
        )

    def user_reality_dir(self) -> Path:
        return self.userdata_root() / "characters" / "reality"

    def reality_lorebook_write_path(self) -> Path:
        """Canonical writer target for the combined Reality lorebook."""
        if self.mode == "test":
            return self._base / "reality" / "lorebook.yaml"
        return self.user_reality_dir() / "lorebook.yaml"

    def reality_jailbreak_write_path(self) -> Path:
        """Canonical writer target for the combined Reality jailbreak entries."""
        if self.mode == "test":
            return self._base / "reality" / "jailbreak_entries.json"
        return self.user_reality_dir() / "jailbreak_entries.json"

    def reality_lorebook_write_dir(self) -> Path:
        """Canonical writer root for modular Reality lorebooks."""
        if self.mode == "test":
            return self._base / "reality" / "lorebooks"
        return self.user_reality_dir() / "lorebooks"

    def reality_jailbreak_write_dir(self) -> Path:
        """Canonical writer root for modular Reality jailbreaks."""
        if self.mode == "test":
            return self._base / "reality" / "jailbreaks"
        return self.user_reality_dir() / "jailbreaks"

    def legacy_reality_dir(self) -> Path:
        return Path("characters") / "reality"

    def user_dream_worlds_dir(self) -> Path:
        return self.userdata_root() / "characters" / "dream" / "worlds"

    def dream_world_write_dir(self, world_id: str) -> Path:
        """Canonical writer target for one Dream world package."""
        safe_id = _safe_authored_component(world_id)
        if self.mode == "test":
            return self._base / "dream_worlds" / safe_id
        return self.user_dream_worlds_dir() / safe_id

    def legacy_dream_worlds_dir(self) -> Path:
        return Path("characters") / "dream_worlds"

    def user_dream_presets_dir(self) -> Path:
        return self.userdata_root() / "characters" / "dream" / "presets"

    def dream_preset_write_path(self, preset_id: str) -> Path:
        """Canonical writer target for one Dream preset."""
        safe_id = _safe_authored_component(preset_id)
        if self.mode == "test":
            return self._base / "dream_presets" / f"{safe_id}.md"
        return self.user_dream_presets_dir() / f"{safe_id}.md"

    def legacy_dream_presets_dir(self) -> Path:
        return Path("characters") / "dream_presets"

    def user_dream_scenarios_dir(self) -> Path:
        """Canonical private authored root for Dream scenario scripts."""
        return self.userdata_root() / "characters" / "dream" / "scenarios"

    def dream_scenario_write_path(self, script_id: str) -> Path:
        """Canonical writer target for one Dream scenario script."""
        safe_id = _safe_authored_component(script_id)
        if self.mode == "test":
            return self._base / "dream" / "scenarios" / f"{safe_id}.yaml"
        return self.user_dream_scenarios_dir() / f"{safe_id}.yaml"

    def legacy_dream_scenarios_dir(self) -> Path:
        """Historical authored root retained as a read-only fallback."""
        return Path("data") / "dream" / "scenarios"

    # ── 桌宠端轮询文件（方案A：前缀同步到 config.yaml 的 data_prefix 字段）──────
    def channel_queue(self) -> Path:
        return self._p("runtime", "channel_queue.json")

    def mobile_queue(self) -> Path:
        return self._p("runtime", "mobile_queue.json")

    def mobile_queue_seq(self) -> Path:
        return self._p("runtime", "mobile_queue_seq")

    def agent_actions(self) -> Path:
        return self._p("runtime", "agent_actions.json")

    def dream_group_transition_audit(self, group_id: str) -> Path:
        """Append-only, non-semantic transition audit for one Dream Stage group."""
        return self.dream_group_dir(group_id=group_id) / "transition_audit.jsonl"

    def phone_control_tasks(self) -> Path:
        """data/runtime/phone_control_tasks.json — 手机自动化任务 session 状态

        (task_id -> {step, created_at, status})，供 /phone_control/step 做步数上限/超时判定。
        """
        return self._p("runtime", "phone_control_tasks.json")

    # ── 日志 / 状态 ────────────────────────────────────────────────────────────
    def error_log(self) -> Path:
        return self._p("logs", "error.log")

    def scheduler_cooldowns(self) -> Path:
        return self._p("scheduler_cooldowns.json")

    def scheduler_user_state(self) -> Path:
        return self._p("runtime", "scheduler_user_state.json")

    def service_state(self) -> Path:
        """Lifecycle-owned PID marker used only for fail-closed offline backups."""
        return self._p("runtime", "service_state.json")

    def proactive_ledger(self) -> Path:
        return self._p("runtime", "proactive_ledger.json")

    def autonomy_state(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """Durable internal-autonomy state, isolated per character and owner."""
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p("runtime", "autonomy", safe_user_id(char_id), safe_user_id(user_id), "state.json")

    def agent_runtime_reality_root(self) -> Path:
        """Reality Task Manager root; Dream uses a separate future root."""
        return self._p("runtime", "agent_runtime", "reality")

    def agent_runtime_task_state(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p(
            "runtime", "agent_runtime", "reality", "tasks",
            safe_user_id(char_id), safe_user_id(user_id), "state.json",
        )

    def agent_runtime_work_session_state(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """Durable Reality Agent Work Session records, separate from Task state."""
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p(
            "runtime", "agent_runtime", "reality", "work_sessions",
            safe_user_id(char_id), safe_user_id(user_id), "state.json",
        )

    def agent_runtime_work_sessions_root(self) -> Path:
        """Root used only to enumerate redacted Reality work-session state."""
        return self._p("runtime", "agent_runtime", "reality", "work_sessions")

    def agent_runtime_browser_profiles_root(self) -> Path:
        """Private per-task browser profiles; never exposed to receipts."""
        return self._p("runtime", "agent_runtime", "reality", "browser_profiles")

    def agent_runtime_workspace_versions_dir(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """Private version snapshots for the controlled Reality workspace."""
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p(
            "runtime", "agent_runtime", "reality", "workspace_versions",
            safe_user_id(char_id), safe_user_id(user_id),
        )

    def self_management_state(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """Character-scoped Self Capability state; never shares autonomy state."""
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p("runtime", "self_management", safe_user_id(char_id), safe_user_id(user_id), "state.json")

    def self_management_audit(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """Append-only, capability-only audit trail for one character and owner."""
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p("runtime", "self_management", safe_user_id(char_id), safe_user_id(user_id), "audit.jsonl")

    def wake_delivery_ledger(self, user_id: str | int) -> Path:
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p("wake_delivery", f"{safe_user_id(user_id)}.json")

    def wake_bridge_root(self) -> Path:
        """Root for Wake Bridge source checkpoints; callers must still scope children."""
        return self._p("runtime", "wake_bridge")

    def wake_bridge_state(self, user_id: str | int, *, char_id: str, provider: str) -> Path:
        """Persistent external-stimulus dedupe/cursor state, scoped by char, owner and provider."""
        assert_production_identity_allowed(user_id, mode=self.mode)
        return (
            self.wake_bridge_root() / safe_user_id(char_id) / safe_user_id(user_id)
            / f"{safe_user_id(provider)}.json"
        )

    # ── 记忆根目录 ─────────────────────────────────────────────────────────────
    def character_growth(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        # legacy/dead registered artifact（core/memory/path_resolver.py LEGACY_ARTIFACTS）；
        # get_growth 工具与 character_growth.py 模块已随 Brief 35 删除，本方法只为
        # path_resolver 的 legacy 兼容解析与一次性迁移脚本（scripts/migrate_data_v1.py）保留。
        return self._p("runtime", "characters", char_id, "character_growth")

    def diary_context(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("chars", char_id, "diary_context")

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

    def character_inner_diary(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """Canonical per-character authored diary path."""
        return self._p("runtime", "characters", safe_user_id(char_id), "inner", "diary")

    def history(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("chars", char_id, "history")

    def profiles(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("chars", char_id, "profiles")

    def reminders(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("chars", char_id, "reminders")

    def diary_fallback(self) -> Path:
        return self._p("diary_fallback")

    def character_document_root(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """Private searchable material, isolated by the Reality uid and character."""
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p("runtime", "character_library", safe_user_id(char_id), safe_user_id(user_id))

    def character_document_index(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self.character_document_root(user_id, char_id=char_id) / "index.json"

    def character_document_stats(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self.character_document_root(user_id, char_id=char_id) / "stats.json"

    def character_document_blob_dir(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self.character_document_root(user_id, char_id=char_id) / "blobs"

    def pending_perception_dir(self) -> Path:
        p = self._p("runtime", "pending_perception")
        p.mkdir(parents=True, exist_ok=True)
        return p

    def visual_trace_log(self) -> Path:
        """Brief 56 shadow-only VLM observation trace (never stores images)."""
        return self._p("runtime", "perception", "visual_trace.jsonl")

    def event_context_trace(self) -> Path:
        """Content-free Brief 217 EventContext propagation trace."""
        return self._p("runtime", "observability", "event_context.jsonl")

    def api_call_log(self) -> Path:
        """Fail-open external API observability ledger, rotated by the writer."""
        return self._p("runtime", "observability", "api_calls.jsonl")

    def conversation_stats_db(self) -> Path:
        """Numeric calendar activity, independent of expiring forensic logs."""
        return self._p("runtime", "observability", "conversation_stats.sqlite3")

    def llm_reasoning_db(self) -> Path:
        """Private archive of reasoning explicitly returned by model APIs."""
        return self._p("runtime", "observability", "llm_reasoning.sqlite3")

    def ime_drafts_db(self) -> Path:
        return self._p("runtime", "integrations", "ime_drafts.sqlite3")

    def life_records_db(self) -> Path:
        return self._p("runtime", "life_records", "records.sqlite3")

    def mail_execution_log(self) -> Path:
        """Sanitized forensic outcomes for scheduler-driven character letters."""
        return self._p("runtime", "observability", "mail_executions.jsonl")

    def letter_weekly_state(self) -> Path:
        return self._p("runtime", "mail", "letter_weekly_state.json")

    def llm_debug_request_log(self) -> Path:
        """Explicit opt-in LLM request snapshots; contains sensitive prompt content."""
        return self._p("runtime", "observability", "llm_debug_requests.jsonl")

    def spend_ledger(self) -> Path:
        """Brief 57 append-only spending mandate ledger."""
        return self._p("runtime", "spend", "ledger.jsonl")

    def spend_mandates(self) -> Path:
        """Brief 63 purchase-intent journal; it is absent until that feature is enabled."""
        return self._p("runtime", "spend", "mandates.jsonl")

    def interest_state(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "characters", char_id, "inner", "interest_state.json")

    def growth_works_dir(self, interest_id: str, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        safe_interest_id = safe_user_id(interest_id)
        return self._p("runtime", "characters", char_id, "works", safe_interest_id)

    def growth_note(self, interest_id: str, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        safe_interest_id = safe_user_id(interest_id)
        return self._p("runtime", "characters", char_id, "notes", f"{safe_interest_id}.md")

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
        primary = self.user_authored_character_dir(char_id=char_id) / "activity_pool.yaml"
        legacy = self.legacy_authored_character_dir(char_id=char_id) / "activity_pool.yaml"
        bundled = self.bundled_default_character_dir() / "activity_pool.yaml"
        legacy_default = self.legacy_authored_character_dir(char_id="default") / "activity_pool.yaml"
        if primary.exists():
            return primary
        if char_id == "default" and bundled.exists():
            return bundled
        if legacy.exists():
            return legacy
        if bundled.exists():
            return bundled
        if legacy_default.exists():
            return legacy_default
        return Path("data/yexuan_inner/activity_pool.yaml")

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

    def dreams_postcards_dir(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "dreams", char_id, "postcards")

    def dreams_exit_lifecycle_path(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """Sanitized per-character lifecycle ledger for solo Dream after-exit talk."""
        return self._p("runtime", "dreams", char_id, "exit_lifecycle.json")

    def dreams_scenario_progress_audit_path(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """Bounded, text-free Scenario progression audit ledger."""
        return self._p("runtime", "dreams", safe_user_id(char_id), "scenario_progress_audit.json")

    def dreams_invariants_dir(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "dreams", char_id, "invariants")

    def dream_state_path(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p("runtime", "dreams", char_id, "state", safe_user_id(user_id), "dream_state.json")

    def dream_rpg_session_path(self, user_id: str | int, dream_id: str, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """RPG session root; later briefs add event/dice/transcript ledgers beside it."""
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p("runtime", "dreams", safe_user_id(char_id), "rpg", safe_user_id(user_id), safe_user_id(dream_id), "session.json")

    def dream_rpg_archive_path(self, user_id: str | int, dream_id: str, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p("runtime", "dreams", safe_user_id(char_id), "rpg_archive", safe_user_id(user_id), safe_user_id(dream_id), "transcript.jsonl")

    def dream_rpg_archive_metadata_path(self, user_id: str | int, dream_id: str, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p("runtime", "dreams", safe_user_id(char_id), "rpg_archive", safe_user_id(user_id), safe_user_id(dream_id), "metadata.json")

    def dream_rpg_archive_owner_dir(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p("runtime", "dreams", safe_user_id(char_id), "rpg_archive", safe_user_id(user_id))

    def dream_settings_path(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p("runtime", "dreams", char_id, "settings", safe_user_id(user_id) + ".json")

    def dream_hud_state_path(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p("runtime", "dreams", char_id, "state", safe_user_id(user_id), "dream_hud_state.json")

    def coplay_state_path(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p("runtime", "coplay", char_id, "state", safe_user_id(user_id), "coplay_state.json")

    def coplay_games_root(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """data/runtime/coplay/{char_id}/games/{uid}/ — parent of all per-game dirs (Brief 42 listing)."""
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p("runtime", "coplay", char_id, "games", safe_user_id(user_id))

    def coplay_game_dir(self, user_id: str | int, game_id: str, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """data/runtime/coplay/{char_id}/games/{uid}/{game_id}/ — game_state.json + log.md (Brief 41/42).

        game_id can contain ':' (e.g. "steam:123", from core.coplay.watcher) which
        is illegal in a Windows path segment — sanitize before it ever reaches _p().
        """
        assert_production_identity_allowed(user_id, mode=self.mode)
        safe_game_id = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(game_id)).strip(". ") or "unknown"
        return self.coplay_games_root(user_id, char_id=char_id) / safe_game_id

    def coplay_game_state_path(self, user_id: str | int, game_id: str, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self.coplay_game_dir(user_id, game_id, char_id=char_id) / "state.json"

    def coplay_afterglow_path(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """Brief 42 — session 结束后的软提示残留（纯文本 TTL，不挂 hidden_state）。"""
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p("runtime", "coplay", char_id, "afterglow", f"{safe_user_id(user_id)}.json")

    def coplay_game_log_path(self, user_id: str | int, game_id: str, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self.coplay_game_dir(user_id, game_id, char_id=char_id) / "log.md"

    def garden(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        return self._p("runtime", "characters", char_id, "garden")

    def author_notes_pool(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        primary = self.user_authored_character_dir(char_id=char_id) / "author_notes.json"
        new = self.legacy_authored_character_dir(char_id=char_id) / f"{char_id}_author_notes.json"
        legacy = Path(f"characters/{char_id}_author_notes.json")
        bundled = self.bundled_default_character_dir() / "author_notes.json"
        legacy_default = Path("characters/default_author_notes.json")
        if primary.exists():
            return primary
        if char_id == "default" and bundled.exists():
            return bundled
        if new.exists():
            return new
        if legacy.exists():
            return legacy
        if bundled.exists():
            return bundled
        return legacy_default

    def _bundled_seed(self, name: str) -> Path:
        """Prefer the packaged canonical seed, retaining one legacy read period."""
        bundled = self.bundled_reality_seed_dir() / name
        return bundled if bundled.exists() else Path("defaults") / name

    def _seed_if_missing(self, runtime_path: Path, seed_name: str) -> Path:
        """Seed missing runtime state from bundled public assets, then return it."""
        if not runtime_path.exists():
            src = self._bundled_seed(seed_name)
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
        primary = self.user_reality_dir() / filename
        legacy = self.legacy_reality_dir() / filename
        if primary.exists() or not legacy.exists():
            return primary
        return legacy

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
        primary = self.user_reality_dir() / "lorebooks"
        return primary if primary.exists() else self.legacy_reality_dir() / "lorebooks"

    def lorebook_read_dirs(self) -> tuple[Path, Path | None]:
        if self.mode == "test":
            return self._base / "reality" / "lorebooks", None
        return self.user_reality_dir() / "lorebooks", self.legacy_reality_dir() / "lorebooks"

    def dream_worlds_dir(self) -> Path:
        """Dream world read root, with packaged seed on a fresh v1 install."""
        if self.mode == "test":
            return self._base / "dream_worlds"
        primary = self.user_dream_worlds_dir()
        if primary.exists():
            return primary
        legacy = self.legacy_dream_worlds_dir()
        return legacy if legacy.exists() else self.bundled_dream_seed_dir() / "worlds"

    def dream_world_read_dirs(self) -> tuple[Path, Path | None]:
        if self.mode == "test":
            return self._base / "dream_worlds", None
        legacy = self.legacy_dream_worlds_dir()
        fallback = legacy if legacy.exists() else self.bundled_dream_seed_dir() / "worlds"
        return self.user_dream_worlds_dir(), fallback

    def dream_presets_dir(self) -> Path:
        """Dream preset read root, with packaged seed on a fresh v1 install."""
        if self.mode == "test":
            return self._base / "dream_presets"
        primary = self.user_dream_presets_dir()
        if primary.exists():
            return primary
        legacy = self.legacy_dream_presets_dir()
        return legacy if legacy.exists() else self.bundled_dream_preset_seed_dir()

    def dream_preset_read_dirs(self) -> tuple[Path, Path | None]:
        if self.mode == "test":
            return self._base / "dream_presets", None
        legacy = self.legacy_dream_presets_dir()
        fallback = legacy if legacy.exists() else self.bundled_dream_preset_seed_dir()
        return self.user_dream_presets_dir(), fallback

    def dream_scenarios_dir(self) -> Path:
        """Backward-compatible alias for the canonical scenario write root."""
        if self.mode == "test":
            return self._base / "dream" / "scenarios"
        return self.user_dream_scenarios_dir()

    def dream_scenario_read_dirs(self) -> tuple[Path, Path | None]:
        """Layered scenario roots: userdata first, historical data/ fallback."""
        if self.mode == "test":
            return self._base / "dream" / "scenarios", None
        return self.user_dream_scenarios_dir(), self.legacy_dream_scenarios_dir()

    def default_dream_world_template_dir(self) -> Path:
        """Read-only packaged Dream world seed, with legacy fallback."""
        bundled = self.bundled_dream_seed_dir() / "worlds" / "_default"
        return bundled if bundled.exists() else Path("defaults") / "dream_worlds" / "_default"

    def jailbreaks_dir(self) -> Path:
        """characters/reality/jailbreaks/ 目录（authored，不走 data/ 沙盒偏移）"""
        if self.mode == "test":
            return self._base / "reality" / "jailbreaks"
        primary = self.user_reality_dir() / "jailbreaks"
        return primary if primary.exists() else self.legacy_reality_dir() / "jailbreaks"

    def jailbreak_read_dirs(self) -> tuple[Path, Path | None]:
        if self.mode == "test":
            return self._base / "reality" / "jailbreaks", None
        return self.user_reality_dir() / "jailbreaks", self.legacy_reality_dir() / "jailbreaks"

    # ── authored reality prompt assets（characters/reality/，不走 data/ 沙盒偏移）
    def jailbreak_entries(self) -> Path:
        """主路径：characters/reality/jailbreak_entries.json（无 data/ fallback）。

        首次运行缺失时从 defaults/ 播种一个空壳（entries: []，不含任何私人条目），
        production/test 两种模式都播种——保证 fresh clone 不改配置就能直接启动；
        用户可随时用自己的真实内容覆盖播种出的文件。
        """
        p = self._reality_p("jailbreak_entries.json")
        if not p.exists():
            src = self._bundled_seed("jailbreak_entries.json")
            if src.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, p)
                logger.info(
                    "[authored-writer] kind=reality_jailbreak effective_read_source=default "
                    "canonical_write_target=user"
                )
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
            src = self._bundled_seed("lorebook.yaml")
            if src.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, p)
                logger.info(
                    "[authored-writer] kind=reality_lorebook effective_read_source=default "
                    "canonical_write_target=user"
                )
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
        primary = self.user_authored_character_dir(char_id=char_id) / "traits.yaml"
        legacy = self.legacy_authored_character_dir(char_id=char_id) / "traits.yaml"
        bundled = self.bundled_default_character_dir() / "traits.yaml"
        legacy_default = self.legacy_authored_character_dir(char_id="default") / "traits.yaml"
        if primary.exists():
            return primary
        if char_id == "default" and bundled.exists():
            return bundled
        if legacy.exists():
            return legacy
        if bundled.exists():
            return bundled
        if legacy_default.exists():
            return legacy_default
        return Path("data/yexuan_traits.yaml")

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
        assert_production_identity_allowed(user_id, mode=self.mode)
        return self._p("runtime", "memory", char_id, safe_user_id(user_id))

    # ── 信件内容资产（authored static content）────────────────────────────────
    def letter_samples_dir(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """示范信件库目录（静态内容）: content/characters/{char_id}/letter_samples/"""
        primary = self.user_authored_character_dir(char_id=char_id) / "letter_samples"
        return primary if primary.exists() else self.legacy_authored_character_dir(char_id=char_id) / "letter_samples"

    def letter_samples_read_dirs(self, *, char_id: str = _DEFAULT_CHAR_ID) -> tuple[Path, Path]:
        user, legacy = self.authored_character_dirs(char_id=char_id)
        return user / "letter_samples", legacy / "letter_samples"

    def letter_knowledge_dir(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """知识库目录（静态内容）: content/characters/{char_id}/knowledge/"""
        primary = self.user_authored_character_dir(char_id=char_id) / "knowledge"
        return primary if primary.exists() else self.legacy_authored_character_dir(char_id=char_id) / "knowledge"

    def letter_knowledge_read_dirs(self, *, char_id: str = _DEFAULT_CHAR_ID) -> tuple[Path, Path]:
        user, legacy = self.authored_character_dirs(char_id=char_id)
        return user / "knowledge", legacy / "knowledge"

    def stream_collapse_signal(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """ACT-2：流式路径反坍缩一次性降级信号，下一轮 build_prompt 读到后立即消费清除。"""
        return self.user_memory_root(user_id, char_id=char_id) / "stream_collapse_signal.json"

    def event_store(self, user_id: str | int, *, char_id: str) -> Path:
        """Memory Event evidence ledger for one explicit character/user scope."""
        return self.user_memory_root(user_id, char_id=char_id) / "event_store.sqlite3"

    def sent_letters(self, user_id: str | int, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """已发送信件归档: data/runtime/memory/{char_id}/{uid}/sent_letters.json"""
        return self.user_memory_root(user_id, char_id=char_id) / "sent_letters.json"

    def memory_char_root(self, *, char_id: str = _DEFAULT_CHAR_ID) -> Path:
        """S6: per-char memory 根目录: data/runtime/memory/{char_id}/
        用于 v1 模式下枚举所有用户（各 uid 是其直接子目录）。"""
        return self._p("runtime", "memory", char_id)

    def _memory_character_ids(self) -> list[str]:
        """Return safe character directory names for metadata-only cross-character lookup."""
        root = self._p("runtime", "memory")
        try:
            children = root.iterdir()
        except OSError:
            return []
        result: list[str] = []
        for child in children:
            if not child.is_dir():
                continue
            try:
                result.append(safe_user_id(child.name))
            except ValueError:
                continue
        return sorted(set(result))

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

    def owner_turn_receipt(self, *, caller_label: str, receipt_key: str) -> Path:
        """Persistent metadata-only idempotency receipt for one owner turn."""
        return self._p(
            "runtime", "owner_turn", "receipts", safe_user_id(caller_label),
            f"{safe_user_id(receipt_key)}.json",
        )

    def owner_turn_receipts_root(self) -> Path:
        return self._p("runtime", "owner_turn", "receipts")

    # ── External companion runtime ingress ─────────────────────────────────
    def companion_root(self) -> Path:
        """Metadata-only state for the external companion ingress."""
        return self._p("runtime", "companion")

    def companion_receipt(self, *, caller_label: str, receipt_key: str) -> Path:
        """One opaque companion idempotency receipt; never a content store."""
        return self._p(
            "runtime", "companion", "receipts", safe_user_id(caller_label),
            f"{safe_user_id(receipt_key)}.json",
        )

    def companion_receipts_root(self) -> Path:
        return self._p("runtime", "companion", "receipts")

    def companion_session(self, *, caller_label: str) -> Path:
        return self._p(
            "runtime", "companion", "sessions", f"{safe_user_id(caller_label)}.json",
        )

    def companion_stats(self) -> Path:
        return self._p("runtime", "companion", "stats.json")

    def diary_mirror_root(self, *, owner_id: str | int) -> Path:
        """Private server-side mirror of client-owned dated diary entries."""
        return self._p("runtime", "integrations", "diary", safe_user_id(owner_id))

    def diary_mirror_manifest(self, *, owner_id: str | int) -> Path:
        return self.diary_mirror_root(owner_id=owner_id) / "manifest.json"

    def diary_mirror_status(self, *, owner_id: str | int) -> Path:
        return self.diary_mirror_root(owner_id=owner_id) / "status.json"

    def diary_mirror_entry(self, *, owner_id: str | int, logical_date: str) -> Path:
        return self.diary_mirror_root(owner_id=owner_id) / "entries" / f"{logical_date}.md"

    def web_autosearch_state(self) -> Path:
        """data/runtime/web_autosearch_state.json — rate-limit state for autonomous web search (X3)."""
        return self._p("runtime", "web_autosearch_state.json")

    def prompt_layer_ablation(self) -> Path:
        """Runtime config: data/runtime/prompt_layer_ablation.json — layer ablation switches (CC 任务 23)."""
        return self._p("runtime", "prompt_layer_ablation.json")

    def dream_prompt_layer_ablation(self) -> Path:
        """Runtime config: Dream-only prompt layer ablation switches."""
        return self._p("runtime", "dream_prompt_layer_ablation.json")

    def hardware_jobs(self) -> Path:
        """Persistent hardware actuator jobs and their terminal outcomes."""
        return self._p("runtime", "hardware_jobs.json")

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

    def char_relation(self, *, char_a: str, char_b: str) -> Path:
        """Global bilateral Stage relation, keyed by the canonical character pair."""
        first, second = sorted((safe_user_id(char_a), safe_user_id(char_b)))
        if first == second:
            raise ValueError("character relation requires two distinct characters")
        return self._p("runtime", "relations", f"{first}__{second}.json")

    # ── Dream Stage: group dream session (Brief 100) ──────────────────────────
    # Physically isolated from stage_group_dir() — dream-only artifacts must
    # never be visible to reality's `*/meta.json` glob (see private_exchange_dir()
    # for the identical rationale) and must never share a transcript file with
    # the reality group's own transcript.json.
    def dream_group_dir(self, *, group_id: str) -> Path:
        """data/runtime/dreams/_stage/{group_id}/ — group dream root."""
        return self._p("runtime", "dreams", "_stage", safe_user_id(group_id))

    def dream_group_root_dir(self) -> Path:
        """data/runtime/dreams/_stage/ — scan root for cross-group owner lookups
        (get_reality_guard_status() checking "does this owner have any active
        group dream anywhere")."""
        return self._p("runtime", "dreams", "_stage")

    def dream_group_tmp_path(self, *, group_id: str) -> Path:
        """data/runtime/dreams/_stage/{group_id}/tmp/current_dream.jsonl —
        shared in-dream transcript (speaker-prefixed), never read by any loader."""
        return self.dream_group_dir(group_id=group_id) / "tmp" / "current_dream.jsonl"

    def dream_group_archive_dir(self, *, group_id: str) -> Path:
        """data/runtime/dreams/_stage/{group_id}/archive/ — dream_*.jsonl, replay-only."""
        return self.dream_group_dir(group_id=group_id) / "archive"

    def dream_group_state_path(self, *, group_id: str) -> Path:
        return self.dream_group_dir(group_id=group_id) / "state" / "dream_state.json"

    def dream_group_settings_path(self, *, group_id: str) -> Path:
        return self.dream_group_dir(group_id=group_id) / "settings.json"

    def dream_group_arbiter_trace(self, *, group_id: str) -> Path:
        """Append-only arbiter trace for a group dream round — mirrors
        stage_arbiter_trace() but lives under the isolated dream tree so a
        group dream's decision log never lands in the reality group dir."""
        return self.dream_group_dir(group_id=group_id) / "arbiter_trace.jsonl"

    # ── Private exchange: off-hours char-to-char sessions (Brief 86) ─────────
    def private_exchange_dir(self) -> Path:
        """data/runtime/groups/_private/ — root for private exchange transcripts.

        Deliberately siblings `stage_group_dir()`'s parent but under a `_private`
        segment so `GET /group/list`'s `*/meta.json` glob never sees these pairs —
        private exchanges have no Stage meta.json, only a rolling transcript.
        """
        return self._p("runtime", "groups", "_private")

    def private_exchange_transcript(self, *, char_a: str, char_b: str) -> Path:
        """data/runtime/groups/_private/{char_a}__{char_b}/transcript.jsonl"""
        first, second = sorted((safe_user_id(char_a), safe_user_id(char_b)))
        if first == second:
            raise ValueError("private exchange requires two distinct characters")
        return self.private_exchange_dir() / f"{first}__{second}" / "transcript.jsonl"

    def private_exchange_presence(self, *, char_id: str) -> Path:
        """data/runtime/relations/_presence/{char_id}.json — 12h TTL 'just talked
        to X' ambient stamp, read by activity_manager / stage presence rendering."""
        return self._p("runtime", "relations", "_presence", f"{safe_user_id(char_id)}.json")

    def private_exchange_budget_state(self) -> Path:
        """data/runtime/private_exchange_state.json — daily session-count budget
        (logical_day + count), reset by rhythm.logical_day()."""
        return self._p("runtime", "private_exchange_state.json")

    # ── Activity: reading ─────────────────────────────────────────────────────
    def reading_char_root(self, *, char_id: str) -> Path:
        """data/runtime/activity/reading/{char_id}/  — enumerate all uid subdirs."""
        return self._p("runtime", "activity", "reading", char_id)

    def reading_sessions_root(self, *, char_id: str, uid: str) -> Path:
        """data/runtime/activity/reading/{char_id}/{uid}/"""
        assert_production_identity_allowed(uid, mode=self.mode)
        return self._p("runtime", "activity", "reading", char_id, safe_user_id(uid))

    def reading_session_dir(self, *, char_id: str, uid: str, session_id: str) -> Path:
        """data/runtime/activity/reading/{char_id}/{uid}/{session_id}/"""
        assert_production_identity_allowed(uid, session_id, mode=self.mode)
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
        assert_production_identity_allowed(uid, mode=self.mode)
        return self._p("runtime", "activity", char_id, safe_user_id(uid), activity_type)

    def activity_session_dir(self, *, char_id: str, uid: str, activity_type: str, session_id: str) -> Path:
        """data/runtime/activity/{char_id}/{uid}/{activity_type}/{session_id}/"""
        assert_production_identity_allowed(uid, session_id, mode=self.mode)
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
