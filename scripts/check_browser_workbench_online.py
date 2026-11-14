"""Explicit, billable synthetic acceptance: mounted HTTP broker -> packaged Chromium -> screen VLM.

Pass --worker with the packaged desktop executable. This is not a Tauri app test.
Only configured screen credentials are reused; task writes go to a fresh sandbox.
Screenshots remain in memory. No real chat, owner history or character card is loaded.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def check(executable):
    from core.sandbox import init_paths
    paths = init_paths(mode="test", test_session_id="browser_acceptance_" + uuid4().hex)
    try:
        await _check(executable)
    finally:
        paths.cleanup()


async def _check(executable):
    from core import config_loader, image_presets, llm_client, tool_dispatcher
    from core.agent_runtime import computer_use, device_file_agent as service
    from admin import auth
    from admin.admin_server import app
    import httpx
    from unittest.mock import patch

    configured = config_loader.get_config()
    if not image_presets.screen_vision_chain(configured):
        raise RuntimeError("screen vision route is not configured")
    config = {**configured, "device_file_agent": {"enabled": True},
              "scheduler": {"owner_id": "synthetic-owner"}, "deployment": {"mode": "remote_server"}}
    token = auth.TokenInfo("synthetic-desktop", frozenset({"sensor.write", "ws.desktop", "state.read"}), "desktop")
    grant = {"workspace_id": uuid4().hex, "revision": uuid4().hex, "alias": "synthetic-preview", "write": False}
    packet = {"protocol": service.VERSION, "device_id": uuid4().hex, "device_epoch": uuid4().hex,
              "state": "enabled", "grants": [grant], "operations": ["browser"]}
    source = """<!doctype html><style>body{margin:0;background:#fff;color:#111;font:24px sans-serif}
    .overflow{width:1900px;background:#eee}button{color:#ddd;background:#eee;border:0;font-size:36px;padding:32px}
    .overlap{position:absolute;top:340px;left:30px;width:290px;height:100px;background:#e93;color:#111}
    .collision{position:absolute;top:375px;left:90px;width:290px;height:100px;background:#26a;color:white}
    canvas{display:block}</style><h1>Synthetic visual fixture</h1><div class="overflow">Overflow panel</div>
    <button onclick="this.textContent='Clicked'">Low contrast button</button><canvas width="300" height="200"></canvas>
    <script>let c=document.querySelector('canvas').getContext('2d');c.fillStyle='#f0254f';c.fillRect(0,0,150,200);
    c.fillStyle='#205fe5';c.fillRect(150,0,150,200);</script>
    <div class="overlap">First panel: text is obscured by the second panel</div>
    <div class="collision">Second panel overlaps first panel</div>"""
    env = {key: value for key, value in os.environ.items() if "proxy" not in key.lower()}
    env["PLAYWRIGHT_BROWSERS_PATH"] = "0"
    child = await asyncio.create_subprocess_exec(str(executable), stdin=asyncio.subprocess.PIPE,
                                                stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL, env=env)

    async def execute(command):
        action = command["action"]
        parameters = json.loads(action["query"])
        if parameters["op"] in {"start", "reload"}:
            parameters["files"] = {"fixture.html": source}
        parameters.update(task_id=command["ref"]["task_id"], seq=command["ref"]["seq"], path=action["path"])
        child.stdin.write((json.dumps(parameters) + "\n").encode("utf-8"))
        await asyncio.wait_for(child.stdin.drain(), 5)
        reply = json.loads(await asyncio.wait_for(child.stdout.readline(), 25))
        return {"ref": command["ref"], "code": "ok" if reply["ok"] else "invalid_action", "body": reply.get("body", {})}

    with patch.object(config_loader, "get_config", return_value=config), \
         patch.object(image_presets, "get_config", return_value=config), \
         patch.object(computer_use.pipeline_registry, "get", return_value=SimpleNamespace(_active_character_id="synthetic-char", character=None)), \
         patch.object(auth, "resolve_token", return_value=token):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture",
                                     headers={"Authorization": "Bearer synthetic"}) as client:
            async def call(name, arguments):
                pending = asyncio.create_task(tool_dispatcher.execute_structured(
                    name, arguments, "synthetic-owner", "synthetic-owner", False, None,
                    origin="assistant_loop", char_id="synthetic-char"))
                async def pump():
                    while not pending.done():
                        reply = (await client.post("/device-file-agent/poll", json=packet)).json()
                        if command := reply.get("command"):
                            result = await execute(command)
                            response = await client.post("/device-file-agent/result", json=result)
                            assert response.status_code == 200
                        await asyncio.sleep(.02)

                try:
                    await asyncio.wait_for(pump(), 110)
                    result = json.loads((await pending).result)
                    if result.get("code"):
                        raise RuntimeError("tool failed: " + str(result["code"]) + ": " + str(result.get("evidence", {}).get("vision_error", "")))
                    return result
                finally:
                    if not pending.done():
                        pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)

            try:
                await client.post("/device-file-agent/poll", json=packet)
                opened = await call("browser_session_start", {"device_id": packet["device_id"],
                    "workspace_id": grant["workspace_id"], "path": "fixture.html", "resources": "[]"})
                task = opened["task_id"]
                for viewport in ("desktop", "mobile"):
                    await call("browser_session_action", {"task_id": task, "operation": "resize",
                               "parameters": json.dumps({"viewport": viewport})})
                    image = await call("browser_screenshot", {"task_id": task, "parameters": "{}"})
                    review = await call("browser_visual_review", {"task_id": task, "sha256": image["evidence"]["screenshot_sha256"]})
                    assert review["evidence"]["vision_status"] == "reviewed"
                    print(json.dumps({"viewport": viewport, "evidence": review["evidence"],
                                      "observations": review["observations"]}, ensure_ascii=True))
                    assert review["observations"]["contrast"] or review["observations"]["layout_issues"] or review["observations"]["overflow"], "visual fixture issues were not detected"
                closed = await call("browser_session_action", {"task_id": task, "operation": "close", "parameters": "{}"})
                assert closed["status"] == "succeeded"
                print("PASS: real Chromium + mounted broker + configured screen VLM (not full Tauri E2E)")
            finally:
                await service.shutdown()
                await llm_client.reload_client()
                child.stdin.close()
                try:
                    await asyncio.wait_for(child.wait(), 5)
                except asyncio.TimeoutError:
                    child.kill()
                    await child.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(check(args.worker.resolve(strict=True)))
