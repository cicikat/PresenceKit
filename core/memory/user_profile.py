"""
用户画像模块
存储从对话中提炼出的结构化用户信息
持久化到 data/profiles/{user_id}.json
"""

import json
import logging
import re
import time
from pathlib import Path
from threading import Lock, RLock
from typing import Callable

from core.config_loader import get_config
from core.error_handler import log_error
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope, require_character_id
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

# Profile writers are synchronous and may be reached from async routes.  Keep a
# small, per-profile lock pool so a read-modify-write transaction is serialized
# without coupling different users or characters.
_profile_locks: dict[tuple[str, str], RLock] = {}
_profile_locks_guard = Lock()


def _profile_lock(user_id: str, char_id: str) -> RLock:
    key = (str(user_id), require_character_id(char_id))
    with _profile_locks_guard:
        lock = _profile_locks.get(key)
        if lock is None:
            lock = RLock()
            _profile_locks[key] = lock
        return lock

# 画像字段的默认结构
_DEFAULT_PROFILE = {
    "name": None,           # 真实姓名/常用称呼
    "location": None,       # 所在地
    "pets": None,           # 宠物
    "interests": None,      # 兴趣爱好
    "occupation": None,     # 职业/学校
    "important_facts": [],  # 其他重要事实（列表，元素可为 str 或 {text,tag,ts}）
}

# important_facts 中受控 tag 集合
# pref.* 类（易变偏好）、habit（行为习惯）、health（身体/精神状态）走 recency 召回；
# stable（稳定事实）/ misc（未分类）/ 空字符串始终平铺注入
_RECENCY_TAGS: frozenset[str] = frozenset({
    "pref.music", "pref.food", "pref.media", "habit", "health",
    "status.project",   # 正在做的事 / 近期项目 / 临时近况
})
_PREF_PREFIX = "pref."
_RECENCY_WINDOW_SECONDS = 90 * 86400  # 90 天默认

# 按 tag 定制新鲜度窗口：近况类 30 天过期，避免旧项目常驻
_RECENCY_WINDOW_BY_TAG: dict[str, int] = {
    "status.project": 30 * 86400,
}

_DELETED = object()


class _ProfileDocument(dict):
    """A loaded profile snapshot that records its top-level mutations."""

    def __init__(self, initial: dict):
        super().__init__(initial)
        self._dirty: dict[str, object] = {}

    def __setitem__(self, key: str, value: object) -> None:
        super().__setitem__(key, value)
        self._dirty[key] = value

    def __delitem__(self, key: str) -> None:
        super().__delitem__(key)
        self._dirty[key] = _DELETED

    def pop(self, key: str, *default: object) -> object:
        if key in self:
            value = super().pop(key)
            self._dirty[key] = _DELETED
            return value
        if default:
            return default[0]
        raise KeyError(key)

    def setdefault(self, key: str, default: object = None) -> object:
        if key not in self:
            self[key] = default
        return self[key]

# Prompt-side profile budgets.  These intentionally protect the hot read path
# rather than deleting or rewriting the underlying profile: older free-form
# facts remain available for the later, explicit atomisation migration.
PROFILE_CORE_MAX_CHARS = 360
PROFILE_PREF_MAX_CHARS = 360
PROFILE_PREF_MAX_FACTS = 6

# Only objective, compact profile fields are suitable for every-turn context.
# Interests are preferences, not identity core; relationship/history/inference
# already belong to their dedicated memory layers.
_PROFILE_CORE_FIELDS: tuple[tuple[str, str], ...] = (
    ("name", "名字"),
    ("location", "地点"),
    ("occupation", "职业"),
    ("pets", "宠物"),
)

# This is a deliberately narrow final read-side guard.  It does not classify
# data or delete it; it prevents explicitly intimate/sexual material from
# becoming an ambient every-turn prompt injection when older data has been
# misclassified as stable or a preference.
_SENSITIVE_PROMPT_RE = re.compile(
    r"性偏好|性经验|性行为|性爱|性交|裸体|裸露|自慰|高潮|性欲|床事|床上|亲密关系"
)


def _is_sensitive_for_prompt(text: str) -> bool:
    return bool(_SENSITIVE_PROMPT_RE.search(text or ""))


def _take_with_char_budget(text: str, remaining: int) -> str:
    """Fit text into a hard character budget without emitting an empty item."""
    if remaining <= 0:
        return ""
    if len(text) <= remaining:
        return text
    if remaining == 1:
        return "…"
    return text[: remaining - 1] + "…"


def select_for_prompt(
    profile: dict,
    tags: set[str] | None = None,
    *,
    now: float | None = None,
) -> dict:
    """Select the bounded, safe profile projection used by prompt layer 5.

    ``stable``/``misc`` legacy facts are intentionally archival on this read
    path: they stay on disk untouched, but no longer become permanent prompt
    context.  Tagged or fresh preference facts keep their retrieval behaviour,
    subject to a strict item and character cap.
    """
    current_ts = time.time() if now is None else now
    current_tags = tags or set()

    core_prefix = "<用户概况>\n【关于这个用户】\n"
    core_suffix = "\n</用户概况>"
    core_remaining = max(0, PROFILE_CORE_MAX_CHARS - len(core_prefix) - len(core_suffix))
    core_parts: list[str] = []
    selected_fields: list[str] = []
    sensitive_blocked = 0
    core_budget_excluded = 0
    archived_scalar_fields: list[str] = []

    # ``interests`` is intentionally not listed above: it is a preference and
    # must not bypass relevance selection merely because it is a legacy scalar.
    if profile.get("interests"):
        archived_scalar_fields.append("interests")

    for field, label in _PROFILE_CORE_FIELDS:
        value = str(profile.get(field) or "").strip()
        if not value:
            continue
        if _is_sensitive_for_prompt(value):
            sensitive_blocked += 1
            continue
        item_prefix = "" if not core_parts else "，"
        rendered_prefix = f"{item_prefix}{label}："
        available_value = core_remaining - len(rendered_prefix)
        fitted = _take_with_char_budget(value, available_value)
        if not fitted:
            core_budget_excluded += 1
            continue
        core_parts.append(f"{rendered_prefix}{fitted}")
        core_remaining -= len(rendered_prefix) + len(fitted)
        selected_fields.append(field)

    core_text = ""
    if core_parts:
        core_text = core_prefix + "".join(core_parts) + core_suffix

    tagged_candidates: list[tuple[float, str, str]] = []
    recency_candidates: list[tuple[float, str, str]] = []
    archived_fact_count = 0
    for raw_fact in profile.get("important_facts") or []:
        norm = _normalize_fact(raw_fact)
        text = norm["text"].strip()
        if not text:
            continue
        fact_tag = norm["tag"]
        if _is_sensitive_for_prompt(text):
            sensitive_blocked += 1
            if not _is_recency_tag(fact_tag):
                archived_fact_count += 1
            continue
        if not _is_recency_tag(fact_tag):
            # Legacy stable/misc text is retained as archival data until the
            # structured-record migration; it is not safe as ambient context.
            archived_fact_count += 1
            continue
        tag_key = fact_tag.removeprefix("pref.") if fact_tag.startswith("pref.") else fact_tag
        tag_hit = any(tag_key in current_tag or current_tag in tag_key for current_tag in current_tags)
        candidate = (norm["ts"], text, fact_tag)
        if tag_hit:
            tagged_candidates.append(candidate)
        elif (current_ts - norm["ts"]) < _recency_window_for(fact_tag):
            recency_candidates.append(candidate)

    tagged_candidates.sort(key=lambda item: -item[0])
    recency_candidates.sort(key=lambda item: -item[0])
    pref_prefix = "<用户偏好>\n【用户近期偏好与习惯】\n"
    pref_suffix = "\n</用户偏好>"
    pref_remaining = max(0, PROFILE_PREF_MAX_CHARS - len(pref_prefix) - len(pref_suffix))
    pref_lines: list[str] = []
    selected_tagged = 0
    selected_recency = 0
    pref_budget_excluded = 0
    seen_texts: set[str] = set()

    for source, candidates in (("tagged", tagged_candidates), ("recency", recency_candidates)):
        for _, text, _ in candidates:
            if len(pref_lines) >= PROFILE_PREF_MAX_FACTS:
                pref_budget_excluded += 1
                continue
            if text in seen_texts:
                continue
            line_prefix = "- " if not pref_lines else "\n- "
            fitted = _take_with_char_budget(text, pref_remaining - len(line_prefix))
            if not fitted:
                pref_budget_excluded += 1
                continue
            pref_lines.append(line_prefix + fitted)
            pref_remaining -= len(line_prefix) + len(fitted)
            seen_texts.add(text)
            if source == "tagged":
                selected_tagged += 1
            else:
                selected_recency += 1

    pref_text = ""
    if pref_lines:
        pref_text = pref_prefix + "".join(pref_lines) + pref_suffix

    return {
        "core_text": core_text,
        "pref_text": pref_text,
        "core_provenance": {
            "mode": "budgeted_whitelist",
            "source": "user_profile",
            "selected_fields": selected_fields,
            "archived_fact_count": archived_fact_count,
            "archived_scalar_fields": archived_scalar_fields,
            "sensitive_blocked_count": sensitive_blocked,
            "budget_chars": PROFILE_CORE_MAX_CHARS,
            "budget_excluded_count": core_budget_excluded,
        },
        "pref_provenance": {
            "mode": "tagged" if selected_tagged else "recency",
            "source": "user_profile",
            "tagged_count": selected_tagged,
            "recency_count": selected_recency,
            "sensitive_blocked_count": sensitive_blocked,
            "budget_chars": PROFILE_PREF_MAX_CHARS,
            "budget_items": PROFILE_PREF_MAX_FACTS,
            "budget_excluded_count": pref_budget_excluded,
        },
    }


def _recency_window_for(tag: str) -> int:
    return _RECENCY_WINDOW_BY_TAG.get(tag, _RECENCY_WINDOW_SECONDS)


def _normalize_fact(fact) -> dict:
    """将画像条目归一化为 {text, tag, ts} 格式。旧 str 条目兼容处理，不强制迁移磁盘。"""
    if isinstance(fact, dict):
        return {
            "text": str(fact.get("text", "")),
            "tag": str(fact.get("tag", "misc")),
            "ts": float(fact.get("ts", 0)),
        }
    return {"text": str(fact), "tag": "misc", "ts": 0.0}


def _is_recency_tag(tag: str) -> bool:
    """判断该 tag 是否属于需要 recency 门控的偏好/习惯类别。"""
    return tag in _RECENCY_TAGS or tag.startswith(_PREF_PREFIX)


def _profile_read_path(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    require_character_id(char_id)
    scope = MemoryScope.reality_scope(str(user_id), char_id)
    return resolve_path(scope, "profile")


def _profile_write_path(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> Path:
    require_character_id(char_id)
    scope = MemoryScope.reality_scope(str(user_id), char_id)
    p = resolve_path(scope, "profile")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_unlocked(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> dict:
    """
    读取用户画像，文件不存在时返回空模板
    """
    path = _profile_read_path(user_id, char_id=char_id)
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 用默认模板填充缺失字段，保证结构完整
            merged = dict(_DEFAULT_PROFILE)
            merged.update(data)
            return _ProfileDocument(merged)
    except Exception as e:
        log_error("user_profile.load", e)
    return _ProfileDocument(dict(_DEFAULT_PROFILE))


def load(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> dict:
    """Load a profile snapshot.

    Readers intentionally do not acquire the writer lock: atomic replacement
    means they observe either the old complete JSON document or the new one.
    Call ``mutate`` for any read-modify-write operation.
    """
    return _load_unlocked(user_id, char_id=char_id)


async def _compress_facts(facts: list, *, char_id: str = DEFAULT_CHAR_ID) -> list:
    """
    调用 LLM 对 important_facts 列表做合并去重，
    返回不超过 30 条的精简版本。失败时原样返回。
    """
    try:
        from core import llm_client
        import json as _json

        prompt = (
            "以下是用户的重要事实列表（每条为 {text, tag, ts} 对象或旧格式字符串），请整理精简。规则：\n"
            "1. 语义相同或高度相似的条目只保留一条，措辞最准确的那条\n"
            "2. 以下类型直接删除：测试AI行为的记录、单次临时状态、对话玩笑、已在name/location/pets/interests/occupation字段存储的信息\n"
            "3. 输出不超过25条\n"
            "4. 每条输出为 {\"text\": \"内容\", \"tag\": \"标签\", \"ts\": 时间戳} 格式；旧字符串条目保留原 tag=misc/ts=0\n"
            "只输出JSON数组，不要其他内容：\n"
            + _json.dumps([_normalize_fact(f) for f in facts], ensure_ascii=False)
        )
        raw = await llm_client.chat(
            [{"role": "user", "content": prompt}],
            max_tokens_override=2000,
            char_id=char_id,
        )
        raw = raw.strip()
        # 清理各种markdown代码块格式
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)
        raw = raw.strip()
        # 提取JSON数组
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            raw = match.group()
        else:
            # 尝试补全截断的JSON数组
            if raw.startswith("[") and not raw.endswith("]"):
                last_quote = raw.rfind('"')
                if last_quote > 0:
                    raw = raw[:last_quote+1] + "]"
        compressed = _json.loads(raw)
        if isinstance(compressed, list):
            logger.info(
                f"[user_profile] important_facts 已合并压缩：{len(facts)} → {len(compressed)} 条"
            )
            return compressed
    except Exception as e:
        log_error("user_profile._compress_facts", e)
    return facts


_PENDING_OVERRIDE_THRESHOLD = 2  # 连续 N 次一致提取才落盘覆盖（默认，name/pets/interests/occupation 用）

# 2026-07-25（茶茶反馈：说了"我现在在绍兴"，地点始终不更新，天气也一直查杭州）：
# 默认阈值=2 对 location 不成立——extract_and_update 每次只喂最近 10 条用户发言，
# 用户提一次新地点、话题很快聊别的岔开后就再也凑不出第二次"连续一致提取"，
# pending_overrides 永远停在 count=1，profile['location'] 从此追不上现实（进而
# get_probe_prompt 把这个陈旧值当"用户位置"注入，天气工具跟着一起查旧城市）。
# location 是"此刻状态"而非容易被幻觉污染的人设描述，单次清晰提取（"我在绍兴"）
# 应该立即生效；其余字段（尤其 name/occupation，更容易被单次误提取污染）继续用默认阈值。
_PENDING_OVERRIDE_THRESHOLD_BY_FIELD: dict[str, int] = {
    "location": 1,
}

# important_facts 冲突裁决合法 op 集合（Brief 45）
_VALID_FACT_OPS: frozenset[str] = frozenset({"add", "update", "noop"})


async def update(user_id: str, new_facts: dict, *, char_id: str = DEFAULT_CHAR_ID):
    """
    合并更新用户画像。

    important_facts 列表去重追加；超 30 条触发 LLM 压缩。
    其他标量字段：
      - 原值为空 → 直接填入（同旧逻辑）
      - 原值非空且新值不同 → 写入 _pending_overrides 挂起，
        连续 _PENDING_OVERRIDE_THRESHOLD 次同一新值才落盘覆盖，
        防止单次偶然提取翻转已确认的值。
    """
    profile = load(user_id, char_id=char_id)

    for key, value in new_facts.items():
        if key == "important_facts":
            # 列表字段：去重追加（支持旧 str 和新 {text,tag,ts} 两种格式）
            existing = profile.get("important_facts") or []
            existing_texts = {_normalize_fact(f)["text"] for f in existing}
            items = value if isinstance(value, list) else ([value] if value else [])
            for item in items:
                norm = _normalize_fact(item)
                if norm["text"] and norm["text"] not in existing_texts:
                    existing.append(norm)
                    existing_texts.add(norm["text"])

            # 超过 30 条时触发 LLM 合并压缩
            if len(existing) > 30:
                logger.info(
                    f"[user_profile] important_facts 已达 {len(existing)} 条，触发 LLM 压缩"
                )
                existing = await _compress_facts(existing, char_id=char_id)

            profile["important_facts"] = existing
        else:
            old_value = profile.get(key)
            if not old_value:
                # 空值直接填
                if value:
                    profile[key] = value
            elif value and value != old_value:
                # 非空旧值且新值不同：走 pending-override 计数
                # _ProfileDocument 只跟踪顶层 mutation；复制后显式回写，避免
                # 对已存在 nested dict 的原地修改在三方合并保存时被忽略。
                pending = dict(profile.get("_pending_overrides") or {})
                current = dict(pending.get(key) or {})
                if current.get("new_value") == value:
                    current["count"] = current.get("count", 1) + 1
                else:
                    current = {"new_value": value, "count": 1}

                threshold = _PENDING_OVERRIDE_THRESHOLD_BY_FIELD.get(key, _PENDING_OVERRIDE_THRESHOLD)
                if current["count"] >= threshold:
                    profile[key] = value
                    pending.pop(key, None)
                    if not pending:
                        profile.pop("_pending_overrides", None)
                    else:
                        profile["_pending_overrides"] = pending
                    logger.info(
                        f"[user_profile] {key} 覆盖更新：{old_value!r} → {value!r}"
                        f"（{current['count']} 次连续提取）"
                    )
                else:
                    pending[key] = current
                    profile["_pending_overrides"] = pending
                    logger.debug(
                        f"[user_profile] {key} pending override {current['count']}/{threshold}"
                        f"：候选值 {value!r}"
                    )

    _save(user_id, profile, char_id=char_id)


async def _apply_important_facts_ops(user_id: str, ops: list, *, char_id: str = DEFAULT_CHAR_ID) -> None:
    """按冲突裁决 op（add/update/noop）逐条落盘 important_facts 候选事实（Brief 45）。

    - add：走 update() 原有的去重追加（+超30条压缩）逻辑，行为不变。
    - update：越界/非 int/op 非法一律降级为 add（fail-open，只 WARN 不抛），
      合法时调用 overwrite_important_fact 原地替换，trigger_signal 标 "fact_update"
      以区别于 admin 显式删除/覆盖（"explicit_forget"）。
    - noop：语义重复，直接丢弃。
    """
    if not ops:
        return
    current = load(user_id, char_id=char_id).get("important_facts") or []
    current_count = len(current)
    add_items = []
    for raw_op in ops:
        if not isinstance(raw_op, dict):
            continue
        op = raw_op.get("op", "add")
        if op not in _VALID_FACT_OPS:
            logger.warning(f"[user_profile] important_facts 非法 op {op!r}，降级为 add")
            op = "add"
        if op == "noop":
            continue
        if op == "update":
            idx = raw_op.get("target_index")
            if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < current_count:
                overwrite_important_fact(
                    user_id, idx, str(raw_op.get("text", "")),
                    char_id=char_id, tag=str(raw_op.get("tag", "misc")),
                    trigger_signal="fact_update",
                )
                continue
            logger.warning(
                f"[user_profile] important_facts update op target_index 非法/越界"
                f"({idx!r}，现有 {current_count} 条），降级为 add"
            )
            op = "add"
        if op == "add":
            add_items.append({
                "text": str(raw_op.get("text", "")),
                "tag": str(raw_op.get("tag", "misc")),
                "ts": float(raw_op.get("ts") or time.time()),
            })

    if add_items:
        await update(user_id, {"important_facts": add_items}, char_id=char_id)


async def extract_and_update(user_id: str, recent_messages: list[dict], *, char_id: str = DEFAULT_CHAR_ID):
    """
    用 LLM 从最近对话中提取新的用户信息，并更新画像
    应每 N 轮调用一次（N = summary_every_n_rounds）

    LLM 被要求只返回 JSON，不输出其他内容
    """
    if not recent_messages:
        return

    # 只喂用户轮——角色发言不是事实证据，防止角色幻觉被当事实写入画像
    user_turns = [m for m in recent_messages if m.get("role") == "user"]
    conv_text = "\n".join(m["content"] for m in user_turns[-10:])

    existing_facts = load(user_id, char_id=char_id).get("important_facts") or []
    existing_facts_listing = "\n".join(
        f"{i}: {_normalize_fact(f)['text']}" for i, f in enumerate(existing_facts)
    ) or "（当前没有已记录的 important_facts）"

    prompt_messages = [
        {
            "role": "system",
            "content": (
                "你是一个信息提取助手。请从下面的用户发言中提取用户的个人信息。\n"
                "注意：以下文字仅包含用户自己说的话，不含AI发言。\n"
                "只返回 JSON 对象，不要输出任何其他内容。\n"
                "JSON 格式：\n"
                '{"name": null或字符串, "location": null或字符串, "pets": null或字符串, "interests": null或字符串, "occupation": null或字符串, "important_facts": [op条目列表]}\n'
                "现有 important_facts 列表（index 从 0 开始，供你判断新信息是否与某条已有事实矛盾/更新/重复）：\n"
                f"{existing_facts_listing}\n"
                "important_facts 中每条候选新事实输出对象："
                '{"op": "add"或"update"或"noop", "target_index": null或上面列表中的index数字, '
                '"text": "事实内容", "tag": "分类标签", "ts": 时间戳数字}。\n'
                "op 判定规则：\n"
                "- add：全新事实，现有列表里没有对应条目，target_index 填 null。\n"
                "- update：新信息是对某条现有事实的状态更新或矛盾（如\"搬家了\"推翻\"住在北京\"、"
                "\"分手了\"推翻\"和男朋友在一起\"），target_index 填该条在现有列表中的 index，text 填更新后的完整事实。\n"
                "- noop：新信息与某条现有事实语义重复（说的是同一件事，没有新增信息），"
                "target_index 填该条 index，text 可留空。\n"
                "没有可对照的新证据时，important_facts 填 []。\n"
                "tag 从以下受控集合中选择：pref.music（音乐偏好）/ pref.food（饮食偏好）/ pref.media（影视/游戏偏好）/ habit（日常习惯）/ health（身体/精神状态）/ status.project（用户最近在做的事、在开发的项目、临时近况）/ stable（稳定的性格/观点/情感/关系等长期概况）/ misc（其他）。\n"
                "情感、价值观、性格、关系定位 → stable；具体口味、在追的作品、手头项目、近期状态 → 对应 pref.*/status.project，不要塞进 stable。\n"
                "ts 填写当前 Unix 时间戳（秒），用于判断事实新鲜度。\n"
                "important_facts 只记录稳定的、有意义的个人事实，例如：性格特点、生活习惯、重要经历、身体状况（包括精神状态）、明确的偏好（喜欢/不喜欢）。\n"
                "绝对不要记录：用户测试AI功能的行为、单次询问某件事、临时状态、对话中的玩笑或表情包、已经在其他字段记录的信息。\n"
                "没有提到的字段填 null。"
            ),
        },
        {
            "role": "user",
            "content": f"用户发言（当前时间戳约 {int(time.time())}）：\n{conv_text}",
        },
    ]

    try:
        from core import llm_client
        import json as _json

        raw = await llm_client.chat(prompt_messages, char_id=char_id)
        # 清理可能的 markdown 代码块
        raw = raw.strip().strip("```json").strip("```").strip()
        raw = (raw
               .replace("“", '"').replace("”", '"')
               .replace("‘", "'").replace("’", "'"))
        new_facts = _json.loads(raw)
        from core.integrity_check import check_profile
        _issues = check_profile(new_facts)
        if _issues:
            logger.warning(f"[user_profile] 内容未通过规则纠察，拒绝写入: {_issues}")
            return
        facts_ops = new_facts.pop("important_facts", None)
        await update(user_id, new_facts, char_id=char_id)
        if facts_ops:
            await _apply_important_facts_ops(user_id, facts_ops, char_id=char_id)
        logger.info(f"[user_profile] 用户 {user_id} 画像已更新")
    except Exception as e:
        log_error("user_profile.extract_and_update", e)


def _save(user_id: str, profile: dict, *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    """把画像写回磁盘"""
    from core.safe_write import safe_write_text

    with _profile_lock(user_id, char_id):
        path = _profile_write_path(user_id, char_id=char_id)
        try:
            if isinstance(profile, _ProfileDocument):
                latest = _load_unlocked(user_id, char_id=char_id)
                for key, value in profile._dirty.items():
                    if value is _DELETED:
                        latest.pop(key, None)
                    else:
                        latest[key] = value
                data = dict(latest)
            else:
                data = dict(profile)
            payload = json.dumps(data, ensure_ascii=False, indent=2)
            return safe_write_text(path, payload)
        except Exception as e:
            log_error("user_profile._save", e)
            return False


def save(user_id: str, profile: dict, *, char_id: str = DEFAULT_CHAR_ID):
    """公开接口：直接将 profile 写回磁盘（admin 覆盖编辑用）"""
    _save(user_id, profile, char_id=char_id)


def mutate(
    user_id: str,
    mutation: Callable[[dict], None],
    *,
    char_id: str = DEFAULT_CHAR_ID,
) -> dict:
    """Reload, mutate, and atomically save under one profile-specific lock."""
    with _profile_lock(user_id, char_id):
        profile = _load_unlocked(user_id, char_id=char_id)
        mutation(profile)
        _save(user_id, profile, char_id=char_id)
        return profile


def delete_important_fact(user_id: str, index: int, *, char_id: str = DEFAULT_CHAR_ID) -> bool:
    """Delete one important_fact entry by list index.

    Returns True if removed, False if index out of range.
    Appends provenance record on success.
    """
    profile = load(user_id, char_id=char_id)
    facts = profile.get("important_facts") or []
    if index < 0 or index >= len(facts):
        return False
    removed = _normalize_fact(facts.pop(index))
    profile["important_facts"] = facts
    _save(user_id, profile, char_id=char_id)

    try:
        from core.memory import provenance_log
        provenance_log.append(
            user_id, char_id,
            artifact="profile.important_facts",
            field=str(index),
            before_gist=removed["text"][:120],
            after_gist="",
            trigger_signal="explicit_forget",
            origin={"source": "admin"},
        )
    except Exception:
        pass
    return True


def overwrite_important_fact(
    user_id: str, index: int, new_text: str, *,
    char_id: str = DEFAULT_CHAR_ID, tag: str = "misc",
    trigger_signal: str = "explicit_forget",
) -> bool:
    """Overwrite one important_fact entry by list index with new_text.

    Returns True if updated, False if index out of range.
    Appends provenance record on success. trigger_signal 默认 "explicit_forget"
    （admin 显式改写场景）；事实冲突裁决（Brief 45 的 update op）传 "fact_update" 以区分来源。
    """
    import time as _time
    profile = load(user_id, char_id=char_id)
    facts = profile.get("important_facts") or []
    if index < 0 or index >= len(facts):
        return False
    old = _normalize_fact(facts[index])
    facts[index] = {"text": new_text, "tag": tag, "ts": _time.time()}
    profile["important_facts"] = facts
    _save(user_id, profile, char_id=char_id)

    try:
        from core.memory import provenance_log
        provenance_log.append(
            user_id, char_id,
            artifact="profile.important_facts",
            field=str(index),
            before_gist=old["text"][:120],
            after_gist=new_text[:120],
            trigger_signal=trigger_signal,
            origin={"source": "admin" if trigger_signal == "explicit_forget" else "extract_and_update"},
        )
    except Exception:
        pass
    return True


def clear(user_id: str, *, char_id: str = DEFAULT_CHAR_ID):
    """清空用户画像（admin 用）"""
    def reset_owned_fields(profile: dict) -> None:
        for key, value in _DEFAULT_PROFILE.items():
            profile[key] = list(value) if isinstance(value, list) else value
        profile.pop("_pending_overrides", None)

    mutate(user_id, reset_owned_fields, char_id=char_id)


# ─── 生理期 ────────────────────────────────────────────────────────────────────

def get_period_info(user_id: str) -> dict:
    """Deprecated compatibility shim for the uid-global period state."""
    logger.warning("user_profile.get_period_info is deprecated; use health_state.get_period_info")
    from core.memory.health_state import get_period_info as _get_period_info

    return _get_period_info(user_id)


def set_period_date(user_id: str, date_str: str):
    """Deprecated compatibility shim for the uid-global period state."""
    logger.warning("user_profile.set_period_date is deprecated; use health_state.set_period_date")
    from core.memory.health_state import set_period_date as _set_period_date

    return _set_period_date(user_id, date_str)


class UserProfile:
    """用户画像类，封装模块级函数，供外部按类方式导入使用"""

    def load(self, user_id: str) -> dict:
        return load(user_id)

    async def update(self, user_id: str, new_facts: dict):
        await update(user_id, new_facts)

    async def extract_and_update(self, user_id: str, recent_messages: list[dict]):
        await extract_and_update(user_id, recent_messages)

    def save(self, user_id: str, profile: dict):
        save(user_id, profile)

    def clear(self, user_id: str):
        clear(user_id)
