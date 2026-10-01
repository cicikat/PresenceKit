"""Isolated-admin browser check for the sherpa-onnx engine UI on 本地模型运行 (工单 H).

Real router + static assets; the Whisper loader, the sherpa engine build and the model download are
faked at the Python boundary, so this proves what the page *shows and sends*: the engine dropdown, which
parameter group is visible, the download progress, a loud failure when the package is missing (nothing
saved, Whisper kept), a successful switch, and that both groups keep their values across switches.
Production :8080 and real models are not touched.
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

PORT = 18773


def main(screenshot_dir: str | None = None) -> None:
    from admin.routers import settings_local_runtime as routes
    from core import config_loader, stt_local, stt_sherpa

    root = Path(__file__).resolve().parents[1]
    store = {"cfg": {}}
    sim = {"installed": False, "present": False, "download": {"state": "idle", "model": "", "file": "",
                                                              "bytes_done": 0, "bytes_total": 0, "error": ""}}
    model_id = stt_sherpa.DEFAULT_MODEL

    def fake_probe():
        total = 199_000_000
        return {"installed": sim["installed"], "version": "1.13.8" if sim["installed"] else "",
                "models": {model_id: {"model": model_id, "label": "fake", "present": sim["present"],
                                      "missing": [] if sim["present"] else ["encoder-epoch-99-avg-1.int8.onnx"],
                                      "corrupt": [], "bytes": total if sim["present"] else 0, "expected_bytes": total}},
                "download": dict(sim["download"]), "hint": ""}

    def fake_start(model, base):
        sim["base_seen"] = base
        sim["download"].update(state="running", model=model, file="encoder-epoch-99-avg-1.int8.onnx",
                               bytes_done=60_000_000, bytes_total=199_000_000, error="")

        def finish():
            time.sleep(2.2)
            sim["present"] = True
            sim["download"].update(state="done", file="", bytes_done=199_000_000)
        threading.Thread(target=finish, daemon=True).start()
        return True

    def fake_build(group):
        return {"model": object(), "backend": "sherpa_onnx", "device": "cpu", "model_size": group["model"],
                "compute_type": "int8", "fallback": None, "hotwords": "active"}

    with MonkeyPatch.context() as patch:
        stt_local.reset_for_tests()
        patch.setattr(stt_local, "_load", lambda size, device, compute: object())
        patch.setattr(stt_local, "_smoke", lambda model: None)
        patch.setattr(stt_local, "_cuda_device_count", lambda: 0)
        patch.setattr(stt_sherpa, "probe", fake_probe)
        patch.setattr(stt_sherpa, "start_download", fake_start)
        patch.setattr(stt_sherpa, "download_status", lambda: dict(sim["download"]))
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
                page = browser.new_page(viewport={"width": 1280, "height": 1500})

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
                page.locator("#local-runtime-engine option").first.wait_for(state="attached")

                def shot(name):
                    if screenshot_dir:
                        page.screenshot(path=f"{screenshot_dir}/sherpa-{name}.png", full_page=True)

                # 1. default: faster-whisper, whisper group visible, sherpa group hidden, package missing
                engines = page.locator("#local-runtime-engine option").all_inner_texts()
                assert len(engines) == 2 and "faster-whisper" in engines[0] and "sherpa-onnx" in engines[1], engines
                assert page.locator("#local-runtime-engine").input_value() == "faster_whisper"
                assert page.locator("#local-runtime-whisper-group").is_visible()
                assert not page.locator("#local-runtime-sherpa-group").is_visible()
                assert "没有安装 sherpa-onnx" in page.locator("#local-runtime-hardware-body").inner_text()
                assert not page.locator("#local-runtime-remote-note").is_visible()
                shot("1-default")

                # 2. choose sherpa: groups swap, defaults shown, repeat-safe decoding is the default
                page.select_option("#local-runtime-engine", "sherpa_onnx")
                assert not page.locator("#local-runtime-whisper-group").is_visible()
                assert page.locator("#local-runtime-sherpa-group").is_visible()
                assert page.locator("#local-runtime-sherpa-decoding").input_value() == "modified_beam_search"
                assert page.locator("#local-runtime-sherpa-repeat").input_value() == "4"
                assert "不容易出现单字重复" in page.locator("#local-runtime-sherpa-decoding-note").inner_text()
                assert "还没下载" in page.locator("#local-runtime-sherpa-assets-body").inner_text()
                shot("2-sherpa-selected")

                # 3. save without the package: loud failure, nothing saved, Whisper still the engine in use
                page.fill("#local-runtime-sherpa-threads", "4")
                page.click("#local-runtime-save")
                page.wait_for_function(
                    "() => document.getElementById('local-runtime-result').dataset.state === 'err'")
                failure = page.locator("#local-runtime-result").inner_text()
                assert "切换失败，未保存" in failure and "pip install sherpa-onnx" in failure, failure
                assert "stt_local" not in store["cfg"]
                shot("3-missing-package")

                # 4. package installed, model still missing: download shows progress, then completes
                sim["installed"] = True
                page.click("[data-action='loadLocalModelRuntime']")
                page.wait_for_function("() => document.getElementById('local-runtime-sherpa-threads').value === '2'")
                assert page.locator("#local-runtime-engine").input_value() == "faster_whisper"
                page.select_option("#local-runtime-engine", "sherpa_onnx")
                page.fill("#local-runtime-sherpa-base", "https://mirror.example/")
                page.click("#local-runtime-sherpa-download")
                page.wait_for_function(
                    "() => document.getElementById('local-runtime-sherpa-assets-body').innerText.includes('正在下载')")
                assert "57.2 / 189.8 MB" in page.locator("#local-runtime-sherpa-assets-body").inner_text()
                assert page.locator("#local-runtime-sherpa-download").is_disabled()
                shot("4-downloading")
                page.wait_for_function(
                    "() => document.getElementById('local-runtime-sherpa-assets-body').innerText.includes('已就位并通过校验')",
                    timeout=15000)
                assert not page.locator("#local-runtime-sherpa-download").is_disabled()

                # 5. save sherpa: effective state shows the engine and hotword status, config persisted
                patch.setattr(stt_sherpa, "build", fake_build)
                page.fill("#local-runtime-sherpa-threads", "4")
                page.click("#local-runtime-save")
                page.wait_for_function(
                    "() => document.getElementById('local-runtime-result').dataset.state === 'ok'")
                effective = page.locator("#local-runtime-effective-body").inner_text()
                assert "zipformer-bilingual-zh-en-2023-02-20" in effective and "热词偏置：active" in effective, effective
                assert page.locator("#local-runtime-stt-status").inner_text() == "已生效"
                saved = store["cfg"]["stt_local"]
                assert saved["engine"] == "sherpa_onnx" and saved["sherpa_onnx"]["num_threads"] == 4
                assert saved["sherpa_onnx"]["download_base"] == "https://mirror.example"
                shot("5-sherpa-active")

                # 6. switch back: Whisper group returns with its values, and the sherpa group is kept in config
                page.select_option("#local-runtime-engine", "faster_whisper")
                assert page.locator("#local-runtime-whisper-group").is_visible()
                assert not page.locator("#local-runtime-sherpa-group").is_visible()
                page.fill("#local-runtime-beam-size", "3")
                page.click("#local-runtime-save")
                page.wait_for_function(
                    "() => document.getElementById('local-runtime-result').dataset.state === 'ok'")
                saved = store["cfg"]["stt_local"]
                assert saved["engine"] == "faster_whisper" and saved["beam_size"] == 3
                assert saved["sherpa_onnx"]["num_threads"] == 4                      # 另一组没被清空
                page.click("[data-action='loadLocalModelRuntime']")
                page.wait_for_function("() => document.getElementById('local-runtime-beam-size').value === '3'")
                page.select_option("#local-runtime-engine", "sherpa_onnx")
                assert page.locator("#local-runtime-sherpa-threads").input_value() == "4"
                shot("6-switched-back")

                # 7. a remote STT block makes /transcribe prefer it: the page says so
                store["cfg"]["stt_presets"] = {"enabled": False}
                page.click("[data-action='loadLocalModelRuntime']")
                page.wait_for_function("() => !document.getElementById('local-runtime-remote-note').hidden")
                assert "暂时不会被用到" in page.locator("#local-runtime-remote-note").inner_text()
                shot("7-remote-overrides")
                assert sim.get("base_seen") == "https://mirror.example"
                browser.close()
                print("sherpa engine browser passed: engine select, group swap, loud missing-package failure, "
                      "download progress, active state, values kept across switches, remote-override note.")
        finally:
            server.should_exit = True
            stt_local.reset_for_tests()


def test_sherpa_engine_ui_flows_in_a_real_browser():
    pytest.importorskip("playwright.sync_api")
    main()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
