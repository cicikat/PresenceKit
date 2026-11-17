// Actual admin assets and clickable controls with isolated synthetic APIs.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:process.env.ADMIN_TEST_BROWSER||undefined});
  try {
    const context=await browser.newContext({viewport:{width:1400,height:1000}});
    const page=await context.newPage(), errors=[], writes=[];
    let phone={enabled:false,light:true,motion:true,usage:true,revision:0};
    page.on('pageerror',error=>errors.push(error.message));
    const root=path.resolve(__dirname,'../admin');
    await page.route('**/*',route=>{
      const req=route.request(), url=new URL(req.url());
      if(url.pathname.startsWith('/static/')) {
        const file=path.resolve(root,'.'+url.pathname);
        assert.ok(file.startsWith(root+path.sep));
        if(!fs.existsSync(file))return route.fulfill({status:404,body:''});
        return route.fulfill({body:fs.readFileSync(file),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css'})[path.extname(file)]||'application/octet-stream'});
      }
      let body={};
      if(url.pathname==='/settings/minecraft')body={configured:{},runtime:{connection_state:'disconnected'},routing:{presets:[]}};
      if(url.pathname==='/settings/food-memory')body={effective:false,route:{}};
      if(url.pathname==='/video-call/presence')body={settings:{enabled:false,max_talks_per_call:1,min_gap_seconds:60,silence_seconds:60,camera_signal_interval_seconds:30},talks_total:0,gate_blocks:{}};
      if(url.pathname==='/admin/autonomy/config') {
        if(req.method()==='PATCH') {
          writes.push(req.postDataJSON());phone={...phone,...writes.at(-1).phone_ambient,revision:phone.revision+1};
        }
        body={phone_ambient:phone,enabled:true,max_steps:3,max_tools:0,max_write_tools:0};
      }
      if(url.pathname==='/observability/phone-ambient')body={configured:phone,effective:{light:false,motion:false,usage:false},reason_codes:{light:'unavailable'},sources:[]};
      return route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    });
    const cdp=await context.newCDPSession(page);
    await cdp.send('Network.enable');await cdp.send('Network.clearBrowserCache');
    await cdp.send('Network.setCacheDisabled',{cacheDisabled:true});
    await page.goto('http://round1.invalid/static/index.html');
    await page.reload({waitUntil:'networkidle'});
    await page.evaluate(()=>{document.getElementById('auth-overlay').style.display='none';document.getElementById('app').style.display='flex';goto('autonomy-settings');});
    const card=page.locator('#phone-ambient-card');
    await card.waitFor({state:'visible'});
    await page.waitForFunction(()=>document.getElementById('autonomy-phone-light').checked);
    assert.equal(await page.locator('#autonomy-phone-enabled').isChecked(),false);
    await page.locator('#autonomy-phone-enabled').check();
    await page.locator('#autonomy-phone-motion').uncheck();
    await card.locator('[data-action="saveAutonomyCard"]').click();
    await page.waitForFunction(()=>document.getElementById('autonomy-phone-state').textContent.includes('unavailable'));
    assert.deepEqual(writes,[{phone_ambient:{enabled:true,light:true,motion:false,usage:true}}]);
    await page.reload({waitUntil:'networkidle'});
    await page.evaluate(()=>{document.getElementById('auth-overlay').style.display='none';document.getElementById('app').style.display='flex';goto('autonomy-settings');});
    await page.waitForFunction(()=>document.getElementById('autonomy-phone-enabled')?.checked);
    assert.equal(await page.locator('#autonomy-phone-motion').isChecked(),false);
    const unexpected=[];
    for(const node of await page.getByText(/Cannot read properties/).all()) {
      if(await node.isVisible())unexpected.push(await node.textContent());
    }
    assert.deepEqual(unexpected,[]);
    await card.locator('summary').click();
    await card.scrollIntoViewIfNeeded();
    fs.mkdirSync('.tmp',{recursive:true});
    await page.screenshot({path:'.tmp/round1-phone-ambient-admin.png',fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('Phone ambient admin browser: default off, scoped patch, reload and effective diagnostics passed.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
