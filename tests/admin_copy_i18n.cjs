const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let language = 'en', writes = 0;
const element = {closest: selector => selector === '[data-i18n]' ? {dataset:{i18n:'nav.page.tools'}} : null, matches:()=>false};
let text = 'Tools';
const node = {parentElement:element, get textContent(){return text;},set textContent(value){writes++;text=value;}};
const context = vm.createContext({
  window:{AdminI18n:{translateUiText:value=>language === 'en' && value === '工具状态' ? 'Tool status' : value}},
  document:{getElementById:()=>({}), createTreeWalker:()=>{let done=false;return {currentNode:node,nextNode:()=>done ? false : (done=true)};}},
  NodeFilter:{SHOW_TEXT:4}, WeakMap, MutationObserver:class {observe(){}}, requestAnimationFrame:()=>{},
});
context.window.window=context.window;
vm.runInContext(fs.readFileSync('admin/static/js/admin-copy.js','utf8').replaceAll('AdminCopyDefaults', 'window.AdminCopyDefaults'),context);
assert.equal(text,'Tool status');
context.window.AdminCopy.apply();
assert.equal(writes,1,'repeated observation must not oscillate between languages');
language='zh-CN';context.window.AdminCopy.apply();assert.equal(text,'工具状态');
context.window.AdminCopy.apply({'i18n:nav.page.tools':'Custom label'});assert.equal(text,'Custom label');
console.log('admin copy locale projection passed');
