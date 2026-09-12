// Real assets and browser interactions; all API responses are synthetic.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const base = process.env.ADMIN_TEST_URL || 'http://127.0.0.1:8769';
(async()=>{
  const browser = await chromium.launch({headless:true, executablePath:process.env.ADMIN_TEST_BROWSER || undefined});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    const page = await context.newPage();
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    let blocked=true, fail=false; const writes=[];
    await page.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(url.origin===base && url.pathname.startsWith('/static/'))return route.continue();
      if(route.request().method()!=='GET')writes.push(url.pathname);
      let body={};
      if(url.pathname==='/admin/control-center/effective-state') {
        if(fail)return route.fulfill({status:503,contentType:'application/json',body:'{}'});
        body={features:[{id:'mcp',configured_value:true,effective_value:!blocked,runtime_status:blocked?'unavailable':'enabled',blocking_reason:blocked?'no_connected_mcp_server':null,runtime_consumer:'core.mcp_client.server_runtime',override_source:'config',edit_page:'mcp'}, {id:'model_routing',configured_value:'default',effective_value:'default',runtime_status:'enabled',runtime_consumer:'core.model_registry.resolve_routing_info',edit_page:'model-routing'}, {id:'fixture',configured_value:null,effective_value:null,runtime_status:null}]};
      }
      if(url.pathname==='/model-presets')body={presets:{},routing_profiles:{default:{chat:'fixture'}},active_routing:'default'};
      if(url.pathname==='/scheduler/config')body={enabled:true,presence_nag_minutes:60,global_proactive_min_gap_hours:1.5};
      if(url.pathname==='/scheduler/status')body={triggers:{}};
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
    });
    const cdp=await context.newCDPSession(page);
    await cdp.send('Network.enable'); await cdp.send('Network.clearBrowserCache'); await cdp.send('Network.setCacheDisabled',{cacheDisabled:true});
    await page.goto(base+'/static/index.html'); await page.reload({waitUntil:'networkidle'});
    await page.evaluate(()=>{document.getElementById('auth-overlay').style.display='none';document.getElementById('app').style.display='flex';});
    await page.evaluate(()=>goto('observation-center'));
    await page.locator('.chain-table').waitFor();
    assert.equal(await page.locator('.configuration-notice').isVisible(),true);
    assert.match(await page.locator('.chain-table').innerText(),/core.mcp_client.server_runtime/);
    assert.equal(await page.locator('.chain-table tbody tr').last().locator('td').nth(1).innerText(),'—');
    await page.screenshot({path:'.tmp/admin-design-chains.png',fullPage:true});
    blocked=false;
    await page.locator('.chain-overview .settings-footer button').click();
    await page.waitForFunction(()=>!document.querySelector('.configuration-notice'));
    fail=true; await page.locator('.chain-overview .settings-footer button').click();
    await page.waitForFunction(()=>document.querySelector('.chain-content').textContent.includes('读取失败'));
    assert.equal(await page.locator('.chain-table').count(),0); fail=false;
    await page.evaluate(()=>goto('scheduler'));
    const details=page.locator('#page-scheduler details').filter({has:page.locator('[data-action="saveSchedulerConfig"]')});
    await details.waitFor(); assert.equal(await details.getAttribute('open'),null);
    await page.locator('#page-scheduler .settings-search').fill('saveSchedulerConfig');
    await page.waitForFunction(()=>document.querySelector('#page-scheduler details.settings-disclosure').open);
    await page.locator('#page-scheduler .settings-search').fill('');
    await page.locator('#sc-presence-minutes').fill('77');
    await details.locator('summary').click(); await details.locator('summary').click();
    assert.equal(await page.locator('#sc-presence-minutes').inputValue(),'77');
    await page.screenshot({path:'.tmp/admin-design-settings.png',fullPage:true});
    await page.evaluate(()=>goto('model-routing'));
    await page.locator('[data-action="openCreatePresetModal"]').click();
    assert.equal(await page.locator('#preset-import-panel').getAttribute('open'),null);
    await page.locator('#preset-import-panel summary').click();
    await page.locator('#preset-import-json').fill(JSON.stringify({baseURL:'https://example.test/v1',apiKey:'fixture-key',model:'fixture-model',params:{temperature:0.4,stream:true}}));
    await page.locator('[data-action="importPresetJson"]').click();
    assert.equal(await page.locator('#mr-preset-model').inputValue(),'fixture-model');
    assert.equal(await page.locator('#mr-preset-api-key').inputValue(),'fixture-key');
    assert.equal(await page.locator('#preset-import-json').inputValue(),'');
    assert.deepEqual(await page.evaluate(()=>readKeyValueEditor('mr-preset-params')),{temperature:0.4,stream:true});
    const count=writes.length;
    for(const input of ['[]','{"model":"changed","params":{"nested":{}}}','{"model":"changed","api_protocol":"invalid"}','{"base_url":"javascript:alert(1)"}']) {
      await page.locator('#preset-import-json').fill(input);await page.locator('[data-action="importPresetJson"]').click();
      assert.equal(await page.locator('#mr-preset-model').inputValue(),'fixture-model');
      assert.match(await page.locator('#preset-import-result').innerText(),/表单未修改/);
    }
    assert.equal(writes.length,count);
    await page.locator('[data-action="closePresetModal"]').click();
    assert.equal(await page.locator('#preset-import-json').inputValue(),'');
    await page.setViewportSize({width:390,height:844});
    await page.evaluate(()=>goto('observation-center'));await page.locator('.chain-table').waitFor();
    await page.waitForFunction(()=>document.querySelector('nav').getBoundingClientRect().right <= 0);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.screenshot({path:'.tmp/admin-design-mobile.png',fullPage:true});
    await page.locator('#admin-language-select').selectOption('en');
    await page.getByText('Chain state · locate configuration blockers',{exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    console.log('Admin design browser checks passed: collapse, import validation, chain blockers, refresh, English, narrow screen.');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
