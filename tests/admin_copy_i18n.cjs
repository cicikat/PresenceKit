const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let language = 'en', writes = 0;
const element = {closest: selector => selector === '[data-i18n]' ? {dataset:{i18n:'nav.page.tools'}} : null, matches:()=>false};
let text = 'Tools';
const root = {isConnected:true,nodeType:1};
const node = {parentElement:element,parentNode:root,isConnected:true,nodeType:3,get textContent(){return text;},set textContent(value){writes++;text=value;}};
const context = vm.createContext({
  window:{AdminI18n:{translateUiText:value=>language === 'en' && value === '工具状态' ? 'Tool status' : value}},
  document:{getElementById:()=>root, createTreeWalker:()=>{let done=false;return {nextNode:()=>done ? null : (done=true,node)};}},
  Node:{TEXT_NODE:3}, NodeFilter:{SHOW_TEXT:4}, WeakMap, MutationObserver:class {observe(){} disconnect(){}}, requestAnimationFrame:()=>{},
});
context.window.window=context.window;
// Load the actual shared observer dependency. The DOM/observer boundary stays
// synthetic; the copy projection and scheduling integration remain production code.
const core=fs.readFileSync('admin/static/js/core.js','utf8');
vm.runInContext(core.slice(core.indexOf('function observeAdminChanges('),core.indexOf('\nconst ADMIN_PAGE_ALIASES')),context);
vm.runInContext(fs.readFileSync('admin/static/js/admin-copy.js','utf8').replaceAll('AdminCopyDefaults', 'window.AdminCopyDefaults'),context);
assert.equal(text,'Tool status');
context.window.AdminCopy.apply();
assert.equal(writes,1,'repeated observation must not oscillate between languages');
language='zh-CN';context.window.AdminCopy.apply();assert.equal(text,'工具状态');
context.window.AdminCopy.apply({'i18n:nav.page.tools':'Custom label'});assert.equal(text,'Custom label');
console.log('admin copy locale projection passed');
