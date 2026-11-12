// Exercise the shipped UI against synthetic HTTP responses. No live backend,
// credentials, mailbox, model service or external request is accessible.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const base = process.env.ADMIN_TEST_URL;

(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.ADMIN_TEST_BROWSER || undefined});
  try {
    const context = await browser.newContext({viewport:{width:1365,height:900}});
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    const errors = [];
    const writes = [];
    let needsSetup = false;
    let oauth = {configured:false, source:'none', client_secret_set:false};
    let language = 'zh';
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== base) return route.abort('blockedbyclient');
      if (url.pathname.startsWith('/static/')) return route.continue();
      let data = {};
      let status = 200;
      const method = request.method();
      const body = request.postDataJSON();
      if (method !== 'GET') writes.push({path:url.pathname, method, body});
      if (url.pathname === '/auth/whoami') {
        status = request.headers().authorization === 'Bearer fixture-admin' ? 200 : 401;
        data = {scopes:['admin']};
      } else if (url.pathname === '/settings/setup-status') {
        data = {needs_setup:needsSetup, owner:{configured:true}, embedding:{}};
      } else if (url.pathname === '/settings/prompt-language') {
        if (method === 'POST') language = body.language;
        data = {effective_language:language};
      } else if (url.pathname === '/settings/gmail/oauth/config') {
        if (method === 'PUT') oauth = {configured:true, source:'private_file',
          client_id:body.client_id, redirect_uri:body.redirect_uri, client_secret_set:true, credential_revision:'fixture'};
        data = oauth;
      } else if (url.pathname === '/settings/gmail') {
        data = {token_state:'waiting_authorization', scopes:[], poll:{enabled:false}};
      } else if (url.pathname === '/settings/base-model') {
        data = {configured:true};
      } else if (url.pathname === '/settings/optional-models') {
        data = {models:[]};
      } else if (url.pathname === '/characters') {
        data = {characters:[], active_id:'fixture_character'};
      } else {
        // Unknown API calls cannot read private state or perform writes.
        status = 403;
      }
      await route.fulfill({status, contentType:'application/json', body:JSON.stringify(data)});
    });
    async function active(name) {
      await page.locator(`#page-${name}.active[data-page-loaded="true"]`).waitFor();
    }
    await page.goto(base + '/static/index.html');
    await page.locator('#key-input').fill('invalid-fixture');
    await page.locator('[data-action="doLogin"]').click();
    await page.locator('#auth-err').filter({hasText:'密钥错误'}).waitFor();
    assert.equal(await page.locator('#auth-overlay').isVisible(), true);
    assert.equal(await page.evaluate(() => localStorage.getItem('qq_admin_key')), null);
    assert.deepEqual(writes, []);
    await page.locator('#key-input').fill('fixture-admin');
    await page.locator('[data-action="doLogin"]').click();
    await active('setup');
    await page.evaluate(() => goto('guide'));
    await active('guide');
    await page.reload({waitUntil:'networkidle'});
    await active('guide');
    needsSetup = true;
    await page.reload({waitUntil:'networkidle'});
    await active('setup');
    needsSetup = false;
    await page.locator('nav a[data-page="character-behavior"]').click();
    await active('character-behavior');
    const oauthForm = page.locator('details').filter({has: page.locator('#setup-gmail-client-id')});
    await oauthForm.locator('summary').click();
    await page.locator('#setup-gmail-oauth-status').filter({hasText:'未配置'}).waitFor();
    assert.equal(await page.locator('#setup-gmail-client-secret').getAttribute('type'), 'password');
    await page.locator('#setup-gmail-client-id').fill('123-fixture.apps.googleusercontent.com');
    await page.locator('#setup-gmail-client-secret').fill('fixture-browser-secret');
    await page.locator('#setup-gmail-redirect-uri').fill('https://example.test/settings/gmail/oauth/callback');
    await page.locator('#setup-gmail-oauth-save').click();
    await page.locator('#setup-gmail-oauth-status').filter({hasText:'已配置'}).waitFor();
    await page.waitForFunction(() => document.getElementById('setup-gmail-client-secret').value === '');
    const saved = writes.filter(write => write.path === '/settings/gmail/oauth/config');
    assert.equal(saved.length, 1);
    assert.equal(saved[0].method, 'PUT');
    assert.equal(saved[0].body.client_secret, 'fixture-browser-secret');
    assert.equal(saved[0].body.client_id, oauth.client_id);
    assert.equal(saved[0].body.redirect_uri, oauth.redirect_uri);
    assert.ok(!(await page.locator('body').innerText()).includes('fixture-browser-secret'));
    await page.selectOption('#admin-language-select', 'en');
    await active('character-behavior');
    await page.locator('#setup-gmail-oauth-status').filter({hasText:/^Configured$/}).waitFor();
    assert.equal(await page.locator('#setup-gmail-client-id').inputValue(), oauth.client_id);
    assert.equal(await page.locator('#setup-gmail-client-secret').inputValue(), '');
    await oauthForm.locator('summary').click();
    await page.getByRole('button', {name:'Save', exact:true}).and(page.locator('#setup-gmail-oauth-save')).waitFor();
    await page.locator('#setup-gmail-client-id').fill('draft.apps.googleusercontent.com');
    await page.locator('nav a[data-page="guide"]').click();
    await active('guide');
    await page.locator('#nav-back').click();
    await active('character-behavior');
    assert.equal(await page.locator('#setup-gmail-client-id').inputValue(), 'draft.apps.googleusercontent.com');
    // Discover actual save buttons in every loaded fragment, then execute i18n.
    await page.evaluate(async () => {
      for (const element of document.querySelectorAll('[data-page-fragment]')) {
        const response = await fetch(`/static/pages/${element.dataset.pageFragment}.html`);
        const host = document.createElement('div');
        host.hidden = true;
        host.dataset.testTranslations = 'true';
        const fragment = document.createElement('template');
        fragment.innerHTML = await response.text();
        for (const button of fragment.content.querySelectorAll('button')) {
          if (button.dataset.i18n === 'common.save' || button.dataset.i18n === 'settings_center.save'
              || /^(保存|Save\\b)/.test(button.textContent.trim())) {
            const copy = button.cloneNode(true);
            copy.removeAttribute('id');
            host.append(copy);
          }
        }
        document.body.append(host);
      }
    });
    for (const [lang, expected] of [['zh-CN','保存'], ['en','Save']]) {
      await page.selectOption('#admin-language-select', lang);
      await page.waitForFunction(value => document.getElementById('setup-gmail-oauth-save').textContent.trim() === value, expected);
      const buttons = await page.locator('button[data-i18n="common.save"], button[data-i18n="settings_center.save"]').allTextContents();
      assert.ok(buttons.length > 0);
      for (const text of buttons) assert.equal(text.trim(), expected);
      const missing = await page.locator('[data-test-translations] button').evaluateAll(buttons => buttons
        .filter(button => !button.dataset.i18n)
        .map(button => button.outerHTML));
      assert.deepEqual(missing, []);
      const localizedButtons = await page.locator('[data-test-translations] button').allTextContents();
      const saveLabel = lang === 'en' ? /^Save\b/i : /^保存/;
      for (const label of localizedButtons) assert.match(label.trim(), saveLabel);
    }
    await page.screenshot({path:'.tmp/admin-navigation/oauth-en.png'});
    assert.deepEqual(errors, []);
    console.log('PASS: login denial/success, refresh restore, setup redirect, config PUT, secret clearing, bilingual save buttons, draft/history/language persistence.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
