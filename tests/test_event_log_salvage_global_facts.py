"""
tests/test_event_log_salvage_global_facts.py — Brief 89 §1: event_log_salvage 分流

覆盖：event_log_salvage 可选 global_facts 段解析 + 落盘 + important_facts_ops
主产物零回归（含旧裸数组格式兼容）。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


UID_PREFIX = "gf_salvage"


@pytest.fixture
def fake_llm():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=json.dumps({"important_facts_ops": [], "global_facts": []}))
    return llm


@pytest.fixture(autouse=True)
def patch_llm_client(fake_llm):
    with patch("core.llm_client", fake_llm, create=True):
        yield fake_llm


def _make_registry(*char_ids: str) -> MagicMock:
    reg = MagicMock()
    entries = []
    for cid in char_ids:
        e = MagicMock()
        e.id = cid
        entries.append(e)
    reg.list_all.return_value = entries
    return reg


def _write_day_file(sandbox, char_id, uid, date_str, content="## 10:00\n**用户**：我喜欢喝咖啡\n**叶瑄**：好呀\n> emotion:gentle intensity:0 speaker:assistant\n---\n"):
    day_dir = sandbox.memory_char_root(char_id=char_id) / uid / "event_log"
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / f"{date_str}.md").write_text(content, encoding="utf-8")


def _date_n_days_ago(n: int) -> str:
    return (datetime.now().date() - timedelta(days=n)).strftime("%Y-%m-%d")


def _run_salvage():
    from core.scheduler.triggers.event_log_salvage import _check_event_log_salvage
    with patch("core.scheduler.loop._is_ready", return_value=True), \
         patch("core.scheduler.loop._mark"), \
         patch("core.asset_registry.get_registry", return_value=_make_registry("yexuan")):
        asyncio.run(_check_event_log_salvage())


def test_salvage_applies_global_facts_object_format(sandbox, fake_llm):
    from core.memory import user_facts as uf
    from core.memory import provenance_log

    uid = f"{UID_PREFIX}_ok"
    date_str = _date_n_days_ago(28)
    _write_day_file(sandbox, "yexuan", uid, date_str)

    fake_llm.chat = AsyncMock(return_value=json.dumps({
        "important_facts_ops": [
            {"op": "add", "target_index": None, "text": "喜欢喝咖啡", "tag": "pref.food", "ts": 1000.0},
        ],
        "global_facts": [{"key": "preferred_language", "value": "zh-CN"}],
    }, ensure_ascii=False))

    _run_salvage()

    assert uf.load_user_facts(uid).get("preferred_language") == "zh-CN"
    records = provenance_log.query(uid, "yexuan", artifact="user_facts")
    assert any(r["field"] == "preferred_language" for r in records)


def test_salvage_legacy_bare_list_still_works_no_global_facts(sandbox, fake_llm):
    """旧格式（裸 JSON 数组）仍应正常处理 important_facts_ops，无 global_facts。"""
    from core.memory import user_profile as _up
    from core.memory import user_facts as uf

    uid = f"{UID_PREFIX}_legacy"
    date_str = _date_n_days_ago(28)
    _write_day_file(sandbox, "yexuan", uid, date_str)

    fake_llm.chat = AsyncMock(return_value=json.dumps([
        {"op": "add", "target_index": None, "text": "打算下个月搬家", "tag": "status.project", "ts": 1000.0},
    ], ensure_ascii=False))

    _run_salvage()

    facts = _up.load(uid).get("important_facts") or []
    assert any(f.get("text") == "打算下个月搬家" for f in facts)
    assert uf.load_user_facts(uid) == {}


def test_salvage_malformed_global_facts_does_not_break_ops(sandbox, fake_llm):
    """global_facts 段不是数组时，important_facts_ops 仍应正常落盘（不互相拖累）。"""
    from core.memory import user_profile as _up

    uid = f"{UID_PREFIX}_badgf"
    date_str = _date_n_days_ago(28)
    _write_day_file(sandbox, "yexuan", uid, date_str)

    fake_llm.chat = AsyncMock(return_value=json.dumps({
        "important_facts_ops": [
            {"op": "add", "target_index": None, "text": "喜欢猫", "tag": "pref.media", "ts": 1000.0},
        ],
        "global_facts": "oops-not-a-list",
    }, ensure_ascii=False))

    _run_salvage()

    facts = _up.load(uid).get("important_facts") or []
    assert any(f.get("text") == "喜欢猫" for f in facts)


def test_salvage_denied_global_facts_key_rejected(sandbox, fake_llm):
    from core.memory import user_facts as uf

    uid = f"{UID_PREFIX}_denied"
    date_str = _date_n_days_ago(28)
    _write_day_file(sandbox, "yexuan", uid, date_str)

    fake_llm.chat = AsyncMock(return_value=json.dumps({
        "important_facts_ops": [],
        "global_facts": [{"key": "impression", "value": "warm"}],
    }, ensure_ascii=False))

    _run_salvage()

    assert "impression" not in uf.load_user_facts(uid)
