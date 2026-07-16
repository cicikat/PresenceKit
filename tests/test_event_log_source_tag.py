"""
tests/test_event_log_source_tag.py — Brief 79 §1 event_log 来源标记

覆盖：
  1. event_log.append(source=...) 把 source: 写进 assistant / user 行的 meta。
  2. 不传 source（老调用方式）→ meta 不含 source 字段，行为完全不变（回归）。
  3. fixation_pipeline.capture_turn(source=...) 透传给 event_log.append。
"""
from __future__ import annotations

from core.memory import event_log


def _day_text(sandbox, uid: str, char_id: str = "yexuan") -> str:
    day_dir = sandbox.memory_char_root(char_id=char_id) / uid / "event_log"
    files = [f for f in day_dir.glob("*.md") if f.name != "full_log.md"]
    assert len(files) == 1, f"期望恰好一个按天日志文件，实际 {len(files)} 个"
    return files[0].read_text(encoding="utf-8")


def test_append_assistant_with_source_writes_meta_field(sandbox):
    uid = "u_source_web"
    event_log.append(uid, "user", "帮我查一下天气", char_id="yexuan")
    event_log.append(uid, "assistant", "查到了，明天晴", char_id="yexuan", source="web")

    text = _day_text(sandbox, uid)
    assert "source:web" in text


def test_append_user_line_with_source_writes_meta_field(sandbox):
    uid = "u_source_user_line"
    event_log.append(uid, "user", "触发内容", char_id="yexuan", source="dream_echo")

    text = _day_text(sandbox, uid)
    assert "source:dream_echo" in text


def test_append_without_source_omits_field(sandbox):
    """老调用方式（不传 source）→ meta 无 source 字段，回归保证。"""
    uid = "u_source_default"
    event_log.append(uid, "user", "普通一轮", char_id="yexuan")
    event_log.append(uid, "assistant", "普通回复", char_id="yexuan")

    text = _day_text(sandbox, uid)
    assert "source:" not in text


def test_capture_turn_forwards_source_to_event_log(sandbox):
    from core.memory.fixation_pipeline import capture_turn
    from core.write_envelope import WriteEnvelope

    uid = "u_capture_source"
    capture_turn(
        uid, "用户说了什么", "角色回了什么",
        envelope=WriteEnvelope(can_write_memory=True),
        char_id="yexuan", source="coplay",
    )

    text = _day_text(sandbox, uid)
    assert "source:coplay" in text
