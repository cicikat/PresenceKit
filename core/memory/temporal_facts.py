"""Temporal claims inside the existing scoped profile, backed by immutable events.

No second memory database. Readers recompute validity using event time, never
ingest order; legacy summaries cannot write or promote current claims.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import time

from core.memory.scope import MemoryScope
from core.memory.authority import AUTHORITY_PRIORITY

FIELD = "temporal_facts"
SOURCE_RANK = {source: AUTHORITY_PRIORITY[authority] for source, authority in
               {"user": "canonical_evidence", "admin": "canonical_evidence", "imported_user": "user_stated_fact", "derived": "derived_compat"}.items()}
_PLAN = re.compile(r"打算|计划|准备|想要|如果|假如|可能|梦里|梦到|希望|将来|明天|下周")
_TOPICS = {
    "residence": re.compile(r"青旅|出租屋|租的房|租房|住在|住进|搬家|搬进|现住"),
    "employment": re.compile(r"离职|辞职|在职|任职|入职|工作单位|现在.*工作"),
    "purchase": re.compile(r"购买|订单|消费|支付|购物"),
}


def _require(scope):
    if not isinstance(scope, MemoryScope) or scope.domain != "reality":
        raise ValueError("temporal facts require a Reality user/character scope")


def claim(topic, value, *, source_id, occurred_at, source="user", kind="current",
          valid_from=None, valid_to=None, recorded_at=None):
    """Create a validated claim; source/kind are trusted writer metadata."""
    occurred_at = float(occurred_at)
    start = occurred_at if valid_from is None else float(valid_from)
    end = None if valid_to is None else float(valid_to)
    recorded = time.time() if recorded_at is None else float(recorded_at)
    if (not topic or len(topic) > 128 or not source_id or not str(value).strip()
            or len(str(value)) > 500 or source not in SOURCE_RANK
            or kind not in {"current", "plan", "uncertain", "retraction", "historical"}
            or not all(math.isfinite(x) for x in [occurred_at, recorded, start, *([] if end is None else [end])])
            or (end is not None and end < start)):
        raise ValueError("invalid temporal claim")
    record = {"topic": topic, "value": str(value).strip(), "source_id": str(source_id),
              "occurred_at": occurred_at, "recorded_at": recorded,
              "valid_from": start, "valid_to": end, "source": source, "kind": kind,
              "status": "pending", "superseded_by": None}
    # The source event is immutable. Replaying it with a new capture clock must
    # not create a second claim or advance its event-time authority.
    identity = [record[key] for key in ("topic", "value", "source_id", "source", "kind")]
    record["id"] = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]
    return record


def adjudicate(records, *, now=None):
    """Return historical claims plus one current winner per unambiguous topic."""
    now = time.time() if now is None else now
    rows = [deepcopy(row) for row in records if isinstance(row, dict) and row.get("source") in SOURCE_RANK]
    current, decisions = {}, []
    for topic in sorted({row["topic"] for row in rows}):
        group = [row for row in rows if row["topic"] == topic]
        candidates = []
        retractions = sorted([row for row in group if row.get("kind") == "retraction" and row["occurred_at"] <= now and SOURCE_RANK[row["source"]] > 0],
                             key=lambda row: (row["occurred_at"], SOURCE_RANK[row["source"]], row["id"]))
        for row in group:
            row.update(status="pending", superseded_by=None)
            if row.get("valid_to") is not None and row["valid_to"] <= now:
                row["status"] = "expired"
            elif row.get("kind") == "historical":
                row["status"] = "expired"
            if row.get("kind") == "current" and row["valid_from"] <= now and row["occurred_at"] <= now and SOURCE_RANK[row["source"]] > 0:
                rejected = next((r for r in reversed(retractions) if (r["occurred_at"], SOURCE_RANK[r["source"]]) >= (row["occurred_at"], SOURCE_RANK[row["source"]]) and r["value"] in row["value"]), None)
                if rejected:
                    row.update(status="superseded", superseded_by=rejected["id"])
                candidates.append(row)
        reason = "no_current_evidence"
        if candidates:
            best = max((row["occurred_at"], SOURCE_RANK[row["source"]]) for row in candidates)
            leaders = [row for row in candidates if (row["occurred_at"], SOURCE_RANK[row["source"]]) == best]
            if len({row["value"] for row in leaders}) == 1:
                winner = sorted(leaders, key=lambda row: row["id"])[0]
                usable = winner["status"] not in {"expired", "superseded"}
                if usable: current[topic] = winner
                reason = "event_time_then_source_authority" if usable else "latest_evidence_expired_or_retracted"
                for row in candidates:
                    if row["id"] == winner["id"]:
                        if usable: row["status"] = "valid"
                    elif row["status"] != "expired":
                        row.update(status="superseded", superseded_by=winner["id"])
            else:
                reason = "conflicting_equal_evidence"
                # An older claim must not become current merely because the
                # latest equally strong claims disagree.
        decisions.append({"topic": topic, "reason": reason, "winner_id": current.get(topic, {}).get("id"),
                          "source_ids": [row["source_id"] for row in group]})
    return {"claims": rows, "current": current, "decisions": decisions}


def extract(text, *, source_id, occurred_at, source="user", recorded_at=None):
    """High precision explicit self statements. No role/assistant inference.

    This fast path handles corrections without waiting for periodic LLM profile
    extraction. Ambiguous statements remain unknown; plans never replace facts.
    """
    result = []
    # Quoted statements are somebody's words, not a new owner assertion.
    text = re.sub(r'[“「『\"].*?[”」』\"]', '', str(text), flags=re.S)
    for sentence in re.split(r"[。！!\n；;，,]", text):
        sentence = sentence.strip()
        if not sentence or ("我" not in sentence and not re.match(r"^(现在|目前)在", sentence)):
            continue
        if re.search(r"他说|她说|你说|据说|举例|例如|假设|引用|转述|故事|小说|扮演|系统提示", sentence):
            continue
        if re.search(r"哪里|哪儿|什么地方|是不是|难道|吗|[？?]", sentence):
            continue
        kind = "plan" if _PLAN.search(sentence) else "current"
        topic, value = None, None
        if re.search(r"我(?:现在|已经|早就|本来|可|又|也|并)?(?:不是|不再|没)(?:住|在住).*青旅", sentence):
            topic, value, kind = "residence", "青旅", "retraction" if kind == "current" else kind
        else:
            housing = re.search(r"我(?:现在|目前|已经|早就|早已|一直)?(?:搬进|搬到|住在|住进|住)([^，,]{1,80})", sentence)
            if housing:
                topic, value = "residence", housing.group(1).strip()
            elif housing := re.match(r"(?:我(?:现在|目前)?是\s*|(?:我)?(?:现在|目前)在\s*)(出租屋|青旅|自己租的房间|租的房间)", sentence):
                topic, value = "residence", housing.group(1)
            elif re.search(r"我.*(?:搬家|搬走)了", sentence):
                topic, value = "residence", "已搬离原住处，现住址未知"
        if not topic and re.search(r"我(?:现在|已经|早就|早已)?(?:已)?(?:离职|辞职)", sentence):
            topic, value = "employment", "已离开原工作，现任工作未知"
        if not topic:
            job = re.search(r"我(?:现在|目前|已经)?在([^，,]{1,60}?)(?:工作|任职|上班)", sentence)
            if job: topic, value = "employment", job.group(1)
        if not topic and re.search(r"我.*(?:取消了?.*(?:购买|订单)|(?:购买|订单).*取消)", sentence):
            topic, value = "purchase", "购买已取消，不代表已消费"
        if topic:
            # Relative historical clauses are historical evidence, not updates
            # to the present. Precise imports should supply actual occurred_at.
            if kind == "current" and re.search(r"以前|曾经|过去|去年|上个月|那时候", sentence): kind = "historical"
            result.append(claim(topic, value, source_id=source_id, occurred_at=occurred_at, source=source, kind=kind, recorded_at=recorded_at))
    return result


def record(scope, records):
    """Idempotently append claims under the existing profile lock/provenance."""
    _require(scope)
    if not records: return
    from core.memory import user_profile, provenance_log
    added = []
    def mutation(profile):
        existing = list(profile.get(FIELD) or [])
        ids = {row.get("id") for row in existing}
        for row in records:
            if row["id"] not in ids:
                existing.append(row);ids.add(row["id"]);added.append(row)
        if added: profile[FIELD] = adjudicate(existing)["claims"]
    user_profile.mutate(scope.uid, mutation, char_id=scope.character_id)
    persisted = {row["id"] for row in user_profile.load(scope.uid, char_id=scope.character_id).get(FIELD, [])}
    if any(row["id"] not in persisted for row in added):
        raise RuntimeError("temporal claim persistence failed")
    for row in added:
        try:
            provenance_log.append(scope.uid, scope.character_id, artifact="profile.temporal_facts", field=row["topic"],
                                  before_gist="", after_gist=row["id"], trigger_signal="explicit_user_fact",
                                  origin={"source_id": row["source_id"], "source": row["source"]})
        except Exception:
            pass  # existing provenance is fail-open; no private text in logs


def evidence_claims(scope):
    """Read-only compatibility projection of retained original user evidence."""
    _require(scope)
    from core.memory import event_query
    records = {}
    for keyword in ("住", "出租屋", "青旅", "搬家", "离职", "工作", "取消"):
        page = event_query.search(scope, text=keyword, actor="user", kind="", source="",
                                  occurred_after=None, occurred_before=None, cursor="", limit=32, order="desc", busy_timeout_ms=100)
        for row in page["items"]:
            if (row.get("source") not in {"user_chat", "qq", "wechat"} or row.get("asr_low_confidence")
                    or row.get("channel") in {"stage", "group", "dream"}):
                continue
            for item in extract(row.get("raw_text") or row.get("memory_text", ""), source_id=row["event_id"], occurred_at=row["occurred_at"], recorded_at=row.get("ingested_at")):
                records[item["id"]] = item
    return list(records.values())


def project(scope, profile, context=None, *, now=None, include_evidence=True):
    """Organize context before prompt construction; never mutate the stores."""
    _require(scope)
    claims = deepcopy(list(profile.get(FIELD) or []))
    evidence_unavailable = False
    if include_evidence:
        from core.memory import event_query
        candidates = {row["id"] for row in adjudicate(claims, now=now)["current"].values()}
        for row in claims:
            if row["id"] not in candidates:
                continue  # historical volume must not cause one query per old claim
            try:
                original = event_query.get_event(scope, row["source_id"], busy_timeout_ms=100)
            except event_query.EventQueryError:
                evidence_unavailable = True
                original = None
            if original and original.get("tombstoned"):
                # Withdrawal is an expired barrier, not permission to resurrect
                # an even older state. Stored audit evidence stays untouched.
                row.update(kind="current", valid_to=max(row["valid_from"], time.time() if now is None else now))
        try:
            claims += evidence_claims(scope)
        except event_query.EventQueryError:
            evidence_unavailable = True
    unique = {row["id"]: row for row in claims}
    result = adjudicate(unique.values(), now=now)
    if evidence_unavailable:
        result["current"].clear()
        for row in result["claims"]:
            if row["status"] == "valid": row["status"] = "pending"
        for decision in result["decisions"]:
            decision.update(reason="evidence_unavailable", winner_id=None)
    projected = deepcopy(profile)
    projected["_temporal_current"] = [
        {"topic": topic, "value": row["value"], "source_id": row["source_id"], "occurred_at": row["occurred_at"]}
        for topic, row in result["current"].items() if topic not in {"residence", "employment"}
    ]
    projected["_temporal_decisions"] = result["decisions"]
    # A governed field never falls back to its unversioned legacy scalar when
    # expired/ambiguous. Archival values remain intact in the stored profile.
    governed = {row["topic"] for row in result["claims"] if row["kind"] not in {"plan", "uncertain"}}
    mapping = {"residence": "location", "employment": "occupation"}
    for topic, field in mapping.items():
        if topic in governed or (evidence_unavailable and profile.get(field)):
            governed.add(topic)
            winner = result["current"].get(topic)
            projected[field] = winner["value"] if winner else ("未知（暂时无法核对原始证据）" if evidence_unavailable else "未知（旧状态已失效或存在冲突）")
            if evidence_unavailable and not any(row["topic"] == topic for row in result["decisions"]):
                result["decisions"].append({"topic":topic,"reason":"evidence_unavailable","winner_id":None,"source_ids":[]})
    # Current statements carry event-time semantics in the existing profile layer.
    for topic, field in mapping.items():
        winner = result["current"].get(topic)
        if winner:
            day = datetime.fromtimestamp(winner["valid_from"], timezone.utc).isoformat()
            projected[field] += f"（当前；证据时间 {day}）"
    for field, text_key in (("important_facts", "text"), ("pinned_facts", "text")):
        projected[field] = [row for row in profile.get(field, []) if not any(
            topic in _TOPICS and _TOPICS[topic].search(str(row.get(text_key, "") if isinstance(row, dict) else row))
            for topic in governed)]
    context = dict(context or {})
    for key in ("user_identity_text", "event_search_result", "episodic_result", "episodic_fallback_result", "mid_term", "memory_dossier_context"):
        text = context.get(key)
        if not isinstance(text, str) or not text: continue
        topics = [topic for topic in governed if topic in _TOPICS and _TOPICS[topic].search(text)]
        if topics:
            # Preserve original recall verbatim, explicitly as historical
            # evidence rather than promoting a compressed summary to current.
            context[key] = "【历史/派生记录，涉及已裁决主题；以下旧时状态不能作为当前事实】\n" + text
    projected[FIELD] = result["claims"]
    context["profile"] = projected
    context["_temporal_fact_decisions"] = result["decisions"]
    return context


def observe(scope):
    """Metadata only; original bodies remain behind existing memory detail APIs."""
    from core.memory import user_profile
    profile = user_profile.load(scope.uid, char_id=scope.character_id)
    projected = project(scope, profile)
    return {"decisions": projected["_temporal_fact_decisions"],
            "claims": [{key: row.get(key) for key in ("id", "topic", "source_id", "source", "kind", "occurred_at", "recorded_at", "valid_from", "valid_to", "status", "superseded_by")} for row in projected["profile"].get(FIELD, [])]}
