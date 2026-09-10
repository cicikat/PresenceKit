const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const nodes = {};
const writes = [];
let respond = async () => ({});
const context = vm.createContext({
  document: {getElementById: id => nodes[id], querySelectorAll: () => []},
  t: (_key, fallback, args = {}) => Object.entries(args).reduce((s, [k, v]) => s.replace(`{${k}}`, v), fallback || _key),
  escapeHtml: value => String(value ?? '').replaceAll('<', '&lt;'),
  bindPageActions: () => {}, toast: () => {},
  api: async (method, path, body) => { writes.push({method,path,body}); return respond(method,path,body); },
  loadOutputSegmentEnforce: () => {}, loadContextConfig: () => {}, loadLlmParams: () => {},
});
vm.runInContext(fs.readFileSync('admin/static/js/settings-center.js', 'utf8'), context);
const run = expression => vm.runInContext(expression, context);
const plain = value => JSON.parse(JSON.stringify(value));
(async () => {
  assert.deepEqual(plain(run("centerDestination('scheduler','enabled')")), ['scheduler','observe-autonomy']);
  assert.deepEqual(plain(run("centerDestination('autonomy','enabled')")), ['autonomy-settings','observe-autonomy']);
  assert.deepEqual(plain(run("centerDestination('tts','enabled')")), ['tts-config','call-records']);
  const pages = fs.readFileSync('admin/static/index.html','utf8');
  for (const mapping of [run('CENTER_DESTINATIONS'),run('CENTER_FLAG_DESTINATIONS')]) {
    for (const destinations of Object.values(mapping)) for (const page of destinations) {
      assert.ok(pages.includes(`data-page-fragment="${page}"`), `missing destination ${page}`);
    }
  }
  for(const [value,expected] of [[true,true],[false,false],[[],false],[['qq'],true],[undefined,false]]) {
    assert.equal(context.centerRestartRequired(value),expected);
  }
  assert.deepEqual(plain(context.centerRecordRows({tasks:{entries:[{status:'failed'}]}})),[{status:'failed'}]);
  const recordRoot = nodes['unified-records'] = {};
  nodes['record-source'] = {value:'/first'};
  nodes['record-filter'] = {value:''};
  let finishOld;
  respond = () => new Promise(resolve => {finishOld=resolve;});
  const old = context.loadUnifiedRecords();
  nodes['record-source'].value='/second';
  respond=async()=>({entries:[{caller:'fresh',ok:false,error_category:'timeout',ts:1}]});
  await context.loadUnifiedRecords();
  finishOld({entries:[{caller:'stale'}]});await old;
  assert.ok(recordRoot.innerHTML.includes('fresh'));
  assert.ok(!recordRoot.innerHTML.includes('stale'));
  assert.ok(recordRoot.innerHTML.includes('timeout'));
  const select=nodes['binding-character']={value:'first',disabled:false};
  const input={dataset:{bindingAsset:'tts_preset'},value:'',disabled:false};
  nodes['role-binding-fields']={querySelectorAll: selector => selector==='[data-binding-asset]'?[input]:[input]};
  let finishSave;
  respond=()=>new Promise(resolve=>{finishSave=resolve;});
  const save=context.saveRoleAssets();
  assert.equal(select.disabled,true);
  assert.equal(input.disabled,true);
  select.value='second';run('++roleBindingGeneration');
  finishSave({});await save;
  assert.equal(writes.at(-1).path,'/character/first/asset-bindings');
  assert.deepEqual(plain(writes.at(-1).body),{tts_preset:null});
  assert.equal(select.disabled,false);
  const checkbox={checked:true,disabled:false,dataset:{centerSource:'scheduler',centerName:'enabled'}};
  respond=async()=>{throw Error('offline');};
  await context.saveCenterSwitch(checkbox);
  assert.equal(checkbox.checked,false);
  assert.equal(checkbox.disabled,false);
  assert.equal(writes.at(-1).path,'/scheduler/config');
})().catch(error=>{console.error(error);process.exitCode=1;});
