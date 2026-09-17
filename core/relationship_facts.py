"""
关系事实表（动态世界书）

每个用户的 relationship_facts.yaml 存放关系事实条目，格式:
  - keywords: ["主人", "叫你"]
    content: "..."
    status: confirmed     # pending | confirmed | archived
    confidence: 0.8
    source: "event_log:主人×176/68天"
    first_seen: "2026-05-21"
    last_seen: "2026-06-20"
    hit_count: 176
    insertion_order: 60

注入规则: 只有 status=confirmed 的条目参与 5.5_lore 注入。
pending 只在 admin 面板可见；archived 归档不注入。

写入路径: data/runtime/memory/{char_id}/{uid}/relationship_facts.yaml
（通过 path_resolver 解析，符合 S6 布局规范）
"""

import logging
import re
from collections import Counter
from datetime import date, timedelta

import yaml

from core.error_handler import log_error
from core.memory.path_resolver import resolve_path
from core.memory.scope import MemoryScope, require_character_id
from core.safe_write import safe_write_text

logger = logging.getLogger(__name__)

# 通用中文词语黑名单 — 不作为称呼建议
_COMMON_WORDS: frozenset[str] = frozenset({
    "我", "你", "她", "他", "它", "们", "的", "了", "是", "在", "有",
    "不", "就", "都", "也", "要", "会", "可", "没", "来", "去", "很",
    "说", "那", "这", "什么", "怎么", "为什么", "因为", "所以", "然后",
    "但是", "如果", "虽然", "因此", "还有", "只是", "还是", "一个", "一些",
    "一起", "一样", "已经", "可以", "需要", "知道", "觉得", "感觉", "现在",
    "今天", "昨天", "明天", "时候", "地方", "喜欢", "讨厌", "开心", "难过",
    "好的", "嗯", "哦", "啊", "哈哈", "哈", "嘻嘻", "呢", "吧", "啦",
    "呀", "么", "哇", "对", "好", "行", "嗯嗯", "嗯嗯嗯", "对对",
    "然而", "不过", "其实", "毕竟", "确实", "而且", "或者", "即使",
})

_USER_LINE_RE = re.compile(r"\*\*用户\*\*[：:]\s*(.+)")
# 句首短词+中文标点（称呼模式检测）
_SENTENCE_START_RE = re.compile(r"^(.{1,6})[，,、！!？?～~]")


# ── 路径 ──────────────────────────────────────────────────────────────────────

def _facts_path(uid: str, *, char_id: str):
    require_character_id(char_id)
    scope = MemoryScope.reality_scope(uid, char_id)
    return resolve_path(scope, "relationship_facts")


# ── 核心存取 ──────────────────────────────────────────────────────────────────

def load(uid: str, *, char_id: str) -> list[dict]:
    """加载全部事实条目（含 pending/confirmed/archived）"""
    p = _facts_path(uid, char_id=char_id)
    if not p.exists():
        return []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or []
        return data if isinstance(data, list) else []
    except Exception as e:
        log_error("relationship_facts.load", e)
        return []


def save(uid: str, facts: list[dict], *, char_id: str) -> bool:
    """原子写入事实列表"""
    p = _facts_path(uid, char_id=char_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        text = yaml.dump(
            facts,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
            indent=2,
        )
        return safe_write_text(p, text)
    except Exception as e:
        log_error("relationship_facts.save", e)
        return False


# ── 注入侧（复用 LoreEngine 原生 enabled 闸门）───────────────────────────────
# 施工单 07 §0.2：实际闸门用 enabled:true/false，status 只作语义注解。
# load_entries() → _normalize_entry() 已原生跳过 enabled:false 条目，
# 无需在此层额外过滤 status，直接复用现成机制。

def match(
    uid: str,
    user_message: str,
    history: list[dict] | None = None,
    *,
    char_id: str,
) -> list[str]:
    """
    匹配关系事实，返回命中的 content 列表（按 insertion_order 升序）。
    闸门：enabled:true 的条目才被注入；建议器写 enabled:false，confirm 后才开。
    接口与 lore_engine.match() 相同，直接拼入 lore_entries。
    """
    facts = load(uid, char_id=char_id)
    if not facts:
        return []

    from core.lore_engine import LoreEngine
    engine = LoreEngine()
    # load_entries 内部调用 _normalize_entry，原生跳过 enabled:false
    engine.load_entries(facts)
    if not engine.entries:
        return []

    return engine.match(user_message, history)


# ── 称呼建议器（Path B MVP）──────────────────────────────────────────────────

def run_address_suggester(
    uid: str,
    char_id: str,
    *,
    days: int = 30,
    freq_threshold: int = 15,
    min_start_count: int = 3,
) -> list[dict]:
    """
    扫描近 days 天的 event_log，统计用户高频固定称呼，产出 pending 建议条目。

    只产出 pending，不自动写入 confirmed。每条带 source 证据，供人工核对。
    返回本次新增的 pending 条目列表（已写入 relationship_facts.yaml）。
    """
    from core.memory.event_log import _event_log_read_dir, _legacy_read_dir_if_eligible

    log_dirs = [_event_log_read_dir(uid, char_id=char_id)]
    old_dir = _legacy_read_dir_if_eligible(uid, char_id)
    if old_dir is not None and old_dir not in log_dirs:
        log_dirs.append(old_dir)

    if not any(d.is_dir() for d in log_dirs):
        logger.debug(f"[relationship_facts.suggester] uid={uid} 无 event_log 目录，跳过")
        return []

    today = date.today()
    user_lines: list[str] = []
    days_scanned = 0

    for i in range(days):
        d = today - timedelta(days=i)
        date_str = d.isoformat()
        day_hit = False
        for log_dir in log_dirs:
            fpath = log_dir / f"{date_str}.md"
            if not fpath.exists():
                continue
            try:
                text = fpath.read_text(encoding="utf-8")
                for m in _USER_LINE_RE.finditer(text):
                    user_lines.append(m.group(1).strip())
                day_hit = True
            except Exception as e:
                logger.warning(f"[relationship_facts.suggester] 读取 {fpath} 失败: {e}")
        if day_hit:
            days_scanned += 1

    if not user_lines:
        return []

    # 统计短词总频次 + 句首出现次数（句首是称呼信号）
    word_total: Counter = Counter()
    word_start: Counter = Counter()

    for line in user_lines:
        sm = _SENTENCE_START_RE.match(line)
        if sm:
            term = sm.group(1).strip()
            if term and term not in _COMMON_WORDS and 1 <= len(term) <= 6:
                word_start[term] += 1

        for m in re.finditer(r"[一-鿿]{1,6}", line):
            t = m.group(0)
            if t not in _COMMON_WORDS:
                word_total[t] += 1

    # 候选：总频次 >= freq_threshold 且 句首出现 >= min_start_count
    candidates = {
        term for term, cnt in word_total.items()
        if cnt >= freq_threshold and word_start.get(term, 0) >= min_start_count
    }

    if not candidates:
        return []

    # 读取现有条目，去除已有关键词
    existing = load(uid, char_id=char_id)
    existing_keywords: set[str] = set()
    for f in existing:
        for kw in (f.get("keywords") or []):
            existing_keywords.add(str(kw))

    today_str = today.isoformat()
    new_facts: list[dict] = []

    for term in sorted(candidates):
        if term in existing_keywords:
            continue
        total_cnt = word_total[term]
        confidence = round(min(0.4 + total_cnt / 400, 0.90), 2)
        new_facts.append({
            "keywords":        [term],
            "content":         f'〔你们之间〕用户习惯称呼你为"{term}"。',
            "enabled":         False,   # 闸门：confirm 前不注入
            "status":          "pending",  # 语义注解，方便面板过滤
            "confidence":      confidence,
            "source":          f"event_log:{term}×{total_cnt}/{days_scanned}天",
            "first_seen":      today_str,
            "last_seen":       today_str,
            "hit_count":       total_cnt,
            "insertion_order": 60,
        })

    if new_facts:
        all_facts = existing + new_facts
        save(uid, all_facts, char_id=char_id)
        logger.info(
            f"[relationship_facts.suggester] uid={uid} 新增 {len(new_facts)} 条 pending 称呼建议"
        )

    return new_facts
