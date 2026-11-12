"""Serve only public static files and run the isolated browser contracts."""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
import threading


ROOT = Path(__file__).resolve().parents[1]


def main():
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Browser acceptance requires Node and Playwright")

    class StaticOnly(SimpleHTTPRequestHandler):
        def do_GET(self):
            if not self.path.startswith("/static/"):
                self.send_error(403)
                return
            super().do_GET()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(StaticOnly, directory=str(ROOT / "admin")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {key: value for key, value in os.environ.items()
           if not any(word in key.upper() for word in ("TOKEN", "SECRET", "GOOGLE", "GMAIL", "API_KEY"))}
    env["ADMIN_TEST_URL"] = f"http://127.0.0.1:{server.server_port}"
    output = ROOT / ".tmp" / "admin-navigation"
    output.mkdir(parents=True, exist_ok=True)
    try:
        for script in ("admin_navigation_browser.cjs", "admin_acceptance_browser.cjs"):
            result = subprocess.run([node, str(ROOT / "tests" / script)], cwd=ROOT, env=env,
                                    capture_output=True, text=True, encoding="utf-8", timeout=120)
            (output / f"{script}.log").write_text(result.stdout + result.stderr, encoding="utf-8", newline="\n")
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            print(result.stdout.strip())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
