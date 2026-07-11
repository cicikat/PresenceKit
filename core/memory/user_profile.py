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

from core.config_loader import get_config
from core.character_name_provider import get_active_char_name
from core.error_handler import log_error
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope, require_character_id
from core.data_paths import DEFAULT_CHAR_ID

logger = logging.getLogger(__name__)

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


def load(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> dict:
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
            return merged
    except Exception as e:
        log_error("user_profile.load", e)
    return dict(_DEFAULT_PROFILE)


async def _compress_facts(facts: list) -> list:
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
        raw = await llm_client.chat([{"role": "user", "content": prompt}], max_tokens_override=2000)
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


_PENDING_OVERRIDE_THRESHOLD = 2  # 连续 N 次一致提取才落盘覆盖

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
                existing = await _compress_facts(existing)

            profile["important_facts"] = existing
        else:
            old_value = profile.get(key)
            if not old_value:
                # 空值直接填
                if value:
                    profile[key] = value
            elif value and value != old_value:
                # 非空旧值且新值不同：走 pending-override 计数
                pending = profile.setdefault("_pending_overrides", {})
                current = pending.get(key, {})
                if current.get("new_value") == value:
                    current["count"] = current.get("count", 1) + 1
                else:
                    current = {"new_value": value, "count": 1}

                if current["count"] >= _PENDING_OVERRIDE_THRESHOLD:
                    profile[key] = value
                    pending.pop(key, None)
                    if not pending:
                        profile.pop("_pending_overrides", None)
                    logger.info(
                        f"[user_profile] {key} 覆盖更新：{old_value!r} → {value!r}"
                        f"（{current['count']} 次连续提取）"
                    )
                else:
                    pending[key] = current
                    logger.debug(
                        f"[user_profile] {key} pending override {current['count']}/{_PENDING_OVERRIDE_THRESHOLD}"
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

        raw = await llm_client.chat(prompt_messages)
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


def _save(user_id: str, profile: dict, *, char_id: str = DEFAULT_CHAR_ID):
    """把画像写回磁盘"""
    path = _profile_write_path(user_id, char_id=char_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_error("user_profile._save", e)


def save(user_id: str, profile: dict, *, char_id: str = DEFAULT_CHAR_ID):
    """公开接口：直接将 profile 写回磁盘（admin 覆盖编辑用）"""
    _save(user_id, profile, char_id=char_id)


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
    _save(user_id, dict(_DEFAULT_PROFILE), char_id=char_id)


# ─── 好感度系统（已冻结） ────────────────────────────────────────────────────────────────

_AFFECTION_LEVELS = [
    (0,   99,   "陌生人",   "{char}对她还不太了解"),
    (100, 299,  "普通朋友", "{char}对她有些印象"),
    (300, 499,  "好朋友",   "{char}很高兴认识她"),
    (500, 699,  "亲密朋友", "{char}很珍惜和她在一起的时光"),
    (700, 899,  "挚友",     "{char}对她有深厚的情感"),
    (900, 1000, "灵魂伴侣", "{char}认为她是最重要的人"),
]


def get_affection(user_id: str) -> int:
    """读取用户好感度，默认 0"""
    path = _profile_read_path(user_id)
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return int(data.get("affection", 0))
    except Exception as e:
        log_error("user_profile.get_affection", e)
    return 0


def add_affection(user_id: str, delta: int):
    """增减好感度，结果限制在 0-1000"""
    read_path = _profile_read_path(user_id)
    write_path = _profile_write_path(user_id)
    try:
        if read_path.exists():
            with open(read_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = dict(_DEFAULT_PROFILE)
        current = int(data.get("affection", 0))
        data["affection"] = max(0, min(1000, current + delta))
        with open(write_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_error("user_profile.add_affection", e)


def set_affection(user_id: str, value: int):
    """直接设置好感度（管理员用）"""
    read_path = _profile_read_path(user_id)
    write_path = _profile_write_path(user_id)
    try:
        if read_path.exists():
            with open(read_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = dict(_DEFAULT_PROFILE)
        data["affection"] = max(0, min(1000, int(value)))
        with open(write_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_error("user_profile.set_affection", e)


def get_affection_level(user_id: str) -> dict:
    """返回好感度等级信息：{value, label, description}"""
    char_name = get_active_char_name()
    value = get_affection(user_id)
    for lo, hi, label, desc in _AFFECTION_LEVELS:
        if lo <= value <= hi:
            return {"value": value, "label": label, "description": desc.replace("{char}", char_name)}
    return {"value": value, "label": "灵魂伴侣", "description": _AFFECTION_LEVELS[-1][3].replace("{char}", char_name)}


# ─── 生理期 ────────────────────────────────────────────────────────────────────

def get_period_info(user_id: str, *, char_id: str = DEFAULT_CHAR_ID) -> dict:
    """读取生理期信息，返回包含 last_period_date 字段的字典"""
    profile = load(user_id, char_id=char_id)
    return {"last_period_date": profile.get("last_period_date")}


def set_period_date(user_id: str, date_str: str):
    """设置上次生理期日期（格式：YYYY-MM-DD）"""
    read_path = _profile_read_path(user_id)
    write_path = _profile_write_path(user_id)
    try:
        if read_path.exists():
            with open(read_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = dict(_DEFAULT_PROFILE)
        data["last_period_date"] = date_str
        with open(write_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_error("user_profile.set_period_date", e)


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

    def get_affection(self, user_id: str) -> int:
        return get_affection(user_id)

    def add_affection(self, user_id: str, delta: int):
        add_affection(user_id, delta)

    def set_affection(self, user_id: str, value: int):
        set_affection(user_id, value)

    def get_affection_level(self, user_id: str) -> dict:
        return get_affection_level(user_id)
