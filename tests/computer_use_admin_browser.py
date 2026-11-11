"""Hard-refresh the Computer Use controls against an isolated admin instance."""
from pathlib import Path
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

import uvicorn
import yaml
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright
from pytest import MonkeyPatch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    from admin.routers import settings_llm
    from core import config_loader, llm_client

    with tempfile.TemporaryDirectory() as temporary, MonkeyPatch.context() as patch:
        config = Path(temporary) / "config.yaml"
        local = {"kind": "vision", "is_local": True, "api_protocol": "chat_completions",
                 "base_url": "http://127.0.0.1:8810/v1", "model": "fixture"}
        config.write_text(yaml.safe_dump({"image_presets": {"presets": {
            "local": local, "public": {**local, "base_url": "https://example.test/v1"},
        }}}), encoding="utf-8")

        def read_config():
            return yaml.safe_load(config.read_text(encoding="utf-8"))

        async def no_network_reload():
            return None

        patch.setattr(settings_llm, "CONFIG_FILE", config)
        patch.setattr(config_loader, "get_config", read_config)
        patch.setattr(config_loader, "reload_config", read_config)
        patch.setattr(llm_client, "reload_client", no_network_reload)
        app = FastAPI()
        app.include_router(settings_llm.router)
        for route in app.routes:
            for dep in getattr(getattr(route, "dependant", None), "dependencies", []):
                app.dependency_overrides[dep.call] = object
        app.mount("/static", StaticFiles(directory=ROOT / "admin/static"))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18831, log_level="error"))
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
                page = browser.new_page(viewport={"width": 1440, "height": 900})

                def route_request(route):
                    path = urlsplit(route.request.url).path
                    if path.startswith("/static/") or path.startswith("/image-presets"):
                        route.continue_()
                    else:
                        route.fulfill(json={})

                page.route("**/*", route_request)
                cdp = page.context.new_cdp_session(page)
                cdp.send("Network.enable")
                cdp.send("Network.clearBrowserCache")
                cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
                page.goto("http://127.0.0.1:18831/static/index.html")

                def load():
                    page.reload(wait_until="networkidle")
                    page.evaluate("""async () => {
                        document.getElementById('auth-overlay').style.display = 'none';
                        const app = document.getElementById('app');
                        app.classList.remove('admin-inline-056');
                        app.style.display = 'flex';
                        await goto('model-routing');
                        await loadImageConnections();
                        const select = document.getElementById('image-route-computer_use');
                        for (let card = select?.closest('.card'); card; card = card.parentElement.closest('.card')) {
                            if (card.dataset.pkCollapsed === 'true') card.querySelector(':scope > .pk-collapse, :scope > .pk-card-heading > .pk-collapse')?.click();
                        }
                    }""")
                    summary = page.locator("details:not([open]) > summary").filter(
                        has=page.get_by_text("图像用途", exact=False)
                    )
                    if summary.count() == 1:
                        summary.click()
                    else:
                        page.locator("details:not([open]):has(#image-route-computer_use) > summary").click()

                load()
                select = page.locator("#image-route-computer_use")
                select.wait_for(state="visible")
                assert select.locator("option").evaluate_all("items => items.map(item => item.value)") == ["", "local"]
                assert select.input_value() == ""
                select.select_option("local")
                page.evaluate("() => saveImageRoutes()")
                assert read_config()["image_presets"]["routes"]["computer_use"] == "local"
                load()
                assert select.input_value() == "local"
                select.select_option("")
                page.evaluate("() => saveImageRoutes()")
                assert read_config()["image_presets"]["routes"]["computer_use"] == ""
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)
    print("Computer Use admin route saved, restored after hard refresh, and cleared.")


if __name__ == "__main__":
    main()
