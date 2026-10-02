"""Character self structured library (self_db_*, work order T2)."""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from core import character_self as self_mod
from core import character_self_db as db
from core import tool_dispatcher
from core.autonomy.policy import tool_eligibility
from tests.fixtures.public_assets import TEST_CHAR_ID

_UID = "owner-one"
_CHAR = TEST_CHAR_ID
_COLS = [{"name": "标题", "type": "text"}, {"name": "分数", "type": "number"},
         {"name": "看过", "type": "bool"}, {"name": "日期", "type": "date"}]


def _j(raw: str) -> dict:
    return json.loads(raw)


def _kw(uid=_UID, char=_CHAR):
    return {"user_id": uid, "char_id": char}


def _make(table="电影", **kw):
    return _j(db.db_create_table(table, _COLS, "想看的电影", **(kw or _kw())))


def test_accessor_is_sibling_of_self_root_and_registered(sandbox):
    path = sandbox.character_self_db(_UID, char_id=_CHAR)
    root = sandbox.character_self_root(_UID, char_id=_CHAR)
    assert root not in path.parents
    assert str(path).replace("\\", "/").endswith(f"runtime/self_db/{_CHAR}/{_UID}/self.db")
    from core.data_registry import REGISTRY
    assert REGISTRY["character_self_db"].scope == "per_char_user"


def test_create_insert_query_update_delete_drop(sandbox):
    assert _make()["ok"] is True
    again = _make()
    assert again["ok"] is False and again["code"] == "table_exists"

    ins = _j(db.db_insert("电影", [
        {"标题": "海上钢琴师", "分数": 9.5, "看过": False, "日期": "2026-10-01"},
        {"标题": "霸王别姬", "分数": 9, "看过": True},
    ], **_kw()))
    assert ins["ok"] and ins["rows_affected"] == 2

    q = _j(db.db_query("电影", {"标题": {"contains": "钢琴"}}, **_kw()))
    assert q["row_count"] == 1 and q["rows"][0]["看过"] is False
    assert q["rows"][0]["日期"] == "2026-10-01"
    ordered = _j(db.db_query("电影", None, "-分数", 10, **_kw()))
    assert [r["标题"] for r in ordered["rows"]] == ["海上钢琴师", "霸王别姬"]
    assert _j(db.db_query("电影", {"分数": {"gt": 9}}, **_kw()))["row_count"] == 1
    assert _j(db.db_query("电影", {"标题": {"in": ["霸王别姬", "x"]}}, **_kw()))["row_count"] == 1
    assert _j(db.db_query("电影", {"看过": {"ne": True}}, **_kw()))["row_count"] == 1

    upd = _j(db.db_update("电影", {"标题": "海上钢琴师"}, {"看过": True}, **_kw()))
    assert upd["rows_affected"] == 1
    assert _j(db.db_query("电影", {"看过": True}, **_kw()))["row_count"] == 2

    deleted = _j(db.db_delete("电影", {"_id": 1}, **_kw()))
    assert deleted["rows_affected"] == 1

    tables = _j(db.db_tables(**_kw()))
    assert tables["tables"][0]["table"] == "电影" and tables["tables"][0]["rows"] == 1

    dropped = _j(db.db_drop_table("电影", **_kw()))
    assert dropped["ok"] and dropped["recoverable"] is True
    assert _j(db.db_tables(**_kw()))["tables"] == []
    assert _j(db.db_tables(**_kw()))["trash_count"] == 1
    gone = _j(db.db_query("电影", None, **_kw()))
    assert gone["code"] == "table_not_found"
    # the name is free again after a drop
    assert _make()["ok"] is True


def test_trash_is_pruned_after_ttl(sandbox):
    _make()
    db.db_drop_table("电影", **_kw())
    path = sandbox.character_self_db(_UID, char_id=_CHAR)
    conn = sqlite3.connect(path)
    conn.execute(f"UPDATE {db.META_TABLE} SET trashed_at = ? WHERE trashed_at IS NOT NULL", (time.time() - 90 * 86400 * 2,))
    conn.commit()
    conn.close()
    _make("别的")  # any write prunes expired trash
    assert _j(db.db_tables(**_kw()))["trash_count"] == 0


@pytest.mark.parametrize("bad", ["", "1abc", "a b", "a-b", "x" * 33, "_hidden", "t;DROP", "\"quote", "名字\n"])
def test_invalid_table_identifiers_rejected(sandbox, bad):
    result = _j(db.db_create_table(bad, [{"name": "a", "type": "text"}], **_kw()))
    assert result["ok"] is False and result["code"] == "invalid_identifier"


def test_invalid_columns_rejected(sandbox):
    for cols in ([{"name": "a b", "type": "text"}], [{"name": "_id", "type": "text"}],
                 [{"name": "a", "type": "blob"}], [{"name": "a", "type": "text"}, {"name": "a", "type": "text"}],
                 [], "x"):
        result = _j(db.db_create_table("t", cols, **_kw()))
        assert result["ok"] is False, cols


def test_sql_injection_attempts_are_inert(sandbox):
    attack = "x'); DROP TABLE 电影; --"
    _make()
    assert _j(db.db_create_table("a'; DROP TABLE 电影;--", [{"name": "a", "type": "text"}], **_kw()))["ok"] is False
    assert _j(db.db_create_table("t2", [{"name": attack, "type": "text"}], **_kw()))["ok"] is False
    assert _j(db.db_insert("电影", [{attack: "v"}], **_kw()))["code"] == "unknown_column"
    assert _j(db.db_query("电影", {attack: 1}, **_kw()))["ok"] is False
    assert _j(db.db_query("电影", None, attack, **_kw()))["ok"] is False
    stored = _j(db.db_insert("电影", [{"标题": attack}], **_kw()))
    assert stored["ok"]
    hit = _j(db.db_query("电影", {"标题": {"contains": "'); DROP"}}, **_kw()))
    assert hit["rows"][0]["标题"] == attack
    assert _j(db.db_query("电影", {"标题": attack}, **_kw()))["row_count"] == 1
    assert _j(db.db_tables(**_kw()))["tables"][0]["table"] == "电影"
    # LIKE wildcards in user input are literal
    assert _j(db.db_query("电影", {"标题": {"contains": "%"}}, **_kw()))["row_count"] == 0


def test_where_is_required_for_update_and_delete(sandbox):
    _make()
    db.db_insert("电影", [{"标题": "a"}, {"标题": "b"}], **_kw())
    for where in (None, {}, "1=1"):
        assert _j(db.db_delete("电影", where, **_kw()))["code"] == "where_required"
        assert _j(db.db_update("电影", where, {"标题": "z"}, **_kw()))["code"] == "where_required"
    assert _j(db.db_query("电影", None, **_kw()))["row_count"] == 2


def test_value_type_validation(sandbox):
    _make()
    for row in ({"分数": "abc"}, {"看过": "maybe"}, {"日期": "2026-13-40"}, {"标题": ["x"]}, {"标题": "x" * 5000}):
        assert _j(db.db_insert("电影", [row], **_kw()))["ok"] is False, row
    assert _j(db.db_insert("电影", [{"标题": "a"}] * 51, **_kw()))["code"] == "invalid_rows"
    assert _j(db.db_tables(**_kw()))["tables"][0]["rows"] == 0  # failed batches leave nothing behind


def test_quota_tables_rows_and_file(sandbox, monkeypatch):
    for i in range(db.MAX_TABLES):
        assert _j(db.db_create_table(f"t{i}", [{"name": "a", "type": "text"}], **_kw()))["ok"]
    over = _j(db.db_create_table("overflow", [{"name": "a", "type": "text"}], **_kw()))
    assert over["code"] == "quota_exhausted"

    monkeypatch.setattr(db, "MAX_ROWS_PER_TABLE", 3)
    assert _j(db.db_insert("t0", [{"a": "1"}] * 3, **_kw()))["ok"]
    assert _j(db.db_insert("t0", [{"a": "1"}], **_kw()))["code"] == "quota_exhausted"

    monkeypatch.setattr(db, "_quota_limits", lambda principal: {"max_total_bytes": 4096, "trash_ttl_days": 30, "max_trash": 50})
    big = _j(db.db_insert("t1", [{"a": "y" * 3000}] * 5, **_kw()))
    assert big["code"] == "quota_exhausted"
    assert _j(db.db_query("t1", None, **_kw()))["row_count"] == 0  # rolled back, nothing silently deleted


def test_isolation_between_characters_and_owners(sandbox):
    _make()
    db.db_insert("电影", [{"标题": "mine"}], **_kw())
    assert _j(db.db_tables(**_kw("owner-two")))["tables"] == []
    assert _j(db.db_tables(**_kw(_UID, "other_character")))["tables"] == []
    assert _j(db.db_query("电影", None, **_kw("owner-two")))["code"] == "table_not_found"


def test_grant_revocation_freezes_library(sandbox):
    _make()
    self_mod.set_grant(_UID, _CHAR, False)
    for result in (db.db_tables(**_kw()), db.db_insert("电影", [{"标题": "x"}], **_kw()),
                   db.db_query("电影", None, **_kw()), db.db_drop_table("电影", **_kw())):
        assert _j(result)["code"] == "self_revoked"
    self_mod.set_grant(_UID, _CHAR, True)
    assert _j(db.db_tables(**_kw()))["tables"][0]["table"] == "电影"


def test_library_is_not_reachable_through_self_file_tools(sandbox):
    _make()
    listed = self_mod.list_self(path=None, depth=2, user_id=_UID, char_id=_CHAR)
    assert not any("self.db" in e["path"] or "self_db" in e["path"] for e in listed["entries"])
    assert self_mod.read_self("self.db", user_id=_UID, char_id=_CHAR)["ok"] is False


def test_audit_has_counts_not_contents_and_observability(sandbox):
    _make()
    db.db_insert("电影", [{"标题": "绝密内容ZZZ"}], **_kw())
    audit = sandbox.character_self_audit(_UID, char_id=_CHAR).read_text(encoding="utf-8")
    assert "绝密内容ZZZ" not in audit
    rows = [json.loads(line) for line in audit.splitlines()]
    insert = next(r for r in rows if r["operation"] == "db_insert")
    assert insert["path"] == "电影" and insert["rows"] == 1 and insert["origin"] == "tool"

    snap = self_mod.observability_snapshot(_UID, _CHAR)
    lib = snap["self_db"]
    assert lib["present"] and lib["table_count"] == 1 and lib["tables"][0] == {"table": "电影", "columns": 4, "rows": 1}
    assert lib["file_bytes"] > 0
    assert "绝密内容ZZZ" not in json.dumps(snap, ensure_ascii=False)


@pytest.mark.asyncio
async def test_tools_registered_and_dispatched(sandbox, monkeypatch):
    monkeypatch.setattr(tool_dispatcher, "_is_tool_enabled", lambda _: True)
    monkeypatch.setattr(tool_dispatcher, "_current_mode", lambda: "safe")
    names = ["self_db_tables", "self_db_create_table", "self_db_insert", "self_db_query",
             "self_db_update", "self_db_delete", "self_db_drop_table"]
    schemas = {s["function"]["name"] for s in tool_dispatcher.get_tools_schema(categories=["self"])}
    for name in names:
        spec = tool_dispatcher._TOOL_REGISTRY[name]
        assert spec["category"] == "self" and spec["examples"] and spec["keywords"]
        assert name in schemas
        eligible, _ = tool_eligibility(name, {"enabled": True}, registry=tool_dispatcher._TOOL_REGISTRY, effect=spec["effect"])
        assert eligible is True, name
    assert "self_db_create_table" in tool_dispatcher._TOOL_REGISTRY["self_db_create_table"]["func"].__name__.replace("_tool", "")

    class S:
        status = "idle"

    created = await tool_dispatcher.execute_structured(
        "self_db_create_table", {"table": "清单", "columns": [{"name": "项", "type": "text"}]},
        _UID, _UID, False, S(), origin="assistant_loop", char_id=_CHAR,
    )
    assert "created" in created.result
    spoof = await tool_dispatcher.execute_structured(
        "self_db_tables", {"user_id": "someone-else"}, _UID, _UID, False, S(),
        origin="assistant_loop", char_id=_CHAR,
    )
    assert "grant_principal_mismatch" in spoof.result
