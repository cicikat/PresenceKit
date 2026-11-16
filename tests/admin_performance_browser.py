"""Real admin assets: navigation and input latency with many mounted pages.

Synthetic API boundary; no production service, credentials or configuration writes.
Protects against global mutation observers making each edit scan every visited page.
"""
import asyncio
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import argparse

from playwright.async_api import async_playwright


async def main(cpu_slowdown=1):
    root = Path(__file__).resolve().parents[1]

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(root / "admin")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            errors = []
            requests = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append(request.url))

            async def route(request):
                if "/static/" in request.request.url:
                    return await request.continue_()
                return await request.fulfill(status=403, json={})

            await page.route("**/*", route)
            await page.goto(f"http://127.0.0.1:{server.server_port}/static/index.html")
            cold_shell = await page.evaluate("performance.getEntriesByType('navigation')[0].domContentLoadedEventEnd")
            cdp = await page.context.new_cdp_session(page)
            await cdp.send('Emulation.setCPUThrottlingRate', {'rate': cpu_slowdown})
            await page.evaluate("""async () => {
                document.getElementById('auth-overlay').style.display = 'none';
                document.getElementById('app').style.display = 'flex';
                await goto('guide');
                // Large but realistic accumulated DOM from many visited settings pages.
                const hidden = document.getElementById('page-observe-memory');
                hidden.innerHTML = '<div class="card"><h3>Fixture</h3><p>fixture status</p></div>'.repeat(1500);
                const input = document.createElement('input'); input.id = 'perf-input';
                const output = document.createElement('span'); output.id = 'perf-output';
                input.addEventListener('input', () => output.textContent = input.value);
                document.getElementById('page-guide').append(input, output);
            }""")
            await page.wait_for_timeout(500)
            samples = []
            for i in range(12):
                samples.append(await page.evaluate("""value => new Promise(resolve => {
                    const start = performance.now();
                    const input = document.getElementById('perf-input');
                    input.value = value; input.dispatchEvent(new Event('input', {bubbles:true}));
                    requestAnimationFrame(() => requestAnimationFrame(() => resolve(performance.now() - start)));
                })""", str(i)))
            assert await page.locator('#perf-output').inner_text() == '11'
            navigation = {}
            for target in ['setup', 'runtime-config', 'guide']:
                navigation[target] = await page.evaluate("""target => new Promise((resolve, reject) => {
                    const original = window.goto;
                    const start = performance.now();
                    window.goto = (...args) => original(...args).then(() => {
                        window.goto = original;
                        requestAnimationFrame(() => requestAnimationFrame(() => resolve(performance.now() - start)));
                    }, reject);
                    document.querySelector(`nav a[data-page="${target}"]`).click();
                })""", target)
                resolved = await page.evaluate('target => AdminPageLayout.resolve(ADMIN_PAGE_ALIASES[target] || target)', target)
                assert await page.locator(f'#page-{resolved}').is_visible()
            print(json.dumps({"cpu_slowdown": cpu_slowdown, "input_next_paint_ms": samples,
                              "cold_shell_dom_ready_ms": cold_shell,
                              "navigation_shell_ms": navigation, "max_input_ms": max(samples)}))
            assert max(samples) < 200, samples
            assert max(navigation.values()) < 200, navigation
            assert not any('NotoSerifSC-VF.woff2' in url for url in requests), 'Full CJK font downloaded'
            assert not errors, errors
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cpu-slowdown', type=float, default=1)
    asyncio.run(main(parser.parse_args().cpu_slowdown))
