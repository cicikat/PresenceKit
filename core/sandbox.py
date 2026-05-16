"""
测试沙盒隔离 — DataPaths 单例
production 模式：路径前缀 data/
test 模式：路径前缀 data/test_sandbox/{test_session_id}/
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


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

    def _p(self, *parts: str) -> Path:
        return self._base.joinpath(*parts)

    # ── 桌宠端轮询文件（方案A：前缀同步到 config.yaml 的 data_prefix 字段）──────
    def channel_queue(self) -> Path:
        return self._p("channel_queue.json")

    def agent_actions(self) -> Path:
        return self._p("agent_actions.json")

    # ── 日志 / 状态 ────────────────────────────────────────────────────────────
    def error_log(self) -> Path:
        return self._p("error.log")

    def scheduler_state(self) -> Path:
        return self._p("scheduler_state.json")

    # ── 记忆根目录 ─────────────────────────────────────────────────────────────
    def character_growth(self) -> Path:
        return self._p("character_growth")

    def diary_context(self) -> Path:
        return self._p("diary_context")

    def pet_file(self) -> Path:
        return self._p("pet.json")

    def episodic_memory(self) -> Path:
        return self._p("episodic_memory")

    def memory_index(self) -> Path:
        return self._p("memory_index")

    def event_log(self) -> Path:
        return self._p("event_log")

    def group_context(self) -> Path:
        return self._p("group_context")

    def yexuan_inner_diary(self) -> Path:
        return self._p("yexuan_inner", "diary")

    def history(self) -> Path:
        return self._p("history")

    def profiles(self) -> Path:
        return self._p("profiles")

    def reminders(self) -> Path:
        return self._p("reminders")

    def diary_fallback(self) -> Path:
        return self._p("diary_fallback")

    def pending_perception_dir(self) -> Path:
        p = self._p("pending_perception")
        p.mkdir(parents=True, exist_ok=True)
        return p

    def activity_snapshot(self) -> Path:
        return self._p("activity_snapshot.json")

    def inbox_dir(self) -> Path:
        p = self._p("inbox")
        p.mkdir(parents=True, exist_ok=True)
        return p

    def notes_dir(self) -> Path:
        p = self._p("yexuan_inner", "notes")
        p.mkdir(parents=True, exist_ok=True)
        return p

    def notes_index(self) -> Path:
        return self._p("yexuan_inner", "notes_index.json")

    def mood_state(self) -> Path:
        return self._p("yexuan_inner", "mood_state.json")

    def activity_pool(self) -> Path:
        # activity_pool.yaml 是手写配置，不走沙盒，固定路径
        return Path(__file__).parent.parent / "data" / "yexuan_inner" / "activity_pool.yaml"

    def activity_state(self) -> Path:
        return self._p("yexuan_inner", "activity_state.json")

    def observations(self) -> Path:
        return self._p("yexuan_inner", "observations.jsonl")

    def mid_term(self) -> Path:
        return self._p("mid_term")

    def garden(self) -> Path:
        return self._p("garden")

    def author_notes_pool(self) -> Path:
        return Path("characters/yexuan_author_notes.json")

    def author_note_state(self) -> Path:
        return self._p("yexuan_inner", "author_note_state.json")

    def trait_state(self) -> Path:
        return self._p("yexuan_inner", "trait_state.json")

    def dead_letter_queue(self) -> Path:
        return self._p("dead_letter_queue")

    def fixation_state_dir(self) -> Path:
        return self._p("fixation_state")

    def fixation_log(self) -> Path:
        return self._p("logs", "fixation.jsonl")

    def cleanup(self):
        if self.mode != "test":
            raise RuntimeError("只有 test 模式才能执行 cleanup()")
        sandbox_dir = Path("data") / "test_sandbox" / self.test_session_id
        if sandbox_dir.exists():
            shutil.rmtree(sandbox_dir)
            logger.info(f"[sandbox] 已清理沙盒目录: {sandbox_dir}")
        else:
            logger.info(f"[sandbox] 沙盒目录不存在，无需清理: {sandbox_dir}")


# ── 单例 ───────────────────────────────────────────────────────────────────────

_instance: DataPaths | None = None


def get_paths() -> DataPaths:
    global _instance
    if _instance is None:
        _instance = DataPaths()
    return _instance


def init_paths(mode: str | None = None, test_session_id: str | None = None) -> DataPaths:
    """项目启动时调用一次（run_test.py 用），之后所有模块调用 get_paths()。"""
    global _instance
    _instance = DataPaths(mode=mode, test_session_id=test_session_id)
    if _instance.mode == "test":
        _write_active_prefix(str(_instance._base).replace("\\", "/"))
        logger.info(
            f"[sandbox] TEST 模式已激活 session={_instance.test_session_id} "
            f"数据根目录={_instance._base}"
        )
    return _instance


def _write_active_prefix(prefix: str):
    """把沙盒前缀写入 config.yaml 的 data_prefix 字段，供 Emerald-desktop 读取。"""
    try:
        lines = _CONFIG_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
        updated = False
        for i, line in enumerate(lines):
            if line.startswith("data_prefix:"):
                lines[i] = f'data_prefix: "{prefix}"\n'
                updated = True
                break
        if not updated:
            lines.append(f'data_prefix: "{prefix}"\n')
        _CONFIG_PATH.write_text("".join(lines), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[sandbox] 写入 config.yaml data_prefix 失败: {e}")
