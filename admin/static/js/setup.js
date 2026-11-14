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

async function checkSetupStatus(options = {}) {
  try { return await api('GET', '/settings/setup-status', undefined, options); }
  catch (e) { return null; }
}

let _setupLoadGeneration = 0;
const _setupInputSections = {
  owner: ['setup-owner-id', 'setup-owner-birthday'],
  base: ['setup-base-url', 'setup-base-model-name', 'setup-base-api-key'],
};
function _setupInputSnapshot() {
  return Object.fromEntries(Object.values(_setupInputSections).flat().map(id => [id, document.getElementById(id).value]));
}

async function loadSetupPage({section = null, expectedValues = _setupInputSnapshot()} = {}) {
  const generation = ++_setupLoadGeneration;
  const setInput = (id, value) => {
    const el = document.getElementById(id);
    if ((!section || _setupInputSections[section].includes(id)) && el.value === expectedValues[id]) el.value = value;
  };
  document.getElementById('setup-missing-banner').style.display = 'none';
  document.getElementById('setup-optional-body').innerHTML = `<div class="loading">${escapeHtml(t('common.loading', '加载中…'))}</div>`;
  try {
    const [status, base, embed, chars] = await Promise.all([
      api('GET', '/settings/setup-status'),
      api('GET', '/settings/base-model'),
      api('GET', '/settings/embedding'),
      api('GET', '/characters').catch(() => null),
    ]);
    if (generation !== _setupLoadGeneration) return;
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

    setInput('setup-owner-id', owner.owner_id || '');
    setInput('setup-owner-birthday', owner.owner_birthday || '');
    document.getElementById('setup-owner-status').innerHTML = _setupBadge(owner.configured);

    setInput('setup-base-url', base.base_url || '');
    setInput('setup-base-model-name', base.model || '');
    setInput('setup-base-api-key', '');
    document.getElementById('setup-base-key-state').textContent = base.api_key_set
      ? t('setup.secret.configured', '（已配置 {value}）', {value: base.api_key_masked})
      : `（${t('common.not_configured', '未配置')}）`;
    document.getElementById('setup-base-status').innerHTML = _setupBadge(base.configured);

    await _loadSetupOptional(generation);

  } catch (e) {
    toast(t('setup.load_error', '加载配置中心失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

function gmailError(error) {
  const code = _setupErrMsg(error);
  return t('gmail.error.' + code, code);
}

async function loadSetupGmailOAuth() {
  const field = id => document.getElementById('setup-gmail-' + id);
  const mounted = field('client-id');
  if (!mounted) return;
  const state = await api('GET', '/settings/gmail/oauth/config');
  if (!mounted.isConnected) return;
  field('client-id').value = state.client_id || '';
  field('client-secret').value = '';
  field('redirect-uri').value = state.redirect_uri || '';
  field('secret-state').textContent = state.client_secret_set
    ? t('common.configured', '已配置') : t('common.not_configured', '未配置');
  field('oauth-status').textContent = state.configured
    ? t('common.configured', '已配置') : t('common.not_configured', '未配置');
  field('oauth-source').textContent = t('gmail.oauth.source', '当前来源：{source} · 版本：{revision}', {
    source: t('gmail.oauth.source.' + state.source, state.source), revision: state.credential_revision || '—',
  });
}

function setupGmailOAuthBody() {
  const field = id => document.getElementById('setup-gmail-' + id);
  if (!field('client-id').reportValidity() || !field('redirect-uri').reportValidity()) return null;
  return {client_id: field('client-id').value.trim(), client_secret: field('client-secret').value,
    redirect_uri: field('redirect-uri').value.trim()};
}

async function saveSetupGmailOAuth() {
  const body = setupGmailOAuthBody();
  if (!body) return;
  const button = document.getElementById('setup-gmail-oauth-save');
  button.disabled = true;
  button.textContent = t('gmail.oauth.saving', '正在保存…');
  try {
    await api('PUT', '/settings/gmail/oauth/config', body);
    document.getElementById('setup-gmail-client-secret').value = '';
    await loadSetupGmailOAuth();
    await loadSetupGmail();
    toast(t('gmail.oauth.saved', 'OAuth 配置已保存，请测试连接或授权'), 'ok');
  } catch (e) {
    toast(t('gmail.oauth.failed', 'OAuth 配置操作失败：{error}', {error: gmailError(e)}), 'err');
  } finally {
    body.client_secret = '';
    button.disabled = false;
    button.textContent = t('common.save', '保存');
  }
}

async function validateSetupGmailOAuth() {
  const body = setupGmailOAuthBody();
  if (!body) return;
  try {
    await api('POST', '/settings/gmail/oauth/config/validate', body);
    toast(t('gmail.oauth.valid', '格式有效；尚未验证 Google 授权'), 'ok');
  } catch (e) {
    toast(t('gmail.oauth.failed', 'OAuth 配置操作失败：{error}', {error: gmailError(e)}), 'err');
  } finally {
    body.client_secret = '';
  }
}

async function copySetupGmailCallback() {
  try {
    const state = await api('GET', '/settings/gmail/oauth/config');
    if (!state.configured) throw new Error(t('gmail.state.not_configured', '配置未完成'));
    await navigator.clipboard.writeText(state.redirect_uri);
    toast(t('gmail.oauth.copied', '已复制当前有效回调地址'), 'ok');
  } catch (e) {
    toast(t('gmail.oauth.failed', 'OAuth 配置操作失败：{error}', {error: gmailError(e)}), 'err');
  }
}

async function loadSetupGmail() {
  const field = id => document.getElementById('setup-gmail-' + id);
  const mounted = field('status');
  if (!mounted) return;
  try {
    const state = await api('GET', '/settings/gmail');
    if (!mounted.isConnected) return;
    field('status').textContent = state.bound_elsewhere ? t('gmail.bound_elsewhere', '邮箱已绑定到其他角色')
      : t('gmail.state.' + state.token_state, state.token_state);
    if (state.email) field('email').value = state.email;
    field('connect').disabled = !state.configured || state.bound_elsewhere;
    field('test').disabled = !state.connected;
    field('save').disabled = !state.connected;
    field('disconnect').disabled = !state.connected;
    field('sync').checked = !!state.sync_enabled;
    field('interval').value = state.sync_interval_seconds;
    const scopes = (state.scopes || []).map(scope => scope.split('/').pop()).join('、');
    const lastSync = state.last_sync_at ? new Date(state.last_sync_at * 1000).toLocaleString() : '—';
    const retry = state.sync_retry_at ? new Date(state.sync_retry_at * 1000).toLocaleString() : '';
    field('detail').textContent = [
      scopes ? t('gmail.scopes', '授权范围 {value}', {value: scopes}) : '',
      state.connected ? t(state.sync_effective ? 'gmail.sync_on' : 'gmail.sync_off', state.sync_effective ? '同步已启用' : '同步未生效') : '',
      state.connected ? t('gmail.last_sync', '上次成功同步 {value}', {value: lastSync}) : '',
      state.error ? gmailError(state.error) : '',
      state.sync_error ? t('gmail.sync_error', '同步错误 {value}', {value: gmailError(state.sync_error)}) : '',
      retry ? t('gmail.retry', '下次尝试 {value}', {value: retry}) : '',
    ].filter(Boolean).join(' · ');
    field('pending-status').textContent = state.pending_count == null ? '' : [
      t('gmail.pending.count', '待处理 {pending} · 近期已处理 {handled}', {
        pending: state.pending_count, handled: state.handled_count_recent,
      }),
      state.pending_sync_state === 'mailbox_unavailable' ? t('gmail.pending.unavailable', '邮箱暂不可用') : '',
      state.overflow ? t('gmail.pending.overflow', '待办已满，同步保留进度并等待可用容量') : '',
    ].filter(Boolean).join(' · ');
    const pending = field('pending');
    pending.hidden = !state.mailbox_identity_valid;
    pending.ontoggle = () => {
      if (pending.open && !pending.hidden) void loadSetupGmailPending();
    };
    if (pending.open && !pending.hidden) await loadSetupGmailPending();
    else field('pending-items').replaceChildren();
    if (!mounted.isConnected) return;
    field('unknown').hidden = !state.unknown_delivery_count;
    field('unknown-ids').textContent = (state.unknown_deliveries || [])
      .map(row => row.message_id).join(' · ');
  } catch (e) {
    if (mounted.isConnected) mounted.textContent = t('gmail.load_failed', '读取 Gmail 状态失败 {error}', {error: gmailError(e)});
  }
}

async function loadSetupGmailPending(cursor = '') {
  const list = document.getElementById('setup-gmail-pending-items');
  const next = document.getElementById('setup-gmail-pending-next');
  list.replaceChildren();
  next.hidden = true;
  try {
    const page = await api('GET', '/settings/gmail/pending?cursor=' + encodeURIComponent(cursor));
    for (const item of page.items) {
      const row = document.createElement('li');
      const status = t('gmail.pending.' + item.status, item.status);
      const timestamp = new Date(item.first_seen_at * 1000).toLocaleString();
      const unavailable = item.availability === 'unavailable'
        ? ' · ' + t('gmail.pending.message_unavailable', '邮件已移出收件箱或不可读取') : '';
      row.textContent = status + ' · ' + timestamp + unavailable + ' ';
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn btn-ghost btn-sm';
      button.textContent = item.status === 'pending'
        ? t('gmail.pending.mark', '标记已处理') : t('gmail.pending.reopen', '重新打开');
      button.onclick = async () => {
        button.disabled = true;
        try {
          await api('PUT', '/settings/gmail/pending', {
            pending_id: item.pending_id, revision: item.revision, handled: item.status === 'pending',
          });
          await loadSetupGmail();
        } catch (e) {
          toast(t('gmail.load_failed', '读取 Gmail 状态失败 {error}', {error: gmailError(e)}), 'err');
          await loadSetupGmailPending(cursor);
        }
      };
      row.append(button);
      list.append(row);
    }
    if (!page.items.length) {
      const row = document.createElement('li');
      row.textContent = t('gmail.pending.empty', '暂无本地待办记录');
      list.append(row);
    }
    next.hidden = !page.next_cursor;
    next.onclick = () => loadSetupGmailPending(page.next_cursor);
  } catch (e) {
    const row = document.createElement('li');
    row.textContent = t('gmail.load_failed', '读取 Gmail 状态失败 {error}', {error: gmailError(e)});
    list.append(row);
  }
}

async function connectSetupGmail() {
  const email = document.getElementById('setup-gmail-email');
  if (!email.value.trim() || !email.reportValidity()) {
    toast(t('gmail.enter_email', '请填写角色的 Google 邮箱'), 'err');
    return;
  }
  if (!document.getElementById('setup-gmail-consent').checked) {
    toast(t('gmail.confirm_consent', '请确认模型服务处理说明'), 'err');
    return;
  }
  const popup = window.open('about:blank', '_blank');
  if (!popup) {
    toast(t('gmail.allow_popup', '请允许浏览器打开授权窗口'), 'err');
    return;
  }
  popup.opener = null;
  try {
    const result = await api('POST', '/settings/gmail/oauth/start', {
      email: email.value.trim(), model_processing_consent: true,
    });
    popup.location.replace(result.authorization_url);
  } catch (e) {
    popup.close();
    toast(t('gmail.connect_failed', '无法启动授权 {error}', {error: gmailError(e)}), 'err');
  }
}

async function testSetupGmail() {
  try {
    await api('POST', '/settings/gmail/test');
    toast(t('gmail.test_ok', 'Google 邮箱授权有效'), 'ok');
  } catch (e) {
    toast(t('gmail.test_failed', '测试连接失败 {error}', {error: gmailError(e)}), 'err');
  }
  await loadSetupGmail();
}

async function disconnectSetupGmail() {
  if (!window.confirm(t('gmail.disconnect_confirm', '断开此角色的 Google 邮箱，并请求撤销 Google 授权？'))) return;
  try {
    const result = await api('POST', '/settings/gmail/disconnect');
    toast(result.result === 'local_disconnect_upstream_unconfirmed'
      ? t('gmail.disconnect_local', '本地已断开，请在 Google 账号中手动撤销授权') : t('gmail.disconnected', '邮箱已断开'), 'ok');
    await loadSetupGmail();
  } catch (e) {
    toast(t('gmail.disconnect_failed', '断开失败 {error}', {error: gmailError(e)}), 'err');
  }
}

async function saveSetupGmailSync() {
  const interval = document.getElementById('setup-gmail-interval');
  if (!interval.reportValidity()) return;
  try {
    await api('PUT', '/settings/gmail/sync', {
      enabled: document.getElementById('setup-gmail-sync').checked,
      interval_seconds: Number(interval.value),
    });
    await loadSetupGmail();
    toast(t('gmail.sync_saved', '同步设置已保存'), 'ok');
  } catch (e) {
    toast(t('gmail.sync_failed', '保存同步设置失败 {error}', {error: gmailError(e)}), 'err');
  }
}

async function _loadSetupMail({inline = false} = {}) {
  const mounted = document.getElementById('setup-mail-enabled');
  if (!mounted) return;
  const gmail = Promise.all([loadSetupGmail(), loadSetupGmailOAuth()]).catch(e => e);
  try {
    const mail = await api('GET', '/settings/mail');
    if (!mounted.isConnected) return;
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
    document.getElementById('setup-mail-connection-mode').value = mail.connection_mode || 'auto';
    document.getElementById('setup-mail-backend').value = mail.backend || 'smtp';
    document.getElementById('setup-mail-status').innerHTML = _setupBadge(mail.configured);
    const failure = await gmail;
    if (failure && mounted.isConnected) throw failure;
  } catch (e) {
    if (!mounted.isConnected) return;
    if (inline) throw e;
    toast(t('setup.mail.load_error', '加载邮件通道配置失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

async function saveSetupMail() {
  const body = {
    enabled: document.getElementById('setup-mail-enabled').checked,
    backend: document.getElementById('setup-mail-backend').value,
    connection_mode: document.getElementById('setup-mail-connection-mode').value,
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

async function _loadSetupAnniversaries({inline = false} = {}) {
  const mounted = document.getElementById('setup-anniversaries-list');
  if (!mounted) return;
  try {
    const { anniversaries } = await api('GET', '/settings/anniversaries');
    if (!mounted.isConnected) return;
    _renderSetupAnniversaries(anniversaries || []);
  } catch (e) {
    if (!mounted.isConnected) return;
    if (inline) throw e;
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

let _setupDiaryCharId = null;
async function _loadSetupDiary({inline = false} = {}) {
  const mounted = document.getElementById('setup-diary-path');
  if (!mounted) return;
  try {
    const diary = await api('GET', '/settings/diary');
    if (!mounted.isConnected) return;
    _setupDiaryCharId = diary.char_id;
    mounted.value = diary.obsidian_path || '';
    document.getElementById('setup-diary-status').innerHTML = _setupBadge(diary.configured);
  } catch (e) {
    if (!mounted.isConnected) return;
    if (inline) throw e;
    toast(t('setup.diary.load_error', '加载日记路径失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

async function saveSetupDiary() {
  const path = document.getElementById('setup-diary-path').value.trim();
  if (!path) { toast(t('setup.diary.required', 'obsidian_path 不能为空'), 'err'); return; }
  try {
    if (!_setupDiaryCharId) throw new Error('请先加载角色日记设置');
    await api('PUT', '/settings/diary?char_id=' + encodeURIComponent(_setupDiaryCharId), { obsidian_path: path });
    toast(t('setup.diary.saved', '日记路径已保存'), 'ok');
    _loadSetupDiary();
  } catch (e) {
    toast(t('common.save_failed', '保存失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

async function _loadSetupCoplayGames({inline = false} = {}) {
  const mounted = document.getElementById('setup-coplay-games-list');
  if (!mounted) return;
  refreshSetupCoplayStatus();
  try {
    const { game_whitelist } = await api('GET', '/settings/coplay-games');
    if (!mounted.isConnected) return;
    _renderSetupCoplayGames(game_whitelist || []);
  } catch (e) {
    if (!mounted.isConnected) return;
    if (inline) throw e;
    toast(t('setup.coplay.load_error', '加载 coplay 游戏白名单失败: {error}', {error: _setupErrMsg(e)}), 'err');
  }
}

async function refreshSetupCoplayStatus() {
  const root = document.getElementById('setup-coplay-collection-status');
  if (!root) return;
  try {
    const state = await api('GET', '/coplay/state');
    const collection = state.collection;
    if (!collection) { root.textContent = t('setup.coplay.no_collection', '后端尚未提供采集状态'); return; }
    if (collection.source === 'backend_local') {
      root.textContent = t('setup.coplay.local_collection', '本机部署：使用后端所在电脑采集。');
      return;
    }
    const freshness = collection.fresh ? t('setup.coplay.fresh', '设备报告有效') : t('setup.coplay.stale', '设备离线或报告已过期');
    const permission = collection.frame_authorized ? t('setup.coplay.frames_on', '已授权游戏窗口截图') : t('setup.coplay.frames_off', '当前未授权截图');
    const reason = collection.blocked_reason ? ` · ${collection.blocked_reason}` : '';
    root.textContent = `${t('setup.coplay.device_source', '用户电脑采集')} · ${freshness} · ${permission}${reason}`;
  } catch (error) {
    root.textContent = t('setup.coplay.collection_error', '采集状态读取失败：{error}', {error: _setupErrMsg(error)});
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

async function _loadSetupOptional(generation = _setupLoadGeneration) {
  const el = document.getElementById('setup-optional-body');
  try {
    const [mp, tts, vision] = await Promise.all([
      api('GET', '/model-presets'),
      api('GET', '/tts-config').catch(() => null),
      api('GET', '/vision-params').catch(() => null),
    ]);
    if (generation !== _setupLoadGeneration) return;
    const effective = (mp.routing_effective || {})[mp.active_routing] || {};
    const resolve = (cat) => effective[cat]?.effective_preset || t('setup.no_preset', '（无 preset）');
    const enabled = escapeHtml(t('common.enabled', '已启用'));
    const disabled = escapeHtml(t('common.disabled', '未启用'));
    const unknown = escapeHtml(t('common.unknown', '未知'));

    el.innerHTML = `
      <div class="setup-optional-model-summary">
        <div class="setup-optional-summary-row">
          <span>TTS: ${tts ? (tts.enabled ? `<span class="badge badge-success">${enabled}</span>` : `<span class="badge">${disabled}</span>`) : `<span class="badge">${unknown}</span>`}</span>
          <button class="btn btn-ghost btn-sm" data-action="goto" data-action-args='["tts-config"]'>${escapeHtml(t('setup.go_tts', '前往语音合成与声音（TTS、STT）'))}</button>
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
  const expectedValues = _setupInputSnapshot();
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
    await loadSetupPage({section: 'base', expectedValues});
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
  const expectedValues = _setupInputSnapshot();
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
    await loadSetupPage({section: 'owner', expectedValues});
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
let _loginSequence = 0;
let _loginController = null;
let _loginBootstrapController = null;
function setLoginBusy(busy, silent = false) {
  document.querySelector('[data-action="doLogin"]').disabled = busy && !silent;
  document.getElementById('auth-progress').textContent = busy ? t('auth.verifying', '正在验证管理密钥…') : '';
}
async function tryLogin(key, silent) {
  const sequence = ++_loginSequence;
  _loginController?.abort();
  _loginBootstrapController?.abort();
  const controller = new AbortController();
  _loginController = controller;
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 10000);
  setLoginBusy(true, silent);
  document.getElementById('auth-err').textContent = '';
  try {
    // Keep credentials local to this request: stale auto-login cannot overwrite a manual login.
    const r = await fetch(`${BASE}/auth/whoami`, {headers: {'Authorization': `Bearer ${key}`}, signal: controller.signal});
    if (sequence !== _loginSequence) return false;
    if (r.status === 401) {
      TOKEN = '';
      try { localStorage.removeItem('qq_admin_key'); } catch (_) {}
      document.getElementById('auth-err').textContent = silent
        ? t('dynamic.auth.stale_key', 'Token 已失效，请重新输入')
        : t('dynamic.auth.invalid_key', '密钥错误，请重试');
      return false;
    }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const identity = await r.json();
    if (sequence !== _loginSequence) return false;
    if (!Array.isArray(identity.scopes) || !identity.scopes.includes('admin')) {
      document.getElementById('auth-err').textContent = t('auth.admin_required', '这个密钥有效，但没有管理权限，请使用管理密钥。');
      return false;
    }
    TOKEN = key;
    try { localStorage.setItem('qq_admin_key', key); } catch (_) {}
    document.getElementById('auth-overlay').style.display = 'none';
    document.getElementById('app').style.display = 'flex';
    document.getElementById('nav-host').textContent = window.location.host;
    document.getElementById('nav-key-hint').textContent = t('dynamic.auth.key_prefix', '密钥: {value}', {value: key.slice(0,4) + '…'});
    restoreNavGroups();
    const initialPage = getRememberedPage() || 'setup';
    Promise.resolve(goto(initialPage)).catch(() => toast(t('auth.page_retry', '页面加载失败，请点击菜单重试。'), 'err'));
    // Setup/language lookups cannot keep an authenticated user trapped behind the login form.
    const bootstrap = new AbortController();
    _loginBootstrapController = bootstrap;
    const bootstrapTimeout = setTimeout(() => bootstrap.abort(), 8000);
    Promise.allSettled([loadAdminPromptLanguage({signal: bootstrap.signal}), checkSetupStatus({signal: bootstrap.signal})]).then(results => {
      if (sequence !== _loginSequence) return;
      if (results[0].status === 'rejected') toast(t('auth.language_retry', '语言设置暂时不可用，已保留当前界面语言。'), 'err');
      const status = results[1].status === 'fulfilled' ? results[1].value : null;
      if (status?.needs_setup && document.querySelector('.page.active')?.id === 'page-' + initialPage && initialPage !== 'setup') goto('setup');
    }).finally(() => clearTimeout(bootstrapTimeout));
    _initCharName().catch(() => {});
    initSecretsBookFab().catch(() => {});
    return true;
  } catch(e) {
    if (sequence !== _loginSequence) return false;
    document.getElementById('auth-err').textContent = timedOut
      ? t('auth.timeout', '连接超时，请检查管理隧道后重试；这不代表密钥错误。')
      : t('dynamic.auth.connect_failed', '连接失败: {error}', {error: e.message});
    return false;
  } finally {
    clearTimeout(timeout);
    if (sequence === _loginSequence) setLoginBusy(false);
  }
}

async function doLogin() {
  const key = document.getElementById('key-input').value.trim();
  if (!key) return;
  await tryLogin(key, false);
}

function logout() {
  ++_loginSequence;
  _loginController?.abort();
  _loginBootstrapController?.abort();
  setLoginBusy(false);
  _promptLanguageReady = false;
  try { localStorage.removeItem('qq_admin_key'); } catch (_) {}
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
function readNavGroups() {
  try {
    const state = JSON.parse(localStorage.getItem('navGroupsCollapsed') || '{}');
    return state && typeof state === 'object' && !Array.isArray(state) ? state : {};
  } catch (_error) { return {}; }
}
function setNavGroupExpanded(key, expanded, persist = true) {
  const group = document.getElementById('navgroup-' + key);
  if (!group) return;
  group.hidden = !expanded;
  document.getElementById('nav-toggle-' + key)?.setAttribute('aria-expanded', String(expanded));
  const caret = document.getElementById('caret-' + key);
  if (caret) caret.textContent = expanded ? '▾' : '▸';
  if (persist) {
    try {
      const state = readNavGroups();
      state[key] = !expanded;
      localStorage.setItem('navGroupsCollapsed', JSON.stringify(state));
    } catch (_error) { /* Collapsing works even when storage is unavailable. */ }
  }
}
function toggleNavGroup(key){
  const group = document.getElementById('navgroup-' + key);
  if (group) setNavGroupExpanded(key, group.hidden);
}
function restoreNavGroups(){
  const st = readNavGroups();
  const groups = document.querySelectorAll('[id^="navgroup-"]');
  for(const group of groups){
    const key = group.id.slice('navgroup-'.length);
    setNavGroupExpanded(key, !st[key], false);
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
