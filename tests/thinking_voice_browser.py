"""Manual browser check: actual admin assets, synthetic settings, no credentials."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.thinking_voice import compose


def main():
    state = {'enabled': True, 'character_voice': True, 'mode': 'native',
             'monologue_max_tokens': 200, 'apply_to_proactive': False,
             'display_prefer_monologue': True}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        def route(request):
            path = request.request.url.split('8080', 1)[-1].split('?', 1)[0]
            if path.startswith('/static/'):
                return request.continue_()
            if path == '/settings/thinking':
                if request.request.method == 'POST':
                    state.update(request.request.post_data_json)
                voice = compose(char_id='fixture', mood={'current': 'sad', 'intensity': .5}, now=100000)
                voice.update(effective=state['enabled'] and state['character_voice'],
                             blocking_reason='' if state['character_voice'] else 'voice_disabled')
                return request.fulfill(json={**state, 'voice_preview': voice})
            return request.fulfill(json={})
        page.route('**/*', route)
        cdp = page.context.new_cdp_session(page)
        cdp.send('Network.enable')
        cdp.send('Network.setCacheDisabled', {'cacheDisabled': True})
        cdp.send('Network.clearBrowserCache')
        page.goto('http://127.0.0.1:8080/static/index.html')
        page.reload(wait_until='networkidle')
        page.evaluate("""async () => {
          document.getElementById('auth-overlay').style.display='none';
          document.getElementById('app').style.display='flex';
          await goto('model-routing');
        }""")
        page.get_by_text('心声引导：已启用', exact=False).wait_for()
        box = page.locator('#mr-thinking-card')
        assert box.locator('[data-field="character_voice"]').is_checked()
        box.get_by_text('查看当前拼接提示').click()
        assert '真挚、诚恳' in box.locator('pre').inner_text()
        box.locator('[data-field="character_voice"]').uncheck()
        box.locator('[data-action="saveThinkingSettings"]').click()
        page.get_by_text('心声引导：未启用', exact=False).wait_for()
        assert state['character_voice'] is False
        assert state['mode'] == 'native'
        browser.close()
        print('Thinking voice browser check passed: hard refresh, toggle, preview, saved effective state.')


if __name__ == '__main__':
    main()
