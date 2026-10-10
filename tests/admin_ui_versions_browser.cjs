// Real HTML/CSS/JS assets. API fixtures or denied requests; no production credentials.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const origin = 'http://127.0.0.1:8769';
const staticRoot = path.join(__dirname, '..', 'admin', 'static');
const output = path.join(__dirname, '..', '.tmp', 'admin-all');
const mode = process.env.ADMIN_UI_TEST_PHASE || 'shell';

(async () => {
  fs.mkdirSync(output, {recursive:true});
  const browser = await chromium.launch({headless:true});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    const page = await context.newPage();
    const errors = []; const apiCalls = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const request = route.request(); const url = new URL(request.url());
      if (url.origin === origin && url.pathname.startsWith('/static/')) {
        const target = path.resolve(staticRoot, decodeURIComponent(url.pathname.slice('/static/'.length)));
        assert.ok(target.startsWith(staticRoot + path.sep));
        const ext = path.extname(target);
        return route.fulfill({body:fs.readFileSync(target),contentType:({'.html':'text/html','.css':'text/css','.js':'text/javascript','.woff2':'font/woff2'})[ext] || 'application/octet-stream'});
      }
      apiCalls.push({method:request.method(),path:url.pathname});
      return route.fulfill({status:403,contentType:'application/json',body:'{"detail":"browser-test-denied"}'});
    });
    const cdp = await context.newCDPSession(page);
    await cdp.send('Network.enable'); await cdp.send('Network.clearBrowserCache'); await cdp.send('Network.setCacheDisabled',{cacheDisabled:true});
    await page.goto(origin + '/static/index.html');
    await page.evaluate(() => {
      document.getElementById('auth-overlay').style.display='none';
      document.getElementById('app').style.display='flex';
    });
    async function mount(name) {
      await page.evaluate(async name => {
        await loadPageFragment(name);
        document.querySelectorAll('.page.active').forEach(node => node.classList.remove('active'));
        const root = document.getElementById('page-' + name);
        root.classList.add('active');
        decorateSettingsPanels(name, root);
        AdminUI.apply();
        document.querySelector('main').scrollTop=0;
      }, name);
    }
    async function switchTo(version) {
      const before = apiCalls.length;
      await page.selectOption('#admin-ui-version',version);
      await page.waitForTimeout(30);
      assert.equal(apiCalls.length,before,'version switching must not call APIs');
    }
    assert.equal(await page.locator('#admin-ui-version').inputValue(),'legacy');
    await mount('overview');
    const legacy = await page.evaluate(() => ({background:getComputedStyle(document.body).backgroundColor,font:getComputedStyle(document.body).fontFamily}));
    await page.screenshot({path:path.join(output,'overview-legacy.png')});
    await switchTo('new');
    assert.equal(await page.locator('body.pk-admin').count(),1);
    assert.equal(await page.evaluate(() => getComputedStyle(document.body).backgroundColor),'rgb(238, 237, 243)');
    await page.screenshot({path:path.join(output,'overview-new.png')});
    await switchTo('legacy');
    assert.deepEqual(await page.evaluate(() => ({background:getComputedStyle(document.body).backgroundColor,font:getComputedStyle(document.body).fontFamily})),legacy);
    await switchTo('new');
    await page.reload(); assert.equal(await page.locator('#admin-ui-version').inputValue(),'new');
    await page.evaluate(() => {document.getElementById('auth-overlay').style.display='none';document.getElementById('app').style.display='flex';});
    await mount('overview');
    await page.setViewportSize({width:480,height:900});
    await page.screenshot({path:path.join(output,'overview-narrow.png')});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    assert.deepEqual(errors,[]);
    console.log('PASS: shell defaults, scoped style rollback, persistence, zero API switching, desktop/narrow. Mode:',mode);
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exit(1);});
