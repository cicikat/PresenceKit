"""Isolated admin check that proxy settings show and save model connection mode."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import time

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pytest import MonkeyPatch
import uvicorn

playwright_sync = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_sync.sync_playwright

PORT = 18772


def main() -> None:
    from admin.routers import settings_proxy as proxy_routes
    from core import config_loader, llm_client

    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="model-network-browser-") as temporary, MonkeyPatch.context() as patch:
        cfg_path = Path(temporary) / "config.yaml"
        cfg_path.write_text(
            "proxy:\n  enabled: false\n  http: http://127.0.0.1:7897\n  https: http://127.0.0.1:7897\n",
            encoding="utf-8",
        )
        state = {"config": yaml.safe_load(cfg_path.read_text(encoding="utf-8"))}

        def current_config():
            return state["config"]

        async def fake_reload():
            return None

        patch.setattr(proxy_routes, "CONFIG_FILE", cfg_path)
        patch.setattr(proxy_routes, "get_config", current_config)
        patch.setattr(config_loader, "get_config", current_config)
        patch.setattr(config_loader, "reload_config", lambda: state.update(config=yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}))
        patch.setattr(llm_client, "reload_client", fake_reload)

        app = FastAPI()
        app.include_router(proxy_routes.router)
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
                page = browser.new_page(viewport={"width": 1280, "height": 900})

                def intercept(route):
                    url = route.request.url
                    if "/static/" in url or url.rstrip("/").endswith("/proxy"):
                        return route.continue_()
                    if "/settings/relay" in url:
                        return route.fulfill(json={"relay_base_url": "", "relay_topic": "", "relay_token": ""})
                    if url.rstrip("/").endswith("/status"):
                        return route.fulfill(json={"status": "running", "data_mode": "test"})
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
                    const app = document.getElementById('app');
                    app.classList.remove('admin-inline-056');
                    app.style.display = 'flex';
                    await goto('network-config');
                    await loadProxy();
                }""")
                page.locator("#proxy-model-mode").wait_for(state="attached")
                page.wait_for_function("() => document.getElementById('page-network-config').classList.contains('active')")
                page.wait_for_function("() => document.getElementById('proxy-http').value.includes('127.0.0.1')")
                assert page.locator("#proxy-model-mode").input_value() == "follow_global"
                empty_save = page.evaluate("""async () => {
                    await loadProxy();
                    const select = document.getElementById('proxy-model-mode');
                    select.value = 'auto';
                    select.dispatchEvent(new Event('change', {bubbles: true}));
                    document.getElementById('proxy-http').value = '';
                    const hint = document.getElementById('proxy-model-hint').textContent || '';
                    await saveProxy();
                    return {
                        hint,
                        mode: select.value,
                        toastClass: document.getElementById('toast').className,
                        toastText: document.getElementById('toast').textContent || '',
                    };
                }""")
                assert "HTTP 代理地址" in empty_save["hint"], empty_save
                assert empty_save["mode"] == "auto", empty_save
                assert "err" in empty_save["toastClass"], empty_save
                assert "HTTP 代理地址" in empty_save["toastText"], empty_save
                ok_save = page.evaluate("""async () => {
                    document.getElementById('proxy-http').value = 'http://127.0.0.1:7897';
                    await saveProxy();
                    return {
                        toastClass: document.getElementById('toast').className,
                        toastText: document.getElementById('toast').textContent || '',
                    };
                }""")
                assert "ok" in ok_save["toastClass"], ok_save
                saved = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
                assert saved["proxy"]["model_connection_mode"] == "auto"
                page.evaluate("""async () => {
                    await goto('status');
                    await loadStatus();
                }""")
                page.locator("#s-proxy-model-mode").wait_for(state="attached")
                page.wait_for_function("() => (document.getElementById('s-proxy-model-mode').textContent || '').includes('自动')")
                assert page.locator('#page-status [data-action-args=\'["network-config"]\']').count() >= 1
                browser.close()
                print("Model network browser passed: auto mode requires proxy URL and saves.")
        finally:
            server.should_exit = True


def test_network_config_browser_saves_auto_mode():
    pytest.importorskip("playwright.sync_api")
    main()


if __name__ == "__main__":
    main()
