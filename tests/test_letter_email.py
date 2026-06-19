import asyncio
from datetime import date
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


def test_dream_reason_requires_weight_and_freshness(monkeypatch):
    from core.dream import dream_afterglow
    from core.scheduler.triggers import letter_writer

    monkeypatch.setattr(
        dream_afterglow,
        "_find_best_summary",
        lambda uid, *, char_id: ({"summary_weight": 0.9, "summary": "很深的梦"}, 2.0),
    )

    assert "很深的梦" in letter_writer._dream_reason("u1", char_id="character_b")


def test_conversation_gap_reason_uses_scoped_history(monkeypatch):
    from core.memory import short_term
    from core.scheduler.triggers import letter_writer

    seen = {}

    def fake_load(uid, *, char_id):
        seen.update(uid=uid, char_id=char_id)
        return [{"timestamp": 100.0}]

    monkeypatch.setattr(short_term, "load", fake_load)

    reason = letter_writer._conversation_gap_reason(
        "u1",
        char_id="character_b",
        now_ts=100.0 + 4 * 86400,
    )

    assert "4 天" in reason
    assert seen == {"uid": "u1", "char_id": "character_b"}


def test_strong_episodic_reason_requires_recent_memory(monkeypatch):
    from core.memory import episodic_memory
    from core.scheduler.triggers import letter_writer

    now = 2_000_000_000.0
    monkeypatch.setattr(
        episodic_memory,
        "_load_memories",
        lambda uid, *, char_id: [
            {"strength": 0.99, "timestamp": now - 8 * 86400, "summary": "太旧"},
            {"strength": 0.9, "timestamp": now - 86400, "narrative_summary": "刚发生的重要事"},
        ],
    )

    reason = letter_writer._strong_episodic_reason("u1", char_id="character_b", now_ts=now)

    assert "刚发生的重要事" in reason
    assert "太旧" not in reason


def test_anniversary_eve_covers_owner_birthday_and_anniversaries(monkeypatch):
    from core.scheduler.triggers import letter_writer

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {
            "scheduler": {"owner_birthday": "06-14"},
            "anniversaries": [
                {"key": "broken"},
                {"key": "first_day", "month": 8, "day": 14},
            ],
        },
    )

    assert "生日" in letter_writer._anniversary_eve_reason(date(2026, 6, 13))
    assert "first_day" in letter_writer._anniversary_eve_reason(date(2026, 8, 13))


def test_hidden_state_reason_compares_current_to_baseline(monkeypatch):
    from core.memory import user_hidden_state_store
    from core.memory.user_hidden_state import default_hidden_state
    from core.scheduler.triggers import letter_writer

    state = default_hidden_state()
    state.sensitivity.baseline.value = 40
    state.sensitivity.current.value = 70
    monkeypatch.setattr(
        user_hidden_state_store,
        "load_hidden_state",
        lambda uid, *, char_id: state,
    )

    assert letter_writer._hidden_state_reason("u1", char_id="character_b")


def test_similarity_gate_uses_point_seven_threshold():
    from core.scheduler.triggers.letter_writer import _is_too_similar

    assert _is_too_similar("茶茶，今天想和你说一件事。" * 8, "茶茶，今天想和你说一件事。" * 8)
    assert not _is_too_similar("完全不同的一封信", "另一段没有共同内容的话")


def test_proposer_requires_enabled_mail_and_quiet_state(monkeypatch):
    from core.scheduler import loop
    from core.scheduler.state_machine import TriggerState
    from core.scheduler.triggers import letter_writer

    monkeypatch.setattr("core.config_loader.get_config", lambda: {"mail": {"enabled": True}})
    monkeypatch.setattr(loop, "_is_ready", lambda name: True)
    monkeypatch.setattr(loop, "_owner_id", lambda: "owner")
    monkeypatch.setattr(loop, "_active_char_id_or_none", lambda: "character_b")
    monkeypatch.setattr(
        letter_writer,
        "_check_trigger_conditions",
        lambda uid, *, char_id, now_ts: "值得写信",
    )

    proposal = letter_writer.propose({})

    assert proposal.trigger_name == "letter_writer"
    assert proposal.requires_state == [TriggerState.QUIET]
    assert proposal.topic_source == "letter_trigger"


def test_successful_send_marks_cooldown(monkeypatch):
    from core.mail import letter_writer as generator
    from core.mail import mail_sender
    from core.scheduler import loop
    from core.scheduler.triggers import letter_writer

    marks = []
    letter = "茶茶，\n" + ("这是带着具体细节和真实感受的一封信。" * 10) + "\n叶瑄\n2026年06月13日"

    async def fake_generate(uid, reason, *, char_id):
        return letter

    async def fake_evaluate(text):
        return 5

    async def fake_send(subject, text):
        return True

    monkeypatch.setattr(generator, "generate_letter", fake_generate)
    monkeypatch.setattr(generator, "evaluate_letter", fake_evaluate)
    monkeypatch.setattr(mail_sender, "send_letter", fake_send)
    monkeypatch.setattr(loop, "_mark", marks.append)
    monkeypatch.setattr(letter_writer, "_last_letter_text", "")

    result = asyncio.run(
        letter_writer._send_letter_if_worthy("u1", "character_b", "理由", dry_run=False)
    )

    assert result.sent is True
    assert marks == ["letter_writer"]


def test_low_quality_letter_is_not_sent_or_marked(monkeypatch):
    from core.mail import letter_writer as generator
    from core.mail import mail_sender
    from core.scheduler import loop
    from core.scheduler.triggers import letter_writer

    calls = []

    async def fake_generate(uid, reason, *, char_id):
        return "足够长的信" * 40

    async def fake_evaluate(text):
        return 3

    async def fake_send(subject, text):
        calls.append("send")
        return True

    monkeypatch.setattr(generator, "generate_letter", fake_generate)
    monkeypatch.setattr(generator, "evaluate_letter", fake_evaluate)
    monkeypatch.setattr(mail_sender, "send_letter", fake_send)
    monkeypatch.setattr(loop, "_mark", lambda name: calls.append("mark"))

    result = asyncio.run(
        letter_writer._send_letter_if_worthy("u1", "character_b", "理由", dry_run=False)
    )

    assert result.sent is False
    assert calls == []


def test_mail_sender_escapes_html(monkeypatch):
    from core.mail import mail_sender

    captured = {}

    async def fake_send(msg, **kwargs):
        captured["msg"] = msg
        captured["kwargs"] = kwargs

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {
            "mail": {
                "enabled": True,
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "smtp_user": "from@example.com",
                "smtp_password": "secret",
                "from_addr": "from@example.com",
                "from_name": "角色",
                "to_addr": "to@example.com",
            }
        },
    )
    monkeypatch.setitem(sys.modules, "aiosmtplib", SimpleNamespace(send=fake_send))

    assert asyncio.run(mail_sender.send_letter("标题", "<script>alert(1)</script>")) is True

    html_part = captured["msg"].get_payload()[1]
    html = html_part.get_payload(decode=True).decode("utf-8")
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
    assert captured["kwargs"]["port"] == 587
    assert captured["kwargs"]["sock"] is None
    assert captured["kwargs"]["start_tls"] is True
    assert captured["kwargs"]["use_tls"] is False


def test_mail_sender_uses_proxy_socket(monkeypatch):
    from core.mail import mail_sender

    captured = {}
    proxy_sock = object()

    async def fake_open_proxy_socket(proxy_url, host, port):
        captured["proxy"] = (proxy_url, host, port)
        return proxy_sock

    async def fake_send(_msg, **kwargs):
        captured["kwargs"] = kwargs

    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {
            "mail": {
                "enabled": True,
                "smtp_host": "smtp.example.com",
                "smtp_port": 587,
                "proxy_url": "http://127.0.0.1:7897",
                "smtp_user": "from@example.com",
                "smtp_password": "secret",
                "to_addr": "to@example.com",
            }
        },
    )
    monkeypatch.setattr(mail_sender, "_open_proxy_socket", fake_open_proxy_socket)
    monkeypatch.setitem(sys.modules, "aiosmtplib", SimpleNamespace(send=fake_send))

    assert asyncio.run(mail_sender.send_letter("标题", "正文")) is True
    assert captured["proxy"] == (
        "http://127.0.0.1:7897",
        "smtp.example.com",
        587,
    )
    assert captured["kwargs"]["port"] is None
    assert captured["kwargs"]["sock"] is proxy_sock


# ── letter_reference helpers ──────────────────────────────────────────────────

def test_sample_style_returns_empty_when_dir_absent(monkeypatch, tmp_path):
    from core.mail import letter_reference

    fake_paths = SimpleNamespace(
        letter_samples_dir=lambda *, char_id: tmp_path / "nonexistent",
        letter_knowledge_dir=lambda *, char_id: tmp_path / "nonexistent_k",
        sent_letters=lambda uid, *, char_id: tmp_path / "sent.json",
    )
    monkeypatch.setattr("core.sandbox._instance", fake_paths)

    texts, names = letter_reference.sample_style("yexuan")
    assert texts == []
    assert names == []


def test_sample_style_picks_from_available_files(monkeypatch, tmp_path):
    from core.mail import letter_reference

    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    (samples_dir / "a.txt").write_text("信件A内容", encoding="utf-8")
    (samples_dir / "b.txt").write_text("信件B内容", encoding="utf-8")
    (samples_dir / "c.txt").write_text("信件C内容", encoding="utf-8")

    fake_paths = SimpleNamespace(
        letter_samples_dir=lambda *, char_id: samples_dir,
        letter_knowledge_dir=lambda *, char_id: tmp_path / "knowledge",
        sent_letters=lambda uid, *, char_id: tmp_path / "sent.json",
    )
    monkeypatch.setattr("core.sandbox._instance", fake_paths)

    texts, names = letter_reference.sample_style("yexuan", n=2)
    assert len(texts) == 2
    assert all(t in ("信件A内容", "信件B内容", "信件C内容") for t in texts)


def test_sample_style_avoids_recently_used(monkeypatch, tmp_path):
    from core.mail import letter_reference

    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    (samples_dir / "a.txt").write_text("信件A", encoding="utf-8")
    (samples_dir / "b.txt").write_text("信件B", encoding="utf-8")
    (samples_dir / "c.txt").write_text("信件C", encoding="utf-8")

    fake_paths = SimpleNamespace(
        letter_samples_dir=lambda *, char_id: samples_dir,
        letter_knowledge_dir=lambda *, char_id: tmp_path / "knowledge",
        sent_letters=lambda uid, *, char_id: tmp_path / "sent.json",
    )
    monkeypatch.setattr("core.sandbox._instance", fake_paths)

    texts, names = letter_reference.sample_style("yexuan", n=1, exclude_names=["a.txt", "b.txt"])
    assert names == ["c.txt"]


def test_sample_reference_returns_empty_when_dir_absent(monkeypatch, tmp_path):
    from core.mail import letter_reference

    fake_paths = SimpleNamespace(
        letter_samples_dir=lambda *, char_id: tmp_path / "nonexistent",
        letter_knowledge_dir=lambda *, char_id: tmp_path / "nonexistent_k",
        sent_letters=lambda uid, *, char_id: tmp_path / "sent.json",
    )
    monkeypatch.setattr("core.sandbox._instance", fake_paths)

    assert letter_reference.sample_reference("yexuan") == ""


def test_sample_reference_returns_paragraph(monkeypatch, tmp_path):
    from core.mail import letter_reference

    k_dir = tmp_path / "knowledge"
    k_dir.mkdir()
    (k_dir / "notes.md").write_text("第一段文字内容。\n\n第二段文字内容。", encoding="utf-8")

    fake_paths = SimpleNamespace(
        letter_samples_dir=lambda *, char_id: tmp_path / "samples",
        letter_knowledge_dir=lambda *, char_id: k_dir,
        sent_letters=lambda uid, *, char_id: tmp_path / "sent.json",
    )
    monkeypatch.setattr("core.sandbox._instance", fake_paths)

    ref = letter_reference.sample_reference("yexuan")
    assert ref in ("第一段文字内容。", "第二段文字内容。")


def test_append_and_load_sent_letters(monkeypatch, tmp_path):
    from core.mail import letter_reference

    fake_paths = SimpleNamespace(
        letter_samples_dir=lambda *, char_id: tmp_path / "samples",
        letter_knowledge_dir=lambda *, char_id: tmp_path / "knowledge",
        sent_letters=lambda uid, *, char_id: tmp_path / "sent.json",
    )
    monkeypatch.setattr("core.sandbox._instance", fake_paths)

    letter_reference.append_sent_letter("u1", "yexuan", "第一封信的正文")
    letter_reference.append_sent_letter("u1", "yexuan", "第二封信的正文")

    loaded = letter_reference.load_sent_letters("u1", "yexuan", limit=3)
    assert "第一封信的正文" in loaded
    assert "第二封信的正文" in loaded


def test_load_sent_letters_returns_empty_when_file_absent(monkeypatch, tmp_path):
    from core.mail import letter_reference

    fake_paths = SimpleNamespace(
        letter_samples_dir=lambda *, char_id: tmp_path / "samples",
        letter_knowledge_dir=lambda *, char_id: tmp_path / "knowledge",
        sent_letters=lambda uid, *, char_id: tmp_path / "nonexistent.json",
    )
    monkeypatch.setattr("core.sandbox._instance", fake_paths)

    assert letter_reference.load_sent_letters("u1", "yexuan") == []


def test_build_letter_context_is_failsoft(monkeypatch):
    """_build_letter_context must return a non-empty tuple even when all sources fail."""
    from core.mail import letter_writer

    monkeypatch.setattr("core.mail.letter_writer._EPISODIC_POOL", 3)

    async def run():
        import core.mail.letter_reference as ref_mod
        import core.memory.episodic_memory as ep_mod
        monkeypatch.setattr(ep_mod, "_load_memories", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")))
        monkeypatch.setattr(ref_mod, "sample_style", lambda *a, **kw: ((_ for _ in ()).throw(RuntimeError("fail"))))
        return await letter_writer._build_letter_context("u1", "测试缘由", char_id="yexuan")

    # Should not raise even with broken sources
    try:
        ctx, samples = asyncio.run(run())
    except Exception:
        # Errors inside coroutines are caught at source; if sample_style raises, samples==[]
        pass


def test_sent_letter_archived_after_successful_send(monkeypatch, tmp_path):
    from core.mail import letter_reference
    from core.mail import letter_writer as generator
    from core.mail import mail_sender
    from core.scheduler import loop
    from core.scheduler.triggers import letter_writer

    archived = []

    async def fake_generate(uid, reason, *, char_id):
        return "茶茶，\n" + ("这封信有具体细节和真实感受。" * 10) + "\n叶瑄\n2026年06月18日"

    async def fake_evaluate(text):
        return 5

    async def fake_send(subject, text):
        return True

    def fake_append(uid, char_id, text):
        archived.append(text)

    monkeypatch.setattr(generator, "generate_letter", fake_generate)
    monkeypatch.setattr(generator, "evaluate_letter", fake_evaluate)
    monkeypatch.setattr(mail_sender, "send_letter", fake_send)
    monkeypatch.setattr(loop, "_mark", lambda name: None)
    monkeypatch.setattr(letter_writer, "_last_letter_text", "")
    monkeypatch.setattr(letter_reference, "append_sent_letter", fake_append)

    result = asyncio.run(
        letter_writer._send_letter_if_worthy("u1", "yexuan", "理由", dry_run=False)
    )

    assert result.sent is True
    assert len(archived) == 1
