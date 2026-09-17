"""Dream settings are per-character; live active / default dirs are not shared authority."""
from __future__ import annotations

import json

import pytest

from tests.fixtures.public_assets import TEST_CHAR_ID, TEST_PEER_CHAR_ID, TEST_THIRD_CHAR_ID


_UID = "dream-settings-owner"


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_peer_character_does_not_see_owned_settings(sandbox):
    from core.dream.dream_settings import load, save

    save(_UID, {"world_layer": "abo", "lucid_mode": "non_lucid"}, char_id=TEST_CHAR_ID)
    owned = load(_UID, char_id=TEST_CHAR_ID)
    peer = load(_UID, char_id=TEST_PEER_CHAR_ID)
    assert owned["world_layer"] == "abo"
    assert owned["lucid_mode"] == "non_lucid"
    assert peer["world_layer"] == "reality_derived"
    assert peer["lucid_mode"] == "lucid_shared"
    assert not sandbox.dream_settings_path(_UID, char_id=TEST_PEER_CHAR_ID).exists()


def test_mid_task_active_switch_does_not_rewrite_owned_settings_path(sandbox, monkeypatch):
    from core.dream.dream_settings import load, save
    from core.pipeline_registry import register

    class _Fake:
        _active_character_id = TEST_THIRD_CHAR_ID

    register(_Fake())
    save(_UID, {"memory_access": "card_only"}, char_id=TEST_CHAR_ID)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"character": {"default": TEST_PEER_CHAR_ID}},
    )
    loaded = load(_UID, char_id=TEST_CHAR_ID)
    assert loaded["memory_access"] == "card_only"
    assert sandbox.dream_settings_path(_UID, char_id=TEST_CHAR_ID).exists()
    assert not sandbox.dream_settings_path(_UID, char_id=TEST_THIRD_CHAR_ID).exists()
    assert not sandbox.dream_settings_path(_UID, char_id=TEST_PEER_CHAR_ID).exists()


def test_legacy_uid_only_readable_only_by_frozen_historical_default(sandbox, monkeypatch):
    from core.dream import dream_settings

    monkeypatch.setattr(dream_settings, "_configured_default_char_id", lambda: TEST_CHAR_ID)
    legacy = dream_settings._legacy_uid_only_path(_UID)
    _write_json(legacy, {"world_layer": "vampire", "lucid_mode": "non_lucid"})

    owned = dream_settings.load(_UID, char_id=TEST_CHAR_ID)
    peer = dream_settings.load(_UID, char_id=TEST_PEER_CHAR_ID)
    assert owned["world_layer"] == "vampire"
    assert peer["world_layer"] == "reality_derived"
    assert dream_settings.historical_legacy_dream_settings_char_id(_UID) == TEST_CHAR_ID
    assert dream_settings.may_read_legacy_dream_settings(_UID, TEST_CHAR_ID)
    assert not dream_settings.may_read_legacy_dream_settings(_UID, TEST_PEER_CHAR_ID)
    assert legacy.exists(), "legacy uid-only file must not be deleted"


def test_legacy_freeze_survives_later_default_and_active_switch(sandbox, monkeypatch):
    from core.dream import dream_settings

    monkeypatch.setattr(dream_settings, "_configured_default_char_id", lambda: TEST_CHAR_ID)
    _write_json(dream_settings._legacy_uid_only_path(_UID), {"world_layer": "cat"})
    assert dream_settings.load(_UID, char_id=TEST_CHAR_ID)["world_layer"] == "cat"

    monkeypatch.setattr(dream_settings, "_configured_default_char_id", lambda: TEST_PEER_CHAR_ID)
    assert dream_settings.historical_legacy_dream_settings_char_id(_UID) == TEST_CHAR_ID
    assert dream_settings.load(_UID, char_id=TEST_CHAR_ID)["world_layer"] == "cat"
    assert dream_settings.load(_UID, char_id=TEST_PEER_CHAR_ID)["world_layer"] == "reality_derived"
    assert not sandbox.dream_settings_path(_UID, char_id=TEST_PEER_CHAR_ID).exists()


def test_unreadable_canonical_returns_defaults_without_copying_legacy(sandbox, monkeypatch):
    from core.dream import dream_settings

    monkeypatch.setattr(dream_settings, "_configured_default_char_id", lambda: TEST_CHAR_ID)
    canonical = sandbox.dream_settings_path(_UID, char_id=TEST_CHAR_ID)
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text("{not-json", encoding="utf-8")
    _write_json(dream_settings._legacy_uid_only_path(_UID), {"world_layer": "abo"})

    loaded = dream_settings.load(_UID, char_id=TEST_CHAR_ID)
    assert loaded["world_layer"] == "reality_derived"
    assert canonical.read_text(encoding="utf-8") == "{not-json"
    assert dream_settings._legacy_uid_only_path(_UID).exists()


def test_http_settings_follow_active_character_not_peer_bucket(sandbox, monkeypatch):
    from admin.routers.dream import dream_settings_get, dream_settings_patch
    from core.dream.dream_settings import load, save
    from core.pipeline_registry import register

    class _Fake:
        def __init__(self, char_id):
            self._active_character_id = char_id

    save(_UID, {"world_layer": "abo"}, char_id=TEST_CHAR_ID)
    save(_UID, {"world_layer": "vampire"}, char_id=TEST_PEER_CHAR_ID)
    register(_Fake(TEST_CHAR_ID))
    monkeypatch.setattr("admin.routers.dream._owner_uid", lambda: _UID)

    got = __import__("asyncio").run(dream_settings_get())
    assert got["world_layer"] == "abo"

    register(_Fake(TEST_PEER_CHAR_ID))
    patched = __import__("asyncio").run(dream_settings_patch({"lucid_mode": "non_lucid"}))
    assert patched["ok"]
    assert load(_UID, char_id=TEST_PEER_CHAR_ID)["lucid_mode"] == "non_lucid"
    assert load(_UID, char_id=TEST_CHAR_ID)["lucid_mode"] == "lucid_shared"
    assert load(_UID, char_id=TEST_CHAR_ID)["world_layer"] == "abo"


def test_world_rename_does_not_rewrite_peer_character_settings(sandbox, monkeypatch):
    from admin.routers.dream import rename_dream_world
    from core.dream.dream_settings import load, save
    from core.pipeline_registry import register

    class _Fake:
        _active_character_id = TEST_CHAR_ID

    register(_Fake())
    monkeypatch.setattr("admin.routers.dream._owner_uid", lambda: _UID)
    save(_UID, {"world_layer": "rename_src"}, char_id=TEST_CHAR_ID)
    save(_UID, {"world_layer": "rename_src"}, char_id=TEST_PEER_CHAR_ID)
    # Seed the world folder directly. This test owns settings isolation, not
    # template seeding; HTTP create would require the tracked default package.
    src = sandbox.dream_worlds_dir() / "rename_src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "ruleset.md").write_text("rename isolation", encoding="utf-8")
    result = __import__("asyncio").run(rename_dream_world("rename_src", {"new_name": "rename_dst"}))
    assert result["world"] == "rename_dst"
    assert load(_UID, char_id=TEST_CHAR_ID)["world_layer"] == "rename_dst"
    assert load(_UID, char_id=TEST_PEER_CHAR_ID)["world_layer"] == "rename_src"


def test_observability_snapshot_omits_settings_body(sandbox):
    from core.dream.dream_settings import observability_snapshot, save

    save(_UID, {"world_layer": "abo", "jailbreak_presets": ["secret"]}, char_id=TEST_CHAR_ID)
    snap = observability_snapshot(_UID, char_id=TEST_CHAR_ID)
    encoded = json.dumps(snap)
    assert "abo" not in encoded
    assert "secret" not in encoded
    assert "jailbreak" not in encoded
    assert snap["char_id"] == TEST_CHAR_ID
    assert snap["canonical_exists"] is True
    assert snap["legacy_uid_only_exists"] is False


@pytest.mark.asyncio
async def test_observability_http_returns_effective_char(sandbox, monkeypatch):
    from admin.routers.observability import dream_settings_observability
    from core.dream.dream_settings import save
    from core.pipeline_registry import register

    class _Fake:
        _active_character_id = TEST_CHAR_ID

    register(_Fake())
    save(_UID, {"world_layer": "abo"}, char_id=TEST_CHAR_ID)
    monkeypatch.setattr(
        "core.config_loader.get_config",
        lambda: {"scheduler": {"owner_id": _UID}, "character": {"default": TEST_CHAR_ID}},
    )
    snap = await dream_settings_observability()
    assert snap["uid"] == _UID
    assert snap["char_id"] == TEST_CHAR_ID
    assert snap["canonical_exists"] is True
    assert "world_layer" not in snap
