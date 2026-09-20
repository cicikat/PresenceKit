"""Cache-cleared browser check for the Brief 258 admin control plane."""
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright
from pytest import MonkeyPatch
import uvicorn


def main():
    from admin.routers import memory_consolidation as routes
    from core import config_loader, sandbox
    from core.memory import consolidation_worker
    from core.memory.scope import MemoryScope

    root = Path(__file__).resolve().parents[1]
    config = {
        "memory_consolidation": {
            "enabled": False,
            "paused": False,
            "grant_revision": 1,
            "night_start_hour": 23,
            "night_end_hour": 7,
            "idle_seconds": 600,
            "max_global_workers": 1,
            "batch_size": 100,
            "max_input_chars": 24000,
            "max_tokens_per_call": 1200,
            "daily_call_budget": 8,
            "daily_token_budget": 9600,
            "daily_wall_seconds": 600,
            "per_scope_daily_calls": 4,
            "per_scope_daily_tokens": 4800,
            "call_timeout_seconds": 90,
            "retry_backoff_seconds": 900,
            "background_preset": "",
        }
    }
    with tempfile.TemporaryDirectory(prefix="memory-dossier-browser-") as temporary, MonkeyPatch.context() as patch:
        paths = sandbox.DataPaths(mode="test", test_session_id="memory-dossier-browser")
        paths._base = Path(temporary)
        paths._project_root = Path(temporary)
        patch.setattr(sandbox, "_instance", paths)
        patch.setattr(config_loader, "get_config", lambda: config)
        patch.setattr(routes, "_scope", lambda uid, char_id: MemoryScope.reality_scope(uid, char_id))

        app = FastAPI()
        app.include_router(routes.router)
        for route in app.routes:
            for dependency in getattr(getattr(route, "dependant", None), "dependencies", []):
                app.dependency_overrides[dependency.call] = lambda: object()
        app.mount("/static", StaticFiles(directory=root / "admin/static"))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18770, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            assert server.started
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1100})

                def intercept(route):
                    url = route.request.url
                    if "/static/" in url or "/observability/memory-consolidation" in url or "/observability/memory-history-reconciliation" in url or "/memory/dossiers" in url or "/memory/history-source-items" in url or "/memory-history-reconciliation/" in url:
                        return route.continue_()
                    if "/characters" in url:
                        return route.fulfill(json={"characters": [{"id": "fixture_character", "label": "Fixture Companion"}], "active_id": "fixture_character"})
                    return route.fulfill(json={})

                page.route("**/*", intercept)
                cdp = page.context.new_cdp_session(page)
                cdp.send("Network.enable")
                cdp.send("Network.clearBrowserCache")
                cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
                page.goto("http://127.0.0.1:18770/static/index.html")
                page.reload(wait_until="networkidle")
                page.evaluate("""async () => {
                    document.getElementById('auth-overlay').style.display = 'none';
                    document.getElementById('app').style.display = 'flex';
                    await goto('memory-consolidation');
                    const select = document.getElementById('memory-consolidation-char');
                    select.replaceChildren(new Option('Fixture Companion', 'fixture_character'));
                    document.getElementById('memory-consolidation-uid').value = 'fixture_owner';
                    await loadMemoryConsolidationStatus();
                    await loadMemoryHistoryReconciliation();
                    await searchMemoryHistorySourceItems();
                }""")
                status = page.locator("#memory-consolidation-status")
                assert "未生效" in status.inner_text(), status.inner_text()
                assert "剩余 8" in status.inner_text(), status.inner_text()
                assert page.locator("#memory-consolidation-start").input_value() == "23"
                assert page.locator("[data-action='controlMemoryConsolidation']").count() == 6
                history_status = page.locator("#memory-history-status").inner_text()
                assert "暂停" in history_status, history_status.encode("unicode_escape")
                assert page.locator("[data-action='createMemoryHistoryManifest']").count() == 1
                assert page.locator("[data-action='controlMemoryHistoryReconciliation']").count() == 3
                assert page.locator("[data-action='searchMemoryHistorySourceItems']").count() == 1
                items = page.locator("#memory-history-items").inner_text()
                assert "暂无源项" in items, items.encode("unicode_escape")
                page.evaluate("""async () => {
                    const select = document.getElementById('memory-consolidation-char');
                    select.replaceChildren(new Option('Fixture Companion', 'fixture_character'));
                    document.getElementById('memory-consolidation-uid').value = 'fixture_owner';
                    await searchMemoryConsolidationDossiers();
                }""")
                empty_text = page.locator("#memory-consolidation-dossiers").inner_text()
                assert empty_text == "暂无结果", empty_text.encode("unicode_escape")

                output = root / ".tmp"
                output.mkdir(exist_ok=True)
                page.screenshot(path=str(output / "memory-consolidation-desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(output / "memory-consolidation-mobile.png"), full_page=True)
                assert page.locator("#memory-consolidation-status").bounding_box()["width"] <= 390
                browser.close()
            print("PASS: cache-cleared status, budgets, controls, empty query, desktop/mobile layout.")
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == "__main__":
    main()
