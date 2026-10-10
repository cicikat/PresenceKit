// Browser acceptance of real assets; API fixtures are explicitly synthetic.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
 const page=await browser.newPage({viewport:{width:1440,height:2200}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let fail=false,empty=false,enabled=true;const calls=[];
 await page.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url());
  if(url.pathname.startsWith('/static/')||url.pathname==='/admin/static/index.html')return route.continue();
  calls.push([req.method(),url.pathname,req.postData()]);
  let body={};
  if(url.pathname==='/settings/food-memory') {if(req.method()==='PUT')enabled=req.postDataJSON().enabled;body={enabled,effective:enabled,route:{effective_preset:'fixture',source:'test'}};}
  if(url.pathname==='/observability/food-memory'){
   await new Promise(r=>setTimeout(r,150));
   if(fail)return route.fulfill({status:503,body:'fixture unavailable'});
   body={pending:2,failed:1,total:empty?0:1,count_semantics:'测试证据：非真实总次数',items:empty?[]:[{name:'测试食品',evaluation:'like',recorded_eaten_count:1,last_evidence_at:1700000000,evaluation_quote:'测试食品我喜欢 <script>bad</script>'}],tastes:[]};
  }
  return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
 });
 // Serve fragments at their production /static/ URLs using original files.
 await page.route('**/static/**',route=>{
  const fs=require('node:fs'),path=require('node:path');const url=new URL(route.request().url());
  const file=path.join(process.cwd(),'admin',decodeURIComponent(url.pathname));
  const ext=path.extname(file);return route.fulfill({body:fs.readFileSync(file),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff2':'font/woff2'})[ext]||'application/octet-stream'});
 });
 await page.goto('http://127.0.0.1:8769/static/index.html');
 await page.evaluate(()=>{document.getElementById('auth-overlay').style.display='none';document.getElementById('app').style.display='flex';return goto('tools');});
 await page.waitForFunction(()=>document.getElementById('food-status')?.dataset.state==='success');
 assert.equal(await page.locator('#admin-ui-version').inputValue(),'legacy');
 await page.locator('#food-module > details > summary').click();
 await page.locator('#food-char').fill('fixture-character');await page.locator('#food-query').fill('测试');
 await page.locator('[data-action="loadFoodRecords"]').click();await page.waitForFunction(()=>document.getElementById('food-records').dataset.state==='success');
 await page.locator('#food-module').scrollIntoViewIfNeeded();
 await page.locator('#food-module').screenshot({path:'.tmp/admin-food-legacy.png'});
 await page.locator('#food-correct-name').fill('测试食品');await page.locator('#food-correct-quote').fill('测试食品我喜欢');
 const count=calls.length;await page.selectOption('#admin-ui-version','new');await page.waitForTimeout(200);assert.equal(calls.length,count);
 assert.equal(await page.locator('#food-enabled').count(),1);assert.equal(await page.locator('#food-module details').count(),3);
 await page.locator('#food-module details').nth(2).locator('summary').click();await page.locator('#food-module details').nth(2).locator('summary').click();assert.equal(await page.locator('#food-correct-quote').inputValue(),'测试食品我喜欢');
 await page.locator('#food-module').scrollIntoViewIfNeeded();
 await page.locator('#food-module').screenshot({path:'.tmp/admin-food-new.png'});
 await page.locator('#food-enabled').uncheck();await page.locator('[data-action="saveFoodSettings"]').click();await page.waitForFunction(()=>document.getElementById('food-status').textContent.includes('设置已保存'));assert.equal(enabled,false);
 const saves=calls.filter(c=>c[0]==='PUT').length;
 await page.evaluate(()=>Promise.all([saveFoodSettings(),saveFoodSettings()]));
 assert.equal(calls.filter(c=>c[0]==='PUT').length,saves+1);
 await page.locator('[data-action="retryFoodRecords"]').click();await page.waitForFunction(()=>document.getElementById('food-status').textContent.includes('已排队'));
 await page.locator('[data-action="correctFoodRecord"]').click();await page.waitForFunction(()=>document.getElementById('food-status').textContent.includes('更正已保存'));
 assert.equal(calls.filter(c=>c[0]==='POST'&&c[1].endsWith('/correct')).length,1);
 fail=true;await page.locator('[data-action="loadFoodRecords"]').click();await page.waitForFunction(()=>document.getElementById('food-records').dataset.state==='error');assert.match(await page.locator('#food-records').innerText(),/503/);
 fail=false;empty=true;await page.locator('[data-action="loadFoodRecords"]').click();await page.waitForFunction(()=>document.getElementById('food-records').dataset.state==='success');assert.match(await page.locator('#food-records').innerText(),/暂无记录/);
 await page.selectOption('#admin-ui-version','legacy');assert.equal(await page.locator('#food-correct-quote').inputValue(),'测试食品我喜欢');
 await page.selectOption('#admin-ui-version','new');await page.reload();assert.equal(await page.locator('#admin-ui-version').inputValue(),'new');
 await page.evaluate(()=>{document.getElementById('auth-overlay').style.display='none';document.getElementById('app').style.display='flex';return goto('tools');});
 await page.waitForFunction(()=>document.getElementById('food-status')?.dataset.state==='success');
 await page.setViewportSize({width:480,height:1900});await page.locator('#food-module').screenshot({path:'.tmp/admin-food-narrow.png'});
 assert.ok(await page.locator('#food-module').evaluate(n=>n.scrollWidth<=n.clientWidth+1));
 assert.deepEqual(errors,[]);console.log('PASS: switch persistence/no requests, single controls, drafts, settings/retry/correct, error/empty, narrow layout; synthetic APIs.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
