// AR-01: real Chromium/DOM, public static files, no production API or credentials.
// Optional ignored pre-refactor assets provide a one-time before/after comparison.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '../admin/static');
const tmp = path.resolve(__dirname, '../.tmp');
const before = process.env.ADMIN_AR01_BASELINE === '1';
const hash = value => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
const overrides = {
  'index.html': 'ar01-index-original.html',
  'i18n.js': 'ar01-i18n-original.js',
  'js/core.js': 'ar01-core-original.js',
};

(async () => {
  const browser = await chromium.launch({headless:true});
  const snapshots = {pages:[]};
  try {
    for (const version of ['legacy', 'new']) {
      const context = await browser.newContext({viewport:{width:1440,height:1000}});
      await context.addInitScript(version => localStorage.setItem('presencekit_admin_ui', version), version);
      const page = await context.newPage();
      const errors = [], missing = [], scripts = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (!url.pathname.startsWith('/static/')) {
          return route.fulfill({status:403,contentType:'application/json',body:'{"detail":"fixture-denied"}'});
        }
        const relative = decodeURIComponent(url.pathname.slice('/static/'.length));
        const file = before && overrides[relative] ? path.join(tmp, overrides[relative]) : path.resolve(root, relative);
        assert.ok(file.startsWith(root + path.sep) || before && file.startsWith(tmp + path.sep));
        if (!fs.existsSync(file)) {missing.push(relative); return route.fulfill({status:404,body:''});}
        if (relative.endsWith('.js')) scripts.push(relative);
        return route.fulfill({body:fs.readFileSync(file),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff2':'font/woff2'})[path.extname(file)] || 'application/octet-stream'});
      });
      const cdp = await context.newCDPSession(page);
      await cdp.send('Network.clearBrowserCache');
      await cdp.send('Network.setCacheDisabled',{cacheDisabled:true});
      await page.goto('http://127.0.0.1:8769/static/index.html');
      await page.waitForFunction(() => window.AdminI18n && window.t);
      const dictionary = await page.evaluate(() => AdminI18n.I18N);
      snapshots.dictionary = hash(dictionary);
      assert.equal(new Set(scripts).size, scripts.length, 'no duplicate script initialization');
      if (!before) {
        assert.ok(scripts.indexOf('i18n.js') < scripts.indexOf('js/i18n-runtime.js'));
        assert.ok(scripts.indexOf('js/i18n-runtime.js') < scripts.indexOf('js/core.js'));
        assert.ok(await page.evaluate(() => AdminI18n.I18N === window.AdminI18nMessages));
      }

      // Click actual language controls, then test inserted text, excluded content,
      // native dialogs and restoration. The app is still signed out here.
      await page.evaluate(() => {
        window._localeEvents = 0;
        window.addEventListener('admin-language-changed', () => window._localeEvents++);
        const box = document.createElement('section'); box.id='ar01-dynamic';
        const text = document.createElement('span'); text.id='ar01-legacy'; text.textContent='保存';
        const raw = document.createElement('pre'); raw.id='ar01-raw'; raw.textContent='保存';
        box.append(text,raw); document.body.append(box);
        window._localeNode = text;
      });
      await page.selectOption('#auth-language-select','en');
      await page.waitForFunction(() => AdminI18n.getLanguage() === 'en');
      await page.waitForFunction(() => document.getElementById('ar01-legacy').textContent === 'Save');
      assert.equal(await page.locator('#ar01-raw').textContent(),'保存');
      const dialogs = [];
      page.on('dialog', async dialog => {dialogs.push([dialog.type(),dialog.message(),dialog.defaultValue()]); await dialog.accept(dialog.type()==='prompt'?'draft':undefined);});
      const prompt = await page.evaluate(() => {confirm('保存'); alert('保存'); return window.prompt('保存','draft');});
      assert.equal(prompt,'draft');
      assert.deepEqual(dialogs,[['confirm','Save',''],['alert','Save',''],['prompt','Save','draft']]);
      await page.selectOption('#auth-language-select','zh-CN');
      await page.waitForFunction(() => AdminI18n.getLanguage() === 'zh-CN' && document.getElementById('ar01-legacy').textContent === '保存');
      assert.deepEqual(await page.evaluate(() => [window._localeEvents, document.getElementById('ar01-legacy') === window._localeNode]),[2,true]);
      await page.evaluate(() => {document.getElementById('ar01-dynamic').remove();document.getElementById('auth-overlay').style.display='none';document.getElementById('app').style.display='flex';});

      for (const file of fs.readdirSync(path.join(root,'pages')).filter(file=>file.endsWith('.html')).sort()) {
        const name = file.slice(0,-5);
        await page.evaluate(async name => {
          await loadPageFragment(name);
          document.querySelectorAll('.page.active').forEach(node=>node.classList.remove('active'));
          const element = document.getElementById('page-'+name);element.classList.add('active');
          decorateSettingsPanels(name,element);AdminUI.mount(element);closeSidebar();
          window._ar01Fields = [...element.querySelectorAll('input,select,textarea')];
          const draft=window._ar01Fields.find(node=>node.tagName==='TEXTAREA'||node.type==='text');
          if (draft) draft.value='fixture draft';window._ar01Draft=draft;
        },name);
        // Shared copy/layout observers batch on animation frames. Compare the
        // settled DOM, rather than racing their scheduled projections.
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        for (const width of [1440,480]) {
          await page.setViewportSize({width,height:1000});
          snapshots.pages.push(await page.evaluate(({name,version,width}) => {
            const element=document.getElementById('page-'+name);
            const nodes=[...element.querySelectorAll('input,select,textarea')];
            if (nodes.some((node,index)=>node!==window._ar01Fields[index])) throw Error('field node identity changed');
            if (window._ar01Draft && window._ar01Draft.value!=='fixture draft') throw Error('draft changed');
            const style=getComputedStyle(element);
            return {name,version,width,fields:nodes.length,actions:element.querySelectorAll('[data-action]').length,
              background:style.backgroundColor,color:style.color,font:style.fontFamily,
              fieldValues:nodes.map(node=>[node.id,node.value,node.disabled]),
              text:element.textContent.replace(/\s+/g,' ').trim()};
          },{name,version,width}));
        }
      }
      assert.deepEqual(missing,[],'static asset 404');
      assert.deepEqual(errors,[],'uncaught browser exceptions');
      await context.close();
    }
    const output=path.join(tmp,`ar01-ui-equivalence-${before?'before':'after'}.json`);
    fs.writeFileSync(output,JSON.stringify(snapshots,null,2));
    const baseline=path.join(tmp,'ar01-ui-equivalence-before.json');
    if (!before && process.env.ADMIN_AR01_COMPARE_BASELINE === '1') {
      assert.ok(fs.existsSync(baseline),'explicit comparison requires a captured baseline');
      assert.deepEqual(snapshots,JSON.parse(fs.readFileSync(baseline,'utf8')),'before/after UI contracts');
    }
    console.log(`PASS: ${snapshots.pages.length} page/version/width projections; dictionary ${snapshots.dictionary}; dynamic i18n, dialogs, node/draft identity, cache-disabled scripts, zero 404 or uncaught exceptions.`);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
