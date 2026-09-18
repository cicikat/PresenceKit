function _statusSet(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value == null || value === '' ? '—' : String(value);
}

function _statusOutcome(id, ok, detail = '') {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = ok
    ? t('status.refresh_ok', '已刷新')
    : t('status.refresh_error', '读取失败：{error}', {error: detail});
  el.className = `status-result ${ok ? 'status-result-ok' : 'status-result-error'}`;
}

function _statusLogicalLocation(mode, sessionId) {
  if (mode === 'test') return sessionId ? t('status.data_environment.location_test', '测试沙箱（当前会话）') : t('status.data_environment.location_test_unknown', '测试沙箱');
  if (mode === 'formal') return t('status.data_environment.location_formal', '正式数据区');
  return t('status.data_environment.location_unknown', '数据位置未知');
}

function _statusSessionLabel(mode, sessionId) {
  if (mode !== 'test') return t('status.data_environment.none', '无');
  if (!sessionId) return t('status.data_environment.session_unknown', '测试会话（未提供 ID）');
  const safe = String(sessionId).replace(/[^A-Za-z0-9_-]/g, '').slice(0, 8);
  return safe ? `${t('status.data_environment.session_id', 'ID')} ${safe}…` : t('status.data_environment.session_active', '测试会话已启用');
}

function _statusReadiness(ready, okLabel, notReadyLabel) {
  return ready ? t(okLabel, '已就绪') : t(notReadyLabel, '未就绪');
}

async function _loadStatusRuntime() {
  const d = await api('GET', '/status');
  const mode = d.data_mode || 'unknown';
  const modeEl = document.getElementById('s-data-mode');
  if (modeEl) {
    modeEl.textContent = mode === 'test'
      ? t('status.data_environment.test', '测试沙箱')
      : mode === 'formal' ? t('status.data_environment.formal', '正式数据') : mode;
    modeEl.dataset.mode = mode;
    modeEl.className = `badge ${mode === 'test' ? 'badge-warn' : mode === 'formal' ? 'badge-success' : ''}`;
  }
  _statusSet('s-test-session', _statusSessionLabel(mode, d.test_session_id));
  _statusSet('s-data-root', _statusLogicalLocation(mode, d.test_session_id));
  const testCount = Array.isArray(d.test_user_ids) ? d.test_user_ids.length : 0;
  _statusSet('s-test-users', testCount ? t('status.data_environment.quarantined_count', '{count} 个（已隔离）', {count: testCount}) : t('status.data_environment.none', '无'));
  _statusSet('s-users', d.known_user_count);
  _statusSet('s-runtime-state', d.status === 'running' ? t('status.running', '运行中') : (d.status || '—'));
  _statusSet('s-active-sessions', d.active_session_count ?? 0);
  return d;
}

async function _loadStatusModel() {
  const d = await api('GET', '/model-presets');
  const profile = d.routing_profiles?.[d.active_routing] || {};
  const chatPreset = profile.chat || d.active_chat_preset || '—';
  _statusSet('s-model-current', chatPreset);
  _statusSet('s-model-routing', d.active_routing || 'default');
  _statusSet('s-model-source', t('status.model.source_value', '来源：模型路由 / 当前 chat preset'));
  _statusSet('s-model-result', t('status.read_only_effective', '已读取；未执行外部连通性测试'));
}

async function _loadStatusTts() {
  const [characters, config, calls] = await Promise.all([
    api('GET', '/characters').catch(() => ({})),
    api('GET', '/tts-config'),
    api('GET', '/observability/api-calls?caller=tts&limit=1').catch(() => ({entries: []})),
  ]);
  const activeId = characters.active_id && characters.active_id !== 'default' ? characters.active_id : '';
  const resolved = activeId ? await api('GET', `/tts-config?char_id=${encodeURIComponent(activeId)}`).catch(() => config) : config;
  const providerStatus = resolved.provider_status || config.provider_status || {};
  const options = resolved.resource_options || config.resource_options || {};
  const resourceCount = ['reference_audio', 'gpt_model', 'sovits_model'].reduce((sum, key) => sum + ((options[key] || []).length ? 1 : 0), 0);
  const binding = resolved.character_binding;
  _statusSet('s-tts-enabled', `${resolved.enabled ? t('common.enabled', '已启用') : t('common.disabled', '未启用')} · ${resolved.provider || '—'}`);
  _statusSet('s-tts-provider', t('status.tts.source_value', '来源：TTS 配置；角色绑定可覆盖 provider 参数'));
  _statusSet('s-tts-binding', binding?.tts_preset ? binding.tts_preset : t('status.tts.global', '全局默认'));
  _statusSet('s-tts-resources', t('status.tts.resources_count', '已发现 {count}/3 类逻辑资源', {count: resourceCount}));
  _statusSet('s-tts-provider-ready', _statusReadiness(providerStatus.ready, 'status.tts.ready', 'status.tts.not_ready'));
  _statusSet('s-tts-last-result', calls.entries?.[0]
    ? (calls.entries[0].ok ? t('status.tts.last_success', '最近合成：成功') : t('status.tts.last_failed', '最近合成：失败'))
    : t('status.tts.no_last_result', '暂无合成记录'));
  _statusSet('s-tts-refresh-state', t('status.read_only_effective', '已读取；就绪不等于外部服务健康'));
}

async function _loadStatusProxy() {
  const d = await api('GET', '/proxy');
  _statusSet('s-proxy-status', d.enabled ? t('common.enabled', '已启用') : t('common.disabled', '未启用'));
  _statusSet('s-proxy-scope', t('status.proxy.scope_value', '后端出站请求'));
  _statusSet('s-proxy-result', t('status.read_only_effective', '已读取；未探测外部目标'));
}

async function _loadStatusRelay() {
  const d = await api('GET', '/settings/relay');
  const configured = Boolean(d.relay_base_url || d.relay_topic || d.relay_token);
  _statusSet('s-relay-status', configured ? t('status.relay.configured_short', '已配置') : t('status.relay.unconfigured', '未配置'));
  _statusSet('s-relay-scope', t('status.relay.scope_value', '仅中继唤醒'));
  _statusSet('s-relay-result', t('status.read_only_effective', '已读取；未承诺中继服务健康'));
}

async function _loadStatusScheduler() {
  const [scheduler, flags] = await Promise.all([
    api('GET', '/scheduler/status'),
    api('GET', '/settings/feature-flags'),
  ]);
  _statusSet('s-scheduler-status', scheduler.enabled ? t('status.scheduler.enabled', '已启用') : t('status.scheduler.disabled', '未启用'));
  const channelFlags = ['qq', 'mail'].map(name => flags.flags?.[name]).filter(Boolean);
  const enabled = channelFlags.filter(item => item.enabled).length;
  _statusSet('s-channel-status', t('status.scheduler.channel_count', '{count} 个通道已启用', {count: enabled}));
  _statusSet('s-scheduler-result', t('status.read_only_effective', '已读取；逐项生效方式见配置页'));
}

async function _loadStatusAttention() {
  const d = await api('GET', '/logs?lines=20');
  const lines = String(d.logs || '').split(/\r?\n/).filter(Boolean);
  _statusSet('s-recent-errors', lines.length ? t('status.attention.has_errors', '{count} 行最近日志', {count: lines.length}) : t('status.attention.none', '暂无最近错误'));
  _statusSet('s-recent-errors-detail', lines.length ? t('status.attention.open_detail', '请打开错误日志查看详情') : t('status.attention.no_error_detail', '最近刷新未发现错误日志'));
  _statusSet('s-attention-result', t('status.read_only_effective', '已读取'));
}

async function loadStatus() {
  const jobs = [
    ['runtime', _loadStatusRuntime()],
    ['model', _loadStatusModel()],
    ['tts', _loadStatusTts()],
    ['proxy', _loadStatusProxy()],
    ['relay', _loadStatusRelay()],
    ['scheduler', _loadStatusScheduler()],
    ['attention', _loadStatusAttention()],
  ];
  const results = await Promise.allSettled(jobs.map(([, job]) => job));
  const ids = ['runtime', 'model', 'tts', 'proxy', 'relay', 'scheduler', 'attention'];
  const outcomeIds = {
    model: 's-model-result',
    tts: 's-tts-refresh-state',
    proxy: 's-proxy-result',
    relay: 's-relay-result',
    scheduler: 's-scheduler-result',
    attention: 's-attention-result',
  };
  results.forEach((result, index) => {
    const id = ids[index];
    if (outcomeIds[id]) _statusOutcome(outcomeIds[id], result.status === 'fulfilled', result.reason?.message || 'unknown');
  });
  const failed = results.find(result => result.status === 'rejected');
  if (failed) console.warn('[admin] status summary partial failure', failed.reason);
}

async function reloadConfig() {
  try {
    const d = await api('POST', '/reload');
    toast(d.message, 'ok');
    loadStatus();
  } catch(e) { toast(t('status.error.reload', '热重载失败: {error}', {error: e.message}), 'err'); }
}

// ══════════════════════════════════════════════════════════
//  Users page
// ══════════════════════════════════════════════════════════
async function loadUsers() {
  try {
    const d = await api('GET', '/users/');
    _allUsers = d.users || [];
    renderUsers(_allUsers);
  } catch(e) { toast('加载用户失败: ' + e.message, 'err'); }
}

function filterUsers() {
  const q = document.getElementById('user-search').value.trim().toLowerCase();
  renderUsers(q ? _allUsers.filter(u => u.includes(q)) : _allUsers).catch(() => {});
}

function renderUsers(list) {
  const el = document.getElementById('user-table-body');
  if (!list.length) { el.innerHTML = '<div class="empty">没有找到用户记录</div>'; return; }

  const rows = list.map(uid => `
    <tr>
      <td><code style="font-family:var(--mono);font-size:13px">${uid}</code>
        <button class="btn btn-ghost btn-sm user-rel-toggle" onclick="toggleUserRelPanel('${uid}')">关系配置</button>
      </td>
      <td>
        <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();openMemModal('${uid}')">查看记忆</button>
      </td>
    </tr>
    <tr id="rel-panel-row-${uid}" style="display:none">
      <td colspan="2" style="padding:0">
        <div style="padding:14px 20px 16px;background:rgba(0,0,0,.2);border-top:1px solid var(--border)">
          <div style="font-size:12px;color:var(--muted);margin-bottom:10px">关系配置：${uid}</div>
          <div class="user-rel-fields">
            <label class="field">
              <span>角色 (role)</span>
              <select id="ri-role-${uid}">
                <option value="stranger">stranger（陌生人）</option>
                <option value="friend">friend（朋友）</option>
                <option value="close_friend">close_friend（好友）</option>
                <option value="lover">lover（恋人）</option>
                <option value="master">master（主人）</option>
                <option value="default">default（全局默认）</option>
              </select>
            </label>
            <label class="field">
              <span>昵称 (nickname)</span>
              <input type="text" id="ri-nickname-${uid}" placeholder="留空则无">
            </label>
          </div>
          <div class="user-rel-extra">
            <label class="field">
              <span>额外提示词 (extra_prompt)</span>
              <textarea id="ri-extra-${uid}" style="min-height:56px"
                placeholder="在系统提示末尾追加的用户专属指令…"></textarea>
            </label>
          </div>
          <div style="margin-top:10px">
            <button class="btn btn-primary btn-sm" onclick="saveUserRelInline('${uid}')">保存关系配置</button>
          </div>
        </div>
      </td>
    </tr>`).join('');

  el.innerHTML = `<div class="tbl-wrap"><table>
    <tr><th>用户 ID</th><th style="width:100px">操作</th></tr>
    ${rows}
  </table></div>`;
}

async function toggleUserRelPanel(uid) {
  const row = document.getElementById(`rel-panel-row-${uid}`);
  if (!row) return;
  const isOpen = row.style.display !== 'none';
  if (isOpen) {
    row.style.display = 'none';
    return;
  }
  row.style.display = '';
  try {
    const d = await api('GET', `/relations/${uid}`);
    const r = d.raw || d.merged || {};
    document.getElementById(`ri-role-${uid}`).value     = r.role || 'stranger';
    document.getElementById(`ri-nickname-${uid}`).value = r.nickname || '';
    document.getElementById(`ri-extra-${uid}`).value    = r.extra_prompt || '';
  } catch { /* no existing relation, keep defaults */ }
}

async function saveUserRelInline(uid) {
  const body = {
    role:         document.getElementById(`ri-role-${uid}`).value,
    nickname:     document.getElementById(`ri-nickname-${uid}`).value || null,
    extra_prompt: document.getElementById(`ri-extra-${uid}`).value,
    priority: 1,
    permissions: { agent_control: false, image_gen: false },
  };
  try {
    const d = await api('PUT', `/relations/${uid}`, body);
    toast(d.message, 'ok');
  } catch(e) { toast('保存失败: ' + e.message, 'err'); }
}

// ══════════════════════════════════════════════════════════
//  Blacklist page
// ══════════════════════════════════════════════════════════
async function loadBlacklist() {
  try {
    const d = await api('GET', '/relations/blacklist');
    renderBlacklist(d.blacklist || []);
  } catch(e) { toast('加载黑名单失败: ' + e.message, 'err'); }
}

function renderBlacklist(list) {
  const el = document.getElementById('bl-list');
  if (!list.length) { el.innerHTML = '<div class="empty">黑名单为空</div>'; return; }
  el.innerHTML = `<div class="tbl-wrap"><table>
    <tr><th>用户 ID</th><th>操作</th></tr>
    ${list.map(uid => `<tr>
      <td><code style="font-family:var(--mono)">${uid}</code></td>
      <td><button class="btn btn-ghost btn-sm" onclick="removeBlacklist('${uid}')">解除屏蔽</button></td>
    </tr>`).join('')}
  </table></div>`;
}

async function addBlacklist() {
  const uid = document.getElementById('bl-add-input').value.trim();
  if (!uid) { toast('请输入 QQ 号', 'warn'); return; }
  try {
    const d = await api('POST', '/relations/blacklist', {user_id: uid});
    toast(d.message, 'ok');
    document.getElementById('bl-add-input').value = '';
    renderBlacklist(d.blacklist || []);
  } catch(e) { toast('添加失败: ' + e.message, 'err'); }
}

async function removeBlacklist(uid) {
  try {
    const d = await api('DELETE', `/relations/blacklist/${uid}`);
    toast(d.message, 'ok');
    renderBlacklist(d.blacklist || []);
  } catch(e) { toast('移除失败: ' + e.message, 'err'); }
}

// ══════════════════════════════════════════════════════════
//  Logs page
// ══════════════════════════════════════════════════════════
async function loadLogs() {
  const lines = document.getElementById('log-lines').value;
  try {
    const d = await api('GET', `/logs?lines=${lines}`);
    const box = document.getElementById('log-box');
    box.textContent = d.logs || '（日志为空）';
    box.scrollTop = box.scrollHeight;
    if (d.total_lines !== undefined) {
      document.getElementById('log-meta').textContent = `共 ${d.total_lines} 行，显示最后 ${lines} 行`;
    }
  } catch(e) { toast('加载日志失败: ' + e.message, 'err'); }
}

async function clearLogs() {
  if (!confirm('确定清空错误日志吗？')) return;
  try {
    const d = await api('DELETE', '/logs');
    toast(d.message, 'ok');
    document.getElementById('log-box').textContent = '（日志已清空）';
    document.getElementById('log-meta').textContent = '';
  } catch(e) { toast('清空失败: ' + e.message, 'err'); }
}

// ══════════════════════════════════════════════════════════
//  Memory modal
// ══════════════════════════════════════════════════════════
function openMemModal(uid) {
  _currentUser = uid;
  document.getElementById('mem-modal-title').textContent = `用户记忆：${uid}`;
  document.getElementById('mem-modal').classList.add('open');
  memTab('profile');
}

function closeMemModal() {
  document.getElementById('mem-modal').classList.remove('open');
  _currentUser = null;
}

function memTab(tab) {
  ['profile','history','rag'].forEach(t => {
    document.getElementById(`mem-${t}-panel`).style.display = t === tab ? '' : 'none';
    document.getElementById(`mt-${t}`).classList.toggle('btn-primary', t === tab);
    document.getElementById(`mt-${t}`).classList.toggle('btn-ghost',   t !== tab);
  });
  if (tab === 'profile') loadProfile();
  if (tab === 'history') loadHistory();
}

async function loadProfile() {
  const uid = _currentUser;
  try {
    const d = await api('GET', `/users/${uid}/profile`);
    const p = d.profile || {};
    const el = document.getElementById('profile-view');
    el.innerHTML = `<div class="profile-grid">
      ${[['姓名/称呼','name'],['所在地','location'],['职业/学校','occupation'],['兴趣爱好','interests'],['宠物','pets']].map(([lbl, key]) =>
        `<div class="profile-field"><div class="lbl">${lbl}</div><div class="val">${p[key] ? `<span class="i18n-raw">${escapeHtml(String(p[key]))}</span>` : '<span style="color:var(--muted)">未知</span>'}</div></div>`
      ).join('')}
      <div class="profile-field" style="grid-column:1/-1">
        <div class="lbl">重要事实</div>
        <div class="val">${(p.important_facts||[]).length
          ? (p.important_facts).map(f => `<div class="i18n-raw" style="margin-bottom:3px">• ${escapeHtml(String(f))}</div>`).join('')
          : '<span style="color:var(--muted)">暂无</span>'
        }</div>
      </div>
    </div>`;
  } catch(e) { toast('加载画像失败: ' + e.message, 'err'); }
}

async function loadHistory() {
  const uid = _currentUser;
  try {
    const d = await api('GET', `/memory/${uid}/short-term`);
    const msgs = d.history || [];
    const el = document.getElementById('msg-list');
    if (!msgs.length) { el.innerHTML = '<div class="empty">暂无对话历史</div>'; return; }
    el.innerHTML = msgs.map(m =>
      `<div class="msg ${m.role}">
        <div class="role">${m.role === 'user' ? '用户' : 'Bot'}</div>
        <div class="i18n-raw">${escapeHtml(m.content)}</div>
      </div>`
    ).join('');
    el.scrollTop = el.scrollHeight;
  } catch(e) { toast('加载历史失败: ' + e.message, 'err'); }
}

async function searchRAG() {
  const uid = _currentUser;
  const q = document.getElementById('rag-query').value.trim();
  if (!q) return;
  const el = document.getElementById('rag-results');
  el.innerHTML = '<div class="loading">搜索中…</div>';
  try {
    const d = await api('GET', `/memory/${uid}/rag/search?query=${encodeURIComponent(q)}`);
    const results = d.results || [];
    el.innerHTML = results.length
      ? results.map((r, i) => `<div class="i18n-raw" style="margin-bottom:8px;padding:8px;background:var(--border);border-radius:6px;font-size:12px;line-height:1.6">${i+1}. ${escapeHtml(r)}</div>`).join('')
      : '<div class="empty">未找到相关记忆</div>';
  } catch(e) { toast('搜索失败: ' + e.message, 'err'); el.innerHTML = ''; }
}

async function clearMemoryPart(part) {
  const uid = _currentUser;
  const labels = {'short-term':'短期历史','profile':'画像','rag':'RAG 记忆'};
  if (!confirm(`确定清空用户 ${uid} 的${labels[part]}吗？`)) return;
  try {
    let d;
    if (part === 'short-term') d = await api('DELETE', `/memory/${uid}/short-term`);
    else if (part === 'rag')   d = await api('DELETE', `/memory/${uid}/rag`);
    else if (part === 'profile') {
      d = await api('PUT', `/users/${uid}/profile`, {
        name:null, location:null, pets:null, interests:null, occupation:null, important_facts:[]
      });
    }
    toast(d.message, 'ok');
    // Refresh current tab
    if (part === 'short-term') loadHistory();
    else if (part === 'profile') loadProfile();
  } catch(e) { toast('清空失败: ' + e.message, 'err'); }
}

async function clearAllMemory() {
  const uid = _currentUser;
  if (!confirm(`确定清除用户 ${uid} 的所有记忆（历史 + 画像 + RAG）吗？`)) return;
  try {
    const d = await api('DELETE', `/users/${uid}/memory`);
    toast(d.message, 'ok');
    loadProfile();
  } catch(e) { toast('清除失败: ' + e.message, 'err'); }
}

// ── Profile edit ──
async function editProfile() {
  const uid = _currentUser;
  try {
    const d = await api('GET', `/users/${uid}/profile`);
    const p = d.profile || {};
    document.getElementById('pe-name').value       = p.name || '';
    document.getElementById('pe-location').value   = p.location || '';
    document.getElementById('pe-occupation').value = p.occupation || '';
    document.getElementById('pe-interests').value  = p.interests || '';
    document.getElementById('pe-pets').value       = p.pets || '';
    document.getElementById('pe-facts').value      = (p.important_facts||[]).join('\n');
    document.getElementById('profile-edit-modal').classList.add('open');
  } catch(e) { toast('加载画像失败: ' + e.message, 'err'); }
}

function closeProfileEdit() { document.getElementById('profile-edit-modal').classList.remove('open'); }

async function saveProfile() {
  const uid = _currentUser;
  const facts = document.getElementById('pe-facts').value
    .split('\n').map(s => s.trim()).filter(Boolean);
  const body = {
    name:           document.getElementById('pe-name').value || null,
    location:       document.getElementById('pe-location').value || null,
    occupation:     document.getElementById('pe-occupation').value || null,
    interests:      document.getElementById('pe-interests').value || null,
    pets:           document.getElementById('pe-pets').value || null,
    important_facts: facts,
  };
  try {
    const d = await api('PUT', `/users/${uid}/profile`, body);
    toast(d.message, 'ok');
    closeProfileEdit();
    loadProfile();
  } catch(e) { toast('保存失败: ' + e.message, 'err'); }
}

// ══════════════════════════════════════════════════════════
//  Lorebook
// ══════════════════════════════════════════════════════════
