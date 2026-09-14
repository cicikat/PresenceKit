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
  const controlsHtml = context.centerAutonomyControls({enabled:true,daily_evaluation_budget:48,min_interval_seconds:900});
  assert.ok(controlsHtml.includes('data-autonomy-field="daily_evaluation_budget"'));
  assert.ok(controlsHtml.includes('value="48"'));
  assert.ok(controlsHtml.includes('data-autonomy-field="min_interval_seconds"'));
  assert.ok(controlsHtml.includes('value="900"'));
  const autonomyInputs = [
    {dataset:{autonomyField:'daily_evaluation_budget'},value:'24',reportValidity:()=>true},
    {dataset:{autonomyField:'min_interval_seconds'},value:'1800',reportValidity:()=>true},
  ];
  const autonomyRow={querySelectorAll:()=>autonomyInputs};
  const autonomyButton={disabled:false,closest:()=>autonomyRow};
  context.loadFeatureCenter=async()=>{};
  respond=async()=>({daily_evaluation_budget:24,min_interval_seconds:1800});
  await context.saveCenterAutonomy(autonomyButton);
  assert.equal(writes.at(-1).path,'/admin/autonomy/config');
  assert.deepEqual(plain(writes.at(-1).body),{daily_evaluation_budget:24,min_interval_seconds:1800});
  const creationControls = [{disabled:false},{disabled:false}];
  const creationRoot = nodes['creation-assets'] = {textContent:'', innerHTML:'', querySelectorAll:()=>creationControls};
  nodes['creation-result'] = {textContent:''};
  respond = async () => ({
    characters:[{id:'alice',label:'Alice'}],
    lorebooks:[{id:'base',label:'圣塞西尔 / 学院'}],
    jailbreaks:[{id:'base',label:'性张力'}],
    active:{active_character:'alice',enabled_lorebooks:['base'],enabled_jailbreaks:[]}
  });
  await context.loadCreationAssets();
  assert.ok(creationRoot.innerHTML.includes('admin-settings-list'));
  assert.ok(creationRoot.innerHTML.includes('<strong>圣塞西尔 / 学院</strong>'));
  assert.ok(creationRoot.innerHTML.includes('value="base"'));
  assert.ok(!creationRoot.innerHTML.includes('>base<'));
  assert.ok(creationRoot.innerHTML.includes('id="creation-avatar"'));
  assert.ok(creationRoot.innerHTML.includes('data-action="saveCreationAssets"'));
  assert.ok(creationRoot.innerHTML.includes('data-action="uploadCreationAvatar"'));
  const checked = {checked:true,value:'base'};
  context.document.querySelectorAll = selector => selector.includes('enabled_lorebooks') ? [checked] : [];
  nodes['creation-character'] = {value:'alice'};
  respond = async () => ({});
  await context.saveCreationAssets();
  assert.equal(writes.at(-1).path,'/settings/prompt-assets');
  assert.equal(writes.at(-1).method,'PATCH');
  assert.deepEqual(plain(writes.at(-1).body),{active_character:'alice',enabled_lorebooks:['base'],enabled_jailbreaks:[]});
})().catch(error=>{console.error(error);process.exitCode=1;});
