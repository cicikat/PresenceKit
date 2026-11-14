"""Real Chromium acceptance of the device-policy fragment and production JS.

Only HTTP responses are synthetic. Exercises actual action bindings, authenticated
image requests, image decoding, empty/error states and cache-bypassing reloads.
This does not claim acceptance of the full admin login flow or a real desktop.
"""
import argparse
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import urlsplit

from PIL import Image
from playwright.sync_api import sync_playwright, expect


def main(chromium):
    root = Path(__file__).resolve().parents[1] / "admin/static"
    state = {"empty": False, "expired": False, "requests": []}
    stream = io.BytesIO()
    Image.new("RGB", (390, 844), "#254f98").save(stream, "JPEG")
    digest = "a" * 64

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            path = urlsplit(self.path).path
            content_type, status = "text/html; charset=utf-8", 200
            if path == "/":
                fragment = (root / "pages/device-policy.html").read_text(encoding="utf-8")
                body = ('<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/static/style.css">'
                        '<button id="nav-back" hidden></button><button id="nav-menu-toggle" hidden></button>'
                        '<div id="nav-backdrop" hidden></div><nav hidden></nav>'
                        '<div id="app"><main style="max-width:900px;margin:24px auto">' + fragment + '</main></div>'
                        '<script>localStorage.setItem("qq_admin_key","synthetic-ui-token")</script>'
                        '<script src="/static/i18n.js"></script><script src="/static/js/core.js"></script>'
                        '<script src="/static/js/relationship.js"></script><script>bindPageActions(document)</script>').encode()
            elif path.startswith("/static/"):
                file = (root / path.removeprefix("/static/")).resolve()
                if not file.is_relative_to(root) or not file.is_file():
                    self.send_error(404)
                    return
                body = file.read_bytes()
                content_type = "text/css" if file.suffix == ".css" else "text/javascript; charset=utf-8"
            elif path.startswith("/observability/device-file-agent"):
                state["requests"].append({"path": path, "auth": self.headers.get("Authorization"),
                                          "cache": self.headers.get("Cache-Control")})
                if self.headers.get("Authorization") != "Bearer synthetic-ui-token":
                    self.send_error(401)
                    return
                if path.endswith(digest):
                    content_type = "image/jpeg"
                    body = stream.getvalue()
                    if state["expired"]:
                        body, status = b"expired", 404
                else:
                    content_type = "application/json"
                    body = json.dumps({"browser_screenshots": [] if state["empty"] else [{
                        "screenshot_sha256": digest, "task_id": "synthetic-task", "vision_status": "reviewed",
                        "viewport": {"width": 390, "height": 844},
                    }]}).encode()
            else:
                self.send_error(404)
                return
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(executable_path=str(chromium), headless=True,
                                             args=["--no-proxy-server"])
            page = browser.new_page(viewport={"width": 1100, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(error.stack))
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.reload(wait_until="load")
            refresh = page.locator('[data-action="loadBrowserEvidence"]')
            refresh.click()
            section = page.locator("#browser-preview-evidence details")
            expect(section).to_have_count(1)
            section.locator("summary").click()
            page.wait_for_function("document.querySelector('#browser-preview-evidence img')?.naturalWidth === 390")
            assert state["requests"][-1]["path"].endswith(digest)
            state["expired"] = True
            refresh.click()
            section.locator("summary").click()
            expect(section.locator("p")).to_contain_text("截图已过期")
            expect(section.locator("img")).to_have_count(0)
            assert sum(row["path"].endswith(digest) for row in state["requests"]) == 2
            state["empty"] = True
            refresh.click()
            expect(page.locator("#browser-preview-evidence")).to_have_text("暂无可复核截图")
            assert not errors, errors
            browser.close()
        print("PASS: real Chromium admin fragment, refresh click, authenticated no-store JPEG, expiry and empty state")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium", type=Path, required=True)
    main(parser.parse_args().chromium.resolve(strict=True))
