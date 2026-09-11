"""Cache-cleared browser check with real admin assets and synthetic observation data."""
from playwright.sync_api import sync_playwright


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page()
        def route(r):
            if '/static/' in r.request.url: return r.continue_()
            if '/observability/ime-drafts' in r.request.url:
                return r.fulfill(json={'effective':True,'retention_hours':3,'next_before':None,
                    'awareness':{'effective':True,'route':{'effective_preset':'fixture-small','source':'category'}},
                    'summary':{'draft_count':1,'test_count':0},
                    'analyses':[{'status':'queued','revision':2,'result':{'activity':'chat','summary':'她在聊天。','signal_id':'fixture'}}],
                    'entries':[{'device_id':'fixture','app_package':'example.editor','source':'keyboard','revision':2,
                                'updated_at':1789128000000,'content':'<script>invalid()</script>',
                                'edit_events':[{'kind':'delete_backward','outcome':'requested','text':'示例'}]}]})
            return r.fulfill(json={})
        page.route('**/*',route)
        cdp=page.context.new_cdp_session(page)
        cdp.send('Network.enable'); cdp.send('Network.clearBrowserCache')
        cdp.send('Network.setCacheDisabled',{'cacheDisabled':True})
        page.goto('http://127.0.0.1:8080/static/index.html')
        page.reload(wait_until='networkidle')
        page.evaluate("""async()=>{document.getElementById('auth-overlay').style.display='none';
            document.getElementById('app').style.display='flex';await goto('observation-center');}""")
        page.get_by_text('判定已生效',exact=False).wait_for()
        host=page.locator('#ime-observation')
        assert 'fixture-small' in host.inner_text()
        assert '仅接收与保存，不触发 AI' not in page.locator('#page-observation-center').inner_text()
        host.locator('details').first.locator('summary').click()
        assert '她在聊天' in host.inner_text()
        host.get_by_text('查看草稿正文（敏感内容）',exact=True).click()
        assert 'delete_backward' in host.inner_text()
        assert host.locator('script').count()==0
        assert page.evaluate("MR_CATEGORIES.includes('ime_judge')")
        browser.close()
        print('IME awareness browser passed: refreshed labels, effective route, assessment, escaped edit events.')


if __name__=='__main__': main()
