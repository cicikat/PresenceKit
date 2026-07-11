"""event_log_salvage — event_log 过期归档前抢救持久事实（Brief 46 §2）。

调度器每日触发（冷却 24h），扫描 age ∈ [27, 29] 天、尚未抢救的 event_log 按天
日志文件；每个文件一次 LLM 调用提取"仍然为真的持久事实"（偏好/身份/生活状态/
承诺），显式排除一次性事件和情绪表达。产出走 Brief 45 的 important_facts 冲突
裁决入口（op=add/update/noop，`user_profile._apply_important_facts_ops`），不新建
存储——已被 profile 覆盖的同义信息会被 noop 掉。

每日处理上限 3 个文件（跨全部角色/用户合计，防积压时一次打爆 LLM 配额）。
不发言、不影响 mood，stamp_trigger()。抢救状态记 fixation_state.json 的
salvaged_dates（滚动保留 60 个），不新建状态文件。
"""
from __future__ import annotations

import json as _json
import logging
import re
from datetime import datetime

from core.error_handler import log_error

logger = logging.getLogger(__name__)

_SALVAGE_MIN_AGE_DAYS = 27
_SALVAGE_MAX_AGE_DAYS = 29
_MAX_FILES_PER_RUN = 3
_MAX_SALVAGED_DATES = 60

_DAY_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")

_SALVAGE_SYSTEM_PROMPT = """\
你是一个信息抢救分析器。下面是一天完整的对话日志，这天的记录即将过期归档。
请只提取其中【仍然为真的持久事实】：明确的偏好、身份信息、生活状态、承诺。
明确排除一次性事件（吃了什么、去了哪、当天临时安排）和单纯的情绪表达。
只返回 JSON 数组，不要输出任何其他内容；没有可抢救的持久事实时返回 []。

现有 important_facts 列表（index 从 0 开始，供你判断候选事实是否与某条已有
事实矛盾/更新/重复）：
{existing_listing}

数组每个元素格式：
{{"op": "add"或"update"或"noop", "target_index": null或上面列表中的index数字,
"text": "事实内容", "tag": "分类标签", "ts": 当前 Unix 时间戳数字}}
op 判定规则：
- add：全新持久事实，现有列表没有对应条目，target_index 填 null。
- update：新信息是对某条现有事实的状态更新或矛盾，target_index 填该条 index。
- noop：与某条现有事实语义重复，target_index 填该条 index。
tag 从以下受控集合中选择：pref.music / pref.food / pref.media / habit / health /
status.project / stable / misc。"""


async def _check_event_log_salvage() -> None:
    from core.scheduler.loop import _is_ready, _mark
    from core.write_envelope import stamp_trigger
    from core.asset_registry import get_registry
    from core.sandbox import get_paths

    if not _is_ready("event_log_salvage"):
        return
    _mark("event_log_salvage")

    _envelope = stamp_trigger()  # noqa: F841 — documents caller authority

    char_ids = [e.id for e in get_registry().list_all("character")]
    if not char_ids:
        logger.warning("[event_log_salvage] 无已注册角色，跳过")
        return

    candidates: list[tuple[str, str, str]] = []
    today = datetime.now().date()

    for char_id in char_ids:
        char_root = get_paths().memory_char_root(char_id=char_id)
        if not char_root.exists():
            continue
        for uid_dir in char_root.iterdir():
            if not uid_dir.is_dir():
                continue
            event_log_dir = uid_dir / "event_log"
            if not event_log_dir.is_dir():
                continue
            uid = uid_dir.name
            salvaged = _load_salvaged_dates(uid, char_id=char_id)
            for f in event_log_dir.iterdir():
                m = _DAY_FILE_RE.match(f.name)
                if not m:
                    continue
                date_str = m.group(1)
                if date_str in salvaged:
                    continue
                try:
                    file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                age_days = (today - file_date).days
                if _SALVAGE_MIN_AGE_DAYS <= age_days <= _SALVAGE_MAX_AGE_DAYS:
                    candidates.append((char_id, uid, date_str))

    if not candidates:
        return

    processed = 0
    for char_id, uid, date_str in candidates:
        if processed >= _MAX_FILES_PER_RUN:
            break
        try:
            await _salvage_one(char_id, uid, date_str)
        except Exception as e:
            log_error(f"event_log_salvage.salvage_one.{char_id}.{uid}.{date_str}", e)
        processed += 1

    logger.info(
        "[event_log_salvage] 本轮处理 %d 个到期文件（候选 %d 个）",
        processed, len(candidates),
    )


def _load_salvaged_dates(uid: str, *, char_id: str) -> set[str]:
    from core.memory.fixation_pipeline import _load_fixation_state
    state = _load_fixation_state(uid, char_id=char_id)
    return set(state.get("salvaged_dates") or [])


def _mark_salvaged(uid: str, date_str: str, *, char_id: str) -> None:
    from core.memory.fixation_pipeline import _load_fixation_state, _save_fixation_state
    state = _load_fixation_state(uid, char_id=char_id)
    dates = list(state.get("salvaged_dates") or [])
    if date_str not in dates:
        dates.append(date_str)
    state["salvaged_dates"] = dates[-_MAX_SALVAGED_DATES:]
    _save_fixation_state(uid, state, char_id=char_id)


async def _salvage_one(char_id: str, uid: str, date_str: str) -> None:
    """抢救单个到期 event_log 日文件。失败时不标记 salvaged，留待窗口内下次重试。"""
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope
    from core.memory import user_profile as _up
    from core.memory.user_profile import _apply_important_facts_ops, _normalize_fact
    from core import llm_client

    scope = MemoryScope.reality_scope(str(uid), char_id)
    day_file = resolve_path(scope, "event_log") / f"{date_str}.md"
    if not day_file.exists():
        # 文件已不在（提前被归档/删除），没有可抢救内容，直接标记跳过
        _mark_salvaged(uid, date_str, char_id=char_id)
        return

    try:
        raw_text = day_file.read_text(encoding="utf-8")
    except Exception as e:
        log_error(f"event_log_salvage.read.{char_id}.{uid}.{date_str}", e)
        return

    if not raw_text.strip():
        _mark_salvaged(uid, date_str, char_id=char_id)
        return

    existing_facts = _up.load(uid, char_id=char_id).get("important_facts") or []
    existing_listing = "\n".join(
        f"{i}: {_normalize_fact(f)['text']}" for i, f in enumerate(existing_facts)
    ) or "（当前没有已记录的 important_facts）"

    system_prompt = _SALVAGE_SYSTEM_PROMPT.format(existing_listing=existing_listing)

    try:
        raw = await llm_client.chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"日志原文：\n{raw_text}"},
        ])
        cleaned = (raw or "").strip().strip("```json").strip("```").strip()
        cleaned = (
            cleaned.replace("“", '"').replace("”", '"')
            .replace("‘", "'").replace("’", "'")
        )
        ops = _json.loads(cleaned)
        if not isinstance(ops, list):
            raise ValueError(f"expected JSON list, got {type(ops).__name__}")
    except Exception as e:
        log_error(f"event_log_salvage.llm.{char_id}.{uid}.{date_str}", e)
        return  # 本次失败，不标记 salvaged，留待窗口内下次重试

    if ops:
        await _apply_important_facts_ops(uid, ops, char_id=char_id)

    _mark_salvaged(uid, date_str, char_id=char_id)
    logger.info(
        "[event_log_salvage] 抢救完成 uid=%s char=%s date=%s facts=%d",
        uid, char_id, date_str, len(ops),
    )
