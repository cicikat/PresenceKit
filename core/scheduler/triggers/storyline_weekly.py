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
import copy
import hashlib
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
    event_log_text, next_cursor, event_material_ids = _collect_event_log_since(
        uid, char_id, cursor, consumed_ids=consumed,
    )

    if not new_episodes and not inbox_entries and not event_log_text.strip():
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
    material_parts = []
    if materials:
        material_parts.append(f"【具备精确证据的素材】\n{_format_materials(materials)}")
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
        try:
            sl.record_failure(uid, char_id=char_id, code="invalid_llm_output", stage="llm")
        except Exception:
            pass
        return 0

    material_map = {item["material_id"]: item["source_event_ids"] for item in materials}
    if not _source_material_ids_are_valid(ops, set(material_map)):
        logger.error(
            "[storyline_weekly] LLM 返回非法 material ID，本轮不动 cursor uid=%s char=%s",
            uid, char_id,
        )
        try:
            sl.record_failure(uid, char_id=char_id, code="invalid_material_ids", stage="validation")
        except Exception:
            pass
        return 0
    try:
        planned, applied = _plan_ops(data, ops, material_sources=material_map)
    except ValueError as exc:
        logger.error("[storyline_weekly] batch rejected uid=%s char=%s code=%s", uid, char_id, exc)
        try:
            sl.record_failure(uid, char_id=char_id, code=str(exc), stage="validation")
        except Exception:
            pass
        return 0

    now = time.time()
    consumed_ids = [item["material_id"] for item in materials] + event_material_ids
    try:
        sl.commit_batch(
            uid, planned, char_id=char_id, last_aggregated_at=now,
            event_log_cursor=next_cursor, consumed_material_ids=consumed_ids,
        )
    except Exception:
        logger.exception("[storyline_weekly] batch commit failed uid=%s char=%s", uid, char_id)
        try:
            sl.record_failure(uid, char_id=char_id, code="storyline_write_failed", stage="commit")
        except Exception:
            pass
        return 0
    if inbox_entries:
        try:
            sl.clear_consumed_inbox(uid, set(consumed_ids), char_id=char_id)
        except OSError:
            logger.warning("[storyline_weekly] inbox cleanup deferred uid=%s char=%s", uid, char_id)
            try:
                sl.record_failure(uid, char_id=char_id, code="inbox_cleanup_failed", stage="cleanup")
            except Exception:
                pass

    logger.info(
        "[storyline_weekly] 聚合完成 uid=%s char=%s ops=%d/%d episodes=%d inbox=%d",
        uid, char_id, applied, len(ops), len(new_episodes), len(inbox_entries),
    )
    return applied


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
              fallback_source_event_ids: list[str] | None = None) -> tuple[dict, int]:
    """Validate and apply a complete batch to an in-memory copy."""
    from core.memory import storyline as sl
    from core.memory.lineage import normalize_source_event_ids

    if not isinstance(ops, list):
        raise ValueError("invalid_ops")
    planned = copy.deepcopy(data)
    title_to_arc = {str(a.get("title")): a for a in planned.get("arcs", [])}
    now = time.time()
    for index, op in enumerate(ops):
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
                continue
            if sum(a.get("status") == "active" for a in planned["arcs"]) >= sl.MAX_ACTIVE_ARCS:
                raise ValueError("active_arc_limit")
            digest = hashlib.sha256(f"{title}:{index}".encode()).hexdigest()[:12]
            arc = {"arc_id": f"arc_{digest}", "title": title, "status": "active", "tags": sorted(set(tags)),
                   "nodes": [], "created_at": now, "updated_at": now}
            planned["arcs"].append(arc)
            title_to_arc[title] = arc
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
    if len(planned["arcs"]) > sl.MAX_TOTAL_ARCS:
        raise ValueError("total_arc_limit")
    return planned, len(ops)


def _collect_event_log_since(
    uid: str, char_id: str, cursor: object, *, consumed_ids: set[str] | None = None,
) -> tuple[str, dict, list[str]]:
    """Read canonical and legacy files with independent physical checkpoints."""
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
    parts: list[str] = []
    material_ids: list[str] = []
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
            kept: list[str] = []
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
                kept.append("\n".join(block))
                material_ids.append(material_id)
            if kept:
                parts.append(f"[{day}/{source_name}]\n" + "\n".join(kept))
            next_checkpoint = {"day": day, "offset": len(raw_bytes)}
        next_sources[source_name] = next_checkpoint
    if "legacy" not in next_sources:
        next_sources["legacy"] = checkpoints.get("legacy") or {"day": "", "offset": 0}
    canonical = next_sources["canonical"]
    return "\n\n".join(parts), {
        "version": 3, "sources": next_sources,
        "day": canonical["day"], "offset": canonical["offset"],
    }, material_ids
