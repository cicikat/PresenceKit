import pytest
from core import hds_local
from tests.test_prompt_ablation import _apply_build_stubs, _base_build_kwargs


@pytest.mark.parametrize("age,enabled,consent,uid,expected", [
    (0, True, True, "owner", True), (30, True, True, "owner", True),
    (30.1, True, True, "owner", False), (-1, True, True, "owner", False),
    (1, False, True, "owner", False), (1, True, False, "owner", False),
    (1, True, True, "other", False),
])
def test_only_fresh_consented_owner_samples(monkeypatch, age, enabled, consent, uid, expected):
    monkeypatch.setattr(hds_local, "get_config", lambda: {"scheduler": {"owner_id": "owner"},
        "hds_local": {"enabled": enabled, "character_read_enabled": consent}})
    monkeypatch.setattr(hds_local.time, "time", lambda: 1000000)
    monkeypatch.setattr(hds_local.health_state, "load", lambda _: {"hds_samples": [{"value": 75, "received_at": 1000000-age}]})
    assert bool(hds_local.prompt_hint(uid)) is expected


def test_no_sample_no_injection_and_builder_section(sandbox, monkeypatch):
    _apply_build_stubs(monkeypatch)
    monkeypatch.setattr(hds_local, "get_config", lambda: {"scheduler": {"owner_id": "owner"}, "hds_local": {"enabled": True}})
    monkeypatch.setattr(hds_local.health_state, "load", lambda _: {})
    assert hds_local.prompt_hint("owner") is None
    monkeypatch.setattr(hds_local.time, "time", lambda: 1000000)
    monkeypatch.setattr(hds_local.health_state, "load", lambda _: {"hds_samples": [{"value": 75, "received_at": 999995}]})
    from core.prompt_builder import build
    messages, _ = build(**_base_build_kwargs(user_id="owner"))
    text = "\n".join(m["content"] for m in messages)
    assert text.index("【真实数据】") < text.index("75次/分钟") < text.index("【/真实数据】")
