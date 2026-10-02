"""DLQ 回放脚本（工单 G）：dry-run 默认、按范围去重、已处理的不调模型、成功才归档、服务运行时拒绝写入。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import replay_dlq  # noqa: E402


def _write(dlq, ts, task_type, payload):
    path = dlq / f"{ts}_{task_type}.json"
    path.write_text(json.dumps({"task": {"task_type": task_type, "payload": payload},
                                "error": "x", "failed_at": ts / 1000}), encoding="utf-8")
    return path


@pytest.fixture
def dlq(sandbox, monkeypatch):
    path = sandbox.dead_letter_queue()
    path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(replay_dlq, "_service_pid", lambda: None)
    monkeypatch.setattr("core.memory.mid_term.load", lambda uid, char_id=None: [])
    return path


def _run(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["replay_dlq", *args])
    return replay_dlq.main()


def test_plan_dedupes_consolidate_by_scope_and_skips_unreplayable_types(dlq):
    _write(dlq, 1000, "consolidate_to_identity", {"uid": "u", "char_id": "a"})
    _write(dlq, 1001, "consolidate_to_identity", {"uid": "u", "char_id": "a"})
    _write(dlq, 1002, "consolidate_to_identity", {"uid": "u", "char_id": "b"})
    _write(dlq, 1003, "practice_session", {"uid": "u", "char_id": "a", "interest_id": "i"})
    actions = {(r["type"], r["action"], r["detail"]) for r in replay_dlq.plan(dlq)}
    assert actions == {("consolidate_to_identity", "replay", "scope_first"),
                       ("consolidate_to_identity", "archive", "covered"),
                       ("practice_session", "skip", "type_not_replayed")}


def test_reflect_with_nothing_left_is_archived_without_a_model_call_and_pending_one_is_replayed(dlq, monkeypatch):
    monkeypatch.setattr("core.memory.mid_term.load", lambda uid, char_id=None: [
        {"mid_id": "m2"}, {"mid_id": "m3", "promoted_to_episodic_id": "e"}, {"mid_id": "m4", "is_trigger_turn": True}])
    _write(dlq, 1, "reflect_to_episodic", {"uid": "u", "char_id": "a", "mid_ids": ["m1"]})          # 已不存在
    _write(dlq, 2, "reflect_to_episodic", {"uid": "u", "char_id": "a", "mid_ids": ["m3", "m4"]})    # 已晋升/触发轮
    _write(dlq, 3, "reflect_to_episodic", {"uid": "u", "char_id": "a", "mid_ids": ["m2"]})          # 仍待处理
    got = {r["file"]: (r["action"], r["detail"]) for r in replay_dlq.plan(dlq)}
    assert got["1_reflect_to_episodic.json"] == ("archive", "already_handled")
    assert got["2_reflect_to_episodic.json"] == ("archive", "already_handled")
    assert got["3_reflect_to_episodic.json"][0] == "replay"


def test_dry_run_changes_nothing(dlq, monkeypatch, capsys):
    _write(dlq, 1000, "consolidate_to_identity", {"uid": "u", "char_id": "a"})
    called = []
    monkeypatch.setattr(replay_dlq, "_replay_one", lambda row: called.append(row))
    assert _run(monkeypatch) == 0
    assert '"dry_run"' in capsys.readouterr().out and called == []
    assert len(list(dlq.glob("*.json"))) == 1 and not (dlq / "replayed").exists()


def test_apply_refuses_while_the_service_is_running_and_writes_nothing(dlq, monkeypatch, capsys):
    _write(dlq, 1000, "consolidate_to_identity", {"uid": "u", "char_id": "a"})
    monkeypatch.setattr(replay_dlq, "_service_pid", lambda: 4242)
    called = []
    async def fake(row): called.append(row)
    monkeypatch.setattr(replay_dlq, "_replay_one", fake)
    assert _run(monkeypatch, "--apply") == 3 and called == []
    assert "服务正在运行" in capsys.readouterr().err and len(list(dlq.glob("*.json"))) == 1


def test_apply_archives_successes_and_covered_copies_but_never_deletes_and_is_reentrant(dlq, monkeypatch):
    for ts in (1, 2, 3):
        _write(dlq, ts, "consolidate_to_identity", {"uid": "u", "char_id": "a"})
    seen = []
    async def fake(row): seen.append(row["file"])
    monkeypatch.setattr(replay_dlq, "_replay_one", fake)
    assert _run(monkeypatch, "--apply") == 0
    assert seen == ["1_consolidate_to_identity.json"]                     # 同范围只调一次
    assert list(dlq.glob("*.json")) == []
    archived = sorted(p.name for p in (dlq / "replayed").glob("*.json"))
    assert len(archived) == 3 and sum("covered" in n for n in archived) == 2   # 三个文件都还在，只是挪走
    assert _run(monkeypatch, "--apply") == 0 and seen == ["1_consolidate_to_identity.json"]   # 再跑不重复


def test_failure_keeps_the_entry_and_stops_after_consecutive_failures(dlq, monkeypatch):
    for ts, char in ((1, "a"), (2, "b"), (3, "c")):
        _write(dlq, ts, "consolidate_to_identity", {"uid": "u", "char_id": char})
    attempts = []
    async def boom(row):
        attempts.append(row["file"])
        raise RuntimeError("upstream down")
    monkeypatch.setattr(replay_dlq, "_replay_one", boom)
    assert _run(monkeypatch, "--apply", "--max-failures", "2") == 1
    assert len(attempts) == 2                                              # 连续失败 2 次就停，不烧光积压
    assert len(list(dlq.glob("*.json"))) == 3 and not (dlq / "replayed").exists()


def test_batch_size_bounds_the_number_of_model_calls(dlq, monkeypatch):
    for ts, char in ((1, "a"), (2, "b"), (3, "c")):
        _write(dlq, ts, "consolidate_to_identity", {"uid": "u", "char_id": char})
    seen = []
    async def fake(row): seen.append(row["file"])
    monkeypatch.setattr(replay_dlq, "_replay_one", fake)
    assert _run(monkeypatch, "--apply", "--batch-size", "2") == 0
    assert len(seen) == 2 and len(list(dlq.glob("*.json"))) == 1


def test_monitor_and_scan_ignore_the_replayed_archive(dlq, monkeypatch):
    from core import dlq_inspect
    _write(dlq, 1, "consolidate_to_identity", {"uid": "u", "char_id": "a"})
    async def fake(row): pass
    monkeypatch.setattr(replay_dlq, "_replay_one", fake)
    _run(monkeypatch, "--apply")
    assert dlq_inspect.scan(dlq)["count"] == 0
