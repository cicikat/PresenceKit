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
        closeSidebar();
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
    if (mode !== 'shell') {
      const names = fs.readdirSync(path.join(staticRoot,'pages')).filter(n=>n.endsWith('.html')).map(n=>n.slice(0,-5))
        .filter(name => mode === 'all' || (!name.startsWith('observe-') && !['status','logs','call-records','observation-center'].includes(name)));
      const coverage=[];
      for (const name of names) {
        await page.setViewportSize({width:1440,height:1000});
        await mount(name);
        const snapshot = await page.evaluate(name => {
          const root=document.getElementById('page-'+name);
          window._uiTestFields=[...root.querySelectorAll('input,select,textarea')];
          const text=window._uiTestFields.find(n=>n.tagName==='TEXTAREA'||n.type==='text');
          if(text) text.value='browser-draft';
          window._uiTestDraft=text;
          return {fields:window._uiTestFields.length,actions:root.querySelectorAll('[data-action]').length};
        },name);
        // Every generic fold only hides content; it never remounts controls or dispatches actions.
        const before=apiCalls.length;
        assert.ok(await page.evaluate(name=>[...document.getElementById('page-'+name).querySelectorAll('.pk-collapse')].every(n=>['▾','▸'].includes(n.textContent)&&n.getAttribute('aria-label')),name), 'left triangle, not text buttons: '+name);
        await page.evaluate(name => document.getElementById('page-'+name).querySelectorAll('.pk-collapse').forEach(n=>{n.click();n.click();}),name);
        await switchTo('legacy');
        assert.equal(await page.locator('#page-'+name+' .pk-collapse').count(),0);
        await switchTo('new');
        assert.equal(apiCalls.length,before);
        assert.deepEqual(await page.evaluate(name=>{
          const root=document.getElementById('page-'+name);
          return {fields:root.querySelectorAll('input,select,textarea').length,actions:root.querySelectorAll('[data-action]').length};
        },name),snapshot);
        assert.ok(await page.evaluate(()=>window._uiTestFields.every(n=>n.isConnected)),'original field identity: '+name);
        assert.ok(await page.evaluate(()=>!window._uiTestDraft||window._uiTestDraft.value==='browser-draft'),'draft value: '+name);
        if (['model-routing','character','mcp','tts-config','client-settings','owner-turn-api','listening-player','status','observe-prompt','logs'].includes(name)) {
          await page.screenshot({path:path.join(output,name+'-desktop.png')});
        }
        await page.setViewportSize({width:480,height:900});
        await page.waitForTimeout(200);
        const overflow=await page.evaluate(name=>{
          const root=document.getElementById('page-'+name);
          return {page:root.scrollWidth-root.clientWidth,document:document.documentElement.scrollWidth-innerWidth};
        },name);
        coverage.push({page:name,...snapshot,overflow});
        if (['model-routing','character','mcp','tts-config','client-settings','owner-turn-api','listening-player','status','observe-prompt','logs'].includes(name)) {
          await page.screenshot({path:path.join(output,name+'-narrow.png')});
        }
      }
      fs.writeFileSync(path.join(output,'coverage-'+mode+'.json'),JSON.stringify(coverage,null,2)+'\n');
      console.log('Presentation coverage:',coverage.length,'pages; overflow:',coverage.filter(item=>item.overflow.page>1||item.overflow.document>1));
    }
    await mount('overview');
    await page.setViewportSize({width:480,height:900});
    await page.screenshot({path:path.join(output,'overview-narrow.png')});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    assert.deepEqual(errors,[]);
    console.log('PASS: shell defaults, scoped style rollback, persistence, zero API switching, desktop/narrow. Mode:',mode);
  } finally {await browser.close();}
})().catch(error => {console.error(error);process.exit(1);});
