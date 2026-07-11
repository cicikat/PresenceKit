"""
tests/memeval/engine.py — 记忆质量评测引擎（Brief 44）

不是新召回接口：只是把 case yaml 的种子数据落到 core.memory 各模块的公开/半公开写入点
（episodic_memory._save_memories / user_profile.save / mid_term.append /
event_log.get_recent_days monkeypatch），然后调用真实的
Pipeline.fetch_context() + Pipeline.build_prompt()，对返回结果做确定性断言。

test_memeval.py（pytest 收集）与 run_memeval.py（脚本单跑）共用本模块，
两边只是提供不同的 sandbox / monkeypatch 载体。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CASES_DIR = Path(__file__).parent / "cases"

TEST_CHAR_ID = "memeval_char"
TEST_CHAR_NAME = "测试角色"

_VALID_CATEGORIES = {
    "extraction", "multi_session", "temporal", "knowledge_update", "abstention",
}

_EPISODIC_DEFAULTS = {
    "raw_facts": [],
    "topic_keywords": [],
    "emotion_peak": "neutral",
    "emotion_texture": "",
    "emotion_arc": "",
    "user_state": "",
    "narrative_summary": "",
    "temporal_ref": "none",
    "strength": 0.6,
    "is_core": False,
    "status": "open",
    "resolved_at": None,
    "resolved_by": None,
    "retrieval_count": 0,
    "last_retrieved": None,
    "source_mid_ids": [],
    "consolidated_at": None,
    "event_time": None,
    "expires_at": None,
}


# ── case 加载 ──────────────────────────────────────────────────────────────

def load_cases() -> list[dict]:
    cases = []
    for p in sorted(CASES_DIR.glob("*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not data:
            continue
        data.setdefault("id", p.stem)
        assert data["id"] == p.stem, f"case id({data['id']}) 与文件名({p.stem})不一致"
        assert data.get("category") in _VALID_CATEGORIES, (
            f"{p.name}: category={data.get('category')!r} 不在 {_VALID_CATEGORIES}"
        )
        if data.get("xfail"):
            assert data.get("xfail_reason"), f"{p.name}: xfail=true 必须带 xfail_reason"
        cases.append(data)
    return cases


# ── 种子写入 ───────────────────────────────────────────────────────────────

def _fill_episodic_defaults(now: float, entry: dict) -> dict:
    mem = dict(_EPISODIC_DEFAULTS)
    mem.update(entry)
    assert "id" in mem, f"episodic 种子缺少 id: {entry}"
    days_ago = mem.pop("occurred_days_ago", None)
    hours_ago = mem.pop("occurred_hours_ago", None)
    if days_ago is not None:
        occurred_at = now - float(days_ago) * 86400
    elif hours_ago is not None:
        occurred_at = now - float(hours_ago) * 3600
    else:
        occurred_at = now
    mem.setdefault("occurred_at", occurred_at)
    mem.setdefault("timestamp", mem["occurred_at"])
    return mem


def seed_episodic(uid: str, char_id: str, entries: list[dict]) -> None:
    from core.memory import episodic_memory
    now = time.time()
    memories = [_fill_episodic_defaults(now, e) for e in entries]
    episodic_memory._save_memories(uid, memories, char_id=char_id)


def seed_profile_facts(uid: str, char_id: str, facts: list[dict]) -> None:
    from core.memory import user_profile
    now = time.time()
    normalized = []
    for f in facts:
        f = dict(f)
        days_ago = f.pop("ts_days_ago", None)
        ts = now - float(days_ago) * 86400 if days_ago is not None else now
        normalized.append({
            "text": f["text"],
            "tag": f.get("tag", "misc"),
            "ts": f.get("ts", ts),
        })
    profile = user_profile.load(uid, char_id=char_id)
    profile["important_facts"] = normalized
    user_profile.save(uid, profile, char_id=char_id)


async def apply_profile_extract(uid: str, char_id: str, spec: dict, monkeypatch) -> None:
    """knowledge_update 场景：真实走 user_profile.extract_and_update()（Brief 45）。

    spec.seed_facts 先落一条"旧事实"到 profile（同 seed_profile_facts 格式）；
    spec.new_message 模拟用户新发言；spec.mock_response 是打桩的 LLM 提取输出
    （op-schema：{"op": "add"|"update"|"noop", "target_index", "text", "tag", "ts"}），
    直接监听 core.llm_client.chat 返回，绕过真实网络调用，同时验证冲突裁决落盘逻辑本身。
    """
    from core.memory import user_profile

    if spec.get("seed_facts"):
        seed_profile_facts(uid, char_id, spec["seed_facts"])

    import core.llm_client as _llm
    from unittest.mock import AsyncMock
    mock_json = json.dumps(spec["mock_response"], ensure_ascii=False)
    monkeypatch.setattr(_llm, "chat", AsyncMock(return_value=mock_json))

    await user_profile.extract_and_update(
        uid, [{"role": "user", "content": spec["new_message"]}], char_id=char_id,
    )


def seed_mid_term(uid: str, char_id: str, entries: list[dict]) -> None:
    from core.memory import mid_term
    now = time.time()
    for e in entries:
        e = dict(e)
        hours_ago = e.pop("occurred_hours_ago", None)
        occurred_at = now - float(hours_ago) * 3600 if hours_ago is not None else None
        mid_term.append(
            uid,
            e["summary"],
            e.get("tags", []),
            e.get("mid_id"),
            e.get("source_turn_id"),
            char_id=char_id,
            source=e.get("source", ""),
            memory_strength=e.get("memory_strength", 1.0),
            occurred_at=occurred_at,
        )


def _build_event_log_blob(char_name: str, entries: list[dict]) -> str:
    """把 case 里的 event_log 种子渲染成 event_log.get_recent_days() 会返回的原始文本。

    每个 entry: {days_ago: int, lines: [{speaker: user|assistant, text, emotion?, intensity?}]}
    渲染格式与 core/memory/event_log.py 的落盘格式（P1-1 speaker 元行）完全一致，
    保证 event_log.search() 的 block 解析器按新格式路径解析。
    """
    from datetime import datetime, timedelta
    today = datetime.now()
    parts = []
    for day_entry in entries:
        d = today - timedelta(days=int(day_entry.get("days_ago", 0)))
        date_str = d.strftime("%Y-%m-%d")
        block_lines = ["## 12:00"]
        for line in day_entry.get("lines", []):
            speaker = line["speaker"]
            text = line["text"]
            if speaker == "user":
                block_lines.append(f"**用户**：{text}")
                block_lines.append("> speaker:user")
            else:
                block_lines.append(f"**{char_name}**：{text}")
                emotion = line.get("emotion", "neutral")
                intensity = line.get("intensity", 0)
                block_lines.append(
                    f"> emotion:{emotion} intensity:{intensity} speaker:assistant"
                )
        block_lines.append("---")
        parts.append(f"# {date_str}\n" + "\n".join(block_lines) + "\n")
    return "\n\n".join(parts)


# ── pipeline / 角色卡准备 ────────────────────────────────────────────────
#
# character_loader.load() 经 core.asset_registry 解析 id → 真实仓库根目录下的
# characters/*.json（相对 cwd）。core.config_loader 同样以 cwd 相对路径读
# config.yaml —— 两者都不吃 DataPaths 沙盒重定向，所以这里不 chdir、不搭临时
# characters/ 树，而是直接在真实 characters/ 目录里落一个一次性测试角色卡文件，
# 用完立刻删除（与 tests/conftest.py 的 character_b_registered fixture 同一手法）。
# core.asset_registry._registry 由 tests/conftest.py 的 autouse reset_asset_registry
# 在每个测试前后置空，重新 get_registry() 会重新扫描到这个文件。
#
# char_id 必须每次测试唯一（而非固定常量）：`pytest -n auto` 下多个 worker
# 并发跑不同 case，共享同一个真实文件名会互相覆盖/提前删除。

def _char_file(char_id: str) -> Path:
    return Path("characters") / f"{char_id}.json"


def new_test_char_id() -> str:
    import uuid
    return f"memeval_{uuid.uuid4().hex[:12]}"


def install_test_character(char_id: str, char_name: str = TEST_CHAR_NAME) -> None:
    import json
    _char_file(char_id).write_text(
        json.dumps({"name": char_name, "description": "memeval fixture character", "world_book": []}),
        encoding="utf-8",
    )


def remove_test_character(char_id: str) -> None:
    _char_file(char_id).unlink(missing_ok=True)


def make_pipeline(char_id: str = TEST_CHAR_ID):
    from unittest.mock import MagicMock
    from core.character_loader import load as _load
    from core.pipeline import Pipeline
    char = _load(char_id)
    lore = MagicMock()
    lore.match.return_value = ([], [])
    return Pipeline(char, lore_engine=lore, active_character_id=char_id)


def apply_ambient_stubs(monkeypatch, *, char_name: str = TEST_CHAR_NAME) -> None:
    """把 memeval 不关心的记忆/感知子系统钉死为确定性空值。

    embedding.embed 强制抛异常 —— 模拟"embedding 不可达"，同时避免测试环境
    config.yaml 里的真实 embedding key 被意外发起网络请求（必须做，不是可选项）。
    """
    from unittest.mock import AsyncMock
    import core.memory.embedding as _emb
    monkeypatch.setattr(
        _emb, "embed",
        AsyncMock(side_effect=RuntimeError("memeval: embedding disabled (offline eval)")),
    )

    import core.config_loader as _cl
    monkeypatch.setattr(_cl, "_char_name", lambda: char_name)

    import core.dream.impression_loader as _il
    monkeypatch.setattr(_il, "load_impression_text", lambda *a, **kw: "")

    import core.coplay.game_state as _cgs
    monkeypatch.setattr(_cgs, "build_coplay_context_text", lambda *a, **kw: "")
    monkeypatch.setattr(_cgs, "build_game_log_recall_text", lambda *a, **kw: "")

    import core.coplay.afterglow as _caf
    monkeypatch.setattr(_caf, "load_afterglow_text", lambda *a, **kw: "")

    import core.relationship_facts as _rf
    monkeypatch.setattr(_rf, "match", lambda *a, **kw: [])

    import core.memory.group_context as _gc
    monkeypatch.setattr(_gc, "get_recent", lambda *a, **kw: "")

    import core.tools.reminder as _rem
    monkeypatch.setattr(_rem, "get_reminders", lambda *a, **kw: [])


def apply_event_log_seed(monkeypatch, entries: list[dict] | None, *, char_name: str = TEST_CHAR_NAME) -> None:
    import core.memory.event_log as _el
    blob = _build_event_log_blob(char_name, entries or [])
    monkeypatch.setattr(_el, "get_recent_days", lambda *a, **kw: blob)


def apply_recall_weights(monkeypatch, mode: str) -> None:
    """两种召回权重模式（Brief 44 §4）：

    - "natural"：不改配置，query_vec 恒为 None（embed 已禁用），sem 项天然贡献 0。
    - "sem_zeroed"：额外把 recall.weights.sem 显式钉零，模拟"配置层面也确认关闭语义召回"。
      两者理论上应产出完全一致的结果 —— 用它做一次显式回归锚点，防止未来
      retrieve()/search() 改成即使 query_vec=None 也在内部重新拉一次语义候选。
    """
    if mode == "natural":
        return
    if mode == "sem_zeroed":
        import core.memory.vector_store as _vs
        monkeypatch.setattr(_vs, "_recall_weights", lambda: (0.0, 0.3, 0.3))
        return
    raise ValueError(f"unknown recall mode: {mode}")


# ── 运行单条 case ────────────────────────────────────────────────────────

@dataclass
class CaseResult:
    case_id: str
    episodic_ids: list[str]
    layers_activated: list[str]
    profile_important_facts: list[str]
    event_search_result: str = ""
    mid_term_text: str = ""
    ctx: dict = field(repr=False, default_factory=dict)


def _seed_all(case: dict, uid: str, char_id: str, monkeypatch) -> None:
    seed = case.get("seed") or {}
    if seed.get("episodic"):
        seed_episodic(uid, char_id, seed["episodic"])
    if seed.get("profile_facts"):
        seed_profile_facts(uid, char_id, seed["profile_facts"])
    if seed.get("mid_term"):
        seed_mid_term(uid, char_id, seed["mid_term"])
    apply_event_log_seed(monkeypatch, seed.get("event_log"))


async def _run_case_async(case: dict, monkeypatch, recall_mode: str, char_id: str) -> CaseResult:
    uid = case.get("uid", f"memeval_{case['id']}")

    apply_ambient_stubs(monkeypatch, char_name=TEST_CHAR_NAME)
    apply_recall_weights(monkeypatch, recall_mode)
    _seed_all(case, uid, char_id, monkeypatch)

    profile_extract_spec = (case.get("seed") or {}).get("profile_extract")
    if profile_extract_spec:
        await apply_profile_extract(uid, char_id, profile_extract_spec, monkeypatch)

    pipeline = make_pipeline(char_id)
    from core.memory.scope import MemoryScope
    scope = MemoryScope.reality_scope(uid, char_id)

    ctx = await pipeline.fetch_context(user_id=uid, content=case["query"], frozen_scope=scope)
    _messages, debug_info = pipeline.build_prompt(
        user_id=uid, content=case["query"], context=ctx, char_id=char_id,
    )

    # 直接调用 retrieve()（brief 要求的第二个入口），拿到原始 id 列表做精确断言。
    # 能力探测 since_ts/until_ts 支持：Brief 48 落地前 retrieve() 无此形参，
    # 用 inspect 探测，未来加上后本文件不需要改动即可自动启用时间过滤。
    from core.memory import episodic_memory
    retrieve_kwargs: dict[str, Any] = dict(
        user_id=uid, topic=case["query"], top_k=10, char_id=char_id,
        char_name=TEST_CHAR_NAME, allow_strengthen=False, return_trace=True,
    )
    sig_params = inspect.signature(episodic_memory.retrieve).parameters
    if "since_ts" in sig_params and "until_ts" in sig_params:
        try:
            from core.memory.temporal_query import parse_query_time_range
            rng = parse_query_time_range(case["query"], time.time())
        except ImportError:
            rng = None
        if rng is not None:
            retrieve_kwargs["since_ts"], retrieve_kwargs["until_ts"] = rng
    memories, _trace = episodic_memory.retrieve(**retrieve_kwargs)
    episodic_ids = [m["id"] for m in memories]

    profile_texts = [
        (f["text"] if isinstance(f, dict) else str(f))
        for f in (ctx.get("profile", {}) or {}).get("important_facts", [])
    ]

    return CaseResult(
        case_id=case["id"],
        episodic_ids=episodic_ids,
        layers_activated=debug_info.get("layers_activated", []),
        profile_important_facts=profile_texts,
        event_search_result=ctx.get("event_search_result", "") or "",
        mid_term_text=ctx.get("mid_term", "") or "",
        ctx=ctx,
    )


def run_case(case: dict, monkeypatch, recall_mode: str = "natural", *, char_id: str) -> CaseResult:
    return asyncio.run(_run_case_async(case, monkeypatch, recall_mode, char_id))


def check_expectations(case: dict, result: CaseResult) -> list[str]:
    """返回断言失败信息列表；空列表表示全部通过。"""
    problems = []
    expect = case.get("expect") or {}

    for eid in expect.get("episodic_must_hit", []):
        if eid not in result.episodic_ids:
            problems.append(f"episodic_must_hit 失败：{eid!r} 不在召回结果 {result.episodic_ids}")

    for eid in expect.get("episodic_must_not_hit", []):
        if eid in result.episodic_ids:
            problems.append(f"episodic_must_not_hit 失败：{eid!r} 混入了召回结果 {result.episodic_ids}")

    for layer in expect.get("layers_present", []):
        if layer not in result.layers_activated:
            problems.append(f"layers_present 失败：{layer!r} 未激活，实际 {result.layers_activated}")

    for layer in expect.get("layers_absent", []):
        if layer in result.layers_activated:
            problems.append(f"layers_absent 失败：{layer!r} 不应激活，实际 {result.layers_activated}")

    for substr in expect.get("profile_facts_absent", []):
        hit = [t for t in result.profile_important_facts if substr in t]
        if hit:
            problems.append(f"profile_facts_absent 失败：{substr!r} 出现在 {hit}")

    for substr in expect.get("profile_facts_present", []):
        hit = [t for t in result.profile_important_facts if substr in t]
        if not hit:
            problems.append(
                f"profile_facts_present 失败：{substr!r} 未出现在 {result.profile_important_facts}"
            )

    for substr in expect.get("event_search_contains", []):
        if substr not in result.event_search_result:
            problems.append(
                f"event_search_contains 失败：{substr!r} 未出现在 {result.event_search_result!r}"
            )

    for substr in expect.get("event_search_absent", []):
        if substr in result.event_search_result:
            problems.append(
                f"event_search_absent 失败：{substr!r} 出现在 {result.event_search_result!r}"
            )

    for substr in expect.get("mid_term_contains", []):
        if substr not in result.mid_term_text:
            problems.append(f"mid_term_contains 失败：{substr!r} 未出现在 {result.mid_term_text!r}")

    for substr in expect.get("mid_term_absent", []):
        if substr in result.mid_term_text:
            problems.append(f"mid_term_absent 失败：{substr!r} 出现在 {result.mid_term_text!r}")

    return problems
