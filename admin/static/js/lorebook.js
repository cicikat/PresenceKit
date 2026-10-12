let _loreCharId = null;
let _jbCharId = null;
function ownedEntryUrl(path, charId) {
  if (!charId) throw new Error('请先加载角色条目');
  return path + '?char_id=' + encodeURIComponent(charId);
}
let _loreEntries = [];
let _editingLoreIdx = null;

async function loadLorebook() {
  const el = document.getElementById('lore-table-body');
  el.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const data = await api('GET', '/lorebook');
    _loreCharId = data.char_id;
    _loreEntries = data.entries || [];
    renderLorebook();
  } catch(e) {
    el.innerHTML = `<div class="empty">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

function renderLorebook() {
  const el = document.getElementById('lore-table-body');
  if (!_loreEntries.length) {
    el.innerHTML = '<div class="empty">暂无条目，点击「新增」添加第一条</div>';
    return;
  }
  const sorted = [..._loreEntries].sort((a, b) => (a.insertion_order ?? 100) - (b.insertion_order ?? 100));
  const rows = sorted.map((e, renderIdx) => {
    const eid = e.id || renderIdx;
    const kws = (e.keyword || []).join(', ');
    const badge = e.enabled
      ? '<span class="badge badge-success">启用</span>'
      : '<span class="badge badge-danger">禁用</span>';
    const regexBadge = e.regex
      ? '<span class="badge badge-warn" style="font-size:10px">正则</span>'
      : '';
    const contentId = `lore-content-${renderIdx}`;
    const shortContent = (e.content || '').length > 60
      ? escapeHtml((e.content || '').slice(0, 60)) + '…'
      : escapeHtml(e.content || '');
    const fullContent = escapeHtml(e.content || '');
    return `<tr>
      <td>${badge}</td>
      <td style="text-align:center;color:var(--muted);font-size:12px">${e.insertion_order ?? 100}</td>
      <td style="font-size:12px;color:var(--accent)"><span class="i18n-raw">${escapeHtml(kws)}</span> ${regexBadge}</td>
      <td style="font-size:12px;max-width:280px">
        <span id="${contentId}-short" class="i18n-raw" style="white-space:pre-wrap;cursor:pointer" onclick="toggleLoreContent(${renderIdx})" title="点击展开">${shortContent}</span>
        <span id="${contentId}-full" style="white-space:pre-wrap;display:none"><span class="i18n-raw">${fullContent}</span><br><a href="#" style="font-size:11px;color:var(--muted)" onclick="toggleLoreContent(${renderIdx});return false;">收起</a></span>
      </td>
      <td style="white-space:nowrap">
        <button class="btn btn-ghost btn-sm" onclick="openLoreModal('${eid}')">编辑</button>
        <button class="btn btn-danger btn-sm" onclick="deleteLoreEntry('${eid}')">删除</button>
      </td>
    </tr>`;
  }).join('');
  el.innerHTML = `<div class="tbl-wrap"><table>
    <thead><tr><th>状态</th><th style="width:50px">顺序</th><th>关键词</th><th>内容</th><th style="width:120px">操作</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function toggleLoreContent(i) {
  const short = document.getElementById(`lore-content-${i}-short`);
  const full = document.getElementById(`lore-content-${i}-full`);
  if (!short || !full) return;
  const isExpanded = full.style.display !== 'none';
  short.style.display = isExpanded ? '' : 'none';
  full.style.display = isExpanded ? 'none' : '';
}

async function exportLorebook() {
  try {
    const resp = await fetch(ownedEntryUrl('/lorebook/export/json', _loreCharId), {
      headers: { 'Authorization': `Bearer ${localStorage.getItem('qq_admin_key')||''}` }
    });
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'lorebook.json';
    a.click();
    URL.revokeObjectURL(url);
  } catch(e) {
    toast('导出失败：' + e.message, 'err');
  }
}

async function importLorebookJson(file) {
  const form = new FormData();
  form.append('file', file);
  try {
    const resp = await fetch(ownedEntryUrl('/lorebook/import/json', _loreCharId), {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${localStorage.getItem('qq_admin_key')||''}` },
      body: form,
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(JSON.stringify(data.detail));
    toast(data.message, 'ok');
    loadLorebook();
  } catch(e) { toast('导入失败：' + e.message, 'err'); }
}

function openLoreModal(eid) {
  _editingLoreIdx = eid;  // null = new, string = existing id
  const isNew = eid === null;
  document.getElementById('lore-modal-title').textContent = isNew ? '新增世界书条目' : '编辑世界书条目';
  if (!isNew) {
    const e = _loreEntries.find(x => x.id === eid);
    if (!e) return;
    document.getElementById('lore-keywords').value          = (e.keyword || []).join(', ');
    document.getElementById('lore-content').value            = e.content || '';
    document.getElementById('lore-enabled').checked          = e.enabled !== false;
    document.getElementById('lore-regex').checked            = !!e.regex;
    document.getElementById('lore-insertion-order').value   = e.insertion_order ?? 100;
  } else {
    document.getElementById('lore-keywords').value          = '';
    document.getElementById('lore-content').value            = '';
    document.getElementById('lore-enabled').checked          = true;
    document.getElementById('lore-regex').checked            = false;
    document.getElementById('lore-insertion-order').value   = 100;
  }
  document.getElementById('lore-modal').classList.add('open');
}

function closeLoreModal() {
  document.getElementById('lore-modal').classList.remove('open');
}

async function saveLoreEntry() {
  const kwRaw   = document.getElementById('lore-keywords').value.trim();
  const content = document.getElementById('lore-content').value.trim();
  const enabled = document.getElementById('lore-enabled').checked;
  const regex   = document.getElementById('lore-regex').checked;
  const insertion_order = parseInt(document.getElementById('lore-insertion-order').value) || 100;
  if (!kwRaw) { toast('关键词不能为空', 'err'); return; }
  if (!content) { toast('内容不能为空', 'err'); return; }

  const keyword = kwRaw.split(',').map(s => s.trim()).filter(Boolean);
  const payload = { keyword, content, enabled, regex, insertion_order };

  try {
    if (_editingLoreIdx === null) {
      await api('POST', ownedEntryUrl('/lorebook', _loreCharId), payload);
    } else {
      await api('PUT', ownedEntryUrl(`/lorebook/${_editingLoreIdx}`, _loreCharId), payload);
    }
    closeLoreModal();
    await loadLorebook();
    toast(_editingLoreIdx === null ? '条目已添加' : '条目已更新', 'ok');
  } catch(e) {
    toast('保存失败: ' + e.message, 'err');
  }
}

async function deleteLoreEntry(eid) {
  const entry = _loreEntries.find(x => x.id === eid);
  const label = entry ? (entry.keyword || []).join(', ') : eid;
  if (!confirm(`确定删除条目（${label}）吗？`)) return;
  try {
    await api('DELETE', ownedEntryUrl(`/lorebook/${eid}`, _loreCharId));
    await loadLorebook();
    toast('条目已删除', 'ok');
  } catch(e) {
    toast('删除失败: ' + e.message, 'err');
  }
}

async function importLorebookTxt(event) {
  const file = event.target.files[0];
  if (!file) return;
  event.target.value = '';
  const fd = new FormData();
  fd.append('file', file);
  try {
    const token = localStorage.getItem('qq_admin_key') || '';
    const resp = await fetch(ownedEntryUrl('/lorebook/import/txt', _loreCharId), {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + token },
      body: fd,
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || resp.statusText);
    toast(data.message || '导入成功', 'ok');
    loadLorebook();
  } catch(e) {
    toast('导入失败：' + (e.message || e), 'err');
  }
}
// ══════════════════════════════════════════════════════════
//  破限条目
// ══════════════════════════════════════════════════════════
let _jbEntries = [];
let _editingJbId = null;

async function loadJbEntries() {
  const el = document.getElementById('jb-table-body');
  el.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const data = await api('GET', '/jailbreak-entries');
    _jbCharId = data.char_id;
    _jbEntries = data.entries || [];
    renderJbEntries();
  } catch(e) {
    el.innerHTML = `<div class="empty">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

function renderJbEntries() {
  const el = document.getElementById('jb-table-body');
  if (!_jbEntries.length) {
    el.innerHTML = '<div class="empty">暂无条目，点击「新增」添加第一条</div>';
    return;
  }
  const rows = _jbEntries.map(e => {
    const badge = e.enabled
      ? '<span class="badge badge-success">启用</span>'
      : '<span class="badge badge-danger">禁用</span>';
    const layerLabel = {0:'层0',2:'层2',11:'层11'}[e.layer] || `层${e.layer}`;
    const contentId = `jb-content-${e.id}`;
    const short = (e.content||'').length > 60
      ? escapeHtml((e.content||'').slice(0,60)) + '…'
      : escapeHtml(e.content||'');
    return `<tr>
      <td>${badge}</td>
      <td style="font-size:12px;color:var(--accent)">${escapeHtml(e.title||'')}</td>
      <td style="font-size:11px;color:var(--muted);text-align:center">${layerLabel}</td>
      <td style="font-size:12px;max-width:280px">
        <span id="${contentId}-short" style="white-space:pre-wrap;cursor:pointer" onclick="toggleJbContent('${e.id}')" title="点击展开">${short}</span>
        <span id="${contentId}-full" style="white-space:pre-wrap;display:none">${escapeHtml(e.content||'')}<br><a href="#" style="font-size:11px;color:var(--muted)" onclick="toggleJbContent('${e.id}');return false;">收起</a></span>
      </td>
      <td style="white-space:nowrap">
        <button class="btn btn-ghost btn-sm" onclick="openJbModal('${e.id}')">编辑</button>
        <button class="btn btn-danger btn-sm" onclick="deleteJbEntry('${e.id}')">删除</button>
      </td>
    </tr>`;
  }).join('');
  el.innerHTML = `<div class="tbl-wrap"><table>
    <thead><tr><th>状态</th><th>标题</th><th style="width:60px">层级</th><th>内容</th><th style="width:120px">操作</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function toggleJbContent(id) {
  const short = document.getElementById(`jb-content-${id}-short`);
  const full = document.getElementById(`jb-content-${id}-full`);
  if (!short || !full) return;
  const expanded = full.style.display !== 'none';
  short.style.display = expanded ? '' : 'none';
  full.style.display = expanded ? 'none' : '';
}

function openJbModal(id) {
  _editingJbId = id;
  document.getElementById('jb-modal-title').textContent = id ? '编辑破限条目' : '新增破限条目';
  if (id) {
    const e = _jbEntries.find(x => x.id === id);
    if (!e) return;
    document.getElementById('jb-title').value = e.title || '';
    document.getElementById('jb-content').value = e.content || '';
    document.getElementById('jb-layer').value = String(e.layer ?? 0);
    document.getElementById('jb-enabled').checked = e.enabled !== false;
  } else {
    document.getElementById('jb-title').value = '';
    document.getElementById('jb-content').value = '';
    document.getElementById('jb-layer').value = '0';
    document.getElementById('jb-enabled').checked = true;
  }
  document.getElementById('jb-modal').style.display = 'flex';
}

function closeJbModal() {
  document.getElementById('jb-modal').style.display = 'none';
  _editingJbId = null;
}

async function saveJbEntry() {
  const body = {
    title:   document.getElementById('jb-title').value.trim(),
    content: document.getElementById('jb-content').value.trim(),
    layer:   parseInt(document.getElementById('jb-layer').value),
    enabled: document.getElementById('jb-enabled').checked,
  };
  if (!body.title || !body.content) { toast('标题和内容不能为空', 'err'); return; }
  try {
    if (_editingJbId) {
      await api('PUT', ownedEntryUrl(`/jailbreak-entries/${_editingJbId}`, _jbCharId), body);
    } else {
      await api('POST', ownedEntryUrl('/jailbreak-entries', _jbCharId), body);
    }
    toast('已保存', 'ok');
    closeJbModal();
    loadJbEntries();
  } catch(e) {
    toast('保存失败：' + e.message, 'err');
  }
}

async function deleteJbEntry(id) {
  if (!confirm('确认删除？')) return;
  try {
    await api('DELETE', ownedEntryUrl(`/jailbreak-entries/${id}`, _jbCharId));
    toast('已删除', 'ok');
    loadJbEntries();
  } catch(e) {
    toast('删除失败：' + e.message, 'err');
  }
}

async function exportJbEntries() {
  try {
    const resp = await fetch(ownedEntryUrl('/jailbreak-entries/export/json', _jbCharId), {
      headers: { 'Authorization': `Bearer ${localStorage.getItem('qq_admin_key')||''}` }
    });
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'jailbreak_entries.json'; a.click();
    URL.revokeObjectURL(url);
  } catch(e) { toast('导出失败：' + e.message, 'err'); }
}

async function importJbEntriesJson(file) {
  const form = new FormData();
  form.append('file', file);
  try {
    const resp = await fetch(ownedEntryUrl('/jailbreak-entries/import/json', _jbCharId), {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${localStorage.getItem('qq_admin_key')||''}` },
      body: form,
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(JSON.stringify(data.detail));
    toast(data.message, 'ok');
    loadJbEntries();
  } catch(e) { toast('导入失败：' + e.message, 'err'); }
}

async function importJbEntriesTxt(file) {
  const form = new FormData();
  form.append('file', file);
  try {
    const resp = await fetch(ownedEntryUrl('/jailbreak-entries/import/txt', _jbCharId), {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${localStorage.getItem('qq_admin_key')||''}` },
      body: form,
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(JSON.stringify(data.detail));
    toast(data.message, 'ok');
    loadJbEntries();
  } catch(e) { toast('导入失败：' + e.message, 'err'); }
}


// ══════════════════════════════════════════════════════════
//  Context config
// ══════════════════════════════════════════════════════════
async function loadContextConfig() {
  try {
    const data = await api('GET', '/context-config');
    const val = data.max_turns || 20;
    document.getElementById('ctx-slider').value = val;
    document.getElementById('ctx-val').textContent = val;
  } catch(e) {
    toast(t('status.context.load_error', '读取上下文配置失败: {error}', {error: e.message}), 'err');
  }
}

async function saveContextConfig() {
  const max_turns = parseInt(document.getElementById('ctx-slider').value, 10);
  try {
    await api('PUT', '/context-config', { max_turns });
    toast(t('status.context.saved', '上下文已设为 {count} 轮', {count: max_turns}), 'ok');
  } catch(e) {
    toast(t('common.save_failed', '保存失败: {error}', {error: e.message}), 'err');
  }
}

// ══════════════════════════════════════════════════════════
//  Jailbreak
// ══════════════════════════════════════════════════════════

function _updateJbBadge(enabled) {
  const b = document.getElementById('jb-badge');
  if (!b) return;
  b.textContent = enabled ? '已启用' : '未启用';
  b.className   = 'badge ' + (enabled ? 'badge-warn' : 'badge-danger');
}

document.addEventListener('DOMContentLoaded', () => {
  const cb = document.getElementById('jb-enabled');
  if (cb) cb.addEventListener('change', () => _updateJbBadge(cb.checked));
});


// ══════════════════════════════════════════════════════════
//  LLM 生成参数
// ══════════════════════════════════════════════════════════
