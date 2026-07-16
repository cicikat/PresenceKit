"""storyline_weekly — 叙事弧层周频聚合触发器（Brief 80 §2）。

identity.yaml 回答"他是个什么样的人"（稳定属性），storyline 回答"他在经历什么弧线"
（有时间跨度的叙事）。本触发器周频跑一次 LLM 聚合，把三路输入喂给 LLM 归纳成
open_arc/append_node/set_status 操作列表，代码逐条经 core/memory/storyline.py 的
写 API 落盘——LLM 不直接产出全量文件，防止重写旧节点（00d 裁决 1：增量式 + 旧节点只读只追加）。

三路输入：
  1. 上次聚合后新增的 episodic 条目（含已被 identity 固化的——两层互不排斥）；
  2. storyline_inbox.json 的 episodic 淘汰批次碎片（原 memory_digest 的输入，Brief 80 §3 归并）；
  3. event_log 自 meta.event_log_cursor 以来的日文件，跳过 meta 含 source: 非空的块
     （Brief 79 标记，复用 event_log_salvage 的过滤写法）。

模式仿 hidden_state_decay._check_hidden_state_consolidate：7 天冷却（全局，非按 uid），
挂 scheduler，不发言、不进 pipeline。stamp_trigger()。

LLM 失败 / 输出不合法：本轮放弃、不动 cursor、下周重来（fail-open，聚合是幂等增量）。
"""
from __future__ import annotations

import json as _json
import logging
import re
import time
from datetime import datetime

logger = logging.getLogger(__name__)

_MAX_EXISTING_ARC_SUMMARY = 40  # prompt 里每条已有弧线最多带的历史 node 数（防 prompt 过长）

_STORYLINE_SYSTEM_PROMPT = """\
你是一个长期叙事弧线的归纳员。你的任务是把用户近期的经历归纳成"正在进行的故事线"
（storyline arc），而不是提炼稳定人格特征——那是另一层（identity）的职责，绝对不要输出
"他是个怎样的人"这类结论，也不要产出脱离时间线索的性格断言。

只关注【有时间跨度的过程】：职业方向的转变、一个项目/计划的推进、一段持续的情绪历程、
一件事从萌芽到发展的进度。忽略单次、无后续的一次性事件。

聚类原则：按事件/主题边界分组，不要按时间段生硬切分——同一条弧线的多次相关经历应该被
识别为同一个 arc 的不同 node，而不是分散成互不relate的碎片。

现有弧线（可以向其中追加新 node，或调整 status）：
{existing_arcs}

当前活跃(active)弧线数：{active_count}/{max_active}。{active_hint}

新增素材（供你归纳，不要逐条复述，只提炼出有意义的弧线进展）：
{new_material}

只输出一个 JSON 数组，每个元素是以下三种操作之一，不要输出任何其他文字：
[{{"op": "open_arc", "title": "≤20字新弧线标题", "tags": ["从受控tag集合选0个或多个"]}},
 {{"op": "append_node", "arc_title": "已有或本批新开弧线的标题（须与其 title 完全一致）",
   "summary": "≤80字该阶段发生了什么", "ts": Unix时间戳数字, "span": [起始ts, 结束ts]}},
 {{"op": "set_status", "arc_title": "弧线标题", "status": "active/dormant/closed 之一"}}]

受控 tag 集合：{valid_tags}

没有值得记录的弧线进展时返回空数组 []。"""


def _utcnow_iso() -> str:
    from datetime import timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def _check_storyline_weekly() -> None:
    """7-day tick: 遍历所有注册角色 × 存在 episodic.json 的 uid，跑一次聚合。"""
    from core.scheduler.loop import _is_ready, _mark
    from core.write_envelope import stamp_trigger
    from core.asset_registry import get_registry
    from core.sandbox import get_paths
    from core.memory.locks import uid_lock

    if not _is_ready("storyline_weekly"):
        return
    _mark("storyline_weekly")

    char_ids = [e.id for e in get_registry().list_all("character")]
    if not char_ids:
        logger.warning("[storyline_weekly] 无已注册角色，跳过")
        return

    _envelope = stamp_trigger()  # noqa: F841 — documents caller authority

    total_ops = 0
    for char_id in char_ids:
        char_root = get_paths().memory_char_root(char_id=char_id)
        if not char_root.exists():
            continue
        uids = [
            d.name for d in char_root.iterdir()
            if d.is_dir() and (d / "episodic.json").exists()
        ]
        for uid in uids:
            async with uid_lock(uid):
                try:
                    total_ops += await _aggregate_one(char_id, uid)
                except Exception as exc:
                    logger.error(
                        "[storyline_weekly] error uid=%s char_id=%s: %s", uid, char_id, exc
                    )

    logger.info("[storyline_weekly] 本轮完成，合计落盘 %d 条 op", total_ops)


def _format_existing_arcs(arcs: list[dict]) -> str:
    if not arcs:
        return "（暂无已有弧线）"
    lines = []
    for a in arcs:
        if a.get("status") == "closed":
            continue
        recent_nodes = a["nodes"][-_MAX_EXISTING_ARC_SUMMARY:]
        node_lines = "；".join(n["summary"] for n in recent_nodes) or "（暂无节点）"
        lines.append(
            f"- 《{a['title']}》[status={a['status']}, tags={a['tags']}, "
            f"已有节点数={len(a['nodes'])}] 最近进展：{node_lines}"
        )
    return "\n".join(lines) or "（暂无活跃/半活跃弧线）"


async def _aggregate_one(char_id: str, uid: str) -> int:
    """对单个 (char_id, uid) 跑一次聚合。返回本轮实际落盘的 op 数（用于日志统计）。"""
    from core.memory import storyline as sl
    from core.memory.episodic_memory import _load_memories
    from core.tag_rules import TAG_RULES

    data = sl.load(uid, char_id=char_id)
    meta = data["meta"]
    last_aggregated_at = float(meta.get("last_aggregated_at") or 0.0)
    cursor = meta.get("event_log_cursor") or ""

    # 输入 1：上次聚合后新增的 episodic（含已被 identity 固化的）
    all_episodes = _load_memories(uid, char_id=char_id)
    new_episodes = [
        e for e in all_episodes
        if float(e.get("timestamp", 0.0)) > last_aggregated_at
    ]

    # 输入 2：storyline_inbox 的淘汰批次碎片
    inbox_entries = sl.load_inbox(uid, char_id=char_id)

    # 输入 3：event_log 自 cursor 以来的日文件，过滤 source: 非空块
    event_log_text, latest_day = _collect_event_log_since(uid, char_id, cursor)

    if not new_episodes and not inbox_entries and not event_log_text.strip():
        return 0  # 无新素材，幂等 no-op，不调用 LLM

    material_parts = []
    if new_episodes:
        ep_lines = "\n".join(
            f"- [{datetime.fromtimestamp(float(e.get('timestamp', 0))).strftime('%Y-%m-%d')}] "
            f"{e.get('narrative_summary') or e.get('summary', '')}"
            for e in new_episodes
        )
        material_parts.append(f"【新增情景记忆】\n{ep_lines}")
    if inbox_entries:
        inbox_lines = "\n".join(
            f"- [{datetime.fromtimestamp(float(e.get('ts', 0))).strftime('%Y-%m-%d')}] "
            f"{e.get('summary', '')}"
            for e in inbox_entries
        )
        material_parts.append(f"【淘汰归档的旧记忆批次】\n{inbox_lines}")
    if event_log_text.strip():
        material_parts.append(f"【近期对话日志摘录】\n{event_log_text}")
    new_material = "\n\n".join(material_parts)

    active_count = sum(1 for a in data["arcs"] if a.get("status") == "active")
    active_hint = (
        "已达上限，如需开新弧线请先把不再活跃的弧线 set_status 为 dormant 或 closed。"
        if active_count >= sl.MAX_ACTIVE_ARCS else ""
    )
    valid_tags = ", ".join(sorted({r.tag for r in TAG_RULES}))

    system_prompt = _STORYLINE_SYSTEM_PROMPT.format(
        existing_arcs=_format_existing_arcs(data["arcs"]),
        active_count=active_count,
        max_active=sl.MAX_ACTIVE_ARCS,
        active_hint=active_hint,
        new_material=new_material,
        valid_tags=valid_tags,
    )

    from core import llm_client
    try:
        raw = await llm_client.chat(
            [{"role": "system", "content": system_prompt}],
            max_tokens_override=1200,
            call_category="consolidation",
        )
        cleaned = re.sub(r"```json|```", "", (raw or "")).strip()
        ops = _json.loads(cleaned)
        if not isinstance(ops, list):
            raise ValueError(f"expected JSON list, got {type(ops).__name__}")
    except Exception as e:
        logger.error(
            "[storyline_weekly] LLM 输出不合法，本轮放弃不动 cursor uid=%s char=%s err=%s",
            uid, char_id, e,
        )
        return 0

    applied = _apply_ops(uid, char_id, ops)

    now = time.time()
    sl.save_meta(
        uid, char_id=char_id,
        last_aggregated_at=now,
        event_log_cursor=latest_day or cursor,
    )
    if inbox_entries:
        sl.clear_inbox(uid, char_id=char_id)

    logger.info(
        "[storyline_weekly] 聚合完成 uid=%s char=%s ops=%d/%d episodes=%d inbox=%d",
        uid, char_id, applied, len(ops), len(new_episodes), len(inbox_entries),
    )
    return applied


def _apply_ops(uid: str, char_id: str, ops: list) -> int:
    """按 §1 写 API 逐条落盘。arc_title 作为 LLM 侧句柄解析回 arc_id（本批新开的弧线也纳入映射）。"""
    from core.memory import storyline as sl

    data = sl.load(uid, char_id=char_id)
    title_to_id = {a["title"]: a["arc_id"] for a in data["arcs"]}
    applied = 0
    now = time.time()

    for op in ops:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        try:
            if kind == "open_arc":
                title = str(op.get("title", ""))[:20]
                if not title:
                    continue
                arc_id = sl.open_arc(uid, char_id=char_id, title=title, tags=op.get("tags") or [])
                title_to_id[title] = arc_id
                applied += 1
            elif kind == "append_node":
                arc_id = title_to_id.get(str(op.get("arc_title", "")))
                if not arc_id:
                    logger.warning(
                        "[storyline_weekly] append_node 引用未知 arc_title，跳过 uid=%s title=%r",
                        uid, op.get("arc_title"),
                    )
                    continue
                ts = float(op.get("ts") or now)
                span = op.get("span")
                node_id = sl.append_node(
                    uid, char_id=char_id, arc_id=arc_id,
                    summary=str(op.get("summary", ""))[:80],
                    ts=ts, span=span,
                )
                if node_id is not None:
                    applied += 1
            elif kind == "set_status":
                arc_id = title_to_id.get(str(op.get("arc_title", "")))
                status = op.get("status")
                if not arc_id or status not in ("active", "dormant", "closed"):
                    continue
                if sl.set_arc_status(uid, char_id=char_id, arc_id=arc_id, status=status):
                    applied += 1
        except Exception as e:
            logger.error("[storyline_weekly] apply op 失败 uid=%s op=%r err=%s", uid, op, e)

    return applied


def _collect_event_log_since(uid: str, char_id: str, cursor: str) -> tuple[str, str]:
    """读取 cursor 之后（不含）的 event_log 日文件，过滤 source: 非空块。
    返回 (拼接后的过滤文本, 处理到的最新日期字符串)——latest 为空表示无新日文件。"""
    from core.memory.event_log import list_days
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope
    from core.scheduler.triggers.event_log_salvage import _filter_salvageable_text

    days = sorted(d for d in list_days(uid, char_id=char_id) if d > cursor)
    if not days:
        return "", ""

    scope = MemoryScope.reality_scope(uid, char_id)
    log_dir = resolve_path(scope, "event_log")
    parts = []
    for date_str in days:
        day_file = log_dir / f"{date_str}.md"
        if not day_file.exists():
            continue
        try:
            raw = day_file.read_text(encoding="utf-8")
        except OSError:
            continue
        filtered, _skipped = _filter_salvageable_text(raw)
        if filtered.strip():
            parts.append(f"[{date_str}]\n{filtered}")

    return "\n\n".join(parts), days[-1]
