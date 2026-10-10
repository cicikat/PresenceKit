'use strict';
const $ = id => document.getElementById(id);
const initial = [
 {name:'番茄牛腩',shop:'家常晚餐',evaluation:'喜欢',quote:'番茄酸甜刚好，这个味道我喜欢。',date:'10月08日'},
 {name:'抹茶拿铁',shop:'街角咖啡店',evaluation:'不喜欢',quote:'这杯太甜了，我不喜欢。',date:'10月07日'},
 {name:'鲜虾云吞',shop:'云吞面馆',evaluation:'喜欢',quote:'今天吃了鲜虾云吞，馅很鲜，我喜欢。',date:'10月06日'}
];
let records = structuredClone(initial), savedEnabled = true;
function feedback(id,text,error=false){const node=$(id);node.className='notice '+(error?'error':'success');node.textContent=text;}
function render(){
 const term=$('query').value.trim().toLowerCase();
 const rows=records.filter(r=>(r.name+r.shop).toLowerCase().includes(term));
 $('total').textContent=records.length;$('query-state').textContent=rows.length+' 条结果';
 $('results').replaceChildren();
 if(!rows.length){const empty=document.createElement('div');empty.className='empty';empty.textContent='暂无匹配记录。尝试其他食品、店名，或清空关键词查看全部。';$('results').append(empty);return;}
 const header=document.createElement('div');header.className='result-header';
 for(const text of ['食品 / 来源','当前评价','本人原话 / 记录时间']){const cell=document.createElement('div');cell.textContent=text;header.append(cell);}
 $('results').append(header);
 for(const r of rows){
 const row=document.createElement('article');row.className='record';
 const name=document.createElement('div');name.textContent=r.name;const shop=document.createElement('small');shop.textContent=r.shop;name.append(shop);
 const evaluation=document.createElement('div');const badge=document.createElement('span');badge.className='badge'+(r.evaluation==='喜欢'?' good':'');badge.textContent=r.evaluation;evaluation.append(badge);
 const quote=document.createElement('div');quote.textContent='“'+r.quote+'”';const date=document.createElement('small');date.textContent=r.date+' · 明确评价';quote.append(date);
 row.append(name,evaluation,quote);$('results').append(row);
 }
}
$('enabled').addEventListener('change',()=>{$('dirty').textContent=$('enabled').checked===savedEnabled?'设置已保存':'有未保存的修改';$('auto-state').textContent='未保存 · '+($('enabled').checked?'开启':'关闭');});
$('save').addEventListener('click',()=>{savedEnabled=$('enabled').checked;$('dirty').textContent='设置已保存';$('auto-state').textContent=savedEnabled?'已开启':'已关闭';$('auto-state').className='badge'+(savedEnabled?' good':'');feedback('save-feedback',savedEnabled?'示例设置已保存 · 自动记录已开启':'示例设置已保存 · 停止抽取，既有清单保留');});
$('query-form').addEventListener('submit',event=>{event.preventDefault();if(!$('character').value.trim()){feedback('query-feedback','请填写角色 ID。',true);$('character').focus();return;}if($('simulate-error').checked){feedback('query-feedback','示例查询失败：服务暂时不可用。关闭“模拟查询失败”后重试；已显示结果保留。',true);return;}render();feedback('query-feedback','示例查询完成 · 结果仅来自本页演示数据');});
$('retry').addEventListener('click',()=>{if(!$('character').value.trim()){feedback('query-feedback','请填写角色 ID 后再重试。',true);$('character').focus();return;}feedback('query-feedback','示例已排队重试 · 待处理 3 条，失败 0 条（仅演示排队状态）');$('pending').textContent='3';$('failed').textContent='0';});
$('quote').addEventListener('input',()=>{$('count').textContent=$('quote').value.length;$('edit-state').textContent='正在编辑';});
$('fill').addEventListener('click',()=>{$('food-name').value='番茄牛腩';$('quote').value='今天的番茄牛腩很好吃，我喜欢这个味道。';$('evaluation').value='喜欢';$('count').textContent=$('quote').value.length;$('edit-state').textContent='正在编辑';});
$('edit-form').addEventListener('submit',event=>{
 event.preventDefault();
 for(const [id,label] of [['character','角色 ID'],['food-name','食品名称'],['quote','本人评价原话']]){if(!$(id).value.trim()){feedback('edit-feedback','请填写'+label+'，再提交更正。',true);$(id).focus();return;}}
 const name=$('food-name').value.trim();const old=records.find(r=>r.name===name);
 const updated={name,shop:old?.shop||'手动更正',evaluation:$('evaluation').value,quote:$('quote').value.trim(),date:'本次演示'};
 if(old)records[records.indexOf(old)]=updated;else records.unshift(updated);
 render();$('edit-state').textContent='更正已提交';feedback('edit-feedback','示例更正已提交 · 当前评价已更新；真实历史证据未被访问或修改。');
});
$('reset').addEventListener('click',()=>{if(!window.confirm('重置当前样机的输入和示例记录？此操作仅影响本页。'))return;records=structuredClone(initial);savedEnabled=true;$('enabled').checked=true;$('simulate-error').checked=false;$('character').value='demo-character';$('query').value='';$('food-name').value='';$('quote').value='';$('evaluation').value='喜欢';$('count').textContent='0';$('pending').textContent='2';$('failed').textContent='1';$('dirty').textContent='设置已保存';$('auto-state').textContent='已开启';$('auto-state').className='badge good';$('edit-state').textContent='正在编辑';for(const id of ['query-feedback','edit-feedback']){$(id).textContent='';$(id).className='';}feedback('save-feedback','示例已重置 · 自动记录已开启');render();});
render();