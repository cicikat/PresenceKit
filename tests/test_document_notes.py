import json

import pytest

from core import character_document_library as library, document_notes as notes
from core.document_reading import revision


def upload(text="# 标题\n原文正文", digest="a" * 64):
    return library.store_upload(uid="reader", char_id="char-test", filename="reading.md",
                                media_type="text/markdown", sha256=digest,
                                searchable_text=text, source="upload_file")


def test_automatic_anchors_and_role_revision_updates_persist(sandbox):
    text = "# 标题\n原文正文"
    doc = upload(text)
    assert library.update_derived("reader", "char-test", doc, revision(text),
                                  overview={"parts": [{"start": 0, "end": len(text), "summary": "概要"}]})
    auto = notes.read("reader", "char-test", doc)["notes"][0]
    assert auto["source"] == "summary_model"
    assert library.read("reader", "char-test", doc, offset=auto["start_offset"])["content"] == text
    assert not notes.write("reader", "char-test", doc, "改自动", 0, len(text), auto["note_id"])["ok"]
    created = notes.write("reader", "char-test", doc, "角色观察", 0, len(text))
    assert created["ok"] and created["revision"] == 1
    note_id = created["note_id"]
    assert not notes.write("reader", "char-test", doc, "旧版本", 0, 5, note_id)["ok"]
    updated = notes.write("reader", "char-test", doc, "补充", 0, len(text), note_id, 1, "append")
    assert updated["content"] == "角色观察\n补充" and updated["revision"] == 2
    assert notes.write("reader", "char-test", doc, "修正", 0, 5, note_id, 2)["revision"] == 3
    assert upload(text) == doc
    page = notes.read("reader", "char-test", query="修正")
    assert page["notes"][0]["note_id"] == note_id
    assert library.observability("reader", "char-test")["documents"][0]["character_note_count"] == 1
    assert not notes.read("other", "char-test", doc)["notes"]
    assert not notes.read("reader", "other", doc)["notes"]
    assert not notes.write("other", "char-test", doc, "跨作用域", 0, 5)["ok"]
    assert library.delete("reader", "char-test", doc)
    assert not notes.read("reader", "char-test", doc)["notes"]
    assert not notes.write("reader", "char-test", doc, "复活", 0, 5)["ok"]


@pytest.mark.asyncio
async def test_paging_full_content_and_limits(sandbox):
    doc = upload()
    for index in range(7):
        assert notes.write("reader", "char-test", doc, str(index) + "内容" * 700, 0, 5)["ok"]
    all_ids, offset = [], 0
    while offset is not None:
        result = await notes.read_for_user("reader", char_id="char-test", document_id=doc, offset=offset)
        assert len(result.safe_summary) < 8000
        payload = json.loads(result.safe_summary.split("\n笔记不是原文。")[0])
        for entry in payload["notes"]:
            assert len(entry["content"]) == 1401
            all_ids.append(entry["note_id"])
        offset = payload["next_offset"]
    assert len(all_ids) == len(set(all_ids)) == 7
    assert not notes.write("reader", "char-test", doc, "内容" * 800, 0, 5)["ok"]
    assert not notes.write("reader", "char-test", doc, "错误范围", 8, 100)["ok"]
    assert not notes.write("reader", "char-test", doc, "空范围", 5, 5)["ok"]


def test_concurrent_same_revision_only_one_writer_succeeds(sandbox):
    from concurrent.futures import ThreadPoolExecutor
    doc = upload()
    created = notes.write("reader", "char-test", doc, "初版", 0, 5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda value: notes.write("reader", "char-test", doc, value, 0, 5,
                                                          created["note_id"], 1), ["修订甲", "修订乙"]))
    assert sum(item["ok"] for item in results) == 1
    assert next(item for item in results if not item["ok"])["error"] == "revision_conflict"


@pytest.mark.asyncio
async def test_dispatcher_private_owner_frozen_scope_and_budget(sandbox, monkeypatch):
    from core import tool_dispatcher as td
    monkeypatch.setattr(td, "get_config", lambda: {"scheduler": {"owner_id": "reader"}})
    monkeypatch.setattr(td, "_is_tool_enabled", lambda _: True)
    monkeypatch.setattr("core.self_management.policy.tool_allowed", lambda *args, **kwargs: True)
    doc = upload()
    args = {"document_id": doc, "content": "主模型笔记", "start_offset": 0, "end_offset": 5}
    ok = await td.execute_structured("write_document_note", args, "reader", "reader", False, object(),
                                     char_id="char-test", origin="assistant_loop")
    assert ok.status == "tool_executed" and '"ok": true' in ok.result
    for uid, group in [("reader", True), ("other", False)]:
        result = await td.execute_structured("write_document_note", args, uid, uid, group, object(),
                                            char_id="char-test", origin="assistant_loop")
        assert result.status == "tool_failed"
    read = await td.execute_structured("read_document_notes", {"document_id": doc}, "reader", "reader",
                                       False, object(), char_id="char-test", origin="assistant_loop")
    assert "主模型笔记" in read.result
    for index in range(4):
        assert notes.write("reader", "char-test", doc, "\n" * 1400 + str(index), 0, 5)["ok"]
    offset, seen = 0, 0
    while offset is not None:
        page = await notes.read_for_user("reader", char_id="char-test", document_id=doc, offset=offset)
        assert len(page.safe_summary) < 8000
        parsed = json.loads(page.safe_summary.split("\n笔记不是原文。")[0])
        seen += len(parsed["notes"])
        offset = parsed["next_offset"]
    assert seen == 5
