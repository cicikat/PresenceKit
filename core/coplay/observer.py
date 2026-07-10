"""
core/coplay/observer.py — Brief 40: 感知层。

截屏（mss，低频采样）→ 图像差分剧变检测（直方图，纯本地零成本）→ OCR（rapidocr，
结果只进 GameMoment，不直接进 prompt——防注入 + 防剧透原文）→ VLM 兜底
（仅当画面剧变且 OCR 无法解释时调用一次，结果限一句话场景描述，额度紧张是硬
约束）→ 存档 watch（只读 mtime，不解析存档内容——D4 红线：只读文件，禁止读
游戏进程内存，这里连存档内容本身都不解析，只看"变没变"）。

产出统一 GameMoment，推进按 uid 分桶的 moment 队列（有上限防堆积），供 Brief 41
的 commentator 消费。
"""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.config_loader import get_config
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)


@dataclass
class GameMoment:
    kind: str  # scene_change/death/achievement/save_point/idle/combat_start/combat_end
    summary: str
    ts: float = field(default_factory=time.time)


def _coplay_cfg() -> dict[str, Any]:
    return get_config().get("coplay", {}) or {}


# ═══════════════════════════════════════════════════════════════════════════
# moment 队列（按 uid 分桶，有上限防堆积）
# ═══════════════════════════════════════════════════════════════════════════

MOMENT_QUEUE_MAXLEN = 50
_moment_queues: dict[str, list[GameMoment]] = {}


def push_moment(uid: str, moment: GameMoment) -> None:
    q = _moment_queues.setdefault(uid, [])
    q.append(moment)
    if len(q) > MOMENT_QUEUE_MAXLEN:
        del q[: len(q) - MOMENT_QUEUE_MAXLEN]


def drain_moments(uid: str) -> list[GameMoment]:
    """取走并清空 uid 的 moment 队列（Brief 41 commentator 消费用）。"""
    return _moment_queues.pop(uid, [])


def peek_moments(uid: str) -> list[GameMoment]:
    return list(_moment_queues.get(uid, []))


def clear_moments_for_test(uid: str | None = None) -> None:
    if uid is None:
        _moment_queues.clear()
    else:
        _moment_queues.pop(uid, None)


# ═══════════════════════════════════════════════════════════════════════════
# 截屏（mss）
# ═══════════════════════════════════════════════════════════════════════════

def capture_screen() -> bytes | None:
    """截取主屏幕，返回 PNG bytes。mss 未安装 / 截屏失败 → None（fail-open）。"""
    try:
        import mss
        import mss.tools
    except ImportError:
        logger.debug("[coplay_observer] mss 未安装，跳过截屏")
        return None

    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            shot = sct.grab(monitor)
            return mss.tools.to_png(shot.rgb, shot.size)
    except Exception:
        logger.exception("[coplay_observer] 截屏失败（fail-open）")
        return None


_last_capture_ts: dict[str, float] = {}


def _capture_ready(uid: str) -> bool:
    raw_interval = _coplay_cfg().get("screenshot_interval")
    interval = float(raw_interval) if raw_interval is not None else 8.0
    now = time.time()
    if now - _last_capture_ts.get(uid, 0.0) < interval:
        return False
    _last_capture_ts[uid] = now
    return True


# ═══════════════════════════════════════════════════════════════════════════
# 差分剧变检测（直方图，纯 PIL，零额外依赖，可离线单元测试）
# ═══════════════════════════════════════════════════════════════════════════

SCENE_CHANGE_THRESHOLD = 0.15   # 直方图差异比例超过此值 → 判定为一次画面剧变
COMBAT_LIKELY_STREAK = 3        # 连续 N 帧高变化 → combat_start（持续剧变=战斗）
IDLE_STREAK = 4                 # 连续 N 帧低变化 → idle（长时间无变化）


def histogram_diff_ratio(frame_a: bytes, frame_b: bytes) -> float:
    """返回两帧 PNG 的直方图差异比例（0=完全相同，1=完全不同）。"""
    from io import BytesIO
    from PIL import Image

    img_a = Image.open(BytesIO(frame_a)).convert("RGB").resize((160, 90))
    img_b = Image.open(BytesIO(frame_b)).convert("RGB").resize((160, 90))
    hist_a = img_a.histogram()
    hist_b = img_b.histogram()
    total = sum(hist_a) or 1
    diff = sum(abs(a - b) for a, b in zip(hist_a, hist_b))
    return min(1.0, diff / (2 * total))


@dataclass
class _DiffState:
    last_frame: bytes | None = None
    high_streak: int = 0
    low_streak: int = 0
    in_combat: bool = False


_diff_states: dict[str, _DiffState] = {}


def reset_diff_state_for_test(uid: str | None = None) -> None:
    if uid is None:
        _diff_states.clear()
        _last_capture_ts.clear()
    else:
        _diff_states.pop(uid, None)
        _last_capture_ts.pop(uid, None)


def classify_frame(uid: str, frame: bytes) -> str | None:
    """喂入一帧，返回本次判定的离散事件（不重复报同一状态）：

      scene_change  — 单帧剧变（第一次检测到高变化）
      combat_start  — 连续 COMBAT_LIKELY_STREAK 帧高变化（"持续剧变"=战斗中）
      combat_end    — 从战斗态回落到低变化
      idle          — 连续 IDLE_STREAK 帧低变化（长时间无操作）
      None          — 无事发生，或变化幅度不足以构成新事件（调用方不应为 None 生成 moment）
    """
    state = _diff_states.setdefault(uid, _DiffState())
    if state.last_frame is None:
        state.last_frame = frame
        return None

    ratio = histogram_diff_ratio(state.last_frame, frame)
    state.last_frame = frame

    if ratio >= SCENE_CHANGE_THRESHOLD:
        state.low_streak = 0
        state.high_streak += 1
        if state.high_streak >= COMBAT_LIKELY_STREAK:
            if not state.in_combat:
                state.in_combat = True
                return "combat_start"
            return None
        return "scene_change" if state.high_streak == 1 else None

    state.high_streak = 0
    state.low_streak += 1
    if state.in_combat:
        state.in_combat = False
        return "combat_end"
    if state.low_streak == IDLE_STREAK:
        return "idle"
    return None


# ═══════════════════════════════════════════════════════════════════════════
# OCR（rapidocr，结果只用于关键词匹配，不直接进 prompt）
# ═══════════════════════════════════════════════════════════════════════════

_ocr_engine: Any = None
_ocr_init_failed = False

_DEATH_KEYWORDS = ("you died", "死亡", "game over", "已死亡", "you have died", "wasted")
_ACHIEVEMENT_KEYWORDS = ("achievement unlocked", "成就达成", "获得成就", "trophy unlocked")


def _get_ocr_engine():
    global _ocr_engine, _ocr_init_failed
    if _ocr_init_failed:
        return None
    if _ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR()
        except Exception:
            logger.exception("[coplay_observer] OCR 引擎初始化失败，本 session 内跳过 OCR（fail-open）")
            _ocr_init_failed = True
            return None
    return _ocr_engine


def reset_ocr_engine_for_test() -> None:
    global _ocr_engine, _ocr_init_failed
    _ocr_engine = None
    _ocr_init_failed = False


def ocr_frame(frame: bytes) -> list[str]:
    """OCR 一帧截图，返回识别到的文本行。引擎未安装/初始化失败/识别异常均 fail-open 返回 []。"""
    engine = _get_ocr_engine()
    if engine is None:
        return []
    try:
        import numpy as np
        from io import BytesIO
        from PIL import Image

        arr = np.array(Image.open(BytesIO(frame)).convert("RGB"))
        result, _ = engine(arr)
        if not result:
            return []
        return [str(line[1]) for line in result if len(line) > 1]
    except Exception:
        logger.exception("[coplay_observer] OCR 识别失败（fail-open）")
        return []


def classify_ocr_text(lines: list[str]) -> str | None:
    """在 OCR 文本行里找 death/achievement 关键词。返回 moment kind 或 None。"""
    joined = " ".join(lines).lower()
    for kw in _DEATH_KEYWORDS:
        if kw in joined:
            return "death"
    for kw in _ACHIEVEMENT_KEYWORDS:
        if kw in joined:
            return "achievement"
    return None


# ═══════════════════════════════════════════════════════════════════════════
# VLM 兜底（仅 scene_change 且 OCR 无法解释时调用一次，限一句话）
# ═══════════════════════════════════════════════════════════════════════════

_VLM_PROMPT = (
    "这是一张游戏截图。用一句话（不超过30字）客观描述画面里正在发生什么，"
    "不要猜测剧情走向，不要提前透露后续内容，只说你看到的当下画面。"
)


async def vlm_fallback_summary(frame: bytes) -> str | None:
    """VLM 兜底描述。config.vision.enabled=False 或调用失败 → None（fail-open）。

    调用方负责"只在 scene_change 且 OCR 无法解释时调一次"的节流决策，本函数
    只管单次调用本身。
    """
    if not get_config().get("vision", {}).get("enabled", False):
        return None

    import base64
    b64 = base64.b64encode(frame).decode()
    content_blocks = [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        {"type": "text", "text": _VLM_PROMPT},
    ]
    try:
        from core import llm_client
        result = await llm_client.chat(
            [{"role": "user", "content": content_blocks}], use_vision=True, call_category="vision",
        )
        return (result or "").strip() or None
    except Exception:
        logger.exception("[coplay_observer] VLM 兜底调用失败（fail-open）")
        return None


# ═══════════════════════════════════════════════════════════════════════════
# 存档 watch（只读 mtime，不解析存档内容 —— D4 红线）
# ═══════════════════════════════════════════════════════════════════════════

_save_watch_mtime: dict[str, float] = {}


def reset_save_watch_for_test(key: str | None = None) -> None:
    if key is None:
        _save_watch_mtime.clear()
    else:
        _save_watch_mtime.pop(key, None)


def check_save_point(uid: str, save_dir: str) -> bool:
    """save_dir 下任意文件 mtime 比上次检查更新 → True（save_point）。

    只读 mtime，不读取/解析文件内容。第一次看到某目录不算 save_point（没有
    "之前"可比较，避免 session 一开始就被判定成刚存过档）。
    """
    p = Path(save_dir)
    if not p.exists() or not p.is_dir():
        return False
    try:
        latest = max((f.stat().st_mtime for f in p.rglob("*") if f.is_file()), default=0.0)
    except OSError:
        return False

    key = f"{uid}:{save_dir}"
    prev = _save_watch_mtime.get(key)
    _save_watch_mtime[key] = latest
    if prev is None:
        return False
    return latest > prev


def _resolve_save_dir(game_id: str) -> str | None:
    """从 config.coplay.game_whitelist 里找 game_id 对应条目的 save_dir（可选字段）。"""
    for entry in _coplay_cfg().get("game_whitelist") or []:
        proc_name = (entry.get("process_name") or "").strip().lower()
        if proc_name.endswith(".exe"):
            proc_name = proc_name[:-4]
        if proc_name and proc_name == game_id:
            save_dir = entry.get("save_dir")
            return str(save_dir) if save_dir else None
    return None


# ═══════════════════════════════════════════════════════════════════════════
# 编排入口（供 scheduler trigger 调用）
# ═══════════════════════════════════════════════════════════════════════════

_DIFF_KIND_SUMMARY = {
    "scene_change": "画面发生了明显变化",
    "combat_start": "看起来进入了战斗/高强度场面",
    "combat_end": "战斗/高强度场面结束了",
    "idle": "有一阵子画面没什么变化，可能在停顿或菜单里",
}
_OCR_KIND_SUMMARY = {
    "death": "画面上出现了死亡/失败提示",
    "achievement": "画面上出现了成就/奖杯提示",
}


async def tick(uid: str, *, char_id: str = DEFAULT_CHAR_ID, game_id: str | None = None) -> None:
    """一次 observer tick：截屏节流 → 差分 → OCR → VLM 兜底 → 存档 watch → push moment。

    只在 CoplaySession status == active 时应该被调用（由调用方保证）。
    """
    if not _capture_ready(uid):
        return

    frame = capture_screen()
    if frame is None:
        return

    ocr_lines = ocr_frame(frame)
    ocr_kind = classify_ocr_text(ocr_lines)
    if ocr_kind:
        push_moment(uid, GameMoment(kind=ocr_kind, summary=_OCR_KIND_SUMMARY[ocr_kind]))
    else:
        diff_kind = classify_frame(uid, frame)
        if diff_kind:
            summary = _DIFF_KIND_SUMMARY[diff_kind]
            if diff_kind == "scene_change":
                # 廉价信号（直方图+OCR）都没能解释这次剧变时才兜底 VLM（额度紧张是硬约束，
                # 只在这一种情况下调用，且每次剧变最多调一次）。
                vlm_summary = await vlm_fallback_summary(frame)
                if vlm_summary:
                    summary = vlm_summary
            push_moment(uid, GameMoment(kind=diff_kind, summary=summary))

    if game_id:
        save_dir = _resolve_save_dir(game_id)
        if save_dir and check_save_point(uid, save_dir):
            push_moment(uid, GameMoment(kind="save_point", summary="检测到存档更新"))
