"""Real admin assets; synthetic inbox, no credentials or production writes."""
from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.route('**/observability/ime-drafts?*', lambda route: route.fulfill(json={
            'effective': True, 'retention_hours': 3, 'next_before': None,
            'summary': {'draft_count': 3, 'test_count': 1, 'latest_draft_updated_at': 1789128000000},
            'entries': [{'device_id': 'example', 'app_package': 'example.editor',
                         'source': 'keyboard', 'revision': 2, 'updated_at': 1789128000000,
                         'content': '<script>unsafe()</script>'}]}))
        session = page.context.new_cdp_session(page)
        session.send('Network.enable')
        session.send('Network.setCacheDisabled', {'cacheDisabled': True})
        session.send('Network.clearBrowserCache')
        page.goto('http://127.0.0.1:8080/static/index.html')
        page.reload(wait_until='networkidle')
        page.evaluate("""async () => {
          document.getElementById('auth-overlay').style.display='none';
          document.getElementById('app').style.display='flex';
          await goto('observation-center');
        }""")
        page.get_by_text('IME 输入草稿 3 条', exact=False).wait_for()
        assert 'IME' in page.locator('nav a[data-page="observation-center"]').inner_text()
        assert not page.locator('#ime-observation details').get_attribute('open')
        assert page.locator('#ime-observation script').count() == 0
        page.locator('#ime-observation summary').click()
        assert page.locator('#ime-observation pre').is_visible()
        assert 'unsafe()' in page.locator('#ime-observation pre').inner_text()
        browser.close()
        print('IME browser verification passed (cache cleared, visible labels/counts, escaped draft).')


if __name__ == '__main__':
    main()
