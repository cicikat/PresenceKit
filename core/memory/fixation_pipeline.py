"""
core/memory/fixation_pipeline.py — 信息固化显式 pipeline

四个具名 job，每个有明确触发条件、输入、输出、幂等保证、可观测日志：

  capture_turn         — 同步写 short_term + event_log（含 turn_id 血缘）
  summarize_to_midterm — LLM 压缩单轮到 mid_term（slow_queue handler）
  reflect_to_episodic  — mid_term 列表 → episodic entry（slow_queue handler）
  consolidate_to_growth— episodic 列表 → character_growth 更新（slow_queue handler）

晋升关系：turn → mid_term → episodic → character_growth
所有 IO 走 core/sandbox.get_paths()，写入用 core/safe_write，锁用 core/memory/locks。
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Literal

from core.error_handler import log_error
from core.safe_write import safe_append_jsonl, safe_write_json, safe_write_text
from core.sandbox import get_paths

logger = logging.getLogger(__name__)

# ── 阈值常量 ──────────────────────────────────────────────────────────────────
_HIGH_STRENGTH_THRESHOLD = 0.6     # episodic strength 达到此值算"高强度"
_CONSOLIDATE_MIN_HIGH = 5          # 高强度 episodic 数量门槛（条件 1）
_CONSOLIDATE_MIN_STRENGTH_ACC = 4.0  # 累积 strength 门槛（条件 2）
_CONSOLIDATE_MIN_HOURS = 24        # 时间门槛（小时，条件 3）
_CONSOLIDATE_MIN_EPISODIC_COUNT = 3  # 条件 3 生效时最少 episodic 数

# fixation_state 字段默认值（扩展了 4 个基本字段 + high_strength_since_last）
_STATE_DEFAULTS: dict = {
    "last_consolidated_at": 0.0,
    "episodic_since_last": 0,
    "high_strength_since_last": 0,   # 本字段为扩展，spec JSON 不含但向后兼容
    "strength_accumulated": 0.0,
    "last_sweep_at": 0.0,
}

# ── LLM prompt 模板 ────────────────────────────────────────────────────────────
_REFLECT_PROMPT_TEMPLATE = """\
你是一个对话记录分析器。请分析下面这些近期对话摘要，提炼出一条情景记忆，只输出JSON，不要有任何多余文字：
{{
  "raw_facts": ["事实陈述1（用户说了/做了什么）", "事实2", "事实3"],
  "topic_keywords": ["话题词1", "话题词2", "话题词3"],
  "emotion_peak": "neutral/happy/sad/gentle/surprised/angry 中选一个",
  "emotion_texture": "最有重量的情绪质感描述，20字以内，可留空",
  "emotion_arc": "情绪流动方向，10字以内，可留空",
  "user_state": "用户当时的状态短语，如 stressed_about_work / tired",
  "narrative_summary": "一句自然语言描述这段时期发生了什么，15字以内，供{char_name}回忆用",
  "strength": 0到1之间的浮点数（事件越重要、情绪越强则越高）
}}
重要：用第三人称客观陈述，不要使用文学化语言，不要写动作描写。"""

_GROWTH_SYSTEM_TEMPLATE = """\
你是一个客观的对话分析器。你的任务是根据下面的情景记忆列表，输出对用户的结构化认知更新。

重要：你不是{char_name}。不要以{char_name}的视角写作，不要使用{char_name}的语气，不要使用任何文学化表达。

输出要求：
- 总长度不超过 300 字
- 使用第三人称客观陈述
- 分类列出，每类下用短句要点形式
- 信息密度高，无修饰性语言

输出格式（严格遵守）：
## 用户特点
- [一句话事实]

## 关键事件
- [日期或时间]: [一句话事件]

## 未跟进话题
- [话题]: [用户上次提到的状态]

严格禁止：
① 不要写动作描写（不允许出现中文括号包裹的动作）
② 不要写对白（不允许引号、不允许"他/她说"句式）
③ 不要使用任何文学化句式
④ 不要进入{char_name}的角色

硬规则：
① 只记录情景记忆中明确出现的事实，不推测、不补全
② 如果记忆间有矛盾，以更新的为准

输出完上面的客观认知后，另起一行输出 ===FELT===
然后用{char_name}的第一人称视角，把上面的认知转写成内心独白：
- 用"我"而不是"{char_name}"
- 保留所有事实，但允许有温度和情感
- 不超过 200 字
- 禁止动作描写和对白"""


# ═══════════════════════════════════════════════════════════════════════════════
# fixation_state 读写
# ═══════════════════════════════════════════════════════════════════════════════

def _state_file(uid: str) -> Path:
    return get_paths().fixation_state_dir() / f"{uid}.json"


def _load_fixation_state(uid: str) -> dict:
    """读取 fixation_state，缺失字段按默认值填充，不阻塞读路径。"""
    path = _state_file(uid)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            state = dict(_STATE_DEFAULTS)
            state.update({k: data[k] for k in _STATE_DEFAULTS if k in data})
            return state
    except Exception as e:
        log_error("fixation_pipeline._load_fixation_state", e)
    return dict(_STATE_DEFAULTS)


def _save_fixation_state(uid: str, state: dict) -> None:
    path = _state_file(uid)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_write_json(path, state)


def _should_consolidate(state: dict) -> bool:
    """检查是否满足 consolidate_to_growth 触发阈值（满足任一即返回 True）。"""
    high = state.get("high_strength_since_last", 0)
    strength_acc = state.get("strength_accumulated", 0.0)
    last_at = state.get("last_consolidated_at", 0.0)
    since = state.get("episodic_since_last", 0)
    hours_since = (time.time() - last_at) / 3600

    # 条件 1：高强度 episodic 累计 ≥ 5
    if high >= _CONSOLIDATE_MIN_HIGH:
        return True
    # 条件 2：累积 strength ≥ 4.0
    if strength_acc >= _CONSOLIDATE_MIN_STRENGTH_ACC:
        return True
    # 条件 3：距上次固化 ≥ 24h 且 新增 episodic ≥ 3
    if hours_since >= _CONSOLIDATE_MIN_HOURS and since >= _CONSOLIDATE_MIN_EPISODIC_COUNT:
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 可观测日志
# ═══════════════════════════════════════════════════════════════════════════════

def _log_fixation(
    job: str,
    uid: str,
    extra: dict,
    status: Literal["ok", "error"],
    detail: str = "",
) -> None:
    record: dict = {"ts": time.time(), "job": job, "uid": uid, "status": status}
    record.update(extra)
    if detail:
        record["detail"] = detail
    safe_append_jsonl(get_paths().fixation_log(), record)
    if status == "error":
        logger.warning(f"[fixation.{job}] uid={uid} error: {detail}")
    else:
        logger.debug(f"[fixation.{job}] uid={uid} ok {extra}")


# ═══════════════════════════════════════════════════════════════════════════════
# 校验函数（内部）
# ═══════════════════════════════════════════════════════════════════════════════

def _validate_episode(data: dict) -> bool:
    for key in ("raw_facts", "topic_keywords", "emotion_peak", "strength"):
        if key not in data:
            return False
    if not isinstance(data["raw_facts"], list) or len(data["raw_facts"]) == 0:
        return False
    if not isinstance(data["topic_keywords"], list) or len(data["topic_keywords"]) == 0:
        return False
    if data["emotion_peak"] not in {"neutral", "happy", "sad", "gentle", "surprised", "angry"}:
        return False
    try:
        s = float(data["strength"])
        if not (0.0 <= s <= 1.0):
            return False
    except (TypeError, ValueError):
        return False
    return True


def _validate_growth_content(observer: str) -> bool:
    """校验 observer 段（===FELT=== 前的内容），100~500 字且含 Markdown 标题。"""
    stripped = observer.strip()
    if not stripped:
        return False
    if not (100 <= len(stripped) <= 500):
        return False
    if not re.search(r"^#+ ", stripped, re.MULTILINE):
        return False
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# character_growth 路径辅助（避免循环导入）
# ═══════════════════════════════════════════════════════════════════════════════

def _growth_file(char_name: str, uid: str) -> Path:
    safe_char = "".join(c for c in char_name if c.isalnum() or c in "-_")
    safe_user = "".join(c for c in uid if c.isalnum() or c in "-_")
    root = get_paths().character_growth()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe_char}_{safe_user}.md"


def _backup_growth(char_name: str, uid: str) -> None:
    path = _growth_file(char_name, uid)
    if path.exists():
        bak = path.with_suffix(".md.bak")
        try:
            safe_write_text(bak, path.read_text(encoding="utf-8"))
        except Exception as e:
            log_error("fixation_pipeline._backup_growth", e)


def _restore_growth_from_backup(char_name: str, uid: str) -> None:
    path = _growth_file(char_name, uid)
    bak = path.with_suffix(".md.bak")
    if bak.exists():
        try:
            safe_write_text(path, bak.read_text(encoding="utf-8"))
            logger.info(f"[fixation] character_growth 已从备份回滚: {path.name}")
        except Exception as e:
            log_error("fixation_pipeline._restore_growth_from_backup", e)


# ═══════════════════════════════════════════════════════════════════════════════
# Job 1 — capture_turn（同步，在 uid_lock 内、detect_emotion 后调用）
# ═══════════════════════════════════════════════════════════════════════════════

def capture_turn(
    uid: str,
    user_msg: str,
    reply: str,
    emotion: str = "neutral",
    turn_id: str | None = None,
    trigger_name: str = "",
) -> str:
    """
    生成 turn_id，写 short_term + event_log。
    trigger_name 非空时为 scheduler 触发路径：跳过 user 行写入，assistant meta 附加 trigger: 字段。
    幂等：若 short_term 近 4 条已含相同 turn_id，直接返回。

    调用约束：必须在 uid_lock 内、detect_emotion 完成后调用。
    """
    from core.memory import short_term, event_log

    ts = time.time()
    turn_id = turn_id or f"{uid}_{int(ts * 1000)}"

    if trigger_name:
        # scheduler 触发：只写 assistant，跳过 user 行（prompt 是系统注入的情景描述）
        writes = [
            short_term.append(uid, "assistant", reply, turn_id=turn_id),
            event_log.append(uid, "assistant", reply, emotion=emotion, turn_id=turn_id, trigger_name=trigger_name),
        ]
    else:
        writes = [
            short_term.append(uid, "user", user_msg, turn_id=turn_id),
            short_term.append(uid, "assistant", reply, turn_id=turn_id),
            event_log.append(uid, "user", user_msg, turn_id=turn_id),
            event_log.append(uid, "assistant", reply, emotion=emotion, turn_id=turn_id),
        ]
    if not all(writes):
        raise RuntimeError(f"capture_turn 写入不完整: turn_id={turn_id} writes={writes}")

    return turn_id


# ═══════════════════════════════════════════════════════════════════════════════
# Job 2 — summarize_to_midterm（slow_queue handler）
# ═══════════════════════════════════════════════════════════════════════════════

async def summarize_to_midterm(
    turn_id: str,
    uid: str,
    user_msg: str,
    reply: str,
    tags: list[str],
    emotion: str = "neutral",
) -> str | None:
    """
    LLM 压缩单轮对话到 mid_term，写入血缘字段。
    幂等：source_turn_id 已存在则跳过。
    完成后检查 eager 触发条件，满足则入队 reflect_to_episodic。

    返回 mid_id（已写入）或 None（跳过）。
    """
    from core.memory import locks, mid_term as _mt
    from core import llm_client
    from core.post_process import slow_queue

    _ts_start = time.time()

    async with locks.uid_lock(uid):
        existing = _mt.load(uid)
        if any(e.get("source_turn_id") == turn_id for e in existing):
            logger.debug(f"[fixation] summarize_to_midterm 幂等命中: turn_id={turn_id}")
            return None

    summary = await llm_client.summarize_turn(user_msg, reply, tags=tags)
    if not summary:
        return None

    mid_id = f"mt_{uid}_{int(time.time() * 1000)}"
    async with locks.uid_lock(uid):
        existing = _mt.load(uid)
        if any(e.get("source_turn_id") == turn_id for e in existing):
            logger.debug(f"[fixation] summarize_to_midterm 幂等命中: turn_id={turn_id}")
            return None
        _mt.append(uid, summary, tags=tags, mid_id=mid_id, source_turn_id=turn_id)

    duration_ms = int((time.time() - _ts_start) * 1000)
    _log_fixation("summarize_to_midterm", uid, {
        "mid_id": mid_id, "turn_id": turn_id, "duration_ms": duration_ms,
    }, "ok")

    # eager 触发：情绪显著则立即入队 reflect
    if emotion in ("sad", "angry", "happy"):
        slow_queue.enqueue("reflect_to_episodic", {
            "uid": uid,
            "mid_ids": [mid_id],
            "trigger": "eager",
        })
        logger.info(f"[fixation] reflect_to_episodic eager 已入队: uid={uid} emotion={emotion}")

    return mid_id


# ═══════════════════════════════════════════════════════════════════════════════
# Job 3 — reflect_to_episodic（slow_queue handler）
# ═══════════════════════════════════════════════════════════════════════════════

async def reflect_to_episodic(
    uid: str,
    mid_ids: list[str],
    trigger: Literal["eager", "sweep"] = "eager",
) -> str | None:
    """
    将一批 mid_term 条目合并反思为一条 episodic 记忆。
    幂等：已 promoted 或已生成对应 episodic 的条目跳过。
    完成后更新 fixation_state，如达阈值则入队 consolidate_to_growth。

    返回 ep_id（已写入）或 None（跳过）。
    """
    from core.memory import locks, mid_term as _mt
    from core.memory.episodic_memory import write_episode, _load_memories
    from core import llm_client
    from core.post_process import slow_queue
    from core.config_loader import get_config, _char_name
    from core.llm_output_validator import record_failure, reset as _reset

    _ts_start = time.time()
    mid_ids_set = set(mid_ids)
    ep_id: str | None = None

    async with locks.uid_lock(uid):
        all_events = _mt.load(uid)

        # 只处理请求的、且未晋升的条目
        to_process = [
            e for e in all_events
            if e.get("mid_id") in mid_ids_set and not e.get("promoted_to_episodic_id")
        ]

        if not to_process:
            _log_fixation("reflect_to_episodic", uid, {
                "mid_ids": mid_ids, "trigger": trigger,
            }, "ok", "already promoted")
            return None

        # 幂等：检查 episodic 里是否已有相同 source_mid_ids 的条目
        existing_eps = _load_memories(uid)
        for ep in existing_eps:
            if mid_ids_set & set(ep.get("source_mid_ids", [])):
                _log_fixation("reflect_to_episodic", uid, {
                    "mid_ids": mid_ids, "trigger": trigger,
                }, "ok", "already reflected")
                return None

        # 构造 LLM 输入
        char_name = _char_name()
        summaries_text = "\n".join(
            f"{i+1}. {e.get('summary', '')}"
            for i, e in enumerate(to_process)
        )
        prompt_system = _REFLECT_PROMPT_TEMPLATE.format(char_name=char_name)
        base_user = f"对话摘要：\n{summaries_text}"

        # LLM 调用（最多 3 次）
        _fail_key = f"reflect_to_episodic_{uid}"
        data = None
        _last_raw = ""
        for attempt in range(3):
            suffix = "" if attempt == 0 else "\n\n上次输出不符合格式要求，请严格只输出JSON。"
            _last_raw = await llm_client.chat(
                messages=[{"role": "user", "content": prompt_system + "\n\n" + base_user + suffix}],
                max_tokens_override=400,
            )
            try:
                cleaned = re.sub(r"```json|```", "", _last_raw or "").strip()
                candidate = json.loads(cleaned)
                if _validate_episode(candidate):
                    data = candidate
                    break
            except Exception:
                pass

        if data is None:
            record_failure(_fail_key, (_last_raw or "")[:200], uid)
            _log_fixation("reflect_to_episodic", uid, {
                "mid_ids": mid_ids, "trigger": trigger,
            }, "error", "LLM 解析失败")
            return None

        # 过滤平淡内容
        if data.get("emotion_peak") == "neutral" and data.get("strength", 0) < 0.4:
            _log_fixation("reflect_to_episodic", uid, {
                "mid_ids": mid_ids, "trigger": trigger,
            }, "ok", "neutral skip")
            return None

        ep_id = f"ep_{int(time.time())}"
        episode: dict = {
            "id": ep_id,
            "timestamp": time.time(),
            "raw_facts": data.get("raw_facts", []),
            "topic_keywords": data.get("topic_keywords", []),
            "emotion_peak": data.get("emotion_peak", "neutral"),
            "emotion_texture": data.get("emotion_texture", ""),
            "emotion_arc": data.get("emotion_arc", ""),
            "user_state": data.get("user_state", ""),
            "narrative_summary": data.get("narrative_summary", ""),
            "strength": data.get("strength", 0.5),
            "retrieval_count": 0,
            "last_retrieved": None,
            # 血缘字段
            "source_mid_ids": [e.get("mid_id") for e in to_process if e.get("mid_id")],
            "consolidated_at": None,
        }

        write_episode(uid, episode)
        _reset(_fail_key)

        # 回写 mid_term：标记已晋升
        for e in to_process:
            if e.get("mid_id"):
                _mt.mark_promoted(uid, e["mid_id"], ep_id)

        # 更新 fixation_state
        strength = episode.get("strength", 0.0)
        state = _load_fixation_state(uid)
        state["episodic_since_last"] = state.get("episodic_since_last", 0) + 1
        if strength >= _HIGH_STRENGTH_THRESHOLD:
            state["high_strength_since_last"] = state.get("high_strength_since_last", 0) + 1
        state["strength_accumulated"] = round(
            state.get("strength_accumulated", 0.0) + strength, 3
        )
        _save_fixation_state(uid, state)

    # uid_lock 释放后检查阈值
    if _should_consolidate(state):
        char_name = _char_name()
        slow_queue.enqueue("consolidate_to_growth", {"uid": uid, "char_name": char_name})
        logger.info(f"[fixation] consolidate_to_growth 已入队: uid={uid}")

    duration_ms = int((time.time() - _ts_start) * 1000)
    _log_fixation("reflect_to_episodic", uid, {
        "ep_id": ep_id,
        "input_ids": mid_ids,
        "output_id": ep_id,
        "trigger": trigger,
        "duration_ms": duration_ms,
    }, "ok")
    return ep_id


# ═══════════════════════════════════════════════════════════════════════════════
# Job 4 — consolidate_to_growth（内层纯函数 + 外层 IO）
# ═══════════════════════════════════════════════════════════════════════════════

async def _synthesize_growth(
    episodes: list[dict],
    char_name: str,
    llm_client,
) -> str:
    """
    纯函数：输入 episode 列表，输出 markdown 文本（含 ===FELT=== 分隔的两段），不做任何文件 IO。
    """
    # 格式化 episode 列表为可读输入
    lines = []
    for ep in sorted(episodes, key=lambda e: e.get("timestamp", 0)):
        ts = ep.get("timestamp", 0)
        date_str = time.strftime("%m月%d日", time.localtime(ts)) if ts else "（未知时间）"
        summary = ep.get("narrative_summary") or ep.get("summary", "（无摘要）")
        emotion = ep.get("emotion_peak", "neutral")
        strength = ep.get("strength", 0.5)
        lines.append(f"[{date_str}] {summary}（情绪: {emotion}，强度: {strength:.2f}）")

    episodes_text = "\n".join(lines)
    system_prompt = _GROWTH_SYSTEM_TEMPLATE.format(char_name=char_name)

    _REJECT_KWS = ["作为AI", "作为一个AI", "我无法", "I cannot", "I'm sorry", "As an AI"]
    _retry_suffix = "\n\n[上次输出不符合格式要求，请严格按照## 标题 + - 要点的格式，不要出现任何角色扮演内容]"

    result = ""
    for attempt in range(3):
        user_content = f"情景记忆列表：\n{episodes_text}"
        if attempt > 0:
            user_content += _retry_suffix
        _raw = await llm_client.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens_override=3000,
        )
        _raw = (_raw or "").strip()
        if not _raw or len(_raw) < 20:
            continue
        if any(kw in _raw for kw in _REJECT_KWS):
            continue
        if not re.search(r"^#+ ", _raw, re.MULTILINE):
            continue
        result = _raw
        break

    return result


async def consolidate_to_growth(uid: str, char_name: str, llm_client) -> bool:
    """
    外层 IO：加载所有未固化的 episodic，调内层 _synthesize_growth 合成认知文件，
    校验通过后备份→写入→更新 fingerprint→标记 episodic→重置 fixation_state。
    校验失败则回滚备份，不更新 state（保证下次仍会重试）。
    幂等：consolidated_at 已写的 episodic 不再参与。
    """
    from core.memory import locks
    from core.memory.episodic_memory import _load_memories, _save_memories
    from core.llm_output_validator import record_failure

    _ts_start = time.time()
    _fail_key = f"consolidate_to_growth_{uid}"

    async with locks.uid_lock(uid):
        snapshot = _load_memories(uid)
        unconsolidated = [ep for ep in snapshot if ep.get("consolidated_at") is None]

    if not unconsolidated:
        _log_fixation("consolidate_to_growth", uid, {}, "ok", "no unconsolidated episodes")
        return False

    raw_content = await _synthesize_growth(unconsolidated, char_name, llm_client)

    if not raw_content:
        record_failure(_fail_key, "空输出", uid)
        _log_fixation("consolidate_to_growth", uid, {}, "error", "LLM 返回空")
        return False

    if "===FELT===" in raw_content:
        observer_part, felt_part = raw_content.split("===FELT===", 1)
        observer_part = observer_part.strip()
        felt_part = felt_part.strip()
    else:
        observer_part = raw_content.strip()
        felt_part = ""

    if not _validate_growth_content(observer_part):
        record_failure(_fail_key, observer_part[:200], uid)
        _log_fixation("consolidate_to_growth", uid, {}, "error",
                      f"校验失败 len={len(observer_part)}")
        return False

    try:
        from core.integrity_check import check_growth
        issues = check_growth(observer_part)
        if issues:
            logger.warning(f"[fixation] consolidate_to_growth 纠察拒绝写入: {issues}")
            record_failure(_fail_key, observer_part[:200], uid)
            _log_fixation("consolidate_to_growth", uid, {}, "error", f"纠察失败: {issues}")
            return False
    except Exception as e:
        log_error("fixation_pipeline.consolidate_to_growth.check_growth", e)

    async with locks.uid_lock(uid):
        all_episodes = _load_memories(uid)
        snapshot_ids = {ep.get("id") for ep in unconsolidated}
        still_unconsolidated = [
            ep for ep in all_episodes
            if ep.get("id") in snapshot_ids and ep.get("consolidated_at") is None
        ]
        if not still_unconsolidated:
            _log_fixation("consolidate_to_growth", uid, {}, "ok", "already consolidated")
            return False

        _backup_growth(char_name, uid)
        path = _growth_file(char_name, uid)

        if not safe_write_text(path, observer_part):
            _restore_growth_from_backup(char_name, uid)
            _log_fixation("consolidate_to_growth", uid, {}, "error", "写 observer 失败")
            return False

        fp_path = path.with_suffix(".fingerprint.txt")
        safe_write_text(fp_path, observer_part[:150].strip())

        if felt_part:
            felt_path = path.with_name(path.stem + ".felt.md")
            safe_write_text(felt_path, felt_part)

        now = time.time()
        consolidated_ids = {ep["id"] for ep in still_unconsolidated}
        for ep in all_episodes:
            if ep.get("id") in consolidated_ids:
                ep["consolidated_at"] = now
        _save_memories(uid, all_episodes)

        state = _load_fixation_state(uid)
        state["episodic_since_last"] = 0
        state["high_strength_since_last"] = 0
        state["strength_accumulated"] = 0.0
        state["last_consolidated_at"] = now
        _save_fixation_state(uid, state)

    duration_ms = int((time.time() - _ts_start) * 1000)
    _log_fixation("consolidate_to_growth", uid, {
        "ep_count": len(still_unconsolidated),
        "duration_ms": duration_ms,
    }, "ok")
    logger.info(f"[fixation] consolidate_to_growth 完成: uid={uid} ep_count={len(still_unconsolidated)}")
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# slow_queue handler 包装
# ═══════════════════════════════════════════════════════════════════════════════

async def handler_summarize_to_midterm(payload: dict) -> None:
    await summarize_to_midterm(
        turn_id=payload["turn_id"],
        uid=payload["uid"],
        user_msg=payload["user_content"],
        reply=payload["reply"],
        tags=payload.get("tags", []),
        emotion=payload.get("emotion", "neutral"),
    )


async def handler_capture_turn_retry(payload: dict) -> None:
    from core.memory import locks

    uid = payload["uid"]
    async with locks.uid_lock(uid):
        capture_turn(
            uid,
            payload["user_content"],
            payload["reply"],
            payload.get("emotion", "neutral"),
            turn_id=payload["turn_id"],
            trigger_name=payload.get("trigger_name", ""),
        )
    logger.info(f"[fixation] capture_turn retry 完成: {payload['turn_id']}")


async def handler_reflect_to_episodic(payload: dict) -> None:
    await reflect_to_episodic(
        uid=payload["uid"],
        mid_ids=payload.get("mid_ids", []),
        trigger=payload.get("trigger", "eager"),
    )


async def handler_consolidate_to_growth(payload: dict) -> None:
    from core import llm_client
    from core.config_loader import _char_name
    await consolidate_to_growth(
        uid=payload["uid"],
        char_name=payload.get("char_name") or _char_name(),
        llm_client=llm_client,
    )
