"""Real Chromium/production admin assets; synthetic API data, no production writes."""
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / ".tmp/image-generation-294"
    output.mkdir(parents=True, exist_ok=True)
    handler = partial(SimpleHTTPRequestHandler, directory=str(root / "admin"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    catalog = {"enabled": False, "default_preset": "", "presets": {}}
    binding = {"image_gen": None}
    writes = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        def route(request):
            from urllib.parse import urlsplit
            url = urlsplit(request.request.url).path
            if url.startswith("/static/"):
                return request.continue_()
            method = request.request.method
            body = request.request.post_data_json if request.request.post_data else {}
            result = {}
            if method != "GET":
                writes.append((method, url, body))
            if url == "/image-gen-presets":
                result = catalog
            elif url == "/characters":
                result = {"characters": [{"id": "fixture", "label": "Fixture"}], "active_id": "fixture"}
            elif url == "/character/fixture/asset-bindings":
                if method == "PATCH":
                    binding.update(body)
                result = binding
            elif url == "/image-gen-presets/discover":
                result = {"status": "ok", "models": ["fixture-image-model", "other-model"]}
            elif url.endswith("/test"):
                result = {"ok": True, "duration_ms": 12, "bytes": 100, "tested_model":"cheap-fixture", "same_model":False}
            elif url == "/image-gen-presets/prompt-constraints":
                catalog["prompt_constraints"] = body
                result = body
            elif url == "/image-gen-presets/default-preset":
                catalog.update(body)
            elif url == "/image-gen-presets/enabled":
                catalog.update(body)
            elif url.startswith("/image-gen-presets/presets/"):
                name = url.rsplit("/", 1)[-1]
                catalog["presets"][name] = {**body, "has_api_key": bool(body.get("api_key")), "ready": True}
                catalog["presets"][name].pop("api_key", None)
            return request.fulfill(status=200, content_type="application/json", body=json.dumps(result))

        page.route("**/*", route)
        page.goto(f"http://127.0.0.1:{server.server_port}/static/index.html")
        cdp = page.context.new_cdp_session(page)
        cdp.send("Network.clearBrowserCache")
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
        page.reload()
        page.evaluate("""async () => {
          document.getElementById('auth-overlay').style.display='none';
          document.getElementById('app').style.display='flex';
          AdminUI.select('new');
          await loadPageFragment('model-routing');
          await AdminPageLayout.prepare('model-routing');
          document.querySelectorAll('.page').forEach(el=>el.classList.remove('active'));
          document.getElementById('page-model-routing').classList.add('active');
          await loadImageGeneration();
        }""")
        assert page.locator('[data-layout-key="model-generation"] #image-generation-card').count() == 1
        page.locator('[data-action="editImageGeneration"]').click()
        assert page.locator("#ig-name").is_enabled()
        page.locator("#ig-name").fill("fixture")
        page.locator("#ig-base_url").fill("https://fixture.invalid/v1")
        page.locator("#ig-api_key").fill("fixture-key")
        page.locator('[data-action="discoverImageGeneration"]').click()
        page.wait_for_function("document.querySelectorAll('#ig-models option').length === 2")
        assert page.locator("#ig-model-results").is_visible()
        page.locator("#ig-model-results").select_option("fixture-image-model")
        page.locator("#ig-model").fill("fixture-image-model")
        page.locator("#ig-description").fill("Illustrations")
        page.locator('[data-action="saveImageGeneration"]').click()
        page.wait_for_selector("#ig-list tr")
        assert "fixture-key" not in page.locator("#ig-list").inner_text()
        page.locator("#ig-enabled").check()
        page.locator("#ig-default").select_option("fixture")
        page.locator('[data-action="saveImageGenerationRouting"]').click()
        page.wait_for_function("document.getElementById('ig-status').textContent.includes('已保存')")
        page.locator("#ig-inherit").uncheck()
        page.locator('#ig-allowlist input[value="fixture"]').check()
        page.locator('[data-action="saveImageGenerationCharacter"]').click()
        page.locator("#ig-constraint").select_option("__custom__")
        page.locator("#ig-constraint-text").fill("semi-realistic painterly illustration, expressive brushwork")
        page.locator("#ig-constraint-name").fill("Painterly")
        page.locator('[data-action="saveImageConstraints"]').click()
        page.wait_for_function("document.getElementById('ig-constraint').value === 'Painterly'")
        assert catalog["prompt_constraints"]["selected"] == "Painterly"
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("#ig-list button").nth(1).click()
        page.wait_for_function("document.getElementById('ig-status').textContent.includes('连接测试成功')")
        page.locator('[data-layout-key="model-generation"]').screenshot(path=str(output / "admin.png"))
        page.locator("#ig-list button").nth(0).click()
        assert not page.locator("#ig-model-results").is_visible()
        assert catalog["enabled"] and catalog["default_preset"] == "fixture"
        assert binding["image_gen"] == ["fixture"]
        assert not errors, errors
        print(json.dumps({"browser": "passed", "writes": len(writes), "page_errors": errors}))
        browser.close()
    server.shutdown()


if __name__ == "__main__":
    main()
