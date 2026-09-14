let _rfFacts = [];
let _rfEditIndex = null;

async function loadRelationshipFactsPage() {
  const sel = document.getElementById('rf-uid-select');
  try {
    if (!_allUsers || !_allUsers.length) {
      const d = await api('GET', '/users/');
      _allUsers = d.users || [];
    }
    const current = sel.value;
    sel.innerHTML = _allUsers.map(u => `<option value="${u}">${u}</option>`).join('');
    if (current && _allUsers.includes(current)) sel.value = current;
  } catch (e) {
    toast('加载用户列表失败: ' + e.message, 'err');
  }
  if (sel.value) loadRelationshipFacts();
}

async function loadRelationshipFacts() {
  const uid = document.getElementById('rf-uid-select').value;
  const el = document.getElementById('rf-list-body');
  if (!uid) { el.innerHTML = '<div class="empty">选择一个用户后加载</div>'; return; }
  el.innerHTML = '<div class="loading">加载中…</div>';
  const status = document.getElementById('rf-status-filter').value;
  try {
    const qs = status ? `?status=${encodeURIComponent(status)}` : '';
    const data = await api('GET', `/relationship-facts/${encodeURIComponent(uid)}${qs}`);
    _rfFacts = data.facts || [];
    _renderRelationshipFacts(uid, _rfFacts);
  } catch (e) {
    el.innerHTML = `<div class="empty">加载失败: ${e.message}</div>`;
  }
}

function _renderRelationshipFacts(uid, facts) {
  const el = document.getElementById('rf-list-body');
  if (!facts.length) { el.innerHTML = '<div class="empty">没有符合条件的关系事实</div>'; return; }
  const statusBadge = (s) => {
    if (s === 'pending') return '<span class="badge badge-warn">待审核</span>';
    if (s === 'confirmed') return '<span class="badge badge-success">已确认</span>';
    if (s === 'archived') return '<span class="badge">已归档</span>';
    return `<span class="badge">${s}</span>`;
  };
  const rows = facts.map((f, i) => `
    <tr>
      <td>${(f.keywords || []).map(k => `<span class="badge i18n-raw" style="margin:1px">${escapeHtml(String(k))}</span>`).join('')}</td>
      <td class="i18n-raw" style="max-width:280px">${escapeHtml(String(f.content || ''))}</td>
      <td>${statusBadge(f.status)}</td>
      <td style="font-size:12px;color:var(--muted)">${(f.confidence ?? 1).toFixed?.(2) ?? f.confidence} / hit ${f.hit_count ?? 0}</td>
      <td style="white-space:nowrap">
        ${f.status === 'pending' ? `
          <button class="btn btn-ghost btn-sm" onclick="confirmRelationshipFact(${i})">确认</button>
          <button class="btn btn-ghost btn-sm" onclick="rejectRelationshipFact(${i})">拒绝</button>
        ` : ''}
        <button class="btn btn-ghost btn-sm" onclick="openFactModal(${i})">编辑</button>
        <button class="btn btn-ghost btn-sm" onclick="deleteRelationshipFact(${i})">删除</button>
      </td>
    </tr>
  `).join('');
  el.innerHTML = `<div class="tbl-wrap"><table>
    <tr><th>关键词</th><th>内容</th><th>状态</th><th>置信度/命中</th><th></th></tr>
    ${rows}
  </table></div>`;
}

async function confirmRelationshipFact(index) {
  const uid = document.getElementById('rf-uid-select').value;
  try {
    await api('POST', `/relationship-facts/${encodeURIComponent(uid)}/${index}/confirm`);
    toast('已确认', 'ok');
    loadRelationshipFacts();
  } catch (e) { toast('确认失败: ' + e.message, 'err'); }
}

function rejectRelationshipFact(index) {
  _openAtConfirm('拒绝该条关系事实', '拒绝后条目状态变为 archived（不再注入 prompt），不会被物理删除，可追溯。', async () => {
    const uid = document.getElementById('rf-uid-select').value;
    try {
      await api('POST', `/relationship-facts/${encodeURIComponent(uid)}/${index}/reject`);
      toast('已拒绝', 'ok');
      loadRelationshipFacts();
    } catch (e) { toast('拒绝失败: ' + e.message, 'err'); }
  });
}

function deleteRelationshipFact(index) {
  _openAtConfirm('删除该条关系事实', '将从 relationship_facts.yaml 中物理删除，不可恢复。', async () => {
    const uid = document.getElementById('rf-uid-select').value;
    try {
      await api('DELETE', `/relationship-facts/${encodeURIComponent(uid)}/${index}`);
      toast('已删除', 'ok');
      loadRelationshipFacts();
    } catch (e) { toast('删除失败: ' + e.message, 'err'); }
  });
}

async function runRelationshipFactSuggester() {
  const uid = document.getElementById('rf-uid-select').value;
  if (!uid) { toast('请先选择用户', 'err'); return; }
  try {
    const r = await api('POST', `/relationship-facts/${encodeURIComponent(uid)}/run-suggester`);
    toast(r.message, 'ok');
    loadRelationshipFacts();
  } catch (e) { toast('运行失败: ' + e.message, 'err'); }
}

function openFactModal(index) {
  document.getElementById('rf-fact-err').textContent = '';
  _rfEditIndex = (index === undefined) ? null : index;
  if (_rfEditIndex !== null) {
    const f = _rfFacts[_rfEditIndex];
    document.getElementById('rf-fact-keywords').value = (f.keywords || []).join(', ');
    document.getElementById('rf-fact-content').value = f.content || '';
    document.getElementById('rf-fact-confidence').value = f.confidence ?? 1.0;
    document.getElementById('rf-fact-order').value = f.insertion_order ?? 60;
    document.getElementById('rf-fact-modal-title').textContent = '编辑关系事实';
  } else {
    document.getElementById('rf-fact-keywords').value = '';
    document.getElementById('rf-fact-content').value = '';
    document.getElementById('rf-fact-confidence').value = 1.0;
    document.getElementById('rf-fact-order').value = 60;
    document.getElementById('rf-fact-modal-title').textContent = '添加关系事实';
  }
  document.getElementById('rf-fact-modal').classList.add('open');
}
function closeFactModal() {
  document.getElementById('rf-fact-modal').classList.remove('open');
}
async function submitFactModal() {
  const uid = document.getElementById('rf-uid-select').value;
  const errEl = document.getElementById('rf-fact-err');
  const keywords = document.getElementById('rf-fact-keywords').value.split(',').map(s => s.trim()).filter(Boolean);
  const content = document.getElementById('rf-fact-content').value.trim();
  if (!keywords.length) { errEl.textContent = '至少填一个关键词'; return; }
  if (!content) { errEl.textContent = '内容不能为空'; return; }
  const body = {
    keywords, content,
    confidence: parseFloat(document.getElementById('rf-fact-confidence').value) || 1.0,
    insertion_order: parseInt(document.getElementById('rf-fact-order').value, 10) || 60,
    enabled: true,
    status: 'confirmed',
    source: 'manual',
  };
  try {
    if (_rfEditIndex !== null) {
      await api('PUT', `/relationship-facts/${encodeURIComponent(uid)}/${_rfEditIndex}`, body);
      toast('已更新', 'ok');
    } else {
      await api('POST', `/relationship-facts/${encodeURIComponent(uid)}`, body);
      toast('已添加', 'ok');
    }
    closeFactModal();
    loadRelationshipFacts();
  } catch (e) {
    errEl.textContent = e.message;
  }
}

// ══════════════════════════════════════════════════════════
//  屏幕内容查看 / TTS / 用户称谓（三个小设置卡片）
// ══════════════════════════════════════════════════════════
async function loadScreenPeekSettings() {
  try {
    const d = await api('GET', '/settings/screen-peek');
    document.getElementById('sp-enabled').checked = !!d.enabled;
    document.getElementById('sp-cooldown').value = d.cooldown_minutes ?? 30;
  } catch (e) { toast(t('status.screen.load_error', '读取屏幕内容查看配置失败: {error}', {error: e.message}), 'err'); }
}
async function saveScreenPeekSettings() {
  const body = {
    enabled: document.getElementById('sp-enabled').checked,
    cooldown_minutes: parseInt(document.getElementById('sp-cooldown').value, 10),
  };
  try {
    await api('POST', '/settings/screen-peek', body);
    toast(t('status.screen.saved', '屏幕内容查看配置已保存'), 'ok');
  } catch (e) { toast(t('common.save_failed', '保存失败: {error}', {error: e.message}), 'err'); }
}

// 危险模式：手机自动化（phone_control）、桌面/系统类工具的总闸。legacy-admin
// 的 qq_admin_key 自带 admin scope（见 admin/auth.py _scopes_ok "admin" 短路判断），
// 天然满足 PATCH /system/meta-mode 要求的 hardware scope，不需要单独建 scoped token。
async function loadMetaMode() {
  try {
    const d = await api('GET', '/system/meta-mode');
    const el = document.getElementById('metamode-status');
    const btn = document.getElementById('metamode-toggle-btn');
    if (d.mode === 'danger') {
      el.textContent = t('status.dangermode.active', '当前：危险模式');
      el.style.color = 'var(--danger, #e05252)';
      btn.textContent = t('status.dangermode.exit', '切回安全模式');
      btn.dataset.target = 'safe';
    } else {
      el.textContent = t('status.dangermode.safe', '当前：安全模式');
      el.style.color = 'var(--muted)';
      btn.textContent = t('status.dangermode.enter', '切换到危险模式');
      btn.dataset.target = 'danger';
    }
  } catch (e) {
    toast(t('status.dangermode.load_error', '读取模式失败: {error}', {error: e.message}), 'err');
  }
}

async function toggleMetaMode() {
  const btn = document.getElementById('metamode-toggle-btn');
  const target = btn.dataset.target === 'safe' ? 'safe' : 'danger';
  const body = { mode: target };
  try {
    await api('PATCH', '/system/meta-mode', body);
    toast(target === 'danger' ? t('status.dangermode.entered', '已切换到危险模式') : t('status.dangermode.exited', '已切回安全模式'), 'ok');
    loadMetaMode();
  } catch (e) {
    toast(t('common.save_failed', '保存失败: {error}', {error: e.message}), 'err');
  }
}

