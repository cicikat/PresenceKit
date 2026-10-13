const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const elements = new Map();
const element = id => {
  if (!elements.has(id)) elements.set(id, {value:'', style:{}, textContent:'', innerHTML:'', addEventListener:()=>{}});
  return elements.get(id);
};
let pending = [], defer = true;
const responses = {
  '/settings/setup-status': {owner:{owner_id:'saved-owner',configured:true}},
  '/settings/base-model': {base_url:'saved-url',model:'saved-model',configured:true},
  '/settings/embedding': {}, '/characters': {},
  '/model-presets': {active_routing:'main', routing_effective:{main:{probe:{effective_preset:'probe-real'},summary:{effective_preset:'summary-real'}}}},
};
const context = vm.createContext({TOKEN:'', window:{}, document:{getElementById:element},
  api: async (method,path) => {
    if (defer && method === 'GET') await new Promise(resolve => pending.push(resolve));
    return responses[path] || {};
  }, t:(_key,fallback)=>fallback, escapeHtml:String, toast:()=>{}, bindPageActions:()=>{},
});
vm.runInContext(fs.readFileSync('admin/static/js/setup.js','utf8'),context);
const release = () => {defer=false;pending.splice(0).forEach(resolve=>resolve());};
(async()=>{
  const first=context.loadSetupPage();
  element('setup-owner-id').value='typing-owner';
  element('setup-base-api-key').value='typing-key';
  release();await first;
  assert.equal(element('setup-owner-id').value,'typing-owner');
  assert.equal(element('setup-base-api-key').value,'typing-key');
  assert.match(element('setup-optional-body').innerHTML,/probe-real/);
  assert.match(element('setup-optional-body').innerHTML,/summary-real/);
  await context.saveSetupBaseModel();
  assert.equal(element('setup-owner-id').value,'typing-owner','saving model must preserve owner draft');
  defer=true;
  const old=context.loadSetupPage();
  defer=false;
  responses['/settings/base-model'].base_url='new-url';
  await context.loadSetupPage();
  responses['/settings/base-model'].base_url='stale-url';
  release();await old;
  assert.equal(element('setup-base-url').value,'new-url','old response must not overwrite newer refresh');
  console.log('setup input and refresh races passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
