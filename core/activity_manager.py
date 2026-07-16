"""
activity_manager — 角色当前活动状态管理。
这是 ambient presence（角色此刻在忙什么），不是 core.activity 的 ActivitySession 共玩会话。
每15-45分钟（随机）切换一次 activity，受 daily_arc 时段约束。
activity_pool.yaml 是手写配置，定义角色会做的事。

CC 任务 24 · 3：全链路按 char_id 隔离——此前所有函数都不传 char_id，
_load_state()/_save_state()/_load_pool() 落盘时全部固定读写默认角色（yexuan）
路径，导致切换 active_character 后 /activity/current 仍返回 yexuan 的动向。
"""
import json
import logging
import random
import time
from datetime import datetime
from pathlib import Path

import yaml

from core.sandbox import get_paths

logger = logging.getLogger(__name__)

_DEFAULT_CHAR_ID = "yexuan"

# 时段定义（小时列表）
ARCS = {
    "deep_night":   [23, 0, 1, 2, 3, 4, 5],
    "morning":      [5, 6, 7, 8, 9],
    "late_morning": [9, 10, 11, 12],
    "afternoon":    [12, 13, 14, 15, 16, 17],
    "evening":      [17, 18, 19, 20, 21, 22, 23],
}

def _get_current_arc() -> str:
    hour = datetime.now().hour
    for arc, hours in ARCS.items():
        if hour in hours:
            return arc
    return "afternoon"

def _load_pool(char_id: str = _DEFAULT_CHAR_ID) -> list:
    """加载该角色的 activity_pool.yaml；角色自己的池不存在时 fallback 读默认角色池（不复制文件）。"""
    try:
        pool_path = get_paths().activity_pool(char_id=char_id)
        if char_id != _DEFAULT_CHAR_ID:
            own_pool = Path(f"content/characters/{char_id}/activity_pool.yaml")
            if not own_pool.exists():
                logger.debug(
                    f"[activity] {char_id} 无独立 activity_pool.yaml，fallback 读默认角色池"
                )
        with open(pool_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("activities", [])
    except Exception as e:
        logger.warning(f"[activity] 加载activity_pool失败: {e}")
        return []

def _load_state(char_id: str = _DEFAULT_CHAR_ID) -> dict:
    try:
        return json.loads(get_paths().activity_state(char_id=char_id).read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_state(state: dict, char_id: str = _DEFAULT_CHAR_ID) -> None:
    p = get_paths().activity_state(char_id=char_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def _load_thinking_about(uid: str = "", *, char_id: str = _DEFAULT_CHAR_ID) -> str:
    """
    从 episodic_memory 抽一条具体事件作为 thinking_about。
    按 strength 加权随机，只取最近30天、strength>0.4、有 summary 的记忆。
    """
    if not uid:
        try:
            from core.config_loader import get_config
            uid = get_config().get("default_user_id", "")
        except Exception:
            return ""
    if not uid:
        return ""

    try:
        from core.memory.episodic_memory import _load_memories
        memories = _load_memories(uid, char_id=char_id)
        if not memories:
            return ""

        now = time.time()
        candidates = [
            m for m in memories
            if (now - m.get("timestamp", 0)) < 30 * 86400
            and m.get("strength", 0) > 0.4
            and m.get("summary", "")
        ]
        if not candidates:
            return ""

        total = sum(m.get("strength", 0.5) for m in candidates)
        r = random.uniform(0, total)
        acc = 0
        chosen = candidates[0]
        for m in candidates:
            acc += m.get("strength", 0.5)
            if r <= acc:
                chosen = m
                break

        summary = chosen.get("summary", "")
        anchor = chosen.get("id", "")
        if not summary or not anchor:
            return ""

        return summary

    except Exception as e:
        logger.warning(f"[activity] 读取thinking_about失败: {e}")
        return ""

def _pick_activity(arc: str, char_id: str = _DEFAULT_CHAR_ID) -> dict:
    """按当前时段加权随机抽一个activity（CC 78·2）。

    加权：条目 weight（默认1.0），按 growth active 兴趣做 domain 对齐——
    domain 命中角色真实 active 兴趣 ×1.5，domain 存在但未命中 ×0.3（含
    active_interests 读取为空列表的情况——没有成长数据支撑的练习活动本身就是
    矛盾源），domain 留空不变。active_interests 读取抛异常时 fail-open，退回
    纯 weight 加权（不区分 domain）。全部条目加权后 total<=0 时退回均匀抽样。
    """
    pool = _load_pool(char_id=char_id)
    eligible = [a for a in pool if arc in a.get("arcs", [])]
    if not eligible:
        eligible = pool
    if not eligible:
        return {"id": "thinking", "text": "在思考"}

    active_domains: set | None
    try:
        from core.growth.interest_state import active_interests
        active_domains = {i.get("domain") for i in active_interests(char_id) if i.get("domain")}
    except Exception:
        active_domains = None  # fail-open：读取异常，退回纯 weight 加权

    weights = []
    for a in eligible:
        w = max(float(a.get("weight", 1.0) or 0.0), 0.0)
        domain = a.get("domain")
        if domain and active_domains is not None:
            w = w * 1.5 if domain in active_domains else w * 0.3
        weights.append(w)

    total = sum(weights)
    if total <= 0:
        chosen = random.choice(eligible)
    else:
        r = random.uniform(0, total)
        acc = 0.0
        chosen = eligible[-1]
        for a, w in zip(eligible, weights):
            acc += w
            if r <= acc:
                chosen = a
                break

    # 处理reading的book占位符
    text = chosen.get("text", "在思考")
    if "{book}" in text:
        books = chosen.get("books", ["一本书"])
        text = text.replace("{book}", random.choice(books))
    return {**chosen, "text": text}


def _pick_recent_growth_activity(
    char_id: str = _DEFAULT_CHAR_ID, *, now_ts: float | None = None
) -> dict | None:
    """Occasionally surface an interest that had a real practice in the last 20h."""
    try:
        from core.config_loader import get_config
        from core.growth.interest_state import active_interests
        from core.growth.practice_session import load_index

        probability = float((get_config().get("presence") or {}).get(
            "growth_activity_prob", 0.3
        ))
        probability = max(0.0, min(1.0, probability))
        if probability <= 0 or random.random() >= probability:
            return None

        now_ts = time.time() if now_ts is None else float(now_ts)
        candidates: list[dict] = []
        for interest in active_interests(char_id):
            index = load_index(str(interest.get("id") or ""), char_id=char_id)
            if not index:
                continue
            practiced_at = datetime.fromisoformat(str(index[-1].get("date") or "")).timestamp()
            if 0 <= now_ts - practiced_at <= 20 * 3600:
                candidates.append(interest)
        if not candidates:
            return None
        interest = random.choice(candidates)
        return {
            "id": f"growth:{interest['id']}",
            "text": f"在练{interest['name']}",
            "source": "growth",
            "interest_id": interest["id"],
        }
    except Exception:
        return None

def should_switch(char_id: str = _DEFAULT_CHAR_ID) -> bool:
    """判断是否需要切换activity（距上次切换超过15-45分钟随机值）。"""
    state = _load_state(char_id=char_id)
    if not state:
        return True
    expected_until = state.get("expected_until_ts", 0)
    return time.time() > expected_until

def switch_activity(char_id: str = _DEFAULT_CHAR_ID) -> dict:
    """切换到新activity，返回新状态。"""
    arc = _get_current_arc()
    activity = _pick_recent_growth_activity(char_id) or _pick_activity(arc, char_id=char_id)
    now = datetime.now()
    # 随机持续15-45分钟
    duration_min = random.randint(15, 45)
    expected_until_ts = time.time() + duration_min * 60

    # thinking_about：episodic 来源优先于 thinking_pool 静态文案（真实记忆 > 静态文案，CC 78·1）。
    thinking_about = ""
    thinking_source = ""
    if activity.get("thinking_about_eligible"):
        try:
            from core.config_loader import get_config
            _uid = get_config().get("default_user_id", "")
        except Exception:
            _uid = ""
        thinking_about = _load_thinking_about(_uid, char_id=char_id)
        if thinking_about:
            thinking_source = "episodic"
    if not thinking_about:
        _pool_thinking = activity.get("thinking_pool") or []
        if _pool_thinking:
            thinking_about = random.choice(_pool_thinking)
            thinking_source = "pool"

    state = {
        "current": activity["text"],
        "started_at": now.isoformat(),
        "expected_until_ts": expected_until_ts,
        "thinking_about": thinking_about,
        "thinking_source": thinking_source,
        "arc": arc,
        "source": activity.get("source", "pool"),
        "interest_id": activity.get("interest_id", ""),
        # CC 78·2: domain 锚定的池活动功能上就是 growth 活动，写入 state 供
        # get_prompt_fragment(suppress_growth=True) 一并抑制，避免与 3.8_growth_self
        # 层双重陈述（source 仍是 "pool"，不是 "growth"，不能只靠 source 判断）。
        "domain": activity.get("domain", ""),
    }
    _save_state(state, char_id=char_id)
    logger.info(f"[activity] 切换: {activity['text']} (char={char_id}, arc={arc}, {duration_min}分钟)")
    return state

def get_current(char_id: str = _DEFAULT_CHAR_ID) -> dict:
    """获取当前activity状态，必要时自动切换。"""
    if should_switch(char_id=char_id):
        return switch_activity(char_id=char_id)
    return _load_state(char_id=char_id)

_PATTERN_WORDS = ["每次", "总是", "一直", "从来", "每天", "每周"]

def get_prompt_fragment(
    char_id: str = _DEFAULT_CHAR_ID, *, suppress_growth: bool = False
) -> str:
    """返回注入prompt的文本片段，50字以内。"""
    state = get_current(char_id=char_id)
    # source=="growth"：_pick_recent_growth_activity 直注的「在练X」。
    # domain 非空：池条目本身锚定了某个 growth 域，功能上同属 growth 活动，需同样抑制，
    # 否则聊写作时 3.8_growth_self 层与本层会双重陈述「在写东西」（CC 78·2）。
    if suppress_growth and (state.get("source") == "growth" or state.get("domain")):
        return ""
    current = state.get("current", "")
    thinking = state.get("thinking_about", "")
    if not current:
        return ""
    if thinking:
        # 老状态文件没有 thinking_source 字段时，历史行为一律是 episodic 来源。
        thinking_source = state.get("thinking_source") or ("episodic" if thinking else "")
        if thinking_source == "episodic" and any(w in thinking for w in _PATTERN_WORDS):
            thinking = f"好像{thinking}"
        return f"{current}，想着：{thinking}"
    return current
