"""Real login code: stale auto-login, slow bootstrap, scoped tokens and HTTP errors."""
import asyncio
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    root = Path(__file__).resolve().parents[1]
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(root / "admin")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.add_init_script("localStorage.setItem('qq_admin_key','stale-fixture')")
        async def route(request):
            url = request.request.url
            if "/static/" in url:
                return await request.continue_()
            token = request.request.headers.get("authorization", "").removeprefix("Bearer ")
            if url.endswith("/auth/whoami"):
                if token == "stale-fixture":
                    await asyncio.sleep(2)
                    return await request.fulfill(status=401, content_type="application/json", body='{}')
                if token == "bad-server":
                    return await request.fulfill(status=503, content_type="application/json", body='{}')
                if token == "bad-key":
                    return await request.fulfill(status=401, content_type="application/json", body='{}')
                if token == "scoped":
                    return await request.fulfill(content_type="application/json", body='{"scopes":["state.read"]}')
                return await request.fulfill(content_type="application/json", body='{"scopes":["admin"]}')
            if url.endswith("/settings/prompt-language"):
                await asyncio.sleep(15)
                return await request.fulfill(content_type="application/json", body='{"effective_language":"zh"}')
            return await request.fulfill(content_type="application/json", body='{}')
        await page.route("**/*", route)
        await page.goto(f"http://127.0.0.1:{server.server_port}/static/index.html")
        await page.evaluate("""() => {
          goto = async () => {}; restoreNavGroups = () => {};
          getRememberedPage = () => 'setup'; _initCharName = async () => {}; initSecretsBookFab = async () => {};
        }""")
        await page.locator("#key-input").fill("valid-fixture")
        await page.locator('[data-action="doLogin"]').click()
        await page.locator("#auth-overlay").wait_for(state="hidden", timeout=1500)
        await asyncio.sleep(2.1)
        assert await page.evaluate("TOKEN === 'valid-fixture' && localStorage.getItem('qq_admin_key') === 'valid-fixture'")
        assert not await page.locator("#auth-overlay").is_visible()
        await page.evaluate("logout()")
        assert not await page.evaluate("tryLogin('bad-server', false)")
        assert "HTTP 503" in await page.locator("#auth-err").inner_text()
        assert "密钥错误" not in await page.locator("#auth-err").inner_text()
        assert not await page.evaluate("tryLogin('scoped', false)")
        assert "没有管理权限" in await page.locator("#auth-err").inner_text()
        assert not await page.evaluate("tryLogin('bad-key', false)")
        assert "密钥错误" in await page.locator("#auth-err").inner_text()
        assert not errors, errors
        print("Login race, slow language bootstrap, admin scope and HTTP error classification passed.")
        await browser.close()
    server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
