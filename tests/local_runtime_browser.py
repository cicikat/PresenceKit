"""Isolated-admin browser check for the 本地模型运行 page (video-call work order D2).

Uses the real router and static assets with a fake Whisper loader, so it proves
what the page *shows*: costs next to options, the effective state, a visible
fallback for ``auto`` and a visible failure (nothing saved) for an unsupported
explicit device.  Production :8080 and real models are not touched.
"""
from __future__ import annotations

from pathlib import Path
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pytest import MonkeyPatch
import uvicorn

playwright_sync = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_sync.sync_playwright

PORT = 18772


def main(screenshot_dir: str | None = None) -> None:
    from admin.routers import settings_local_runtime as routes
    from core import config_loader, stt_local

    root = Path(__file__).resolve().parents[1]
    store = {"cfg": {}}
    with MonkeyPatch.context() as patch:
        stt_local.reset_for_tests()
        patch.setattr(stt_local, "_load", lambda size, device, compute: object())
        patch.setattr(stt_local, "_smoke", lambda model: None)
        patch.setattr(stt_local, "_cuda_device_count", lambda: 0)
        patch.setattr(routes, "get_config", lambda: store["cfg"])
        patch.setattr(routes, "read_config_file", lambda _path: dict(store["cfg"]))
        patch.setattr(routes, "write_config_file", lambda _path, data: store.update(cfg=dict(data)))
        patch.setattr(config_loader, "reload_config", lambda: store["cfg"])

        app = FastAPI()
        app.include_router(routes.router)
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
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 1000})

                def intercept(route):
                    url = route.request.url
                    if "/static/" in url or "/settings/local-runtime" in url:
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
                    await goto('local-model-runtime');
                }""")
                page.locator("#local-runtime-model-size option").first.wait_for(state="attached")

                assert "本地模型运行" in page.locator("#page-local-model-runtime .page-title").inner_text()
                sizes = page.locator("#local-runtime-model-size option").all_inner_texts()
                assert len(sizes) == 5 and all("—" in text for text in sizes), sizes  # each option states its cost
                assert any("MB" in text or "GB" in text for text in sizes), sizes
                assert page.locator("#local-runtime-model-size").input_value() == "small"
                assert "尚未加载" in page.locator("#local-runtime-effective-body").inner_text()
                assert "CUDA" in page.locator("#local-runtime-hardware-body").inner_text()
                if screenshot_dir:
                    page.screenshot(path=f"{screenshot_dir}/local-runtime-1-initial.png", full_page=True)

                # An explicit device this machine cannot run must fail visibly and save nothing.
                page.select_option("#local-runtime-device", "cuda")
                page.click("#local-runtime-save")
                page.wait_for_function(
                    "() => document.getElementById('local-runtime-result').dataset.state === 'err'")
                failure = page.locator("#local-runtime-result").inner_text()
                assert "切换失败，未保存" in failure and "CUDA" in failure, failure
                assert "stt_local" not in store["cfg"], store["cfg"]
                if screenshot_dir:
                    page.screenshot(path=f"{screenshot_dir}/local-runtime-2-failure.png", full_page=True)

                # auto on a machine without a usable GPU saves, but the fallback is shown, not hidden.
                page.select_option("#local-runtime-device", "auto")
                page.click("#local-runtime-save")
                page.wait_for_function(
                    "() => document.getElementById('local-runtime-result').dataset.state === 'ok'")
                effective = page.locator("#local-runtime-effective-body").inner_text()
                assert "已回落到 CPU" in effective and "CUDA" in effective, effective
                assert page.locator("#local-runtime-stt-status").inner_text() == "已回落"
                assert store["cfg"]["stt_local"]["device"] == "auto"
                if screenshot_dir:
                    page.screenshot(path=f"{screenshot_dir}/local-runtime-3-fallback.png", full_page=True)
                browser.close()
                print("Local runtime browser passed: costs shown, failure visible and unsaved, auto fallback visible.")
        finally:
            server.should_exit = True
            stt_local.reset_for_tests()


def test_local_runtime_page_shows_effective_state_and_real_failures():
    pytest.importorskip("playwright.sync_api")
    main()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
