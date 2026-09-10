// Run against a local admin service with Playwright available through NODE_PATH.
// Real static assets and navigation; API requests are blocked, no credentials or writes.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const base = process.env.ADMIN_TEST_URL || 'http://127.0.0.1:8080';

(async () => {
  const browser = await chromium.launch({headless: true, executablePath: process.env.ADMIN_TEST_BROWSER || undefined});
  try {
    const context = await browser.newContext({viewport: {width: 1365, height: 900}});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin === base && (url.pathname.startsWith('/static/') || url.pathname === '/')) return route.continue();
      return route.fulfill({status: 403, contentType: 'application/json', body: '{}'});
    });
    const cdp = await context.newCDPSession(page);
    await cdp.send('Network.enable');
    await cdp.send('Network.setCacheDisabled', {cacheDisabled: true});
    await cdp.send('Network.clearBrowserCache');
    async function openShell() {
      await page.goto(base + '/static/index.html');
      await page.reload({waitUntil: 'networkidle'});
      await page.evaluate(async () => {
        document.getElementById('auth-overlay').style.display = 'none';
        document.getElementById('app').style.display = 'flex';
        restoreNavGroups();
        await goto(getRememberedPage() || 'guide');
      });
    }
    async function active(name) {
      await page.locator('#page-' + name + '.active[data-page-loaded="true"]').waitFor();
    }
    await openShell();
    await active('guide');
    assert.equal(await page.locator('#nav-back').isDisabled(), true);
    await page.locator('#nav-toggle-services').click();
    assert.equal(await page.locator('#navgroup-services').isVisible(), false);
    assert.equal(await page.locator('#nav-toggle-services').getAttribute('aria-expanded'), 'false');
    await page.reload({waitUntil: 'networkidle'});
    await page.evaluate(() => {
      document.getElementById('auth-overlay').style.display = 'none';
      document.getElementById('app').style.display = 'flex';
      restoreNavGroups();
    });
    assert.equal(await page.locator('#navgroup-services').isVisible(), false);
    await page.evaluate(() => goto('guide'));
    // Every guide shortcut and sidebar entry targets an existing fragment.
    assert.deepEqual(await page.evaluate(() => [...document.querySelectorAll('#page-guide [data-action="goto"], nav a[data-page]')]
      .map(a => JSON.parse(a.dataset.actionArgs)[0]).filter(p => !document.getElementById('page-' + p))), []);
    await page.locator('#nav-toggle-services').focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('#navgroup-services').isVisible(), true);
    await page.locator('nav a[data-page="observe-memory"]').click();
    await active('observe-memory');
    await page.locator('#obs-mem-uid').fill('example-user');
    await page.locator('nav a[data-page="guide"]').click();
    await active('guide');
    await page.locator('#nav-back').click();
    await active('observe-memory');
    assert.equal(await page.locator('#obs-mem-uid').inputValue(), 'example-user');
    await page.locator('#nav-back').click();
    await active('guide');
    assert.equal(await page.locator('#nav-back').isDisabled(), true);
    // Guide links reopen a collapsed destination group.
    await page.locator('#nav-toggle-observation').click();
    await page.locator('#page-guide [data-action-args=\'["observe-memory"]\']').click();
    await active('observe-memory');
    assert.equal(await page.locator('#navgroup-observation').isVisible(), true);
    await page.evaluate(() => goto('guide'));
    await page.evaluate(() => { document.querySelector('main').scrollTop = 700; });
    await page.evaluate(() => goto('observation-center'));
    await page.locator('#nav-back').click();
    await active('guide');
    assert.ok(await page.evaluate(() => document.querySelector('main').scrollTop) >= 650);
    const historySize = await page.evaluate(() => JSON.parse(sessionStorage.getItem('admin_page_history')).length);
    await page.selectOption('#admin-language-select', 'en');
    await page.getByRole('heading', {name: 'Admin panel guide', exact: true}).waitFor();
    assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem('admin_page_history')).length), historySize);
    await page.selectOption('#admin-language-select', 'zh-CN');
    await page.getByRole('heading', {name: '管理面板使用指南', exact: true}).waitFor();
    // Out-of-order fragment completion cannot change the remembered destination.
    await page.route('**/static/pages/observe-existence.html?*', async route => {
      await new Promise(resolve => setTimeout(resolve, 180));
      await route.continue();
    });
    await page.evaluate(async () => {
      const slow = goto('observe-existence');
      await goto('operations-center');
      await slow;
    });
    await active('operations-center');
    assert.equal(await page.evaluate(() => getRememberedPage()), 'operations-center');
    await openShell();
    await active('operations-center');
    assert.equal(await page.locator('#nav-back').isDisabled(), false);
    await page.locator('#nav-back').click();
    await active('observe-existence');
    // A failed fragment remains retryable and Back remains usable.
    await page.route('**/static/pages/observe-memory-events.html?*', route => route.fulfill({status: 503, body: 'Unavailable'}));
    await page.evaluate(() => goto('observe-memory-events'));
    await page.getByRole('alert').filter({hasText: '页面加载失败'}).waitFor();
    await page.unroute('**/static/pages/observe-memory-events.html?*');
    await page.evaluate(() => goto('observe-memory-events'));
    await active('observe-memory-events');
    // Back resumes scheduler polling without rebuilding an unsaved settings form.
    assert.deepEqual(await page.evaluate(async () => {
      const original = [loadScheduler, _startWatchStatusPoller, _stopWatchStatusPoller];
      let loads = 0, starts = 0, stops = 0;
      loadScheduler = () => { loads++; _startWatchStatusPoller(); };
      _startWatchStatusPoller = () => { starts++; };
      _stopWatchStatusPoller = () => { stops++; };
      try {
        await goto('scheduler');
        await goto('guide');
        await goBackPage();
        return {loads, starts, stops};
      } finally {
        [loadScheduler, _startWatchStatusPoller, _stopWatchStatusPoller] = original;
      }
    }), {loads: 1, starts: 2, stops: 1});
    await page.evaluate(() => goto('guide'));
    await page.evaluate(() => { document.querySelector('main').scrollTop = 0; });
    fs.mkdirSync('.tmp/admin-navigation', {recursive: true});
    await page.screenshot({path: '.tmp/admin-navigation/desktop.png'});
    await page.setViewportSize({width: 390, height: 844});
    await page.locator('#nav-menu-toggle').click();
    assert.equal(await page.locator('#nav-menu-toggle').getAttribute('aria-expanded'), 'true');
    await page.locator('nav a[data-page="observe-memory"]').click();
    await active('observe-memory');
    assert.equal(await page.locator('#nav-menu-toggle').getAttribute('aria-expanded'), 'false');
    await page.locator('#nav-back').click();
    await active('guide');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.screenshot({path: '.tmp/admin-navigation/mobile.png'});
    await page.evaluate(() => { localStorage.setItem('navGroupsCollapsed', '{broken'); });
    await openShell();
    await page.evaluate(() => logout());
    assert.equal(await page.evaluate(() => sessionStorage.getItem('admin_active_page')), null);
    assert.equal(await page.locator('#nav-back').isDisabled(), true);
    assert.deepEqual(errors, []);
    console.log('PASS: desktop/mobile navigation, keyboard, guide links, history, scroll, reload, i18n, races, retry, corrupt storage, logout; zero API access.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
