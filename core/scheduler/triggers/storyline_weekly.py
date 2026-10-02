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

LLM 失败 / 输出不合法：本批放弃、不动 cursor，不消耗 7 天冷却，按 6h/12h/24h 退避重试
（meta.aggregation.consecutive_failures / next_retry_at）；素材分批（≤40 条、≤12000 字）逐批提交；
批内非法 op 逐条丢弃（meta.aggregation.rejected_ops），不再整批拒绝。
"""
from __future__ import annotations

import json as _json
import copy
import hashlib
import logging
import re
import time
from datetime import datetime

logger = logging.getLogger(__name__)

_MAX_BATCH_ITEMS = 40       # 每批最多素材条数
_MAX_BATCH_CHARS = 12000    # 每批最多素材字数
_MAX_UNIT_CHARS = 2000      # 单条日志块截断上限
_MAX_OUTPUT_TOKENS = 3000
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

新增素材会在下一条用户消息里给出（供你归纳，不要逐条复述，只提炼出有意义的弧线进展）。

叙事结构说明：一段争执和后来的和好/澄清，应作为同一条弧线的前后节点，而不是各自独立的弧线。

只输出一个 JSON 数组，每个元素是以下三种操作之一，不要输出任何其他文字：
[{{"op": "open_arc", "title": "≤20字新弧线标题", "tags": ["从受控tag集合选0个或多个"]}},
 {{"op": "append_node", "arc_title": "已有或本批新开弧线的标题（须与其 title 完全一致）",
   "summary": "≤80字该阶段发生了什么", "ts": Unix时间戳数字, "span": [起始ts, 结束ts],
   "source_material_ids": ["只能选本批提供的 material_id，不得编造、重复或遗漏字段"]}},
 {{"op": "set_status", "arc_title": "弧线标题", "status": "active/dormant/closed 之一"}}]

受控 tag 集合：{valid_tags}

每个 append_node 都必须带 source_material_ids。若节点仅来自没有 material_id 的旧日志摘录，
固定写 []；这会被标记为 legacy_unknown，绝不能猜测或生成 event ID。

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

    from core.memory import storyline as sl

    if not _is_ready("storyline_weekly"):
        return

    char_ids = [e.id for e in get_registry().list_all("character")]
    if not char_ids:
        _mark("storyline_weekly")
        logger.warning("[storyline_weekly] 无已注册角色，跳过")
        return

    _envelope = stamp_trigger()  # noqa: F841 — documents caller authority

    total_ops = 0
    needs_retry = False
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
                    needs_retry = True
                try:
                    status = (sl.load(uid, char_id=char_id)["meta"].get("aggregation") or {}).get("status")
                except Exception:
                    status = None
                if status == "failed":
                    needs_retry = True

    # 失败不消耗 7 天冷却：留给退避窗口（next_retry_at）后的下一个 tick 重试。
    if not needs_retry:
        _mark("storyline_weekly")
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


def _build_materials(new_episodes: list[dict], inbox_entries: list[dict]) -> list[dict]:
    """Assign prompt-safe IDs to evidence-bearing aggregation input only."""
    from core.memory.lineage import normalize_source_event_ids
    from core.memory.storyline import stable_material_id

    materials: list[dict] = []
    for kind, entries, timestamp_key, content_keys in (
        ("episode", new_episodes, "timestamp", ("narrative_summary", "summary")),
        ("inbox", inbox_entries, "ts", ("summary",)),
    ):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            summary = next((str(entry.get(key) or "").strip() for key in content_keys if entry.get(key)), "")
            materials.append({
                "material_id": stable_material_id(kind, entry),
                "kind": kind,
                "ts": float(entry.get(timestamp_key) or 0.0),
                "summary": summary,
                "source_event_ids": normalize_source_event_ids(entry.get("source_event_ids")),
            })
    return materials


def _format_materials(materials: list[dict]) -> str:
    lines = []
    for material in materials:
        date = datetime.fromtimestamp(material["ts"]).strftime("%Y-%m-%d") if material["ts"] else "未知日期"
        lines.append(
            f"- material_id={material['material_id']} [{material['kind']}/{date}] {material['summary']}"
        )
    return "\n".join(lines)


def _source_material_ids_are_valid(ops: list, material_ids: set[str]) -> bool:
    """Reject an entire LLM batch when an append node names invalid evidence."""
    for op in ops:
        if not isinstance(op, dict) or op.get("op") != "append_node":
            continue
        values = op.get("source_material_ids")
        if not isinstance(values, list) or len(values) > 50:
            return False
        clean = [str(value).strip() for value in values]
        if any(not value for value in clean) or len(set(clean)) != len(clean):
            return False
        if any(value not in material_ids for value in clean):
            return False
    return True


async def _aggregate_one(char_id: str, uid: str) -> int:
    """对单个 (char_id, uid) 跑一次聚合。返回本轮实际落盘的 op 数（用于日志统计）。"""
    from core.memory import storyline as sl
    from core.memory.episodic_memory import _load_memories
    from core.tag_rules import TAG_RULES

    data = sl.load(uid, char_id=char_id)
    meta = data["meta"]
    if time.time() < float((meta.get("aggregation") or {}).get("next_retry_at") or 0.0):
        return 0  # 失败退避窗口内
    last_aggregated_at = float(meta.get("last_aggregated_at") or 0.0)
    cursor = meta.get("event_log_cursor") or {"version": sl.CURSOR_VERSION, "sources": {
        "canonical": {"day": "", "offset": 0}, "legacy": {"day": "", "offset": 0},
    }}
    consumed = set(meta.get("consumed_material_ids") or [])

    # 输入 1：上次聚合后新增的 episodic（含已被 identity 固化的）
    all_episodes = _load_memories(uid, char_id=char_id)
    new_episodes = [
        e for e in all_episodes
        if float(e.get("timestamp", 0.0)) > last_aggregated_at
        and sl.stable_material_id("episode", e) not in consumed
    ]

    # 输入 2：storyline_inbox 的淘汰批次碎片
    raw_inbox_entries = sl.load_inbox(uid, char_id=char_id)
    if consumed and any(sl.stable_material_id("inbox", entry) in consumed for entry in raw_inbox_entries):
        try:
            sl.clear_consumed_inbox(uid, consumed, char_id=char_id)
            raw_inbox_entries = sl.load_inbox(uid, char_id=char_id)
        except OSError:
            logger.warning("[storyline_weekly] receipt cleanup retry deferred uid=%s char=%s", uid, char_id)
            try:
                sl.record_failure(uid, char_id=char_id, code="inbox_cleanup_failed", stage="cleanup")
            except Exception:
                pass
    inbox_entries = [
        entry for entry in raw_inbox_entries
        if sl.stable_material_id("inbox", entry) not in consumed
    ]

    # 输入 3：event_log 自 cursor 以来的日文件，过滤 source: 非空块
    event_items, next_cursor = _collect_event_log_items(
        uid, char_id, cursor, consumed_ids=consumed,
    )

    if not new_episodes and not inbox_entries and not event_items:
        if next_cursor != cursor:
            try:
                sl.commit_batch(
                    uid, data, char_id=char_id, last_aggregated_at=last_aggregated_at,
                    event_log_cursor=next_cursor, consumed_material_ids=[],
                )
            except Exception:
                logger.warning("[storyline_weekly] empty checkpoint failed uid=%s char=%s", uid, char_id)
        return 0  # 无新素材，幂等 no-op，不调用 LLM

    materials = _build_materials(new_episodes, inbox_entries)
    materials.sort(key=lambda m: m["ts"])
    units: list[dict] = [{"kind": "material", "material": m, "text": _format_materials([m])} for m in materials]
    units.extend(
        {"kind": "event", "header": header, "text": text[:_MAX_UNIT_CHARS], "material_id": mid}
        for header, text, mid in event_items
    )
    batches = _split_batches(units)

    valid_tags = ", ".join(sorted({r.tag for r in TAG_RULES}))
    from core import llm_client

    total_applied = 0
    total_rejected = 0
    rejected_codes: list[str] = []
    all_consumed: list[str] = []
    for batch_no, batch in enumerate(batches):
        is_last = batch_no == len(batches) - 1
        batch_materials = [u["material"] for u in batch if u["kind"] == "material"]
        batch_events = [u for u in batch if u["kind"] == "event"]
        material_parts = []
        if batch_materials:
            material_parts.append(f"【具备精确证据的素材】\n{_format_materials(batch_materials)}")
        if batch_events:
            material_parts.append(f"【近期对话日志摘录】\n{_format_event_units(batch_events)}")
        new_material = "\n\n".join(material_parts)

        active_count = sum(1 for a in data["arcs"] if a.get("status") == "active")
        active_hint = (
            "已达上限，如需开新弧线请先把不再活跃的弧线 set_status 为 dormant 或 closed。"
            if active_count >= sl.MAX_ACTIVE_ARCS else ""
        )
        system_prompt = _STORYLINE_SYSTEM_PROMPT.format(
            existing_arcs=_format_existing_arcs(data["arcs"]),
            active_count=active_count,
            max_active=sl.MAX_ACTIVE_ARCS,
            active_hint=active_hint,
            valid_tags=valid_tags,
        )
        try:
            raw = await llm_client.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": new_material},
                ],
                max_tokens_override=_MAX_OUTPUT_TOKENS,
                call_category="consolidation",
                char_id=char_id,
            )
            cleaned = re.sub(r"```json|```", "", (raw or "")).strip()
            ops = _json.loads(cleaned)
            if not isinstance(ops, list):
                raise ValueError(f"expected JSON list, got {type(ops).__name__}")
        except Exception as e:
            logger.error(
                "[storyline_weekly] LLM 输出不合法，本批放弃不动 cursor uid=%s char=%s batch=%d/%d err=%s",
                uid, char_id, batch_no + 1, len(batches), e,
            )
            _record_failure_safe(sl, uid, char_id, "invalid_llm_output", "llm")
            return total_applied

        material_map = {item["material_id"]: item["source_event_ids"] for item in batch_materials}
        rejected: list[str] = []
        try:
            planned, applied = _plan_ops(data, ops, material_sources=material_map, rejected=rejected)
        except ValueError as exc:
            logger.error("[storyline_weekly] batch rejected uid=%s char=%s code=%s", uid, char_id, exc)
            _record_failure_safe(sl, uid, char_id, str(exc), "validation")
            return total_applied
        total_rejected += len(rejected)
        rejected_codes.extend(rejected)

        consumed_ids = [m["material_id"] for m in batch_materials] + [u["material_id"] for u in batch_events]
        try:
            # 非末批保持 last_aggregated_at / event_log cursor 不动：已消费素材靠 consumed 回执去重。
            sl.commit_batch(
                uid, planned, char_id=char_id,
                last_aggregated_at=time.time() if is_last else last_aggregated_at,
                event_log_cursor=next_cursor if is_last else cursor,
                consumed_material_ids=consumed_ids,
                rejected_ops=total_rejected, rejected_codes=rejected_codes[:10],
            )
        except Exception:
            logger.exception("[storyline_weekly] batch commit failed uid=%s char=%s", uid, char_id)
            _record_failure_safe(sl, uid, char_id, "storyline_write_failed", "commit")
            return total_applied
        data = sl.load(uid, char_id=char_id)  # 重读：meta 回执/游标已由 commit_batch 更新
        total_applied += applied
        all_consumed.extend(consumed_ids)
        if rejected:
            logger.warning(
                "[storyline_weekly] 丢弃非法 op %d 条 uid=%s char=%s codes=%s",
                len(rejected), uid, char_id, rejected[:5],
            )

    if inbox_entries:
        try:
            sl.clear_consumed_inbox(uid, set(all_consumed), char_id=char_id)
        except OSError:
            logger.warning("[storyline_weekly] inbox cleanup deferred uid=%s char=%s", uid, char_id)
            _record_failure_safe(sl, uid, char_id, "inbox_cleanup_failed", "cleanup", retry=False)

    logger.info(
        "[storyline_weekly] 聚合完成 uid=%s char=%s ops=%d rejected=%d episodes=%d inbox=%d batches=%d",
        uid, char_id, total_applied, total_rejected, len(new_episodes), len(inbox_entries), len(batches),
    )
    return total_applied


def _record_failure_safe(sl, uid: str, char_id: str, code: str, stage: str, *, retry: bool = True) -> None:
    try:
        sl.record_failure(uid, char_id=char_id, code=code, stage=stage, retry=retry)
    except Exception:
        pass


def _split_batches(units: list[dict]) -> list[list[dict]]:
    """按顺序切批：每批 ≤_MAX_BATCH_ITEMS 条、≤_MAX_BATCH_CHARS 字。"""
    batches: list[list[dict]] = []
    cur: list[dict] = []
    chars = 0
    for unit in units:
        size = len(unit["text"])
        if cur and (len(cur) >= _MAX_BATCH_ITEMS or chars + size > _MAX_BATCH_CHARS):
            batches.append(cur)
            cur, chars = [], 0
        cur.append(unit)
        chars += size
    if cur:
        batches.append(cur)
    return batches


def _format_event_units(units: list[dict]) -> str:
    parts: list[str] = []
    last_header = None
    for u in units:
        if u["header"] != last_header:
            parts.append(u["header"])
            last_header = u["header"]
        parts.append(u["text"])
    return "\n".join(parts)


def _apply_ops(
    uid: str,
    char_id: str,
    ops: list,
    *,
    material_sources: dict[str, list[str]] | None = None,
    source_event_ids: list[str] | None = None,
) -> int:
    """Compatibility entry point: validate fully, then persist once."""
    from core.memory import storyline as sl
    planned, applied = _plan_ops(
        sl.load(uid, char_id=char_id), ops, material_sources=material_sources,
        fallback_source_event_ids=source_event_ids,
    )
    sl._save(uid, planned, char_id=char_id)
    sl._record_batch_provenance(uid, planned, char_id=char_id)
    return applied


def _plan_ops(data: dict, ops: list, *, material_sources: dict[str, list[str]] | None,
              fallback_source_event_ids: list[str] | None = None,
              rejected: list[str] | None = None) -> tuple[dict, int]:
    """Validate and apply a batch to an in-memory copy.

    ``rejected is None``：严格模式，任一 op 非法即整批 ValueError（_apply_ops 兼容入口）。
    传入 list：逐 op 校验，非法 op 丢弃并把错误码追加进 list，合法 op 照常提交。
    """
    from core.memory import storyline as sl
    from core.memory.lineage import normalize_source_event_ids

    if not isinstance(ops, list):
        raise ValueError("invalid_ops")
    planned = copy.deepcopy(data)
    title_to_arc = {str(a.get("title")): a for a in planned.get("arcs", [])}
    now = time.time()
    applied = 0
    for index, op in enumerate(ops):
        snapshot = copy.deepcopy(planned) if rejected is not None else None
        try:
            _plan_one_op(planned, title_to_arc, index, op, now, material_sources, fallback_source_event_ids)
            applied += 1
        except ValueError as exc:
            if rejected is None:
                raise
            rejected.append(str(exc))
            planned = snapshot
            title_to_arc = {str(a.get("title")): a for a in planned.get("arcs", [])}
    if len(planned["arcs"]) > sl.MAX_TOTAL_ARCS:
        raise ValueError("total_arc_limit")
    return planned, applied


def _plan_one_op(planned: dict, title_to_arc: dict, index: int, op: object, now: float,
                 material_sources: dict[str, list[str]] | None,
                 fallback_source_event_ids: list[str] | None) -> None:
    from core.memory import storyline as sl
    from core.memory.lineage import normalize_source_event_ids
    if not isinstance(op, dict) or op.get("op") not in {"open_arc", "append_node", "set_status"}:
        raise ValueError("invalid_op")
    kind = op["op"]
    if kind == "open_arc":
        title = op.get("title")
        tags = op.get("tags", [])
        if not isinstance(title, str) or not title.strip() or len(title) > 20 or not isinstance(tags, list):
            raise ValueError("invalid_open_arc")
        if any(not isinstance(tag, str) or tag not in sl._valid_tags() for tag in tags):
            raise ValueError("invalid_tag")
        if title in title_to_arc:
            return
        if sum(a.get("status") == "active" for a in planned["arcs"]) >= sl.MAX_ACTIVE_ARCS:
            raise ValueError("active_arc_limit")
        digest = hashlib.sha256(f"{title}:{index}".encode()).hexdigest()[:12]
        arc = {"arc_id": f"arc_{digest}", "title": title, "status": "active", "tags": sorted(set(tags)),
               "nodes": [], "created_at": now, "updated_at": now}
        planned["arcs"].append(arc)
        title_to_arc[title] = arc
        if len(planned["arcs"]) > sl.MAX_TOTAL_ARCS:
            raise ValueError("total_arc_limit")
    elif kind == "append_node":
        title, summary = op.get("arc_title"), op.get("summary")
        arc = title_to_arc.get(title) if isinstance(title, str) else None
        if arc is None or not isinstance(summary, str) or not summary.strip() or len(summary) > 80:
            raise ValueError("invalid_append_node")
        if len(arc.get("nodes", [])) >= sl.MAX_NODES_PER_ARC:
            raise ValueError("node_limit")
        try:
            ts = float(op["ts"])
            span = op["span"]
            if not isinstance(span, list) or len(span) != 2:
                raise ValueError
            span = [float(span[0]), float(span[1])]
        except (KeyError, TypeError, ValueError):
            raise ValueError("invalid_time") from None
        if ts > now + 300 or span[0] > span[1] or not span[0] <= ts <= span[1]:
            raise ValueError("invalid_time")
        if arc["nodes"] and ts < float(arc["nodes"][-1]["ts"]):
            raise ValueError("non_monotonic_time")
        ids = op.get("source_material_ids")
        if material_sources is not None:
            if not isinstance(ids, list) or len(ids) > 50 or len(ids) != len(set(ids)) or any(i not in material_sources for i in ids):
                raise ValueError("invalid_material_ids")
            sources = normalize_source_event_ids([eid for mid in ids for eid in material_sources[mid]])
        else:
            sources = normalize_source_event_ids(fallback_source_event_ids)
        nid = hashlib.sha256(f"{arc['arc_id']}:{index}:{summary}:{ts}".encode()).hexdigest()[:12]
        arc["nodes"].append({"node_id": f"n_{nid}", "ts": ts, "span": span, "summary": summary, "source_ids": sources})
        arc["updated_at"] = now
    else:
        title, status = op.get("arc_title"), op.get("status")
        arc = title_to_arc.get(title) if isinstance(title, str) else None
        if arc is None or status not in {"active", "dormant", "closed"}:
            raise ValueError("invalid_status")
        arc["status"] = status
        arc["updated_at"] = now


def _collect_event_log_since(
    uid: str, char_id: str, cursor: object, *, consumed_ids: set[str] | None = None,
) -> tuple[str, dict, list[str]]:
    """Read canonical and legacy files with independent physical checkpoints."""
    items, next_cursor = _collect_event_log_items(uid, char_id, cursor, consumed_ids=consumed_ids)
    parts: list[str] = []
    last = None
    for header, text, _mid in items:
        if header != last:
            parts.append(header + "\n" + text)
            last = header
        else:
            parts[-1] += "\n" + text
    return "\n\n".join(parts), next_cursor, [mid for _h, _t, mid in items]


def _collect_event_log_items(
    uid: str, char_id: str, cursor: object, *, consumed_ids: set[str] | None = None,
) -> tuple[list[tuple[str, str, str]], dict]:
    """Return [(header, block_text, material_id)] plus next cursor."""
    from core.memory.event_log import _block_key
    from core.memory.event_log_source import block_is_recallable, split_blocks
    from core.memory.path_resolver import resolve_path
    from core.memory.scope import MemoryScope
    from core.sandbox import get_paths

    legacy_day = cursor if isinstance(cursor, str) else ""
    v2_day = str(cursor.get("day") or "") if isinstance(cursor, dict) and cursor.get("version") == 2 else legacy_day
    v2_offset = int(cursor.get("offset") or 0) if isinstance(cursor, dict) and cursor.get("version") == 2 else 0
    checkpoints = (cursor.get("sources") or {}) if isinstance(cursor, dict) and cursor.get("version") == 3 else {
        "canonical": {"day": v2_day, "offset": v2_offset},
        "legacy": {"day": v2_day, "offset": 0},
    }
    from core.memory.event_log import may_read_legacy_event_log

    scope = MemoryScope.reality_scope(uid, char_id)
    directories = {
        "canonical": resolve_path(scope, "event_log"),
    }
    if may_read_legacy_event_log(uid, char_id):
        directories["legacy"] = get_paths()._p("event_log") / uid
    consumed_ids = consumed_ids or set()
    items: list[tuple[str, str, str]] = []
    next_sources: dict[str, dict[str, object]] = {}
    seen_keys: set[str] = set()
    for source_name, directory in directories.items():
        checkpoint = checkpoints.get(source_name) or {"day": "", "offset": 0}
        start_day = str(checkpoint.get("day") or "")
        start_offset = int(checkpoint.get("offset") or 0)
        next_checkpoint: dict[str, object] = {"day": start_day, "offset": start_offset}
        files = sorted(directory.glob("????-??-??.md")) if directory.is_dir() else []
        for day_file in files:
            day = day_file.stem
            if day < start_day:
                continue
            try:
                raw_bytes = day_file.read_bytes()
            except OSError:
                continue
            offset = start_offset if day == start_day and start_offset <= len(raw_bytes) else 0
            raw = raw_bytes[offset:].decode("utf-8", errors="ignore")
            for block in split_blocks(raw):
                if not block_is_recallable(block):
                    continue
                block_key = _block_key(block)
                material_id = f"eventlog:{hashlib.sha256((day + ':' + block_key).encode()).hexdigest()[:24]}"
                legacy_material = day + ':' + '\n'.join(block).strip()
                legacy_digest = hashlib.sha256(legacy_material.encode()).hexdigest()[:24]
                legacy_material_id = f"eventlog:{legacy_digest}"
                dedupe_key = f"{day}:{block_key}"
                if (
                    not block_key
                    or dedupe_key in seen_keys
                    or material_id in consumed_ids
                    or legacy_material_id in consumed_ids
                ):
                    continue
                seen_keys.add(dedupe_key)
                items.append((f"[{day}/{source_name}]", "\n".join(block), material_id))
            next_checkpoint = {"day": day, "offset": len(raw_bytes)}
        next_sources[source_name] = next_checkpoint
    if "legacy" not in next_sources:
        next_sources["legacy"] = checkpoints.get("legacy") or {"day": "", "offset": 0}
    canonical = next_sources["canonical"]
    return items, {
        "version": 3, "sources": next_sources,
        "day": canonical["day"], "offset": canonical["offset"],
    }
