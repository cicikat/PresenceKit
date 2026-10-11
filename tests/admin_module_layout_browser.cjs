// Real static server and Chromium rendering; API reads blocked, no production writes.
const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),http=require('http'),assert=require('node:assert/strict');
(async()=>{
 const server=http.createServer((req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(!url.pathname.startsWith('/static/')){res.writeHead(403,{'Content-Type':'application/json'});return res.end('{}');}
  const file=path.join(process.cwd(),'admin',url.pathname);
  try{res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css'})[path.extname(file)]||'application/octet-stream');res.end(fs.readFileSync(file));}catch(e){res.writeHead(404);res.end();}
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/static/index.html`);
  const cdp=await page.context().newCDPSession(page);await cdp.send('Network.clearBrowserCache');await cdp.send('Network.setCacheDisabled',{cacheDisabled:true});
  await page.reload();
  await page.evaluate(()=>{document.getElementById('auth-overlay').style.display='none';document.getElementById('app').style.display='flex';AdminUI.select('new');});
  await page.evaluate(()=>goto('character-behavior'));
  for(const id of ['tools-xhs-card','tools-weather-card','hds-config-card','video-call-presence-card','ime-reception-card']){
   assert.equal(await page.locator('#'+id).count(),1);
   assert.equal(await page.locator('#page-character-behavior #'+id).count(),1);
   assert.equal(await page.locator('#'+id).evaluate(el=>Boolean(el.closest('.behavior-section-body'))),true);
  }
  await page.evaluate(()=>goto('observe-vector'));
  for(const id of ['food-module','quote-notebook-module'])assert.equal(await page.locator('#page-observe-vector #'+id).count(),1);
  assert.equal(await page.locator('#quotes-note').getAttribute('readonly'),'');
  assert.equal(await page.locator('[data-action="correctFoodRecord"],[data-action="retryFoodRecords"],[data-action="saveNotebookNote"],[data-action="deleteNotebookQuote"]').count(),0);
  // Match the record headings to ordinary collapsible cards and inspect rendered layout.
  await page.locator('[data-layout-key="memory-notebooks"] > .card-header .pk-collapse').click();
  await page.locator('[data-layout-key="memory-notebooks"] > .card-header .pk-collapse').click();
  fs.mkdirSync('.tmp/admin-module-layout',{recursive:true});
  await page.locator('[data-layout-key="memory-notebooks"]').screenshot({path:'.tmp/admin-module-layout/records.png'});
  await page.evaluate(()=>goto('client-settings'));
  assert.equal(await page.locator('#page-client-settings [data-layout-key="service-center"]').count(),1);
  assert.equal(await page.locator('#page-client-settings > details.card').count(),0);
  await page.screenshot({path:'.tmp/admin-module-layout/client.png'});
  await page.evaluate(()=>goto('autonomy-settings'));
  assert.equal(await page.locator('#self-management-panel #scheduler-config-card').count(),1);
  assert.equal(await page.locator('#autonomy-daily').locator('..').textContent().then(s=>s.includes('?')),false);
  await page.evaluate(()=>goto('feature-center'));
  assert.equal(await page.locator('#page-feature-center [data-i18n="settings_center.other_detailed_settings"],#page-feature-center [data-i18n="tools.binding_title"]').count(),0);
  await page.evaluate(()=>{AdminPageLayout.restore();AdminUI.select('legacy');});
  assert.equal(await page.locator('#page-tools #food-module').count(),1);
  assert.equal(await page.locator('#page-scheduler #hds-config-card').count(),1);
  assert.deepEqual(errors,[]);
  console.log('PASS: module ownership, readonly controls, nesting, client alignment, restore; real browser with blocked APIs');
 }finally{await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;});
