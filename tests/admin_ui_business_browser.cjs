// Actual shared handlers and assets, synthetic API responses. Never reaches production.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.join(__dirname,'..','admin','static');
const output=path.join(__dirname,'..','.tmp','admin-all');
const origin='http://127.0.0.1:8769';
(async()=>{
  fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({headless:true});
  try {
    const payloads={};
    for (const version of ['legacy','new']) {
      const context=await browser.newContext({viewport:{width:1440,height:1100}});
      await context.addInitScript(version=>localStorage.setItem('presencekit_admin_ui',version),version);
      const page=await context.newPage();const errors=[],writes=[];let calls=0,denyProxy=false;
      page.on('pageerror',e=>errors.push(e.message));
      const presets={};
      await page.route('**/*',route=>{
        const req=route.request(),url=new URL(req.url()),endpoint=url.pathname;
        if(url.origin===origin&&endpoint.startsWith('/static/')) {
          const file=path.resolve(root,decodeURIComponent(endpoint.slice(8)));assert.ok(file.startsWith(root+path.sep));
          return route.fulfill({body:fs.readFileSync(file),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff2':'font/woff2'})[path.extname(file)]||'application/octet-stream'});
        }
        calls++;
        if(req.method()!=='GET')writes.push({method:req.method(),path:endpoint,body:req.postDataJSON()});
        let data;
        if(endpoint==='/proxy') {
          if(denyProxy)return route.fulfill({status:403,body:'fixture-scope-denied'});
          data={enabled:false,http:'',https:'',model_connection_mode:'follow_global'};
        }
        if(endpoint==='/image-presets')data={presets,routes:{},purposes:[]};
        if(endpoint.startsWith('/image-presets/presets/')) {presets[decodeURIComponent(endpoint.split('/').at(-1))]=req.postDataJSON();data={};}
        if(endpoint==='/vision-params')data={enabled:false,provider:'custom',base_url:'',model:''};
        if(endpoint==='/image-recognition')data={mode:'vision',provider:'glm',api_protocol:'glm_layout_parsing',model:'fixture-ocr',endpoint_url:'',base_url:''};
        if(endpoint==='/settings/mcp')data={enabled:false,servers:[]};
        if(endpoint==='/settings/mcp/test')data={tools:[{name:'fixture_read',description:'Test fixture only',inputSchema:{type:'object',properties:{}}}]};
        if(endpoint==='/settings/mcp/import')data={reload_status:'reloaded'};
        if(endpoint==='/user-data/assets')data={categories:[{id:'sticker',group:'stickers',scope:'character',upload_fields:['file','logical_id','char_id','emotion'],extensions:['.png'],max_bytes:10000}],assets:[],bindings:{}};
        if(endpoint==='/player/state')data={music_control_enabled:false,session:{generation:1,session_id:'fixture-session',revision:1,host_online:false}};
        if(endpoint==='/player/library')data={items:[]};
        if(endpoint.startsWith('/observe/prompt-layers/'))data={n:0,total_snapshots:1,snapshot:{captured_at:'2026-01-01T12:00:00',token_estimate:21000,origin:{origin:'proactive',trigger_name:'fixture',seed_prompt:'Long fixture '.repeat(100)},removed_layers:['fixture'],layers:[]}};
        if(data===undefined)return route.fulfill({status:403,contentType:'application/json',body:'{}'});
        return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
      });
      await page.goto(origin+'/static/index.html');
      await page.evaluate(()=>{document.getElementById('auth-overlay').style.display='none';document.getElementById('app').style.display='flex';});
      async function mount(name,loader) {
        await page.evaluate(async ({name,loader})=>{
          await loadPageFragment(name);document.querySelectorAll('.page.active').forEach(n=>n.classList.remove('active'));
          const el=document.getElementById('page-'+name);el.classList.add('active');
          if(loader)await window[loader]();
          decorateSettingsPanels(name,el);AdminUI.apply();
          el.querySelectorAll('details').forEach(n=>n.open=true);closeSidebar();document.querySelector('main').scrollTop=0;
        },{name,loader});
      }
      async function checkSwitchDraft(selector,value) {
        const before=calls;
        await page.selectOption('#admin-ui-version',version==='new'?'legacy':'new');
        await page.selectOption('#admin-ui-version',version);
        assert.equal(calls,before);
        assert.equal(await page.locator(selector).inputValue(),value);
      }
      await mount('network-config','loadProxy');await page.evaluate(()=>_bindProxyControls());
      await page.locator('#proxy-http').fill('http://proxy.example.test:8080');
      await page.locator('#proxy-model-mode').selectOption('auto');
      await checkSwitchDraft('#proxy-http','http://proxy.example.test:8080');
      await page.locator('[data-action="saveProxy"]').click();
      await page.waitForFunction(()=>document.getElementById('toast').textContent.includes('已保存'));
      assert.equal(writes.filter(w=>w.path==='/proxy').length,1);
      assert.equal(writes.at(-1).body.model_connection_mode,'auto');
      denyProxy=true;await page.locator('[data-action="saveProxy"]').click();
      await page.waitForFunction(()=>document.getElementById('toast').textContent.includes('403'));
      denyProxy=false;
      await mount('model-routing','loadImageConnections');
      await page.locator('[data-action="openCreateImagePreset"]').click();
      await page.locator('#image-preset-name').fill('fixture_connection');
      await page.locator('#vision-model-select').fill('fixture/model:version');
      await page.locator('#vision-base-url').fill('https://api.example.test/v1');
      await checkSwitchDraft('#vision-model-select','fixture/model:version');
      assert.equal(await page.locator('#image-ocr-fields').isVisible(),false);
      await page.locator('[data-action="saveImagePreset"]').click();
      await page.waitForFunction(()=>document.getElementById('vision-connections-body').textContent.includes('fixture_connection'));
      assert.equal(writes.filter(w=>w.path==='/image-presets/presets/fixture_connection').length,1);
      if(version==='new')await page.screenshot({path:path.join(output,'model-routing-business.png')});
      await mount('mcp','loadMcpPage');
      await page.locator('#mcp-import-name').fill('fixture_server');
      await page.locator('#mcp-import-url').fill('https://mcp.example.test/mcp');
      await page.locator('[data-action="testMcpImport"]').click();
      await page.waitForFunction(()=>!document.getElementById('mcp-import-save').disabled);
      await page.locator('[data-mcp-import-tool="fixture_read"]').check();
      await checkSwitchDraft('#mcp-import-name','fixture_server');
      await page.locator('#mcp-import-save').click();
      await page.waitForFunction(()=>document.getElementById('mcp-import-save').disabled);
      assert.equal(writes.filter(w=>w.path==='/settings/mcp/import').length,1);
      assert.deepEqual(writes.at(-1).body.allow_tools,['fixture_read']);
      await mount('user-data','loadUserDataPage');
      await page.locator('#user-data-upload-logical_id').fill('fixture-asset');
      await checkSwitchDraft('#user-data-upload-logical_id','fixture-asset');
      const field=await page.locator('#user-data-upload-logical_id').evaluate(n=>getComputedStyle(n).backgroundColor);
      assert.equal(field,version==='new'?'rgb(243, 241, 247)':'rgb(15, 17, 23)');
      if(version==='new')await page.screenshot({path:path.join(output,'user-data-business.png')});
      await mount('listening-player','refreshListeningPlayer');
      const audio=await page.locator('#listening-audio').elementHandle();
      const count=calls;
      await page.selectOption('#admin-ui-version',version==='new'?'legacy':'new');await page.selectOption('#admin-ui-version',version);
      assert.equal(calls,count);
      assert.equal(await audio.evaluate(n=>n===document.getElementById('listening-audio')),true);
      await mount('observe-prompt');
      await page.locator('#obs-prompt-uid').fill('fixture-owner');
      await page.evaluate(()=>loadObservePrompt());
      await page.waitForTimeout(100);
      assert.equal(await page.locator('#obs-prompt-summary-card').isVisible(),true);
      const signal=page.locator('#obs-prompt-summary strong');
      const original=await signal.getAttribute('style');
      if(version==='new') {
        assert.equal(await signal.getAttribute('data-pk-tone'),'error');
        const fold=page.locator('#obs-recall-card .pk-collapse');
        await fold.focus();await page.keyboard.press('Enter');
        await page.waitForFunction(()=>document.querySelector('#obs-recall-card .pk-collapse').getAttribute('aria-expanded')==='false');
        assert.equal(await fold.getAttribute('aria-expanded'),'false');
        await page.evaluate(()=>loadObservePrompt());await page.waitForTimeout(100);
        assert.equal(await fold.getAttribute('aria-expanded'),'false');
        await fold.click();
        await page.screenshot({path:path.join(output,'observe-prompt-business.png')});
        await page.selectOption('#admin-ui-version','legacy');
        assert.equal(await page.locator('#obs-prompt-summary strong').getAttribute('style'),original);
        assert.equal(await page.locator('[data-pk-tone]').count(),0);
        await page.selectOption('#admin-ui-version','new');
      }
      await page.setViewportSize({width:480,height:900});await page.waitForTimeout(200);
      if(version==='new') {
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
        await page.screenshot({path:path.join(output,'observe-prompt-business-narrow.png')});
      }
      assert.deepEqual(errors,[]);
      payloads[version]=writes;
      await context.close();
    }
    assert.deepEqual(payloads.new,payloads.legacy);
    console.log('PASS: shared proxy/image/MCP payload parity and single writes, scope-denied errors, dynamic assets/drafts, hidden OCR, stable audio; synthetic APIs.');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
