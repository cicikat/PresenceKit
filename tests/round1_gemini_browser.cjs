// Real admin assets and controls; synthetic APIs, no production state or network.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const origin = 'http://round1.invalid';
const root = path.resolve(__dirname, '..', 'admin');
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.ADMIN_TEST_BROWSER || undefined});
  try {
    const context = await browser.newContext({viewport:{width:1440,height:1000}});
    const page = await context.newPage();
    const errors=[], writes=[];
    let presets={};
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const request=route.request(), url=new URL(request.url());
      if (url.origin===origin && url.pathname.startsWith('/static/')) {
        const filename=path.resolve(root, '.' + decodeURIComponent(url.pathname));
        assert.ok(filename.startsWith(root + path.sep));
        if (!fs.existsSync(filename)) return route.fulfill({status:404,body:''});
        return route.fulfill({body:fs.readFileSync(filename),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff2':'font/woff2'})[path.extname(filename)] || 'application/octet-stream'});
      }
      let body={};
      if (url.pathname==='/model-presets') body={presets,routing_profiles:{default:{}},active_routing:'default'};
      if (request.method()==='PUT' && url.pathname.startsWith('/model-presets/presets/')) {
        const value=request.postDataJSON(); writes.push(value);
        const name=decodeURIComponent(url.pathname.split('/').pop());
        presets[name]={...value,api_key:'***'};
        body={name,preset:presets[name]};
      }
      if (url.pathname.endsWith('/test')) body={ok:true,latency_ms:10,model:'fixture-model',api_protocol:'gemini_generate_content',request_path:'/v1beta/models/fixture-model:streamGenerateContent',reply_preview:'pong'};
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
    });
    const cdp=await context.newCDPSession(page);
    await cdp.send('Network.enable');
    await cdp.send('Network.clearBrowserCache');
    await cdp.send('Network.setCacheDisabled',{cacheDisabled:true});
    await page.goto(origin+'/static/index.html');
    await page.reload({waitUntil:'networkidle'});
    await page.evaluate(()=>{document.getElementById('auth-overlay').style.display='none';document.getElementById('app').style.display='flex';goto('model-routing');});
    await page.locator('[data-action="openCreatePresetModal"]').click();
    await page.locator('#mr-preset-name').fill('native-fixture');
    await page.locator('#preset-import-panel summary').click();
    await page.locator('#preset-import-json').fill(JSON.stringify({provider_kind:'gemini',api_protocol:'gemini_generate_content',base_url:'https://native.invalid/v1beta',api_key:'synthetic-browser-key',model:'fixture-model',force_stream:true,params:{temperature:0.3,top_k:20}}));
    await page.locator('[data-action="importPresetJson"]').click();
    assert.equal(await page.locator('#mr-preset-api-protocol').inputValue(),'gemini_generate_content');
    assert.equal(await page.locator('#mr-preset-kind').inputValue(),'gemini');
    assert.equal(await page.locator('#mr-preset-force-stream').isChecked(),true);
    await page.locator('#preset-import-json').fill('{"model":"unexpected","api_protocol":"unknown"}');
    await page.locator('[data-action="importPresetJson"]').click();
    assert.equal(await page.locator('#mr-preset-model').inputValue(),'fixture-model');
    await page.locator('[data-action="submitPresetModal"]').click();
    await page.waitForFunction(()=>!document.querySelector('#mr-preset-modal').classList.contains('open'));
    assert.equal(writes.length,1);
    assert.equal(writes[0].api_protocol,'gemini_generate_content');
    assert.equal(writes[0].force_stream,true);
    assert.deepEqual(writes[0].params,{temperature:0.3,top_k:20});
    await page.locator('[data-preset-name="native-fixture"]').filter({hasText:'编辑'}).click();
    assert.equal(await page.locator('#mr-preset-api-protocol').inputValue(),'gemini_generate_content');
    assert.equal(await page.locator('#mr-preset-api-key').inputValue(),'');
    assert.equal(await page.locator('#mr-preset-force-stream').isChecked(),true);
    await page.locator('#mr-preset-name').scrollIntoViewIfNeeded();
    fs.mkdirSync('.tmp',{recursive:true});
    await page.screenshot({path:'.tmp/round1-gemini-admin.png',fullPage:true});
    await page.locator('[data-action="closePresetModal"]').click();
    await page.locator('[data-preset-name="native-fixture"]').filter({hasText:'测试'}).click();
    await page.getByText(/连接成功.*gemini_generate_content/).waitFor();
    assert.deepEqual(errors,[]);
    console.log('Native Gemini admin browser: JSON import, explicit protocol/provider, streaming, save payload, edit and key masking passed.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
