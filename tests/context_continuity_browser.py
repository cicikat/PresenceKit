"""Real admin page and read API, isolated fixture records, cache disabled."""
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
    from admin.routers import life_records as routes
    from core import config_loader, life_records, sandbox, tool_dispatcher
    from core.scheduler import loop
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='continuity-browser-') as temporary, MonkeyPatch.context() as patch:
        paths = sandbox.DataPaths(mode='test', test_session_id='continuity-browser')
        paths._base = Path(temporary)
        paths._project_root = Path(temporary)
        patch.setattr(sandbox, '_instance', paths)
        config = {'scheduler': {'owner_id': 'fixture-owner'}, 'life_records': {'enabled': True, 'character_readable': True}}
        for module in (config_loader, life_records, routes, tool_dispatcher):
            patch.setattr(module, 'get_config', lambda: config)
        patch.setattr(loop, '_active_char_id_or_none', lambda: 'fixture-char')
        patch.setattr(tool_dispatcher, '_is_tool_enabled', lambda _: True)
        patch.setattr(tool_dispatcher, 'get_tools_schema', lambda **kw: [{'function': {'name': 'read_life_records'}}])
        life_records.sync('fixture-owner', 'fixture-device', {
            'record_id': 'fixture-record', 'operation_id': 'fixture-operation', 'base_revision': 0, 'action': 'upsert',
            'record': {'category': 'diet', 'title': '合成午餐记录', 'occurred_on': '2026-09-13', 'items': [], 'user_edited_fields': []}})
        app = FastAPI()
        app.include_router(routes.router)
        for route in app.routes:
            for dependency in getattr(getattr(route, 'dependant', None), 'dependencies', []):
                app.dependency_overrides[dependency.call] = lambda: object()
        app.mount('/static', StaticFiles(directory=root / 'admin/static'))
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=18769, log_level='error'))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(.05)
            assert server.started
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1100})
                def intercept(route):
                    if '/static/' in route.request.url or '/settings/life-records' in route.request.url:
                        return route.continue_()
                    return route.fulfill(json={})
                page.route('**/*', intercept)
                cdp = page.context.new_cdp_session(page)
                cdp.send('Network.enable')
                cdp.send('Network.clearBrowserCache')
                cdp.send('Network.setCacheDisabled', {'cacheDisabled': True})
                page.goto('http://127.0.0.1:18769/static/index.html')
                page.reload(wait_until='networkidle')
                page.evaluate("""async()=>{document.getElementById('auth-overlay').style.display='none';
                    document.getElementById('app').style.display='flex';await goto('service-center');await loadLifeRecords();}""")
                host = page.locator('#life-status')
                assert '待评估资料 1 条' in host.inner_text(), host.inner_text()
                assert '已读不代表已经回复' in host.inner_text()
                assert '角色可读取生活记录' in host.inner_text()
                config['life_records']['character_readable'] = False
                page.evaluate('loadLifeRecords()')
                assert '角色读取已关闭' in host.inner_text()
                assert '待评估资料 0 条' in host.inner_text()
                config['life_records']['character_readable'] = True
                page.evaluate('loadLifeRecords()')
                host.scroll_into_view_if_needed()
                (root / '.tmp').mkdir(exist_ok=True)
                page.screenshot(path=str(root / '.tmp/continuity-service.png'))
                browser.close()
            print('PASS: hard refresh, real isolated API, pending count, permission state, read-versus-reply text.')
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == '__main__':
    main()
