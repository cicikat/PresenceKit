// Real shipped components, synthetic actions and private-network-denied fixture.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'../admin/static');
(async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage(); const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(!url.pathname.startsWith('/static/')) return route.fulfill({status:403,body:'{}'});
      const file=path.resolve(root,decodeURIComponent(url.pathname.slice(8)));
      assert.ok(file.startsWith(root+path.sep));
      return route.fulfill({body:fs.readFileSync(file),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff2':'font/woff2'})[path.extname(file)]||'application/octet-stream'});
    });
    await page.goto('http://127.0.0.1:8769/static/index.html');
    const samples=[];
    for(const width of [1440,1024,768,480]) {
      await page.setViewportSize({width,height:1000});
      for(const theme of ['day','night']) {
        samples.push(await page.evaluate(async ({width,theme})=>{
          document.documentElement.dataset.adminTheme=theme;
          const result=[];
          for(const id of ['model-routing','tools','character','observe-prompt','dream-settings','setup']) {
            const root=await loadPageFragment(id);
            const button=root.querySelector('.btn'),field=root.querySelector('input:not([type=checkbox]),select,textarea');
            const read=node=>{if(!node)return null;const s=getComputedStyle(node);return {color:s.color,background:s.backgroundColor,font:s.fontFamily,radius:s.borderRadius,minHeight:s.minHeight};};
            result.push({id,button:read(button),field:read(field)});
          }
          return {width,theme,result};
        },{width,theme}));
      }
    }
    fs.mkdirSync(path.resolve(__dirname,'../.tmp/ar03'),{recursive:true});
    fs.writeFileSync(path.resolve(__dirname,'../.tmp/ar03/component-styles.json'),JSON.stringify(samples,null,2));
    await page.evaluate(()=>delete document.documentElement.dataset.adminTheme);
    await page.evaluate(()=>{
      document.getElementById('auth-overlay').style.display='none';document.getElementById('app').style.display='flex';
      const host=document.createElement('section');host.id='ar03-fixture';host.className='page active';
      host.innerHTML='<div class="card"><h2>Fixture</h2><label class="field"><span>Field</span><input value="draft"></label><button data-action="ar03Save" class="btn">Save</button><p style="color:rgb(239,68,68)">Error</p></div><div class="modal"><div class="modal-box"><input><button>Close</button></div></div>';
      document.querySelector('main').append(host);window.ar03Saves=0;window.ar03Save=()=>window.ar03Saves++;
      for(let i=0;i<5;i++){AdminActions.enhance(host);AdminPresentation.enhance(host);}
    });
    const save=page.locator('#ar03-fixture [data-action="ar03Save"]');
    await save.click(); assert.equal(await page.evaluate(()=>window.ar03Saves),1);
    assert.equal(await page.locator('#ar03-fixture .pk-collapse').count(),1);
    await page.locator('#ar03-fixture .pk-collapse').click();
    assert.equal(await page.locator('#ar03-fixture .card').getAttribute('data-pk-collapsed'),'true');
    await page.locator('#ar03-fixture .pk-collapse').click();
    await page.evaluate(()=>AdminActions.dispose(document.getElementById('ar03-fixture')));
    await save.click();assert.equal(await page.evaluate(()=>window.ar03Saves),1);
    await page.evaluate(()=>AdminActions.enhance(document.getElementById('ar03-fixture')));
    await save.click();assert.equal(await page.evaluate(()=>window.ar03Saves),2);
    // Fault injection demonstrates that the single-write oracle catches a second listener.
    await page.evaluate(()=>document.querySelector('#ar03-fixture [data-action]').addEventListener('click',window.ar03Save));
    await save.click();assert.equal(await page.evaluate(()=>window.ar03Saves),4,'injected duplicate must be observable');
    assert.throws(()=>assert.equal(4-2,1));
    await page.evaluate(()=>{
      const button=document.querySelector('#ar03-fixture [data-action]');button.removeEventListener('click',window.ar03Save);button.focus();
      const modal=document.querySelector('#ar03-fixture .modal');window.ar03Closes=0;
      const close=()=>{window.ar03Closes++;AdminModal.close(modal);};
      AdminModal.open(modal,close);AdminModal.open(modal,close);
    });
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.locator('#ar03-fixture .modal button').evaluate(node=>node===document.activeElement),true);
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(()=>window.ar03Closes),1);
    assert.equal(await save.evaluate(node=>node===document.activeElement),true);
    await page.evaluate(()=>AdminPresentation.dispose(document.getElementById('ar03-fixture')));
    assert.equal(await page.locator('#ar03-fixture .pk-collapse').count(),0);
    assert.equal(await page.locator('#ar03-fixture input').first().inputValue(),'draft');
    assert.deepEqual(errors,[]);
    await page.close();
    console.log('PASS: repeated enhance/dispose/remount, single action and injected duplicate detection, card identity/draft, modal Tab/ESC/focus.');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
