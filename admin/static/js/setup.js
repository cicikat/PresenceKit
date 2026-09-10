async function _initCharName() {
  try {
    const d = await api('GET', '/characters/active-info');
    if (d && d.name) {
      window._charName = d.name;
      document.querySelectorAll('#nav-char-label, .nav-char-name, #page-char-name').forEach(el => {
        el.textContent = d.name;
      });
    }
  } catch(e) { /* non-fatal: keep fallback */ }
}

// ══════════════════════════════════════════════════════════
//  密钥本快捷入口悬浮按钮（Brief 93 §2）
// ══════════════════════════════════════════════════════════
async function initSecretsBookFab() {
  const btn = document.getElementById('secrets-book-fab');
  if (!btn) return;
  try {
    const d = await api('GET', '/system/secrets-book');
    btn.style.display = d.available ? 'block' : 'none';
  } catch (e) {
    btn.style.display = 'none';
  }
}

async function openSecretsBook() {
  try {
    await api('POST', '/system/secrets-book/open');
    toast('已用系统默认程序打开密钥本', 'ok');
  } catch (e) {
    toast('打开失败: ' + e.message, 'err');
  }
}

// ══════════════════════════════════════════════════════════
//  配置中心（Brief 93 §1）
// ══════════════════════════════════════════════════════════
function _setupErrMsg(e) {
  const m = /HTTP \d+: (.*)/s.exec(e.message || '');
  if (!m) return e.message || String(e);
  try { return JSON.parse(m[1]).detail || m[1]; } catch { return m[1]; }
}

function _setupBadge(configured, unconfiguredKey='common.not_configured') {
  const label = configured
    ? t('common.configured', '已配置')
    : t(unconfiguredKey, '未配置');
  return `<span class="badge ${configured ? 'badge-success' : 'badge-danger'}">${configured ? '●' : '○'} ${escapeHtml(label)}</span>`;
}

async function checkSetupStatus() {
  try { return await api('GET', '/settings/setup-status'); }
  catch (e) { return null; }
}

async function loadSetupPage() {
  document.getElementById('setup-missing-banner').style.display = 'none';
  document.getElementById('setup-optional-body').innerHTML = `<div class="loading">${escapeHtml(t('common.loading', '加载中…'))}</div>`;
  try {
    const [status, base, embed, chars] = await Promise.all([
      api('GET', '/settings/setup-status'),
      api('GET', '/settings/base-model'),
      api('GET', '/settings/embedding'),
      api('GET', '/characters').catch(() => null),
    ]);
    const owner = status.owner || {};

    window._setupBasePresetName = base.preset_name || 'legacy';
    document.getElementById('setup-base-test-result').textContent = '';
    document.getElementById('setup-character-tip').style.display =
      chars && chars.active_id === 'default' ? 'block' : 'none';

    const missing = [];
    if (!base.configured)  missing.push(t('setup.base.title', '基础聊天模型'));
    if (!owner.configured) missing.push('owner_id');
    document.getElementById('setup-missing-banner').style.display = missing.length ? 'block' : 'none';
    document.getElementById('setup-missing-banner-text').textContent = t(
      'setup.missing',
      '⚠ 未配置将无法聊天/主动触发失效：请先填写下方「{items}」必填项',
      {items: missing.join(t('setup.missing_separator', '」「'))},
    );

    document.getElementById('setup-owner-id').value = owner.owner_id || '';
    document.getElementById('setup-owner-birthday').value = owner.owner_birthday || '';
    document.getElementById('setup-owner-status').innerHTML = _setupBadge(owner.configured);

    document.getElementById('setup-base-url').value = base.base_url || '';
    document.getElementById('setup-base-model-name').value = base.model || '';
    document.getElementById('setup-base-api-key').value = '';
    document.getElementById('setup-base-key-state').textContent = base.api_key_set
      ? t('setup.secret.configured', '（已配置 {value}）', {value: base.api_key_masked})
      : `（${t('common.not_configured', '未配置')}）`;
    document.getElementById('setup-base-status').innerHTML = _setupBadge(base.configured);

    await _loadSetupOptional();

  } catch (e) {
    toast(t('setup.load_error', '加载配置中心失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

async function _loadSetupMail() {
  try {
    const mail = await api('GET', '/settings/mail');
    document.getElementById('setup-mail-enabled').checked = !!mail.enabled;
    document.getElementById('setup-mail-host').value = mail.smtp_host || '';
    document.getElementById('setup-mail-port').value = mail.smtp_port || '';
    document.getElementById('setup-mail-user').value = mail.smtp_user || '';
    document.getElementById('setup-mail-password').value = '';
    document.getElementById('setup-mail-password-state').textContent = mail.smtp_password_set
      ? t('setup.secret.configured', '（已配置 {value}）', {value: mail.smtp_password_masked})
      : `（${t('common.not_configured', '未配置')}）`;
    document.getElementById('setup-mail-from-addr').value = mail.from_addr || '';
    document.getElementById('setup-mail-from-name').value = mail.from_name || '';
    document.getElementById('setup-mail-to-addr').value = mail.to_addr || '';
    document.getElementById('setup-mail-subject-prefix').value = mail.subject_prefix || '';
    document.getElementById('setup-mail-proxy-url').value = mail.proxy_url || '';
    document.getElementById('setup-mail-status').innerHTML = _setupBadge(mail.configured);
  } catch (e) {
    toast(t('setup.mail.load_error', '加载邮件通道配置失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

async function saveSetupMail() {
  const body = {
    enabled: document.getElementById('setup-mail-enabled').checked,
  };
  const host   = document.getElementById('setup-mail-host').value.trim();
  const port   = document.getElementById('setup-mail-port').value.trim();
  const user   = document.getElementById('setup-mail-user').value.trim();
  const pass   = document.getElementById('setup-mail-password').value.trim();
  const from_a = document.getElementById('setup-mail-from-addr').value.trim();
  const from_n = document.getElementById('setup-mail-from-name').value.trim();
  const to_a   = document.getElementById('setup-mail-to-addr').value.trim();
  const prefix = document.getElementById('setup-mail-subject-prefix').value.trim();
  const proxy  = document.getElementById('setup-mail-proxy-url').value.trim();
  if (host)   body.smtp_host = host;
  if (port)   body.smtp_port = parseInt(port, 10);
  if (user)   body.smtp_user = user;
  if (pass)   body.smtp_password = pass;
  if (from_a) body.from_addr = from_a;
  if (from_n) body.from_name = from_n;
  if (to_a)   body.to_addr = to_a;
  if (prefix) body.subject_prefix = prefix;
  if (proxy)  body.proxy_url = proxy;
  try {
    await api('PUT', '/settings/mail', body);
    toast(t('setup.mail.saved', '邮件通道配置已保存'), 'ok');
    _loadSetupMail();
  } catch (e) {
    toast(t('common.save_failed', '保存失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

async function _loadSetupAnniversaries() {
  try {
    const { anniversaries } = await api('GET', '/settings/anniversaries');
    _renderSetupAnniversaries(anniversaries || []);
  } catch (e) {
    toast(t('setup.anniversaries.load_error', '加载自定义纪念日失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

async function saveSetupAnniversaries() {
  const anniversaries = _readSetupAnniversaries();
  if (!anniversaries) return;
  try {
    await api('PUT', '/settings/anniversaries', { anniversaries });
    toast(t('setup.anniversaries.saved', '自定义纪念日已保存'), 'ok');
    _loadSetupAnniversaries();
  } catch (e) {
    toast(t('common.save_failed', '保存失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

async function _loadSetupDiary() {
  try {
    const diary = await api('GET', '/settings/diary');
    document.getElementById('setup-diary-path').value = diary.obsidian_path || '';
    document.getElementById('setup-diary-status').innerHTML = _setupBadge(diary.configured);
  } catch (e) {
    toast(t('setup.diary.load_error', '加载日记路径失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

async function saveSetupDiary() {
  const path = document.getElementById('setup-diary-path').value.trim();
  if (!path) { toast(t('setup.diary.required', 'obsidian_path 不能为空'), 'err'); return; }
  try {
    await api('PUT', '/settings/diary', { obsidian_path: path });
    toast(t('setup.diary.saved', '日记路径已保存'), 'ok');
    _loadSetupDiary();
  } catch (e) {
    toast(t('common.save_failed', '保存失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

async function _loadSetupCoplayGames() {
  try {
    const { game_whitelist } = await api('GET', '/settings/coplay-games');
    _renderSetupCoplayGames(game_whitelist || []);
  } catch (e) {
    toast(t('setup.coplay.load_error', '加载 coplay 游戏白名单失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

async function saveSetupCoplayGames() {
  const game_whitelist = _readSetupCoplayGames();
  if (!game_whitelist) return;
  try {
    await api('PUT', '/settings/coplay-games', { game_whitelist });
    toast(t('setup.coplay.saved', 'coplay 游戏白名单已保存'), 'ok');
    _loadSetupCoplayGames();
  } catch (e) {
    toast(t('common.save_failed', '保存失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

function _renderSetupAnniversaries(values) {
  renderAnniversaryEditor(document.getElementById('setup-anniversaries-list'), values, {removeAction: 'removeSetupRow'});
}
function addSetupAnniversary() {
  addAnniversaryEditorRow(document.getElementById('setup-anniversaries-list'), {removeAction: 'removeSetupRow'});
}
function _readSetupAnniversaries() {
  return readAnniversaryEditor(document.getElementById('setup-anniversaries-list'), {
    onValidationError: () => toast('Each anniversary needs key, month, and day.', 'err'),
  });
}
function _gameRow(value = {}) { return `<div class="form-row" data-setup-game><input type="text" data-name placeholder="game name" value="${escapeHtml(value.name || '')}"><input type="text" data-process placeholder="process_name" value="${escapeHtml(value.process_name || '')}"><input type="text" data-save-dir placeholder="save directory (optional)" value="${escapeHtml(value.save_dir || '')}"><button type="button" class="btn btn-ghost btn-sm" data-action="removeSetupRow">Remove</button></div>`; }
function _renderSetupCoplayGames(values) { const root=document.getElementById('setup-coplay-games-list'); root.innerHTML=(values.length?values:[{}]).map(_gameRow).join(''); bindPageActions(root); }
function addSetupCoplayGame() { const root=document.getElementById('setup-coplay-games-list'); root.insertAdjacentHTML('beforeend', _gameRow()); bindPageActions(root); }
function _readSetupCoplayGames() { const result=[];for(const row of document.querySelectorAll('[data-setup-game]')){const name=row.querySelector('[data-name]').value.trim(),process_name=row.querySelector('[data-process]').value.trim(),save_dir=row.querySelector('[data-save-dir]').value.trim();if(!name&&!process_name&&!save_dir)continue;if(!name||!process_name){toast('Each game needs a name and process name.','err');return null;}result.push({...{name,process_name},...(save_dir?{save_dir}:{})});}return result; }
function removeSetupRow(button) {
  if (!removeAnniversaryEditorRow(button)) button.closest('[data-setup-game]')?.remove();
}

async function _loadSetupOptional() {
  const el = document.getElementById('setup-optional-body');
  try {
    const [mp, tts, vision] = await Promise.all([
      api('GET', '/model-presets'),
      api('GET', '/tts-config').catch(() => null),
      api('GET', '/vision-params').catch(() => null),
    ]);
    const profiles = mp.routing_profiles || {};
    const profile = profiles[mp.active_routing] || Object.values(profiles)[0] || {};
    const presetNames = Object.keys(mp.presets || {});
    const resolve = (cat) => profile[cat] || profile.chat || presetNames[0] || t('setup.no_preset', '（无 preset）');
    const enabled = escapeHtml(t('common.enabled', '已启用'));
    const disabled = escapeHtml(t('common.disabled', '未启用'));
    const unknown = escapeHtml(t('common.unknown', '未知'));

    el.innerHTML = `
      <div class="setup-optional-model-summary">
        <div class="setup-optional-summary-row">
          <span>TTS: ${tts ? (tts.enabled ? `<span class="badge badge-success">${enabled}</span>` : `<span class="badge">${disabled}</span>`) : `<span class="badge">${unknown}</span>`}</span>
          <button class="btn btn-ghost btn-sm" data-action="goto" data-action-args='["tts-config"]'>${escapeHtml(t('setup.go_tts', '前往 TTS 配置'))}</button>
        </div>
        <div class="setup-optional-summary-row">
          <span>Vision: ${vision ? (vision.enabled ? `<span class="badge badge-success">${escapeHtml(t('setup.vision_enabled', '已启用（{model}）', {model: vision.model || t('setup.no_model', '未填模型')}))}</span>` : `<span class="badge">${disabled}</span>`) : `<span class="badge">${unknown}</span>`}</span>
          <button class="btn btn-ghost btn-sm" data-action="goto" data-action-args='["model-routing"]'>${escapeHtml(t('setup.go_vision', '前往视觉模型配置'))}</button>
        </div>
        <div class="setup-optional-summary-row">
          <span>${escapeHtml(t('setup.probe_helper', 'probe 小模型'))} → <code class="setup-optional-mono-code">${escapeHtml(resolve('probe'))}</code>　${escapeHtml(t('setup.summary_helper', 'summary 小模型'))} → <code class="setup-optional-mono-code">${escapeHtml(resolve('summary'))}</code></span>
          <button class="btn btn-ghost btn-sm" data-action="goto" data-action-args='["model-routing"]'>${escapeHtml(t('setup.go_routing', '前往模型路由'))}</button>
        </div>
      </div>
    `;
    bindPageActions(el);
  } catch (e) {
    el.innerHTML = `<div class="empty">${escapeHtml(t('setup.optional_load_error', '加载失败: {error}', {error: _setupErrMsg(e)}))}</div>`;
  }
}

async function saveSetupBaseModel() {
  const body = {};
  const url   = document.getElementById('setup-base-url').value.trim();
  const model = document.getElementById('setup-base-model-name').value.trim();
  const key   = document.getElementById('setup-base-api-key').value.trim();
  if (url)   body.base_url = url;
  if (model) body.model = model;
  if (key)   body.api_key = key;
  if (!Object.keys(body).length) { toast(t('setup.no_changes', '没有要保存的修改'), 'err'); return; }
  try {
    await api('PUT', '/settings/base-model', body);
    toast(t('setup.base.saved', '基础聊天模型已保存'), 'ok');
    loadSetupPage();
  } catch (e) {
    toast(t('common.save_failed', '保存失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

async function testSetupBaseModel() {
  const btn = document.getElementById('setup-base-test-btn');
  const out = document.getElementById('setup-base-test-result');
  const name = window._setupBasePresetName || 'legacy';
  btn.disabled = true;
  out.style.color = 'var(--muted)';
  out.textContent = t('setup.base.testing', '测试中…');
  try {
    const r = await api('POST', `/model-presets/presets/${encodeURIComponent(name)}/test`);
    if (r.ok) {
      out.style.color = 'var(--success)';
      out.textContent = t('setup.base.test_ok', '✓ {latency}ms · {model}', {latency: r.latency_ms, model: r.model});
    } else {
      out.style.color = 'var(--danger)';
      out.textContent = t('setup.base.test_fail', '✗ {error}', {error: r.error || t('setup.base.test_unknown_error', '未知错误')});
    }
  } catch (e) {
    out.style.color = 'var(--danger)';
    out.textContent = t('setup.base.test_fail', '✗ {error}', {error: _setupErrMsg(e)});
  } finally {
    btn.disabled = false;
  }
}

async function saveSetupOwner() {
  const ownerId = document.getElementById('setup-owner-id').value.trim();
  const birthday = document.getElementById('setup-owner-birthday').value.trim();
  if (ownerId && !/^[A-Za-z0-9_-]+$/.test(ownerId)) {
    toast(t('setup.owner.invalid_id', 'owner_id 只能包含字母、数字、下划线、短横线（A-Za-z0-9_-）'), 'err');
    return;
  }
  if (birthday && !/^\d{2}-\d{2}$/.test(birthday)) {
    toast(t('setup.owner.invalid_birthday', 'owner_birthday 必须是 MM-DD 格式，如 04-24'), 'err');
    return;
  }
  try {
    await api('PUT', '/scheduler/config', { owner_id: ownerId, owner_birthday: birthday });
    toast(t('setup.owner.saved', 'owner_id 已保存'), 'ok');
    loadSetupPage();
  } catch (e) {
    toast(t('common.save_failed', '保存失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

async function saveSetupEmbedding() {
  const body = {};
  const url   = document.getElementById('setup-embed-base-url').value.trim();
  const model = document.getElementById('setup-embed-model').value.trim();
  const dim   = document.getElementById('setup-embed-dim').value.trim();
  const key   = document.getElementById('setup-embed-api-key').value.trim();
  if (url)   body.base_url = url;
  if (model) body.model = model;
  if (dim)   body.dim = parseInt(dim, 10);
  if (key)   body.api_key = key;
  if (!Object.keys(body).length) { toast(t('setup.no_changes', '没有要保存的修改'), 'err'); return; }
  try {
    await api('PUT', '/settings/embedding', body);
    toast(t('setup.embedding.saved', 'Embedding 配置已保存'), 'ok');
    loadSetupEmbedding();
  } catch (e) {
    toast(t('common.save_failed', '保存失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

// ══════════════════════════════════════════════════════════
//  Auth
// ══════════════════════════════════════════════════════════
async function tryLogin(key, silent) {
  TOKEN = key;
  try {
    const r = await fetch(`${BASE}/status`, {headers: authHeaders()});
    if (r.status === 401) {
      TOKEN = '';
      localStorage.removeItem('qq_admin_key');
      document.getElementById('auth-err').textContent = silent
        ? t('dynamic.auth.stale_key', 'Token 已失效，请重新输入')
        : t('dynamic.auth.invalid_key', '密钥错误，请重试');
      return false;
    }
    localStorage.setItem('qq_admin_key', key);
    document.getElementById('auth-overlay').style.display = 'none';
    document.getElementById('app').style.display = 'flex';
    document.getElementById('nav-host').textContent = window.location.host;
    document.getElementById('nav-key-hint').textContent = t('dynamic.auth.key_prefix', '密钥: {value}', {value: key.slice(0,4) + '…'});
    const setupStatus = await checkSetupStatus();
    const initialPage = setupStatus && setupStatus.needs_setup
      ? 'setup'
      : getRememberedPage() || 'overview';
    goto(initialPage);
    restoreNavGroups();
    _initCharName().catch(() => {});
    initSecretsBookFab().catch(() => {});
    return true;
  } catch(e) {
    TOKEN = '';
    document.getElementById('auth-err').textContent = t('dynamic.auth.connect_failed', '连接失败: {error}', {error: e.message});
    return false;
  }
}

async function doLogin() {
  const key = document.getElementById('key-input').value.trim();
  if (!key) return;
  await tryLogin(key, false);
}

function logout() {
  localStorage.removeItem('qq_admin_key');
  clearRememberedPage();
  TOKEN = '';
  document.getElementById('key-input').value = '';
  document.getElementById('auth-err').textContent = '';
  document.getElementById('app').style.display = 'none';
  document.getElementById('auth-overlay').style.display = 'flex';
}

document.getElementById('key-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') doLogin();
});

// Auto-login if key stored: silently verify, only surface the input if it's rejected
if (TOKEN) {
  tryLogin(TOKEN, true).catch(() => {});
}

// ══════════════════════════════════════════════════════════
//  Nav group collapse
// ══════════════════════════════════════════════════════════
function toggleNavGroup(key){
  const g=document.getElementById('navgroup-'+key), c=document.getElementById('caret-'+key);
  if(!g) return;
  const willShow = g.style.display==='none';
  g.style.display = willShow?'':'none';
  if(c) c.textContent = willShow?'▾':'▸';
  const st=JSON.parse(localStorage.getItem('navGroupsCollapsed')||'{}');
  st[key]=!willShow;
  localStorage.setItem('navGroupsCollapsed',JSON.stringify(st));
}
function restoreNavGroups(){
  const st=JSON.parse(localStorage.getItem('navGroupsCollapsed')||'{}');
  const groups = document.querySelectorAll('[id^="navgroup-"]');
  for(const group of groups){
    const key = group.id.slice('navgroup-'.length);
    if(st[key]){
      const c=document.getElementById('caret-'+key);
      group.style.display='none';
      if(c) c.textContent='▸';
    }
  }
}

// ══════════════════════════════════════════════════════════
//  MCP 管理（Brief 110）
// ══════════════════════════════════════════════════════════

async function loadSetupEmbedding() {
  try {
    const {embedding: embed} = await api("GET", "/settings/setup-status");
    document.getElementById('setup-embed-base-url').value = embed.base_url || '';
    document.getElementById('setup-embed-model').value = embed.model || '';
    document.getElementById('setup-embed-dim').value = embed.dim || '';
    document.getElementById('setup-embed-api-key').value = '';
    document.getElementById('setup-embed-key-state').textContent = embed.api_key_set
      ? t('setup.secret.configured', '（已配置 {value}）', {value: embed.api_key_masked})
      : `（${t('common.not_configured', '未配置')}）`;
    document.getElementById('setup-embedding-status').innerHTML = embed.configured
      ? _setupBadge(true)
      : `<span class="badge">○ ${escapeHtml(t('setup.embedding.not_configured', '未配置（召回自动降级为关键词匹配，不影响聊天）'))}</span>`;


  } catch(error) { toast("读取失败：" + error.message, "err"); }
}
