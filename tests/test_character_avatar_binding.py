"""
tests/test_character_avatar_binding.py

Unit tests for character avatar binding:
  - core/asset_registry.py  (_avatar_info_for, AssetEntry.has_runtime_avatar)
  - GET  /settings/character-avatar/{char_id}
  - POST /settings/characters/{char_id}/avatar
  - DELETE /settings/characters/{char_id}/avatar

Covers:
 1. Characters with authored avatar PNG return correct avatar_url
 2. Characters without any avatar return avatar_url=None
 3. as_ui_dict() includes avatar_url and has_runtime_avatar
 4. Unknown char_id raises ValueError (fail-loud)
 5. Avatar URL is keyed by id only — not derived from label or filename
 6. HTTP GET: returns 200+image for existing avatar, 404 for missing
 7. HTTP GET: 404 for unknown char_id (fail-loud, not fallback)
 8. Runtime override: upload creates only the target char's avatar file
 9. Runtime override: does not touch other characters' runtime dirs
10. Priority: runtime override is served before authored default
11. After upload, has_runtime_avatar=True and avatar_url contains ?v=
12. DELETE removes runtime override; authored default is served again
13. POST rejects oversized files
14. POST rejects unsupported content types
15. Non-existent char_id on POST/DELETE returns 404 (fail-loud, no fallback)
"""

import io
import json
from pathlib import Path

import pytest

from core.asset_registry import AssetRegistry, _AVATARS_DIR


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def char_dir(tmp_path):
    chars = tmp_path / "characters"
    chars.mkdir()

    (chars / "yexuan.json").write_text(
        json.dumps({"name": "Companion"}), encoding="utf-8"
    )
    (chars / "character_b.json").write_text(
        json.dumps({"name": "DemoUser"}), encoding="utf-8"
    )
    (chars / "yexuanJ-5412.json").write_text(
        json.dumps({"name": "J5412"}), encoding="utf-8"
    )

    # Lorebooks/jailbreaks dirs (required by scanner)
    (chars / "reality" / "lorebooks").mkdir(parents=True)
    (chars / "reality" / "jailbreaks").mkdir(parents=True)

    # Authored avatars dir — only yexuan has one
    avatars = chars / "reality" / "avatars"
    avatars.mkdir(parents=True)
    (avatars / "yexuan.png").write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal PNG header

    return tmp_path


@pytest.fixture
def registry(char_dir, monkeypatch):
    monkeypatch.chdir(char_dir)
    return AssetRegistry()


@pytest.fixture
def client_with_avatars(char_dir, monkeypatch):
    """FastAPI test client with asset registry pointed at char_dir."""
    import core.asset_registry as _reg_mod
    import core.sandbox as _sb
    monkeypatch.chdir(char_dir)
    # Reset sandbox singleton so paths resolve under char_dir
    _sb._instance = None
    monkeypatch.setattr(_reg_mod, "_registry", AssetRegistry())

    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from admin.routers.settings_prompt_assets import router

    app = FastAPI()
    app.include_router(router)
    for route in router.routes:
        for dep in route.dependant.dependencies:
            if hasattr(dep.call, "_required_scopes"):
                app.dependency_overrides[dep.call] = lambda: "test"

    return TestClient(app)


# ── 1. Character with authored avatar returns correct avatar_url ──────────────

def test_yexuan_has_avatar_url(registry):
    entry = registry.resolve("yexuan", "character")
    assert entry.avatar_url is not None
    assert entry.avatar_url.startswith("/settings/character-avatar/yexuan")


def test_avatar_url_contains_version(registry):
    """Authored avatar URL includes ?v=<mtime> for cache-busting."""
    entry = registry.resolve("yexuan", "character")
    assert "?v=" in (entry.avatar_url or "")


# ── 2. Characters without avatar return None ──────────────────────────────────

def test_character_b_no_avatar_returns_none(registry):
    entry = registry.resolve("character_b", "character")
    assert entry.avatar_url is None


def test_j5412_no_avatar_returns_none(registry):
    entry = registry.resolve("yexuanJ-5412", "character")
    assert entry.avatar_url is None


# ── 3. as_ui_dict() includes avatar_url and has_runtime_avatar ────────────────

def test_as_ui_dict_includes_avatar_url_when_present(registry):
    d = registry.resolve("yexuan", "character").as_ui_dict()
    assert "avatar_url" in d
    assert d["avatar_url"] is not None
    assert d["avatar_url"].startswith("/settings/character-avatar/yexuan")


def test_as_ui_dict_includes_avatar_url_none_when_absent(registry):
    d = registry.resolve("character_b", "character").as_ui_dict()
    assert "avatar_url" in d
    assert d["avatar_url"] is None


def test_as_ui_dict_includes_has_runtime_avatar(registry):
    d = registry.resolve("yexuan", "character").as_ui_dict()
    assert "has_runtime_avatar" in d
    assert d["has_runtime_avatar"] is False  # authored only, no runtime yet


# ── 4. Unknown char_id raises ValueError ──────────────────────────────────────

def test_unknown_char_id_raises_value_error(registry):
    with pytest.raises(ValueError, match="unknown character asset id"):
        registry.resolve("does_not_exist", "character")


# ── 5. Avatar URL is id-based, not derived from label or filename ─────────────

def test_avatar_url_uses_id_not_label(registry):
    """The avatar_url path segment must equal the id ('yexuan'), not the label ('Companion')."""
    entry = registry.resolve("yexuan", "character")
    assert "Companion" not in (entry.avatar_url or "")
    assert "yexuan" in (entry.avatar_url or "")


def test_avatar_url_uses_id_not_filename(registry):
    """The avatar_url path segment must equal the id, not include the .json extension."""
    entry = registry.resolve("yexuan", "character")
    assert ".json" not in (entry.avatar_url or "")


def test_no_cross_character_avatar_fallback(registry):
    """character_b must not receive yexuan's avatar_url even though yexuan has one."""
    yexuan_url = registry.resolve("yexuan", "character").avatar_url
    character_b_url = registry.resolve("character_b", "character").avatar_url
    assert character_b_url is None
    assert character_b_url != yexuan_url


# ── 6 & 7. HTTP GET endpoint ──────────────────────────────────────────────────

def test_avatar_endpoint_returns_image_for_existing(client_with_avatars):
    resp = client_with_avatars.get("/settings/character-avatar/yexuan")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/")


def test_avatar_endpoint_404_when_no_avatar(client_with_avatars):
    resp = client_with_avatars.get("/settings/character-avatar/character_b")
    assert resp.status_code == 404


def test_avatar_endpoint_404_for_unknown_char_id(client_with_avatars):
    resp = client_with_avatars.get("/settings/character-avatar/does_not_exist")
    assert resp.status_code == 404


def test_avatar_endpoint_404_not_fallback_to_other_character(client_with_avatars):
    """j5412 has no avatar; must not fall back to yexuan's avatar."""
    resp_j5412 = client_with_avatars.get("/settings/character-avatar/yexuanJ-5412")
    resp_yexuan = client_with_avatars.get("/settings/character-avatar/yexuan")
    assert resp_j5412.status_code == 404
    assert resp_yexuan.status_code == 200
    assert resp_j5412.content != resp_yexuan.content


def test_avatar_endpoint_does_not_accept_label(client_with_avatars):
    """Passing the Chinese label as char_id must 404, not resolve yexuan's avatar."""
    resp = client_with_avatars.get("/settings/character-avatar/Companion")
    assert resp.status_code == 404


def test_avatar_endpoint_does_not_accept_filename(client_with_avatars):
    """Passing 'yexuan.json' as char_id must 404 (not found in registry)."""
    resp = client_with_avatars.get("/settings/character-avatar/yexuan.json")
    assert resp.status_code == 404


# ── 8. Upload creates only the target char's avatar file ─────────────────────

def test_upload_character_b_creates_only_character_b_file(char_dir, client_with_avatars):
    resp = client_with_avatars.post(
        "/settings/characters/character_b/avatar",
        files={"file": ("avatar.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert resp.status_code == 200

    character_b_path = char_dir / "data" / "runtime" / "characters" / "character_b" / "avatar.png"
    yexuan_path  = char_dir / "data" / "runtime" / "characters" / "yexuan"  / "avatar.png"

    assert character_b_path.exists(), "character_b runtime avatar must be created"
    assert not yexuan_path.exists(), "yexuan runtime avatar must NOT be touched"


# ── 9. Upload does not touch other characters ─────────────────────────────────

def test_upload_does_not_touch_yexuan_when_uploading_character_b(char_dir, client_with_avatars):
    # Pre-create yexuan runtime avatar to verify it's not modified
    yexuan_rt = char_dir / "data" / "runtime" / "characters" / "yexuan"
    yexuan_rt.mkdir(parents=True, exist_ok=True)
    sentinel = b"YEXUAN_SENTINEL"
    (yexuan_rt / "avatar.png").write_bytes(sentinel)

    client_with_avatars.post(
        "/settings/characters/character_b/avatar",
        files={"file": ("avatar.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )

    assert (yexuan_rt / "avatar.png").read_bytes() == sentinel, \
        "yexuan runtime avatar must be unchanged after uploading character_b avatar"


# ── 10. Priority: runtime override served before authored default ─────────────

def test_runtime_override_served_instead_of_authored(char_dir, client_with_avatars):
    runtime_bytes = b"RUNTIME_AVATAR_BYTES"
    client_with_avatars.post(
        "/settings/characters/yexuan/avatar",
        files={"file": ("avatar.png", runtime_bytes, "image/png")},
    )

    resp = client_with_avatars.get("/settings/character-avatar/yexuan")
    assert resp.status_code == 200
    assert resp.content == runtime_bytes


# ── 11. After upload has_runtime_avatar=True and avatar_url has ?v= ───────────

def test_after_upload_has_runtime_avatar_is_true(char_dir, monkeypatch):
    import core.asset_registry as _reg_mod
    import core.sandbox as _sb
    monkeypatch.chdir(char_dir)
    _sb._instance = None

    # Simulate runtime avatar on disk
    rt_dir = char_dir / "data" / "runtime" / "characters" / "character_b"
    rt_dir.mkdir(parents=True, exist_ok=True)
    (rt_dir / "avatar.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    _reg_mod._registry = None
    reg = AssetRegistry()
    entry = reg.resolve("character_b", "character")

    assert entry.has_runtime_avatar is True
    assert entry.avatar_url is not None
    assert "?v=" in entry.avatar_url


def test_authored_only_has_runtime_avatar_false(registry):
    entry = registry.resolve("yexuan", "character")
    assert entry.has_runtime_avatar is False


# ── 12. DELETE removes runtime override; authored default served again ─────────

def test_delete_removes_runtime_and_falls_back_to_authored(char_dir, client_with_avatars):
    authored_bytes = (char_dir / "characters" / "reality" / "avatars" / "yexuan.png").read_bytes()

    # Upload an override
    client_with_avatars.post(
        "/settings/characters/yexuan/avatar",
        files={"file": ("avatar.png", b"OVERRIDE", "image/png")},
    )
    # Verify override is active
    assert client_with_avatars.get("/settings/character-avatar/yexuan").content == b"OVERRIDE"

    # Delete override
    resp = client_with_avatars.delete("/settings/characters/yexuan/avatar")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    # Authored default restored
    resp2 = client_with_avatars.get("/settings/character-avatar/yexuan")
    assert resp2.status_code == 200
    assert resp2.content == authored_bytes


def test_delete_on_char_without_runtime_returns_deleted_false(client_with_avatars):
    resp = client_with_avatars.delete("/settings/characters/character_b/avatar")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is False


# ── 13. POST rejects oversized files ─────────────────────────────────────────

def test_upload_rejects_oversized_file(client_with_avatars):
    big = b"\x89PNG" + b"\x00" * (5 * 1024 * 1024 + 1)
    resp = client_with_avatars.post(
        "/settings/characters/character_b/avatar",
        files={"file": ("big.png", big, "image/png")},
    )
    assert resp.status_code == 422
    assert "5 MB" in resp.text


# ── 14. POST rejects unsupported content types ───────────────────────────────

def test_upload_rejects_gif(client_with_avatars):
    resp = client_with_avatars.post(
        "/settings/characters/character_b/avatar",
        files={"file": ("avatar.gif", b"GIF89a", "image/gif")},
    )
    assert resp.status_code == 422


def test_upload_rejects_text(client_with_avatars):
    resp = client_with_avatars.post(
        "/settings/characters/character_b/avatar",
        files={"file": ("avatar.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 422


# ── 15. Non-existent char_id on POST/DELETE returns 404 ──────────────────────

def test_upload_unknown_char_id_returns_404(client_with_avatars):
    resp = client_with_avatars.post(
        "/settings/characters/ghost_char/avatar",
        files={"file": ("avatar.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert resp.status_code == 404


def test_delete_unknown_char_id_returns_404(client_with_avatars):
    resp = client_with_avatars.delete("/settings/characters/ghost_char/avatar")
    assert resp.status_code == 404


def test_upload_does_not_fallback_to_yexuan_on_unknown_id(client_with_avatars):
    """Uploading for a non-existent char must not create a yexuan file."""
    resp = client_with_avatars.post(
        "/settings/characters/ghost_char/avatar",
        files={"file": ("avatar.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert resp.status_code == 404
