import asyncio

from core.memory import short_term


def test_long_user_history_projection_and_scoped_read(sandbox):
    uid = "long_user_history_owner"
    original = "开头" + "甲" * 999 + "末尾细节"
    assert short_term.append(uid, "user", original, turn_id="long-1", char_id="character_b")
    stored = short_term.load(uid, char_id="character_b")
    assert stored[0]["content"].endswith("（已裁剪）")
    assert "末尾细节" not in stored[0]["content"]
    assert stored[0]["_long_user_sequence"] == 1
    assert short_term.append(uid, "user", original, turn_id="long-1", char_id="character_b")
    assert len(short_term.load(uid, char_id="character_b")) == 1
    pending = short_term.load_for_prompt(uid, char_id="character_b")
    assert pending[0]["content"].startswith("[长消息序号 1]")
    assert pending[0]["content"].endswith("（已裁剪）")
    assert "末尾细节" in short_term.read_long_user_message(uid, 1, offset=1000, char_id="character_b")
    assert "找不到" in short_term.read_long_user_message(uid, 1, char_id="character_c")
    assert short_term.set_long_user_summary(uid, 1, "用户讲了开头和末尾细节", char_id="character_b")
    ready = short_term.load_for_prompt(uid, char_id="character_b")
    assert ready[0]["content"] == "[长消息序号 1] 用户讲了开头和末尾细节"
    short_term.clear(uid, char_id="character_b")
    assert "找不到" in short_term.read_long_user_message(uid, 1, char_id="character_b")


def test_failed_summary_stays_pending_for_next_attempt(sandbox, monkeypatch):
    uid = "long_user_retry_owner"
    short_term.append(uid, "user", "甲" * 1001, turn_id="retry-1", char_id="character_b")
    calls = []

    async def summarize(_content):
        calls.append(True)
        return None if len(calls) == 1 else "重试成功"

    from core import llm_client
    monkeypatch.setattr(llm_client, "summarize_long_user_message", summarize)

    async def run():
        short_term.schedule_long_user_summaries(uid, char_id="character_b")
        await asyncio.sleep(0)
        assert short_term.pending_long_user_messages(uid, char_id="character_b") == [1]
        short_term.schedule_long_user_summaries(uid, char_id="character_b")
        await asyncio.sleep(0)

    asyncio.run(run())
    assert len(calls) == 2
    assert short_term.pending_long_user_messages(uid, char_id="character_b") == []
    assert "重试成功" in short_term.load_for_prompt(uid, char_id="character_b")[0]["content"]
