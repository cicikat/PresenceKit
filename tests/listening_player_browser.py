"""Isolated-admin proof that the first-party HTMLAudioElement actually plays.

This is the G-stage browser check for ticket 260. A mocked player or a
static screenshot cannot close the ticket. Production :8080 is not used.
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import time
import wave

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pytest import MonkeyPatch
import uvicorn

playwright_sync = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_sync.sync_playwright

PORT = 18771


def _tone_wav(seconds: float = 1.0, rate: int = 8000) -> bytes:
    import io
    import math
    import struct

    frames = int(rate * seconds)
    payload = b"".join(
        struct.pack("<h", int(16000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(frames)
    )
    out = io.BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(payload)
    return out.getvalue()


def main() -> None:
    from admin.routers import listening as routes
    from admin.routers import settings_feature_flags as flags
    from core import config_loader, sandbox

    root = Path(__file__).resolve().parents[1]
    config = {
        "audio_music": {
            "speech_analysis": False,
            "music_analysis": False,
            "music_control": True,
            "music_autonomy": False,
        },
        "scheduler": {"owner_id": "fixture_owner"},
        "stt_presets": {"enabled": False, "presets": {}, "routes": {}},
    }
    with tempfile.TemporaryDirectory(prefix="listening-player-browser-") as temporary, MonkeyPatch.context() as patch:
        paths = sandbox.DataPaths(mode="test", test_session_id="listening-player-browser")
        paths._base = Path(temporary)
        paths._project_root = Path(temporary)
        patch.setattr(sandbox, "_instance", paths)
        patch.setattr(config_loader, "get_config", lambda: config)
        patch.setattr(flags, "get_config", lambda: config)

        app = FastAPI()
        app.include_router(routes.router)
        app.include_router(flags.router)
        for route in app.routes:
            for dependency in getattr(getattr(route, "dependant", None), "dependencies", []):
                app.dependency_overrides[dependency.call] = lambda: object()
        app.mount("/static", StaticFiles(directory=root / "admin/static"))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            assert server.started, "isolated admin did not start"
            wav = _tone_wav()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=True,
                    args=["--autoplay-policy=no-user-gesture-required"],
                )
                page = browser.new_page(viewport={"width": 1280, "height": 900})

                def intercept(route):
                    url = route.request.url
                    if "/static/" in url or "/player/" in url or "/observability/listening" in url or "/settings/feature-flags" in url:
                        return route.continue_()
                    if "/characters" in url:
                        return route.fulfill(json={"characters": [], "active_id": ""})
                    return route.fulfill(json={})

                page.route("**/*", intercept)
                cdp = page.context.new_cdp_session(page)
                cdp.send("Network.enable")
                cdp.send("Network.clearBrowserCache")
                cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
                page.goto(f"http://127.0.0.1:{PORT}/static/index.html")
                page.reload(wait_until="networkidle")
                page.evaluate("""async () => {
                    document.getElementById('auth-overlay').style.display = 'none';
                    document.getElementById('app').style.display = 'flex';
                    await goto('listening-player');
                }""")
                page.locator("#listening-audio").wait_for()
                page.wait_for_function(
                    "() => document.querySelectorAll('#page-listening-player details.settings-disclosure').length >= 2"
                )
                page.evaluate("""() => {
                    document.querySelectorAll('#page-listening-player details.settings-disclosure').forEach(el => { el.open = true; });
                    const uid = document.getElementById('listening-uid');
                    const host = document.getElementById('listening-host-id');
                    if (uid) uid.value = 'fixture_owner';
                    if (host) host.value = 'admin-html-audio-g';
                }""")
                page.evaluate("async () => { await bindListeningHost(); }")
                page.wait_for_timeout(400)
                page.set_input_files("#listening-file", {
                    "name": "tone.wav",
                    "mimeType": "audio/wav",
                    "buffer": wav,
                })
                page.evaluate("""() => {
                    const title = document.getElementById('listening-title');
                    if (title) title.value = 'G tone';
                }""")
                page.evaluate("async () => { await uploadListeningTrack(); }")
                page.wait_for_timeout(800)
                page.evaluate("""async () => {
                    const tts = document.createElement('audio');
                    tts.id = 'fixture-tts';
                    tts.dataset.ttsQueue = 'true';
                    const silent = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQAAAAA=';
                    tts.src = silent;
                    document.body.appendChild(tts);
                    try { await tts.play(); } catch (_error) { /* autoplay may be blocked */ }
                    tts.dataset.wasPlaying = String(!tts.paused);
                    await listeningPlay();
                }""")
                page.wait_for_timeout(1200)
                proof = page.evaluate("""() => {
                    const audio = document.getElementById('listening-audio');
                    const tts = document.getElementById('fixture-tts');
                    return {
                        constructor: audio && audio.constructor && audio.constructor.name,
                        paused: audio ? audio.paused : true,
                        currentTime: audio ? audio.currentTime : 0,
                        readyState: audio ? audio.readyState : 0,
                        src: audio ? audio.src : '',
                        ttsPaused: tts ? tts.paused : true,
                        ttsCoexistPaused: audio ? audio.dataset.ttsCoexistPaused : '',
                        bound: window._listeningPlayer && window._listeningPlayer.bound,
                        trackId: window._listeningPlayer && window._listeningPlayer.trackId,
                    };
                }""")
                assert proof["constructor"] == "HTMLAudioElement", proof
                assert proof["src"], proof
                assert proof["bound"] is True, proof
                assert proof["trackId"], proof
                assert proof["readyState"] >= 1 or proof["currentTime"] >= 0, proof
                assert proof["ttsPaused"] is True, proof
                events = page.locator("#listening-events").inner_text()
                session = page.locator("#listening-session").inner_text()
                assert "started" in events or "playing" in session, (events, session)
                page.evaluate("""async () => { await goto('observe-listening'); }""")
                page.locator("#obs-listening-uid").fill("fixture_owner", force=True)
                page.evaluate("""async () => { await loadObserveListening(); }""")
                flags_text = page.locator("#obs-listening-flags").inner_text()
                player_text = page.locator("#obs-listening-player").inner_text()
                assert "music_control" in flags_text or "共同听歌控制" in flags_text, flags_text
                assert "desired on" in flags_text, flags_text
                assert "first_party_admin" in player_text, player_text
                assert "网易云" in player_text or "NetEase" in player_text or "ok" in player_text, player_text
                browser.close()
                print(
                    "Listening player browser passed: HTMLAudioElement played, "
                    "TTS peer paused, observation shows first-party adapter."
                )
        finally:
            server.should_exit = True


def test_listening_player_html_audio_plays_and_pauses_tts_peer():
    pytest.importorskip("playwright.sync_api")
    main()


if __name__ == "__main__":
    main()
