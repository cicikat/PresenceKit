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
    // Retain navigation coverage for the supported legacy layout; the companion
    // acceptance script exercises the default presentation and authentication.
    await context.addInitScript(() => localStorage.setItem('presencekit_admin_ui', 'legacy'));
    const page = await context.newPage();
    const errors = [];
    let diaryFragmentUnavailable = false;
    let diarySettingsUnavailable = false;
    let stallMinecraftFragment = false;
    let corruptToolsFragment = false;
    let unavailableToolsScript = false;
    let releaseMinecraft;
    const minecraftGate = new Promise(resolve => { releaseMinecraft = resolve; });
    let releaseDiaryRead;
    const diaryReadGate = new Promise(resolve => { releaseDiaryRead = resolve; });
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/static/js/tools.js' && unavailableToolsScript) {
        return route.fulfill({status: 503, body: 'Script unavailable fixture'});
      }
      if (url.pathname === '/static/pages/tools.html' && corruptToolsFragment) {
        return route.fulfill({contentType: 'text/html', body: '<h1>Incomplete fragment fixture</h1>'});
      }
      if (url.pathname === '/static/pages/minecraft.html' && stallMinecraftFragment) {
        return minecraftGate.then(() => route.continue()).catch(() => {});
      }
      if (url.pathname === '/static/pages/diary-config.html' && diaryFragmentUnavailable) {
        return route.fulfill({status: 503, body: 'Fixture unavailable'});
      }
      if (url.pathname === '/settings/diary' && !diarySettingsUnavailable) {
        return route.fulfill({json: {char_id: 'fixture', obsidian_path: 'fixture-notes', configured: true}});
      }
      if (url.pathname === '/settings/diary' && diarySettingsUnavailable === 'stall') {
        return diaryReadGate.then(() => route.continue()).catch(() => {});
      }
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
      const target = await page.evaluate(value => AdminPageLayout.resolve(ADMIN_PAGE_ALIASES[value] || value), name);
      await page.locator('#page-' + target + '.active[data-page-loaded="true"]').waitFor();
    }
    await openShell();
    await active('guide');
    diaryFragmentUnavailable = true;
    stallMinecraftFragment = true;
    corruptToolsFragment = true;
    await page.evaluate(() => AdminUI.select('new'));
    // Feature Center prepares the shared behavior forms before their own page
    // is visited. Entering Behavior afterwards must retain those mounted forms.
    await page.locator('nav a[data-page="feature-center"]').click();
    await page.locator('#page-character-behavior #setup-mail-enabled').waitFor({state: 'attached', timeout:1500});
    const sharedMailControl = await page.locator('#page-character-behavior #setup-mail-enabled').elementHandle();
    await page.locator('nav a[data-page="character-behavior"]').click();
    const behavior = page.locator('#page-character-behavior');
    await behavior.getByRole('heading', {name: '角色行为', exact: true}).waitFor();
    // A stalled optional fragment must not block independent settings or retry.
    await behavior.locator('#setup-mail-enabled').waitFor({state: 'attached', timeout: 1500});
    assert.equal(await sharedMailControl.evaluate(node => node === document.getElementById('setup-mail-enabled')), true);
    const retryFragment = behavior.locator('[data-behavior-load-error] button');
    await retryFragment.waitFor();
    await behavior.locator('[data-behavior-load-error]').filter({hasText: 'Missing settings section: tools'}).waitFor();
    corruptToolsFragment = false;
    diaryFragmentUnavailable = false;
    diarySettingsUnavailable = true;
    await retryFragment.click();
    await behavior.locator('[data-layout-key="behavior-reading"]').waitFor();
    const retryDiary = behavior.locator('[data-source-error="diary-config"] button');
    await retryDiary.waitFor();
    diarySettingsUnavailable = 'stall';
    await retryDiary.click();
    // Header/body reads have a deadline too; only the failed source gets Retry.
    await behavior.locator('[data-source-error="diary-config"]').filter({hasText: '请求超时'}).waitFor({timeout: 12000});
    assert.equal(await behavior.locator('#setup-mail-enabled').count(), 1);
    await behavior.locator('[data-behavior-load-error]').filter({hasText: 'Minecraft'}).waitFor({timeout: 1000});
    diarySettingsUnavailable = false;
    releaseDiaryRead();
    await behavior.locator('[data-source-error="diary-config"] button').click();
    await page.waitForFunction(() => document.getElementById('setup-diary-path')?.value === 'fixture-notes');
    await behavior.locator('#setup-diary-path').fill('draft-kept');
    stallMinecraftFragment = false;
    releaseMinecraft();
    await behavior.locator('[data-behavior-load-error] button').click();
    await behavior.locator('[data-layout-key="minecraft"]').waitFor();
    const sectionOrder = await behavior.locator(':scope > [data-layout-key]').evaluateAll(cards => cards.map(card => card.dataset.layoutKey));
    assert.ok(sectionOrder.indexOf('minecraft') < sectionOrder.indexOf('listening-player'), 'late fragments retain the settings order');
    assert.equal(await behavior.locator('#setup-diary-path').inputValue(), 'draft-kept');
    assert.equal(await behavior.locator('#setup-diary-path').count(), 1);
    await behavior.locator('#setup-diary-path').fill('fixture-notes');
    await page.evaluate(() => goto('guide'));
    await page.evaluate(() => goto('character-behavior'));
    assert.equal(await behavior.locator('#setup-diary-path').inputValue(), 'fixture-notes');
    await page.evaluate(() => { AdminUI.select('legacy'); resetPageHistory(); });
    await page.evaluate(() => goto('guide'));
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
    await page.route('**/static/pages/observe-dream.html?*', async route => {
      await new Promise(resolve => setTimeout(resolve, 180));
      await route.continue();
    });
    await page.evaluate(async () => {
      const slow = goto('observe-dream');
      await goto('operations-center');
      await slow;
    });
    await active('operations-center');
    assert.equal(await page.evaluate(() => getRememberedPage()), 'status');
    await openShell();
    await active('operations-center');
    assert.equal(await page.locator('#nav-back').isDisabled(), false);
    await page.locator('#nav-back').click();
    await active('observe-dream');
    // A failed fragment remains retryable and Back remains usable.
    await page.route('**/static/pages/observe-memory-events.html?*', route => route.fulfill({status: 503, body: 'Unavailable'}));
    await page.evaluate(() => goto('observe-memory-events'));
    await page.getByRole('alert').filter({hasText: '页面加载失败'}).waitFor();
    await page.unroute('**/static/pages/observe-memory-events.html?*');
    await page.evaluate(() => goto('observe-memory-events'));
    await active('observe-memory-events');
    // Back preserves the scheduler form after the obsolete watch poller was retired.
    assert.deepEqual(await page.evaluate(async () => {
      const original = loadScheduler;
      let loads = 0;
      loadScheduler = () => { loads++; };
      try {
        await goto('scheduler');
        document.getElementById('sc-gap-hours').value = '2.5';
        await goto('guide');
        await goBackPage();
        return {loads, draft:document.getElementById('sc-gap-hours').value};
      } finally {
        loadScheduler = original;
      }
    }), {loads: 1, draft:'2.5'});
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
    unavailableToolsScript = true;
    await openShell();
    // A missing unrelated JS module cannot prevent Guide navigation. Entering
    // the affected page must surface an actionable error instead of rejecting goto.
    await page.locator('#nav-menu-toggle').click();
    await page.locator('nav a[data-page="tools"]').click();
    await page.locator('#page-tools > [data-page-data-error] button').waitFor();
    await page.locator('#nav-menu-toggle').click();
    await page.locator('nav a[data-page="guide"]').click();
    await active('guide');
    await page.locator('#admin-ui-version').selectOption('new');
    await page.locator('#nav-menu-toggle').click();
    await page.locator('nav a[data-page="character-behavior"]').click();
    await page.locator('#page-character-behavior #setup-mail-enabled').waitFor({state: 'attached', timeout:1500});
    await page.locator('#page-character-behavior [data-behavior-load-error]').filter({hasText: 'loadToolsPage'}).waitFor();
    await page.evaluate(() => logout());
    assert.equal(await page.evaluate(() => sessionStorage.getItem('admin_active_page')), null);
    assert.equal(await page.locator('#nav-back').isDisabled(), true);
    assert.deepEqual(errors, []);
    console.log('PASS: desktop/mobile navigation, keyboard, guide links, history, scroll, reload, i18n, races, retry, corrupt storage, logout; zero API access.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
