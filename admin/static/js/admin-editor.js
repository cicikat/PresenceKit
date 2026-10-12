// Temporary authoring tools. Export before removing this file to keep approved copy.
(() => {
  const key = 'presencekit-admin-edits-v1';
  let saved;
  try { saved = JSON.parse(localStorage.getItem(key) || '{}'); } catch (_) { saved = {}; }
  saved.text ||= {}; saved.order ||= {}; saved.cards ||= {};
  for (const name of Object.keys(saved.text)) if (saved.text[name] === '【删掉】') saved.text[name] = '';
  let enabled = false, dragged = null, scheduled = false;
  const originals = new WeakMap();
  const panel = document.createElement('div');
  panel.id = 'admin-copy-editor';
  panel.style.cssText = 'position:fixed;bottom:12px;right:12px;z-index:10000;padding:10px;background:var(--surface,#fff);border:1px solid var(--border,#aaa);border-radius:8px;max-width:360px';
  const toggle = document.createElement('button'); toggle.className = 'btn btn-ghost btn-sm';
  toggle.textContent = '编辑文案与侧边栏';
  const status = document.createElement('small'); status.style.display = 'block';
  status.textContent = '保存在当前浏览器；导出后可交给维护者写回。';
  function persist() {
    try { localStorage.setItem(key, JSON.stringify(saved)); status.textContent = '已保存到当前浏览器'; return true; }
    catch (_) { status.textContent = '保存失败，请立即导出备份'; return false; }
  }
  toggle.onclick = () => { enabled = !enabled; toggle.textContent = enabled ? '结束编辑' : '编辑文案与侧边栏'; apply(); };
  const exportButton = document.createElement('button'); exportButton.className = 'btn btn-ghost btn-sm'; exportButton.textContent = '导出修改';
  exportButton.onclick = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(saved,null,2)],{type:'application/json'}));
    const a = document.createElement('a'); a.href=url; a.download='admin-copy-edits.json'; a.click(); URL.revokeObjectURL(url);
  };
  const importButton = document.createElement('button'); importButton.className='btn btn-ghost btn-sm'; importButton.textContent='导入';
  const input = document.createElement('input'); input.type='file'; input.accept='.json'; input.hidden=true;
  importButton.onclick=()=>input.click();
  input.onchange=async()=>{
    try {
      const data=JSON.parse(await input.files[0].text());
      if (!data.text || !data.order || Object.values(data.text).some(v=>typeof v !== 'string') || Object.values(data.order).some(v=>!Array.isArray(v) || v.some(id=>typeof id !== 'string'))) throw Error();
      if (data.cards && Object.values(data.cards).some(v=>!Array.isArray(v) || v.some(id=>typeof id !== 'string'))) throw Error();
      saved={text:data.text,order:data.order,cards:data.cards || {}}; persist(); location.reload();
    } catch (_) {status.textContent='导入失败：请选择有效的修改文件';}
  };
  panel.append(toggle,exportButton,importButton,input,status); document.body.append(panel);
  function textKey(node) {
    if (window.AdminCopy) return AdminCopy.textKey(node);
    if (originals.has(node)) return originals.get(node);
    const el=node.parentElement, translated=el.closest('[data-i18n]');
    const page=el.closest('.page')?.id || 'shell';
    const scope=el.closest('[id]')?.id || page;
    const value=translated ? 'i18n:'+translated.dataset.i18n : scope+':'+node.textContent.trim();
    originals.set(node,value); return value;
  }
  function apply() {
    scheduled=false;
    window.AdminCopy?.apply(saved.text);
    const walker=document.createTreeWalker(document.getElementById('app'),NodeFilter.SHOW_TEXT);
    while(walker.nextNode()) {
      const node=walker.currentNode;
      if (!node.textContent.trim() || node.parentElement.closest('script,style,textarea,select,[contenteditable]')) continue;
      const raw=saved.text[textKey(node)];
      const value=raw === undefined ? undefined : (window.AdminCopy?.projectText(raw) ?? raw);
      if (value !== undefined && node.textContent !== value) node.textContent=value;
    }
    document.querySelectorAll('nav a[data-page]').forEach(link=>{link.draggable=enabled;});
    document.querySelectorAll('main .card').forEach(card=>{
      const heading=card.querySelector(':scope > .card-header,:scope > .admin-card-header,:scope > h2,:scope > h3,:scope > summary,:scope > details > summary');
      if(!heading) return;
      if(!card.dataset.editorCardKey) {
        const title=heading.querySelector('[data-i18n]');
        const label=title?.dataset.i18n || (heading.querySelector('h2,h3,h4') || heading).textContent.replace(/[▾▸−+]/g,'').trim();
        card.dataset.editorCardKey=card.id || card.dataset.layoutKey || label;
      }
      heading.dataset.editorDragHandle='true'; heading.draggable=enabled;
      heading.style.cursor=enabled?'grab':'';
    });
    for(const [group,ids] of Object.entries(saved.cards)) {
      const cards=[...document.querySelectorAll('main .card[data-editor-card-key]')].filter(c=>cardGroup(c)===group);
      const ordered=ids.map(id=>cards.find(c=>c.dataset.editorCardKey===id)).filter(Boolean);
      for(let i=1;i<ordered.length;i++) if(ordered[i-1].compareDocumentPosition(ordered[i]) & Node.DOCUMENT_POSITION_PRECEDING) ordered[i-1].after(ordered[i]);
    }
    for(const [group,ids] of Object.entries(saved.order)) {
      const parent=document.getElementById(group); if(!parent) continue;
      const links=ids.map(id=>[...document.querySelectorAll('nav a[data-page]')].find(a=>a.dataset.page===id)).filter(a=>a && !a.hidden);
      for(const link of links) if(link.parentElement!==parent) parent.append(link);
      for(let i=1;i<links.length;i++) if(links[i-1].parentElement===parent && links[i].parentElement===parent && links[i-1].compareDocumentPosition(links[i]) & Node.DOCUMENT_POSITION_PRECEDING) parent.insertBefore(links[i],links[i-1].nextSibling);
    }
  }
  document.addEventListener('click',event=>{
    if (!enabled || panel.contains(event.target) || !event.target.closest('#app') || event.target.closest('input,textarea,select,.pk-collapse')) return;
    const el=event.target;
    const node=[...el.childNodes].find(n=>n.nodeType===Node.TEXT_NODE && n.textContent.trim());
    if (!node) return;
    event.preventDefault(); event.stopImmediatePropagation();
    const value=window.prompt('修改显示文案（不修改配置值）',node.textContent.trim());
    if(value===null) return;
    saved.text[textKey(node)]=value; node.textContent=value; persist();
  },true);
  function cardGroup(card) {
    const parent=card.parentElement;
    if(!parent.dataset.editorGroupKey) {
      const path=[];let node=parent;
      while(node && !node.id && !node.dataset.editorCardKey) {
        path.unshift([...node.parentElement.children].indexOf(node));node=node.parentElement;
      }
      parent.dataset.editorGroupKey=(node?.id || node?.dataset.editorCardKey || 'root')+':'+path.join('/');
    }
    return (card.closest('.page')?.id || '')+':'+parent.dataset.editorGroupKey;
  }
  document.addEventListener('dragstart',event=>{
    if(!enabled) return;
    dragged=event.target.closest('nav a[data-page]') || event.target.closest('[data-editor-drag-handle]')?.closest('.card');
    if(dragged) event.dataTransfer.setData('text/plain',dragged.dataset.page || dragged.dataset.editorCardKey);
  });
  document.addEventListener('dragover',event=>{if(enabled && dragged && event.target.closest('nav a[data-page],main .card')) event.preventDefault();});
  document.addEventListener('drop',event=>{
    const isCard=dragged?.matches('.card');
    const target=event.target.closest(isCard?'main .card':'nav a[data-page]');
    if(!enabled || !dragged || !target || target===dragged) return;
    event.preventDefault();
    if(isCard) {
      if(target.parentElement!==dragged.parentElement) {status.textContent='卡片只在同一模块内排序';return;}
      const rect=target.getBoundingClientRect(); target.parentElement.insertBefore(dragged,event.clientY>rect.top+rect.height/2?target.nextSibling:target);
      saved.cards[cardGroup(target)]=[...target.parentElement.children].filter(n=>n.matches('.card')).map(n=>n.dataset.editorCardKey).filter(Boolean);
      persist();dragged=null;return;
    }
    const previous=dragged.parentElement;
    const rect=target.getBoundingClientRect(); target.parentElement.insertBefore(dragged,event.clientY>rect.top+rect.height/2?target.nextSibling:target);
    const parent=target.parentElement;
    for(const group of [previous,parent]) saved.order[group.id]=[...group.children].filter(n=>n.matches('a[data-page]')).map(n=>n.dataset.page);
    persist(); dragged=null;
  });
  document.addEventListener('dragend',()=>{dragged=null;});
  new MutationObserver(()=>{if(!scheduled){scheduled=true;requestAnimationFrame(apply);}}).observe(document.getElementById('app'),{childList:true,subtree:true,characterData:true});
  apply();
})();
