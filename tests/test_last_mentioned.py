import json
from datetime import datetime


def _write_event_log(paths, uid: str, date_text: str, body: str) -> None:
    day_dir = paths.user_memory_root(uid) / "event_log"
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / f"{date_text}.md").write_text(body, encoding="utf-8")


def test_recall_last_mentioned_reads_event_log_by_recent_time(sandbox):
    from core.scheduler.last_mentioned import recall_last_mentioned

    uid = "u1"
    _write_event_log(
        sandbox,
        uid,
        "2026-05-25",
        """
## 14:00
**用户**：我准备继续改实习材料
> turn_id:t1
**叶瑄**：我记得这件事。
> emotion:gentle intensity:1 turn_id:t1
---

## 15:00
**用户**：我明天要测试桌宠通道
> turn_id:t2
**叶瑄**：那我陪你看结果。
> emotion:gentle intensity:1 turn_id:t2
---
""",
    )

    topic = recall_last_mentioned(uid, now=datetime(2026, 5, 25, 16, 0))

    assert topic is not None
    assert "测试桌宠通道" in topic.topic
    assert topic.topic_key == "测试桌宠通道"
    assert "用户：我明天要测试桌宠通道" in topic.context


def test_recall_last_mentioned_skips_no_recent_followable_topic(sandbox):
    from core.scheduler.last_mentioned import recall_last_mentioned

    uid = "u1"
    _write_event_log(
        sandbox,
        uid,
        "2026-05-25",
        """
## 14:00
**用户**：嗯。叶瑄。
> turn_id:t1
**叶瑄**：我在。
> emotion:neutral intensity:0 turn_id:t1
---
""",
    )

    assert recall_last_mentioned(uid, now=datetime(2026, 5, 25, 16, 0)) is None


def test_followed_topics_live_state_uses_scheduler_state(sandbox):
    from core.scheduler.last_mentioned import is_recently_followed, mark_topic_followed

    user_state_path = sandbox.scheduler_user_state()
    user_state_path.parent.mkdir(parents=True, exist_ok=True)
    user_state_path.write_text(json.dumps({"last_diary_share": 1.0}), encoding="utf-8")

    mark_topic_followed("实习材料", now_ts=1_000.0)

    raw = json.loads(user_state_path.read_text(encoding="utf-8"))
    assert raw["last_diary_share"] == 1.0
    assert raw["followed_topics"] == {"实习材料": 1_000.0}
    assert is_recently_followed("实习材料", now_ts=1_100.0)
    assert not is_recently_followed("实习材料", now_ts=1_000.0 + 4 * 24 * 3600)


def test_followed_topics_shadow_is_separate_from_live_state(sandbox):
    from core.scheduler.last_mentioned import (
        is_recently_followed,
        load_followed_topics,
        load_followed_topics_shadow,
        mark_topic_followed_shadow,
    )

    mark_topic_followed_shadow("实习材料", now_ts=1_000.0)

    assert load_followed_topics() == {}
    assert load_followed_topics_shadow() == {"实习材料": 1_000.0}
    assert is_recently_followed("实习材料", now_ts=1_100.0, shadow=True)
    assert not is_recently_followed("实习材料", now_ts=1_100.0, shadow=False)


def test_topic_key_stays_stable_for_same_input_recall(sandbox):
    from core.scheduler.last_mentioned import recall_last_mentioned

    uid = "u1"
    _write_event_log(
        sandbox,
        uid,
        "2026-05-25",
        """
## 15:00
**用户**：我准备继续改实习材料
> turn_id:t1
**叶瑄**：我记得。
> emotion:gentle intensity:1 turn_id:t1
---
""",
    )

    keys = [
        recall_last_mentioned(uid, now=datetime(2026, 5, 25, 16, 0)).topic_key
        for _ in range(3)
    ]

    assert keys == ["继续改实习材料"] * 3
