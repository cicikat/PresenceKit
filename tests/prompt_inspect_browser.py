"""Manual check against local admin assets; no authenticated production requests."""
from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.route('**/*', lambda r: r.continue_() if '/static/' in r.request.url else r.fulfill(json={}))
        cdp = page.context.new_cdp_session(page)
        cdp.send('Network.enable')
        cdp.send('Network.setCacheDisabled', {'cacheDisabled': True})
        cdp.send('Network.clearBrowserCache')
        page.goto('http://127.0.0.1:8080/static/index.html')
        page.reload(wait_until='networkidle')
        page.evaluate("""async () => {
            document.getElementById('auth-overlay').style.display='none';
            document.getElementById('app').style.display='flex';
            await goto('observe-prompt');
        }""")
        assert 'prompt检视' in page.locator('nav a[data-page="observe-prompt"]').inner_text()
        assert 'prompt检视' in page.locator('#page-observe-prompt .page-title').inner_text()
        browser.close()
        print('Prompt inspection title/nav verified after cache-cleared refresh.')


if __name__ == '__main__':
    main()
