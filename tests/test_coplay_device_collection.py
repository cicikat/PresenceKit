"""Remote collection cannot turn missing observations into game exits or OS reads."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.coplay import device_collection as service, observer, session, watcher
from tests.fixtures.public_assets import TEST_CHAR_ID

UID = "coplay_device_owner"
CLIENT = "a" * 32


@pytest.fixture
def remote(sandbox, monkeypatch):
    from core import config_loader, pipeline_registry
    cfg = config_loader.get_config()
    monkeypatch.setitem(cfg, "deployment", {"mode": "remote_server"})
    monkeypatch.setitem(cfg, "client_channels", {"desktop": True, "mobile": False})
    monkeypatch.setitem(cfg, "coplay", {"enabled": True, "poll_interval": 0, "screenshot_interval": 0,
        "game_whitelist": [{"name": "Fixture Game", "process_name": "fixture.exe"}]})
    monkeypatch.setitem(cfg, "scheduler", {"owner_id": UID})
    monkeypatch.setitem(cfg, "screen_observation", {"enabled": True})
    monkeypatch.setattr(pipeline_registry, "_pipeline", SimpleNamespace(_active_character_id=TEST_CHAR_ID))
    service._rows.clear()
    session.arm(UID, char_id=TEST_CHAR_ID)
    yield cfg
    service.revoke(UID, TEST_CHAR_ID)
    service._rows.clear()


def lease():
    return service.poll(UID, TEST_CHAR_ID, "identity", CLIENT, True, True)


def report(request, **overrides):
    return {"client_id": CLIENT, "lease_id": request["lease_id"],
            "char_id": TEST_CHAR_ID, "revision": request["revision"],
            "captured_at": request["server_time"], "status": "ok",
            "game_id": "fixture", "game_name": "untrusted name", "foreground": True,
            "save_changed": False, "image_base64": "", **overrides}


def accept(body):
    return service.accept(UID, TEST_CHAR_ID, "identity", body)


@pytest.mark.asyncio
async def test_remote_never_samples_backend_os(remote, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("remote deployment attempted backend OS collection")
    monkeypatch.setattr(watcher, "_read_steam_running_appid", forbidden)
    monkeypatch.setattr(watcher, "_scan_whitelist_processes", forbidden)
    monkeypatch.setattr(watcher, "_maybe_launch_pet", forbidden)
    monkeypatch.setattr(observer, "capture_screen", forbidden)
    monkeypatch.setattr(observer, "_resolve_save_dir", forbidden)
    await watcher.tick(UID, char_id=TEST_CHAR_ID)
    await observer.tick(UID, char_id=TEST_CHAR_ID, game_id="fixture")
    assert watcher.detect_running_game() is None


def test_single_use_reports_drive_only_fresh_explicit_game_changes(remote):
    request = lease()
    body = report(request)
    assert accept(body)["accepted"]
    state = session.read_state(UID, char_id=TEST_CHAR_ID)
    assert state["status"] == "active"
    assert state["game_name"] == "Fixture Game"
    assert not accept(body)["accepted"]
    assert accept(report(lease(), status="locked", game_id=""))["accepted"]
    assert session.read_state(UID, char_id=TEST_CHAR_ID)["status"] == "active"
    assert accept(report(lease(), game_id=""))["accepted"]
    assert session.read_state(UID, char_id=TEST_CHAR_ID)["status"] == "closing"


@pytest.mark.parametrize("change", ["identity", "client", "character", "revision", "expired", "future", "old", "disarm", "config"])
def test_stale_or_foreign_reports_are_rejected(remote, monkeypatch, change):
    request = lease()
    body = report(request)
    identity = "identity"
    if change == "identity": identity = "other"
    if change == "client": body["client_id"] = "b" * 32
    if change == "character": body["char_id"] = "other"
    if change == "revision": body["revision"] = "0" * 64
    if change == "expired": service._rows[(UID, TEST_CHAR_ID)]["deadline"] = 0
    if change == "future": body["captured_at"] += 100
    if change == "old": body["captured_at"] -= 100
    if change == "disarm": session.disarm(UID, char_id=TEST_CHAR_ID)
    if change == "config": remote["coplay"]["game_whitelist"] = []
    assert not service.accept(UID, TEST_CHAR_ID, identity, body)["accepted"]
    assert session.read_state(UID, char_id=TEST_CHAR_ID)["status"] != "active"


def test_offline_and_revoked_capture_leave_session_active(remote):
    assert accept(report(lease()))["accepted"]
    result = service.poll(UID, TEST_CHAR_ID, "identity", CLIENT, False, False)
    assert result["enabled"] is False
    row = service._rows[(UID, TEST_CHAR_ID)]
    row["seen"] -= 60
    assert not service.projection(UID, TEST_CHAR_ID)["fresh"]
    assert session.read_state(UID, char_id=TEST_CHAR_ID)["status"] == "active"


def test_frames_require_foreground_authority_and_valid_bounded_image(remote):
    assert not accept(report(lease(), foreground=False, image_base64="aA=="))["accepted"]
    assert not accept(report(lease(), image_base64="aA=="))["accepted"]
    assert not accept(report(lease(), game_id="unconfigured"))["accepted"]
    request = service.poll(UID, TEST_CHAR_ID, "identity", CLIENT, True, False)
    assert not accept(report(request, image_base64="aA=="))["accepted"]


@pytest.mark.asyncio
async def test_late_analysis_cannot_cross_revocation_or_character(remote, monkeypatch):
    import asyncio
    import base64
    from io import BytesIO
    from PIL import Image
    frame = BytesIO()
    Image.new("RGB", (8, 8)).save(frame, format="JPEG")
    entered = asyncio.Event()
    release = asyncio.Event()
    async def describe(*args, **kwargs):
        entered.set()
        await release.wait()
        return "observed game scene"
    monkeypatch.setattr(observer, "ocr_frame", lambda _: [])
    monkeypatch.setattr(observer, "classify_frame", lambda *args: "scene_change")
    monkeypatch.setattr(observer, "vlm_fallback_summary", describe)
    assert accept(report(lease(), image_base64=base64.b64encode(frame.getvalue()).decode()))["accepted"]
    await entered.wait()
    task = service._rows[(UID, TEST_CHAR_ID)]["analysis"]
    remote["screen_observation"]["enabled"] = False
    release.set()
    await task
    assert observer.peek_moments(service.moment_scope(UID, TEST_CHAR_ID)) == []


def test_device_endpoints_require_desktop_activity_and_sensor_scopes():
    from admin.routers.coplay import router
    for route in router.routes:
        if route.path.startswith("/coplay/device/"):
            required = [getattr(dep.call, "_required_scopes", ()) for dep in route.dependant.dependencies]
            assert ("activity", "sensor.write", "ws.desktop") in required


def test_poll_is_not_itself_a_fresh_report_and_cannot_bypass_sample_budget(remote):
    remote["coplay"]["screenshot_interval"] = 8
    request = lease()
    assert not service.projection(UID, TEST_CHAR_ID)["fresh"]
    assert service.projection(UID, TEST_CHAR_ID)["online"]
    assert accept(report(request))["accepted"]
    assert lease()["reason"] == "sample_cooldown"


def test_channel_revocation_invalidates_a_lease(remote):
    body = report(lease())
    remote["client_channels"]["desktop"] = False
    assert not accept(body)["accepted"]


def test_role_change_hides_old_observations_without_faking_a_game_exit(remote, monkeypatch):
    from core import pipeline_registry
    from core.coplay import commentator, game_state
    assert accept(report(lease()))["accepted"]
    key = service.moment_scope(UID, TEST_CHAR_ID)
    observer.push_moment(key, observer.GameMoment(kind="achievement", summary="previous game observation"))
    assert commentator._pick_moment(UID, TEST_CHAR_ID) is not None
    monkeypatch.setattr(pipeline_registry, "_pipeline", SimpleNamespace(_active_character_id="other"))
    assert not service.projection(UID, TEST_CHAR_ID)["fresh"]
    assert commentator._pick_moment(UID, TEST_CHAR_ID) is None
    assert game_state.build_coplay_context_text(UID, char_id=TEST_CHAR_ID) == ""
    assert session.read_state(UID, char_id=TEST_CHAR_ID)["status"] == "active"


def test_report_metadata_does_not_return_paths_images_or_process_names(remote):
    assert accept(report(lease()))["accepted"]
    fields = service.projection(UID, TEST_CHAR_ID)
    assert fields["fresh"]
    assert not {"image_base64", "save_dir", "process_name", "steam_library_paths", "client_id"} & fields.keys()
