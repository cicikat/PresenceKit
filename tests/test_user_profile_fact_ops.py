"""
tests/test_user_profile_fact_ops.py — Brief 45 事实更新语义 add/update/noop

覆盖：
1. update op：替换旧条目而非追加，tag 保留，provenance 落 fact_update
2. noop op：语义重复不新增条目
3. update op target_index 越界 → 降级 add，无异常
4. update op target_index 非 int/op 非法 → 降级 add
5. extract_and_update 端到端：现有 facts 喂给 prompt，LLM 返回 op 列表后正确落盘
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. update：替换旧条目，tag 保留，provenance 落 fact_update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_op_replaces_fact_and_keeps_tag(sandbox):
    from core.memory import user_profile as _up

    _up.save("uid_update", {
        "important_facts": [{"text": "住在北京", "tag": "misc", "ts": 0.0}],
    })

    await _up._apply_important_facts_ops(
        "uid_update",
        [{"op": "update", "target_index": 0, "text": "搬到上海了", "tag": "misc", "ts": 0}],
    )

    profile = _up.load("uid_update")
    texts = [_up._normalize_fact(f)["text"] for f in profile["important_facts"]]
    tags = [_up._normalize_fact(f)["tag"] for f in profile["important_facts"]]
    assert texts == ["搬到上海了"], "旧条目应被替换而非追加"
    assert tags == ["misc"], "tag 应保留"

    from core.memory import provenance_log
    records = provenance_log.query("uid_update", _up.DEFAULT_CHAR_ID, artifact="profile.important_facts")
    assert any(r.get("trigger_signal") == "fact_update" for r in records)


# ---------------------------------------------------------------------------
# 2. noop：语义重复不新增条目
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_noop_op_does_not_add_entry(sandbox):
    from core.memory import user_profile as _up

    _up.save("uid_noop", {
        "important_facts": [{"text": "喜欢喝奶茶", "tag": "pref.food", "ts": 0.0}],
    })

    await _up._apply_important_facts_ops(
        "uid_noop",
        [{"op": "noop", "target_index": 0, "text": "", "tag": "pref.food"}],
    )

    profile = _up.load("uid_noop")
    assert len(profile["important_facts"]) == 1, "noop 不应新增或修改条目"


# ---------------------------------------------------------------------------
# 3. update target_index 越界 → 降级 add，无异常
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_op_out_of_range_index_downgrades_to_add(sandbox):
    from core.memory import user_profile as _up

    _up.save("uid_oob", {
        "important_facts": [{"text": "住在北京", "tag": "misc", "ts": 0.0}],
    })

    await _up._apply_important_facts_ops(
        "uid_oob",
        [{"op": "update", "target_index": 5, "text": "新事实", "tag": "misc", "ts": 0}],
    )

    profile = _up.load("uid_oob")
    texts = [_up._normalize_fact(f)["text"] for f in profile["important_facts"]]
    assert "住在北京" in texts, "越界 update 不应破坏旧条目"
    assert "新事实" in texts, "越界 update 应降级为 add"


# ---------------------------------------------------------------------------
# 4. op 非法 / target_index 非 int → 降级 add
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_op_and_non_int_index_downgrade_to_add(sandbox):
    from core.memory import user_profile as _up

    _up.save("uid_bad", {"important_facts": []})

    await _up._apply_important_facts_ops(
        "uid_bad",
        [
            {"op": "rewrite", "target_index": None, "text": "非法op事实", "tag": "misc"},
            {"op": "update", "target_index": "0", "text": "字符串index事实", "tag": "misc"},
        ],
    )

    profile = _up.load("uid_bad")
    texts = [_up._normalize_fact(f)["text"] for f in profile["important_facts"]]
    assert "非法op事实" in texts
    assert "字符串index事实" in texts


# ---------------------------------------------------------------------------
# 5. extract_and_update 端到端：喂现有 facts，LLM 返回 op 列表
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_and_update_end_to_end_update_op(sandbox):
    from core.memory import user_profile as _up

    _up.save("uid_e2e", {
        "important_facts": [{"text": "在便利店打工", "tag": "misc", "ts": 0.0}],
    })

    mock_response = json.dumps({
        "name": None, "location": None, "pets": None,
        "interests": None, "occupation": None,
        "important_facts": [
            {"op": "update", "target_index": 0, "text": "辞职去考研了", "tag": "misc", "ts": 0},
        ],
    }, ensure_ascii=False)

    fake_llm = AsyncMock(return_value=mock_response)
    with patch("core.llm_client.chat", fake_llm):
        await _up.extract_and_update("uid_e2e", [{"role": "user", "content": "我辞职去考研了"}])

    profile = _up.load("uid_e2e")
    texts = [_up._normalize_fact(f)["text"] for f in profile["important_facts"]]
    assert texts == ["辞职去考研了"]
