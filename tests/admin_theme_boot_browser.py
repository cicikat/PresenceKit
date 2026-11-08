"""Verify first rendered content stays dark while deferred theme JS is delayed."""
import asyncio
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    root = Path(__file__).resolve().parents[1]
    output = root / ".tmp/theme-first-paint-295"
    output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(root / "admin")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 850})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.add_init_script("if (!localStorage.getItem('presencekit_admin_theme')) localStorage.setItem('presencekit_admin_theme','night'); localStorage.setItem('presencekit_admin_night_palette','charcoal')")
        async def route(request):
            url = request.request.url
            if "/static/js/admin-theme.js?" in url:
                await asyncio.sleep(3)
                return await request.continue_()
            if "/static/" in url:
                return await request.continue_()
            await request.fulfill(status=401, content_type="application/json", body='{}')
        await page.route("**/*", route)
        await page.goto(f"http://127.0.0.1:{server.server_port}/static/index.html", wait_until="commit")
        await page.locator("#key-input").wait_for(state="visible")
        assert await page.evaluate("document.body.dataset.adminTheme") == "night"
        assert await page.evaluate("document.documentElement.dataset.adminTheme") == "night"
        assert await page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(20, 23, 27)"
        await page.screenshot(path=str(output / "before-deferred-theme.png"))
        await page.wait_for_load_state("load")
        assert await page.evaluate("document.body.dataset.adminTheme") == "night"
        await page.reload(wait_until="commit")
        await page.locator("#key-input").wait_for(state="visible")
        assert await page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgb(20, 23, 27)"
        await page.wait_for_load_state("load")
        # Exercise the actual control even while the sign-in overlay is visible.
        await page.evaluate("document.getElementById('admin-theme-toggle').click()")
        assert await page.evaluate("localStorage.getItem('presencekit_admin_theme')") == "day"
        await page.reload(wait_until="commit")
        await page.locator("#key-input").wait_for(state="visible")
        assert await page.evaluate("document.body.dataset.adminTheme") == "day"
        assert not errors, errors
        print("Theme first-paint and delayed-script reload checks passed; zero page errors.")
        await browser.close()
    server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
