async function loadLlmParams() {
  try {
    const data = await api('GET', '/llm-params');
    const temp = parseFloat(data.temperature) || 0.7;
    const topp = parseFloat(data.top_p)       || 0.9;
    const fp   = parseFloat(data.frequency_penalty) || 0.0;
    const mt   = parseInt(data.max_tokens)    || 1500;
    document.getElementById('llm-temperature').value         = temp;
    document.getElementById('llm-temp-val').textContent      = temp.toFixed(2);
    document.getElementById('llm-top-p').value               = topp;
    document.getElementById('llm-topp-val').textContent      = topp.toFixed(2);
    document.getElementById('llm-max-tokens').value          = mt;
    document.getElementById('llm-max-tokens-val').textContent = mt;
    document.getElementById('llm-frequency-penalty').value   = fp;
    document.getElementById('llm-fp-val').textContent        = fp.toFixed(2);
  } catch(e) {
    toast(t('status.llm.load_error', '加载 LLM 参数失败'), 'err');
  }
}

async function saveLlmParams() {
  const temperature       = parseFloat(document.getElementById('llm-temperature').value);
  const top_p             = parseFloat(document.getElementById('llm-top-p').value);
  const max_tokens        = parseInt(document.getElementById('llm-max-tokens').value);
  const frequency_penalty = parseFloat(document.getElementById('llm-frequency-penalty').value);
  try {
    await api('PUT', '/llm-params', { temperature, top_p, max_tokens, frequency_penalty });
    toast(t('status.llm.saved', 'LLM 参数已保存'), 'ok');
  } catch(e) {
    toast(t('common.save_failed', '保存失败: {error}', {error: e.message || e}), 'err');
  }
}

// ══════════════════════════════════════════════════════════
//  Vision 配置
// ══════════════════════════════════════════════════════════
const VISION_PROVIDERS = {
  gemini: {
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai/",
    models: ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]
  },
  openai: {
    base_url: "https://api.openai.com/v1",
    models: ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"]
  },
  glm: {
    base_url: "https://open.bigmodel.cn/api/paas/v4/",
    models: ["glm-4v-flash", "glm-4v", "glm-4v-plus"]
  },
  custom: {
    base_url: "",
    models: []
  }
};

const _visionConnections = {general: null, ocr: null};
function toggleVisionEditor(id) {
  const editor = document.getElementById(id);
  editor.hidden = !editor.hidden;
  if (!editor.hidden) editor.querySelector('input,select,button')?.focus();
}
function renderImageConnections() {
  const root = document.getElementById('vision-connections-body');
  if (!root) return;
  root.innerHTML = [['general',t('routing.general_vision','通用视觉'),'vision-general-editor'],['ocr','OCR','vision-ocr-editor']].map(([key,label,id])=>{
    const d = _visionConnections[key];
    const ready = d && (key === 'general' ? d.enabled && d.model && d.base_url : d.configured);
    return `<tr><td>${escapeHtml(label)}</td><td>${escapeHtml(d?.provider || '—')}</td><td data-i18n-skip>${escapeHtml(d?.model || '—')}</td><td>${d ? (ready ? t('routing.ready_unchecked','已配置 · 未测试') : t('routing.not_ready','未配置或未启用')) : t('routing.load_unavailable','尚未读取或读取失败')}</td><td><button class="btn btn-ghost btn-sm" data-action="toggleVisionEditor" data-action-args='${JSON.stringify([id])}'>${t('common.edit','编辑')}</button><button class="btn btn-ghost btn-sm" data-action="testImageConnection" data-action-args='${JSON.stringify([key])}' ${ready ? '' : 'disabled'}>${t('routing.test_saved','测试已保存连接')}</button><span id="vision-test-${key}" role="status"></span></td></tr>`;
  }).join('');
  bindPageActions(root);
}
async function loadImageConnections() {
  await Promise.allSettled([loadVisionParams(), loadImageRecognition(), loadPhoneControlVisionParams()]);
}
async function testImageConnection(connection) {
  const state = document.getElementById('vision-test-' + connection);
  const button = document.querySelector(`[data-action="testImageConnection"][data-action-args='["${connection}"]']`);
  if (button) button.disabled = true;
  state.textContent = t('routing.testing','测试中…');
  try {
    const result = await api('POST', '/image-recognition/test/' + connection);
    state.textContent = result.ok ? t('routing.test_ok','连接可用 · {ms} ms',{ms:result.duration_ms}) : t('routing.test_failed','测试失败：{error}',{error:result.error_category});
  } catch(e) { state.textContent = t('routing.test_failed','测试失败：{error}',{error:e.message}); }
  finally { if (button) button.disabled = false; }
}
async function saveImageRoute() {
  try {
    await api('PUT', '/image-recognition', {mode: document.getElementById('image-recognition-mode').value});
    await loadImageRecognition();
    toast(t('common.saved','已保存'),'ok');
  } catch(e) { toast(t('common.save_failed','保存失败: {error}',{error:e.message}),'err'); }
}

function onVisionProviderChange() {
  const provider = document.getElementById('vision-provider').value;
  const info = VISION_PROVIDERS[provider] || VISION_PROVIDERS.custom;
  const address = document.getElementById('vision-base-url');
  if (!address.value.trim()) address.value = info.base_url;
  address.readOnly = false;
  const list = document.getElementById('vision-model-options');
  if (list) list.innerHTML = info.models.map(m => `<option value="${escapeHtml(m)}"></option>`).join('');
  discoverVisionModels().catch(() => {});
}

async function discoverVisionModels() {
  const status = document.getElementById('vision-model-discovery-status');
  if (status) status.textContent = '读取中…';
  const base_url = document.getElementById('vision-base-url').value.trim();
  if (!base_url) return;
  const r = await api('POST', '/model-presets/discover', {base_url, api_key: document.getElementById('vision-api-key').value.trim(), api_protocol: document.getElementById('vision-api-protocol').value});
  const list = document.getElementById('vision-model-options');
  if (list && Array.isArray(r.models)) list.innerHTML = r.models.map(m => `<option value="${escapeHtml(typeof m === 'string' ? m : m.id || '')}"></option>`).join('');
  if (status) status.textContent = `已读取 ${Array.isArray(r.models) ? r.models.length : 0} 个模型，可手动填写`;
}

function onVisionModelSelect() {}

async function loadVisionParams() {
  try {
    const data = await api('GET', '/vision-params');
    _visionConnections.general = data;
    document.getElementById('vision-enabled').checked = data.enabled;
    const provider = data.provider || 'gemini';
    document.getElementById('vision-provider').value = provider;
    document.getElementById('vision-provider').onchange = onVisionProviderChange;
    document.getElementById('vision-base-url').value = data.base_url || '';
    document.getElementById('vision-api-protocol').value = data.api_protocol || 'chat_completions';
    onVisionProviderChange();
    if (data.model) {
      document.getElementById('vision-model-select').value = data.model;
    }
    document.getElementById('vision-api-key').value = '';
    if (data.base_url) document.getElementById('vision-base-url').value = data.base_url;
  } catch(e) {
    _visionConnections.general = null;
    console.error('加载Vision配置失败', e);
  } finally {
    renderImageConnections();
  }
}

async function saveVisionParams() {
  const provider = document.getElementById('vision-provider').value;
  const model = document.getElementById('vision-model-select').value;
  const body = {
    enabled:  document.getElementById('vision-enabled').checked,
    provider: provider,
    api_key:  document.getElementById('vision-api-key').value.trim(),
    model:    model,
    base_url: document.getElementById('vision-base-url').value.trim(),
    api_protocol: document.getElementById('vision-api-protocol').value,
  };
  try {
    await api('PUT', '/vision-params', body);
    document.getElementById('vision-api-key').value = '';
    await loadVisionParams();
    toast(t('common.saved', '已保存'), 'ok');
  } catch(e) {
    toast(t('common.save_failed', '保存失败: {error}', {error: e.message || e}), 'err');
  }
}

function renderOcrProtocol() {
  const layout = document.getElementById('ocr-protocol').value === 'glm_layout_parsing';
  document.getElementById('ocr-endpoint-field').hidden = !layout;
  document.getElementById('ocr-base-field').hidden = layout;
  document.getElementById('ocr-endpoint-field').style.display = layout ? '' : 'none';
  document.getElementById('ocr-base-field').style.display = layout ? 'none' : '';
  const address = document.getElementById(layout ? 'ocr-endpoint' : 'ocr-base-url').value.trim();
  document.getElementById('ocr-request-url').textContent = address ?
    (layout ? address : address.replace(/\/+$/, '') + '/chat/completions') : '-';
}

async function loadImageRecognition() {
  try {
    const data = await api('GET', '/image-recognition');
    _visionConnections.ocr = data;
    for (const [id, key] of Object.entries({
      'image-recognition-mode': 'mode', 'ocr-provider': 'provider', 'ocr-protocol': 'api_protocol',
      'ocr-model': 'model', 'ocr-base-url': 'base_url', 'ocr-endpoint': 'endpoint_url',
    })) document.getElementById(id).value = data[key] || '';
    document.getElementById('ocr-api-key').value = '';
    document.getElementById('ocr-key-state').textContent = data.has_api_key ? '密钥已配置' : '密钥未配置';
    document.getElementById('image-recognition-state').textContent =
      `普通聊天图片用途：${data.mode === 'ocr' ? 'OCR 文字提取' : '图片理解'} · ${data.effective ? '配置就绪，未验证服务连接' : '配置不完整或未启用'}`;
    document.getElementById('ocr-protocol').onchange = renderOcrProtocol;
    document.getElementById('ocr-endpoint').oninput = renderOcrProtocol;
    document.getElementById('ocr-base-url').oninput = renderOcrProtocol;
    renderOcrProtocol();
  } catch (e) {
    _visionConnections.ocr = null;
    document.getElementById('image-recognition-state').textContent = `加载失败：${e.message}`;
  } finally {
    renderImageConnections();
  }
}

async function saveImageRecognition() {
  const body = {};
  for (const [id, key] of Object.entries({
    'ocr-provider': 'provider', 'ocr-protocol': 'api_protocol',
    'ocr-model': 'model', 'ocr-base-url': 'base_url', 'ocr-endpoint': 'endpoint_url', 'ocr-api-key': 'api_key',
  })) body[key] = document.getElementById(id).value.trim();
  try {
    await api('PUT', '/image-recognition', body);
    await loadImageRecognition();
    toast(t('common.saved', '已保存'), 'ok');
  } catch (e) {
    toast(t('common.save_failed', '保存失败: {error}', {error: e.message}), 'err');
  }
}

async function loadPhoneControlVisionParams() {
  try {
    const data = await api('GET', '/vision-params/phone-control');
    const enabled = document.getElementById('phone-vision-enabled');
    enabled.value = data.enabled === true ? 'true' : data.enabled === false ? 'false' : '';
    document.getElementById('phone-vision-model').value = data.model || '';
    document.getElementById('phone-vision-base-url').value = data.base_url || '';
    document.getElementById('phone-vision-api-key').value = data.api_key || '';
    const summary = document.getElementById('vision-phone-summary');
    if (summary) summary.textContent = data.enabled === false ? t('routing.disabled','已关闭') : data.model || t('status.phone_vision.inherit','继承通用配置');
  } catch (e) {
    toast(t('status.phone_vision.load_error', '读取手机自动化视觉覆盖失败: {error}', {error: e.message || e}), 'err');
  }
}

async function savePhoneControlVisionParams() {
  const enabled = document.getElementById('phone-vision-enabled').value;
  const body = {
    enabled: enabled === '' ? null : enabled === 'true',
    model: document.getElementById('phone-vision-model').value.trim(),
    base_url: document.getElementById('phone-vision-base-url').value.trim(),
    api_key: document.getElementById('phone-vision-api-key').value.trim(),
  };
  try {
    await api('PUT', '/vision-params/phone-control', body);
    await loadPhoneControlVisionParams();
    toast(t('status.phone_vision.saved', '手机自动化视觉覆盖已保存'), 'ok');
  } catch (e) {
    toast(t('common.save_failed', '保存失败: {error}', {error: e.message || e}), 'err');
  }
}

// ══════════════════════════════════════════════════════════
//  Proxy settings
// ══════════════════════════════════════════════════════════
async function loadProxy() {
  try {
    const data = await api('GET', '/proxy');
    document.getElementById('proxy-enabled').checked = !!data.enabled;
    document.getElementById('proxy-http').value   = data.http  || '';
    document.getElementById('proxy-https').value  = data.https || '';
    _updateProxyBadge(!!data.enabled);
  } catch(e) {
    toast(t('status.proxy.load_error', '读取代理配置失败: {error}', {error: e.message}), 'err');
  }
}

function _updateProxyBadge(enabled) {
  const badge = document.getElementById('proxy-status-badge');
  if (!badge) return;
  badge.textContent  = enabled ? t('common.enabled', '已启用') : t('common.disabled', '未启用');
  badge.className    = 'badge ' + (enabled ? 'badge-success' : 'badge-danger');
}

// 启用开关改变时实时更新徽标（视觉反馈）
document.addEventListener('DOMContentLoaded', () => {
  const cb = document.getElementById('proxy-enabled');
  if (cb) cb.addEventListener('change', () => _updateProxyBadge(cb.checked));
});

async function saveProxy() {
  const enabled = document.getElementById('proxy-enabled').checked;
  const http    = document.getElementById('proxy-http').value.trim();
  const https_  = document.getElementById('proxy-https').value.trim();
  try {
    await api('PUT', '/proxy', { enabled, http, https: https_ });
    _updateProxyBadge(enabled);
    toast(t('status.proxy.saved', '代理配置已保存并热重载'), 'ok');
  } catch(e) {
    toast(t('common.save_failed', '保存失败: {error}', {error: e.message}), 'err');
  }
}

// ══════════════════════════════════════════════════════════
//  Tool registry (read-only diagnostics, shown in status page)
// ══════════════════════════════════════════════════════════
async function loadToolRegistry() {
  const el = document.getElementById('tool-registry-list');
  if (!el) return;
  el.innerHTML = `<div class="loading">${escapeHtml(t('common.loading', '加载中…'))}</div>`;
  try {
    const d = await api('GET', '/tools/registry');
    const tools = d.tools || [];
    if (!tools.length) {
      el.innerHTML = `<div class="empty">${escapeHtml(t('status.tools.empty', '无已注册工具'))}</div>`;
      return;
    }
    const rows = tools.map(t =>
      `<tr>
        <td><strong>${escapeHtml(t.name)}</strong></td>
        <td style="color:var(--muted);font-size:12px">${escapeHtml(t.description || '')}</td>
      </tr>`
    ).join('');
    el.innerHTML = `<div class="tbl-wrap"><table>
      <thead><tr><th>${escapeHtml(t('status.tools.name', '工具'))}</th><th>${escapeHtml(t('status.tools.description', '描述'))}</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
  } catch(e) {
    el.innerHTML = `<div class="empty">${escapeHtml(t('status.tools.load_error', '加载失败：{error}', {error: e.message}))}</div>`;
  }
}

// ══════════════════════════════════════════════════════════
//  Utilities
// ══════════════════════════════════════════════════════════
function escapeHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Close modal on backdrop click
document.querySelectorAll('.modal-backdrop').forEach(bd => {
  bd.addEventListener('click', e => {
    if (e.target !== bd) return;
    if (bd.id === 'at-success-modal') { closeAtSuccessModal(); return; }
    bd.classList.remove('open');
  });
});

// ══════════════════════════════════════════════════════════
//  Token 管理（Brief 22：whoami / profiles / create / rotate / disable-enable）
// ══════════════════════════════════════════════════════════
let _atProfiles = {};
let _atWhoamiLabel = '';
let _atSuccessLabel = '';
let _atSuccessToken = '';

const _AT_LOCATION_BY_LABEL = {
  'desktop-main':   'PresenceKit-desktop/config/client.local.json → adminToken',
  'mobile-main':    '手机 app 系统设置 → Token 弹窗',
  'sensor-service': '历史已退役；确认无调用后可停用或删除',
  'watch-main':     'Watch 端配置',
  'esp32-device':   '固件配置（firmware/，烧录前写入）',
  'admin-panel':    '浏览器面板登录框（localStorage qq_admin_key）',
};

function _atErrMsg(e) {
  const m = /HTTP \d+: (.*)/s.exec(e.message || '');
  if (!m) return e.message || String(e);
  try { return JSON.parse(m[1]).detail || m[1]; } catch { return m[1]; }
}

// ── 通道开关（Brief 93 §4；复用 /settings/feature-flags，读写 qq.enabled / mail.enabled）──
const CHANNEL_TOGGLES = {
  qq:   { label: 'QQ 通道' },
  mail: { label: '邮件通道' },
};

async function loadChannelToggles() {
  const el = document.getElementById('ch-toggle-body'); if (!el) return;
  el.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const d = await api('GET', '/settings/feature-flags');
    const flags = d.flags || {};
    el.innerHTML = Object.entries(CHANNEL_TOGGLES).map(([name, meta]) => {
      const item = flags[name] || { enabled: false };
      return `<label class="checkbox-row" style="gap:9px;padding:9px 10px;border:1px solid var(--border);border-radius:6px;display:inline-flex;margin-right:10px">
        <input type="checkbox" data-channel-toggle="${name}" ${item.enabled ? 'checked' : ''} onchange="saveChannelToggle('${name}', this.checked)">
        <span>${meta.label}${item.restart_required ? ' <small style="color:var(--muted)">（重启后生效）</small>' : ''}</span>
      </label>`;
    }).join('');
  } catch (e) { el.innerHTML = `<div class="empty">加载失败: ${e.message}</div>`; }
}

async function saveChannelToggle(name, enabled) {
  const meta = CHANNEL_TOGGLES[name];
  try {
    const result = await api('PUT', '/settings/feature-flags', { flags: { [name]: enabled } });
    const restartRequired = (result.restart_required || []).includes(name);
    toast(t('dynamic.channel.saved', '{label}已{state}{restart}', {
      label: t('flag.' + name, meta.label),
      state: t(enabled ? 'dynamic.channel.on' : 'dynamic.channel.off', enabled ? '开启' : '关闭'),
      restart: restartRequired ? t('dynamic.channel.restart_suffix', '，重启后生效') : '',
    }), 'ok');
  } catch (e) {
    toast(e.message, 'err');
    loadChannelToggles();
  }
}

async function loadAuthTokens() {
  const body = document.getElementById('at-table-body');
  body.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const [whoami, tokensResp, profilesResp] = await Promise.all([
      api('GET', '/auth/whoami'),
      api('GET', '/auth/tokens'),
      api('GET', '/auth/profiles'),
    ]);
    _atWhoamiLabel = whoami.label;
    _atProfiles = profilesResp.profiles || {};
    document.getElementById('at-whoami').textContent = `当前身份: ${whoami.label}`;
    _renderAuthCapabilitySummary(whoami);
    _renderAuthTokensTable(tokensResp.tokens || []);
  } catch (e) {
    body.innerHTML = `<div class="empty">加载失败: ${_atErrMsg(e)}</div>`;
    const summary = document.getElementById('at-capability-summary');
    if (summary) summary.innerHTML = `<div class="admin-error-panel">身份与 scope 读取失败：${escapeHtml(_atErrMsg(e))}</div>`;
  }
}

function _renderAuthCapabilitySummary(whoami) {
  const el = document.getElementById('at-capability-summary');
  if (!el) return;
  const scopes = [...new Set(whoami.scopes || [])].sort();
  const profile = Object.entries(_atProfiles).find(([, values]) =>
    values.length === scopes.length && values.every(scope => scopes.includes(scope))
  )?.[0] || 'custom scopes';
  el.innerHTML = `<div class="admin-field-grid admin-field-grid-single">
    <div><div class="admin-source-hint">当前身份</div><strong>${escapeHtml(whoami.label || 'unknown')}</strong></div>
    <div><div class="admin-source-hint">Profile</div><strong>${escapeHtml(profile)}</strong></div>
    <div><div class="admin-source-hint">Scopes</div><div class="admin-scope-list">${scopes.length ? scopes.map(scope => `<span class="badge badge-accent">${escapeHtml(scope)}</span>`).join(' ') : '无 scope'}</div></div>
  </div>`;
}

function _renderAuthTokensTable(tokens) {
  const body = document.getElementById('at-table-body');
  const rows = [];

  rows.push(`
    <tr title="修改：config.yaml 的 admin.secret_key，或环境变量 YEXUAN_ADMIN_SECRET（env 优先）">
      <td><strong>legacy-admin</strong></td>
      <td>admin</td>
      <td><span class="badge badge-accent">◆ break-glass</span></td>
      <td>—</td>
      <td>—</td>
      <td></td>
    </tr>
  `);

  for (const t of tokens) {
    const isSelf = t.label === _atWhoamiLabel;
    const statusBadge = t.disabled
      ? '<span class="badge">○ disabled</span>'
      : '<span class="badge badge-success">● active</span>';
    const scopesText = (t.scopes || []).join(', ');
    const scopesShort = scopesText.length > 30 ? scopesText.slice(0, 30) + '…' : scopesText;
    const rawLabel = String(t.label || '');
    const safeLabel = escapeHtml(rawLabel);
    const safeScopes = escapeHtml(scopesText);
    rows.push(`
      <tr>
        <td>${safeLabel}${isSelf ? ' <span class="badge badge-warn" title="这是你当前登录使用的 token">当前</span>' : ''}</td>
        <td title="${safeScopes}">${escapeHtml(scopesShort)}</td>
        <td>${statusBadge}</td>
        <td>${t.created_at ? t.created_at.slice(0, 10) : '—'}</td>
        <td><span class="badge">${t.hash_prefix}</span></td>
        <td style="white-space:nowrap">
          <button class="btn btn-ghost btn-sm" onclick="confirmRotateToken('${safeLabel}')">Rotate</button>
          <button class="btn btn-ghost btn-sm" onclick="confirmToggleToken('${safeLabel}', ${!t.disabled})">${t.disabled ? 'Enable' : 'Disable'}</button>
          <button class="btn btn-ghost btn-sm" onclick="copyAtLabel('${safeLabel}')">Copy Label</button>
        </td>
      </tr>
    `);
  }

  body.innerHTML = `<div class="tbl-wrap"><table>
    <tr><th>Label</th><th>Scopes</th><th>Status</th><th>Created</th><th>Hash</th><th></th></tr>
    ${rows.join('')}
  </table></div>`;
}

function copyAtLabel(label) {
  navigator.clipboard.writeText(label).then(() => toast('已复制 label')).catch(() => toast('复制失败', 'err'));
}

// ── Create ──
function openCreateTokenModal() {
  document.getElementById('at-create-label').value = '';
  document.getElementById('at-create-err').textContent = '';
  const sel = document.getElementById('at-create-profile');
  sel.innerHTML = Object.keys(_atProfiles).map(p => `<option value="${p}">${p}</option>`).join('');
  renderAtCreateScopesHint();
  document.getElementById('at-create-modal').classList.add('open');
}
function closeCreateTokenModal() {
  document.getElementById('at-create-modal').classList.remove('open');
}
function renderAtCreateScopesHint() {
  const profile = document.getElementById('at-create-profile').value;
  const scopes = _atProfiles[profile] || [];
  document.getElementById('at-create-scopes-hint').textContent = scopes.length ? `包含 scope: ${scopes.join(', ')}` : '';
}
async function submitCreateToken() {
  const label = document.getElementById('at-create-label').value.trim();
  const profile = document.getElementById('at-create-profile').value;
  const errEl = document.getElementById('at-create-err');
  if (!/^[a-z0-9-]{1,32}$/.test(label)) {
    errEl.textContent = 'label 须匹配 ^[a-z0-9-]{1,32}$';
    return;
  }
  try {
    const resp = await api('POST', '/auth/tokens', { label, profile });
    closeCreateTokenModal();
    showAtSuccessModal(resp.label, resp.token);
    loadAuthTokens();
  } catch (e) {
    errEl.textContent = _atErrMsg(e);
  }
}

// ── 通用确认弹窗（Rotate / Disable-Enable） ──
function _openAtConfirm(title, bodyText, onConfirm) {
  document.getElementById('at-confirm-title').textContent = title;
  document.getElementById('at-confirm-body').textContent = bodyText;
  const btn = document.getElementById('at-confirm-btn');
  btn.onclick = async () => { await onConfirm(); closeAtConfirmModal(); };
  document.getElementById('at-confirm-modal').classList.add('open');
}
function closeAtConfirmModal() {
  document.getElementById('at-confirm-modal').classList.remove('open');
}

function confirmRotateToken(label) {
  let body = t('dynamic.tokens.rotate_warning_1', '⚠️ 将立即使旧 Token 失效。') + '\n\n' +
    t('dynamic.tokens.rotate_warning_2', '持有旧 token 的设备会开始认证失败（401），') + '\n' +
    t('dynamic.tokens.rotate_warning_3', '连续失败会触发 429 限速（重启后端可立即解除）。') + '\n' +
    t('dynamic.tokens.rotate_warning_4', '请在 Rotate 后尽快更新对应设备的配置。');
  if (label === _atWhoamiLabel) {
    body += '\n\n' + t('dynamic.tokens.current_operation_warning', '⚠️ 这是你当前登录面板使用的 token，操作后你需要用新值/break-glass 重新登录');
  }
  _openAtConfirm(`Rotate ${label}`, body, async () => {
    try {
      const resp = await api('POST', `/auth/tokens/${encodeURIComponent(label)}/rotate`);
      showAtSuccessModal(resp.label, resp.token);
      loadAuthTokens();
    } catch (e) {
      toast(`Rotate 失败: ${_atErrMsg(e)}`, 'err');
    }
  });
}

function confirmToggleToken(label, disabled) {
  const verb = disabled ? 'Disable' : 'Enable';
  let body = disabled
    ? t('dynamic.tokens.disable_warning', '停用后该 token 立即认证失败（401），持有它的设备需要用新 token 或重新 Enable 才能恢复。')
    : t('dynamic.tokens.enable_warning', '重新启用后该 token 立即恢复可用。');
  if (label === _atWhoamiLabel) {
    body += '\n\n' + t('dynamic.tokens.current_operation_warning', '⚠️ 这是你当前登录面板使用的 token，操作后你需要用新值/break-glass 重新登录');
  }
  _openAtConfirm(`${verb} ${label}`, body, async () => {
    try {
      await api('PATCH', `/auth/tokens/${encodeURIComponent(label)}`, { disabled });
      toast(t(disabled ? 'dynamic.tokens.disabled_state' : 'dynamic.tokens.enabled_state', disabled ? '{label} 已停用' : '{label} 已启用', {label}));
      loadAuthTokens();
    } catch (e) {
      toast(`${verb} 失败: ${_atErrMsg(e)}`, 'err');
    }
  });
}

// ── 成功弹窗（Create / Rotate 共用；明文仅显示一次） ──
function showAtSuccessModal(label, token) {
  _atSuccessLabel = label;
  _atSuccessToken = token;
  document.getElementById('at-success-token').textContent = token;
  document.getElementById('at-success-modal').classList.add('open');
}
function copyAtToken() {
  navigator.clipboard.writeText(_atSuccessToken).then(() => toast('已复制 Token')).catch(() => toast('复制失败', 'err'));
}
function copyAtPasswordEntry() {
  const location = _AT_LOCATION_BY_LABEL[_atSuccessLabel] || '待填写';
  const line = `  ${_atSuccessLabel}: { token: "${_atSuccessToken}", 配置位置: "${location}" }`;
  navigator.clipboard.writeText(line).then(() => toast('已复制密码本条目')).catch(() => toast('复制失败', 'err'));
}
function closeAtSuccessModal() {
  if (!confirm('已保存好了吗？关闭后无法再查看')) return;
  document.getElementById('at-success-modal').classList.remove('open');
  _atSuccessToken = '';
  _atSuccessLabel = '';
}

// ══════════════════════════════════════════════════════════
//  模型路由（model-presets）
// ══════════════════════════════════════════════════════════
let _featureFlags = {};
async function loadFeatureFlags() {
  const el = document.getElementById('feature-flags-grid'); if (!el) return;
  try { const d = await api('GET', '/settings/feature-flags'); _featureFlags = d.flags || {};
    el.innerHTML = Object.entries(_featureFlags).map(([name, item]) => `<label class="checkbox-row" style="gap:9px;padding:9px 10px;border:1px solid var(--border);border-radius:6px"><input type="checkbox" data-feature-flag="${name}" ${item.enabled ? 'checked' : ''}><span>${escapeHtml(t('flag.' + name, item.label))}<small style="display:block;color:var(--muted)">${name}${item.restart_required ? ` ${escapeHtml(t('dynamic.tokens.restart_effect', '（重启后生效）'))}` : ''}</small>${name === 'visual_perception' ? `<small style="display:block;color:var(--warning,#c77)">${escapeHtml(t('flag.visual_perception_hint', '此闸打开后，还需在桌宠客户端「设置→视觉观察」里单独打开本地开关，两处都开才会真正截图'))}</small>` : ''}</span></label>`).join('');
    if (_featureFlags.screen_observation) {
      const item = _featureFlags.screen_observation;
      el.innerHTML += `<div style="grid-column:1/-1"><small>${escapeHtml(item.description || '')} (${escapeHtml(item.effective_state || '')})</small><button type="button" onclick="showScreenObservationStatus()">${escapeHtml(t('screen.status', 'Screen requests and devices'))}</button><pre id="screen-observation-status" style="white-space:pre-wrap"></pre></div>`;
    }
  } catch (e) { el.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`; }
}
async function showScreenObservationStatus() {
  const el = document.getElementById('screen-observation-status');
  try { el.textContent = JSON.stringify(await api('GET', '/perception/screen/status'), null, 2); }
  catch (e) { el.textContent = e.message; }
}
async function saveFeatureFlags() { const flags = {}; document.querySelectorAll('[data-feature-flag]').forEach(el => flags[el.dataset.featureFlag] = el.checked); try { const result = await api('PUT', '/settings/feature-flags', { flags }); toast(result.message || t('common.saved', '已保存'), result.reload_status === 'restart_required' ? 'err' : 'ok'); loadFeatureFlags(); } catch (e) { toast(e.message, 'err'); } }

function _shadowListValue(id) {
  return [...new Set((document.getElementById(id)?.value || '')
    .split(/[\n,]/).map(value => value.trim()).filter(Boolean))];
}

async function loadEventShadowRecallSettings() {
  const enabled = document.getElementById('event-shadow-enabled');
  if (!enabled) return;
  try {
    const settings = await api('GET', '/settings/event-shadow-recall');
    enabled.checked = Boolean(settings.enabled);
    document.getElementById('event-shadow-uids').value = (settings.uids || []).join('\n');
    document.getElementById('event-shadow-char-ids').value = (settings.char_ids || []).join('\n');
  } catch (e) { toast(e.message, 'err'); }
}

async function saveEventShadowRecallSettings() {
  try {
    await api('PUT', '/settings/event-shadow-recall', {
      enabled: Boolean(document.getElementById('event-shadow-enabled')?.checked),
      uids: _shadowListValue('event-shadow-uids'),
      char_ids: _shadowListValue('event-shadow-char-ids'),
    });
    toast(t('common.saved', '已保存'), 'ok');
    loadEventShadowRecallSettings();
    loadFeatureFlags();
  } catch (e) { toast(e.message, 'err'); }
}
let _mrData = { presets: {}, routing_profiles: {}, active_routing: 'default' };
let _mrEditingPresetName = null;
const MR_CATEGORIES = ['chat', 'intent', 'probe', 'summary', 'detect_emotion', 'consolidation', 'perform', 'monologue', 'sensor_judge', 'ime_judge', 'scenario_reconcile', 'event_edge_proposer', 'rpg_kp'];
const MR_CATEGORY_DESC = {
  ime_judge: 'IME 活动和有价值线索判断；可选轻量模型，未配置时沿用 sensor_judge / intent / chat',
  sensor_judge: '后台 sensor 裁决；未配置时沿用 intent / chat。短超时、零 SDK 重试。',
  scenario_reconcile: 'Scenario assistant-turn semantic stage reconciliation; conservative background call.',
  event_edge_proposer: 'Bounded background proposal of unreviewed Memory Event relations; this never changes recall or facts.',
  rpg_kp: 'RPG Dream 中立裁决（KP）；未配置时回退 chat。30 秒超时、零 SDK 重试。',
  chat:           '角色的正式回复，用户实际看到的每一句话（建议配主力模型）',
  intent:         '判断要不要触发某个动作的轻量辅助判断（便宜模型即可）',
  probe:          '每轮先判断要不要调用工具的轻量探针（便宜模型即可）',
  summary:        '生成摘要，如压缩长对话/总结一局陪玩（便宜模型即可）',
  detect_emotion: '识别文字里的情绪标签（便宜模型即可）',
  consolidation:  '短期记忆整理沉淀为长期记忆等后台整理（便宜模型即可）',
  perform:        '回复文字映射成动作/表情演出指令（仅开启该功能时用到）',
  monologue:      '前置独白 / 额外思考链（说话前的内心独白草稿；仅开启思考链功能时用到）',
};

function _renderActiveCharacterRoutingWarning(override) {
  const el = document.getElementById('mr-active-character-routing-warning');
  if (!el) return;
  if (!override || !override.model_routing) {
    el.style.display = 'none';
    el.textContent = '';
    return;
  }

  const character = String(override.label || override.char_id || '');
  const profile = String(override.effective_profile || override.model_routing || '');
  const preset = String(override.resolved_chat_preset || '');
  const scenarioPreset = String(override.resolved_scenario_reconcile_preset || '');
  el.textContent = t(
    'dynamic.routing.active_character_override_v2',
    'Active character {character} is pinned to routing profile {profile} (chat -> {preset}; scenario_reconcile -> {scenarioPreset}).',
    { character, profile, preset, scenarioPreset },
  );
  el.style.display = '';
}

window.addEventListener('admin-language-changed', () => {
  _renderActiveCharacterRoutingWarning(_mrData.active_character_routing);
});

async function loadModelRouting() {
  loadImageRecognition();
  loadVisionParams();
  loadPhoneControlVisionParams();
  if (typeof loadThinkingSettings === 'function') loadThinkingSettings();
  document.getElementById('mr-presets-body').innerHTML = '<div class="loading">加载中…</div>';
  document.getElementById('mr-profiles-body').innerHTML = '<div class="loading">加载中…</div>';
  try {
    const data = await api('GET', '/model-presets');
    _mrData = data;
    document.getElementById('mr-legacy-banner').style.display = data.is_legacy_synth ? '' : 'none';

    const sel = document.getElementById('mr-active-routing-select');
    sel.innerHTML = Object.keys(data.routing_profiles || {}).map(name =>
      `<option value="${name}" ${name === data.active_routing ? 'selected' : ''}>${name}</option>`
    ).join('');
    document.getElementById('mr-active-routing-current').textContent = `当前: ${data.active_routing}`;
    _renderActiveCharacterRoutingWarning(data.active_character_routing);

    _renderPresetsTable(data.presets || {});
    _renderProfilesTable(data.routing_profiles || {}, data.presets || {});
  } catch (e) {
    _renderActiveCharacterRoutingWarning(null);
    document.getElementById('mr-presets-body').innerHTML = `<div class="empty">加载失败: ${e.message}</div>`;
    document.getElementById('mr-profiles-body').innerHTML = '';
  }
}

async function bootstrapModelPresets() {
  try { await api('POST', '/model-presets/bootstrap'); toast('模型 preset 已初始化', 'ok'); loadModelRouting(); }
  catch (e) { toast('初始化失败: ' + e.message, 'err'); }
}

async function switchActiveRouting() {
  const active_routing = document.getElementById('mr-active-routing-select').value;
  if (!active_routing) return;
  try {
    await api('PUT', '/model-presets/active-routing', { active_routing });
    toast(`已切换到路由方案 '${active_routing}'`, 'ok');
    loadModelRouting();
  } catch (e) {
    toast('切换失败: ' + e.message, 'err');
  }
}

function _renderPresetsTable(presets) {
  const el = document.getElementById('mr-presets-body');
  const names = Object.keys(presets);
  if (!names.length) { el.innerHTML = '<div class="empty">暂无 preset</div>'; return; }
  const rows = names.map(name => {
    const p = presets[name];
    const escapedName = escapeHtml(name);
    return `
      <tr>
        <td><strong>${escapedName}</strong></td>
        <td><span class="badge badge-accent">${escapeHtml(p.provider_kind || '?')}</span></td>
        <td><span class="badge">${escapeHtml(p.api_protocol || 'chat_completions')}</span></td>
        <td><code style="font-family:var(--mono);font-size:12px">${escapeHtml(p.model || '')}</code></td>
        <td style="font-size:12px;color:var(--muted)">${escapeHtml(p.base_url || '')}</td>
        <td style="white-space:nowrap">
          <button class="btn btn-ghost btn-sm" data-preset-name="${escapedName}" onclick="testPreset(this)">测试</button>
          <button class="btn btn-ghost btn-sm" data-preset-name="${escapedName}" onclick="openPresetModal(this.dataset.presetName)">编辑</button>
          <button class="btn btn-ghost btn-sm" data-preset-name="${escapedName}" onclick="confirmDeletePreset(this.dataset.presetName)">删除</button>
        </td>
      </tr>
      <tr class="mr-test-row" style="display:none"><td colspan="6" style="font-size:12px" data-preset-test-result></td></tr>
    `;
  }).join('');
  el.innerHTML = `<div class="tbl-wrap"><table>
    <tr><th>名称</th><th>Provider</th><th>Protocol</th><th>Model</th><th>Base URL</th><th></th></tr>
    ${rows}
  </table></div>`;
}

function _renderProfilesTable(profiles, presets) {
  const el = document.getElementById('mr-profiles-body');
  const names = Object.keys(profiles);
  if (!names.length) { el.innerHTML = '<div class="empty">暂无 routing profile</div>'; return; }
  const rows = names.map(name => {
    const profile = profiles[name];
    const chips = Object.entries(profile).map(([cat, preset]) =>
      `<span class="badge" style="margin:2px">${cat}→${preset}</span>`
    ).join('');
    return `
      <tr>
        <td><strong>${name}</strong>${name === _mrData.active_routing ? ' <span class="badge badge-success">生效中</span>' : ''}</td>
        <td>${chips}</td>
        <td><button class="btn btn-ghost btn-sm" onclick="openProfileModal('${name}')">编辑</button></td>
      </tr>
    `;
  }).join('');
  el.innerHTML = `<div class="tbl-wrap"><table>
    <tr><th>名称</th><th>映射</th><th></th></tr>
    ${rows}
  </table></div>`;
}

async function testPreset(button) {
  const name = button.dataset.presetName;
  const row = button.closest('tr').nextElementSibling;
  const cell = row.querySelector('[data-preset-test-result]');
  row.style.display = '';
  cell.textContent = '测试中…';
  try {
    const r = await api('POST', `/model-presets/presets/${encodeURIComponent(name)}/test`);
    if (r.ok) {
      cell.textContent = `✓ 连接成功 · ${r.latency_ms}ms · ${r.api_protocol || ''} ${r.request_path || ''} · model=${r.model}${r.reply_preview ? ' · ' + r.reply_preview : ''}${r.warning ? ' · ' + r.warning : ''}`;
    } else {
      cell.textContent = `✗ ${r.error || '测试失败'} ${r.hint || ''} · ${r.api_protocol || ''} ${r.request_path || ''}${r.http_status ? ' · HTTP ' + r.http_status : ''} · ${r.error_type || ''} · ${r.latency_ms}ms`;
    }
  } catch (e) {
    cell.textContent = `✗ 管理面请求失败：${e.message}`;
  }
}

function openCreatePresetModal() {
  _mrEditingPresetName = null;
  _openPresetModal(null);
}

function openPresetModal(name) {
  // data-action handlers receive their source element as a trailing argument.
  // New and edit flows intentionally use separate entry points so this button
  // can never become the source preset name for a rename request.
  if (typeof name !== 'string') {
    openCreatePresetModal();
    return;
  }
  _mrEditingPresetName = name;
  _openPresetModal(name);
}

function _openPresetModal(name) {
  document.getElementById('preset-parameters').open = false;
  document.querySelector('#preset-parameters > summary').textContent = designText('高级生成参数', 'Advanced generation parameters');
  document.querySelector('#preset-import-panel > summary').textContent = designText('粘贴 JSON 填入表单', 'Fill form from JSON');
  document.getElementById('preset-import-json').value = '';
  document.getElementById('preset-import-result').textContent = '';
  document.getElementById('preset-import-panel').open = false;
  resetModelDiscovery();
  document.getElementById('mr-preset-err').textContent = '';
  const nameInput = document.getElementById('mr-preset-name');
  if (name) {
    const p = _mrData.presets[name] || {};
    nameInput.value = name;
    nameInput.disabled = false;
    document.getElementById('mr-preset-kind').value = p.provider_kind || 'openai';
    document.getElementById('mr-preset-api-protocol').value = p.api_protocol || 'chat_completions';
    document.getElementById('mr-preset-force-stream').checked = p.force_stream === true;
    document.getElementById('mr-preset-anthropic-auth-mode').value = p.anthropic_auth_mode || 'x_api_key';
    document.getElementById('mr-preset-tool-mode').value = p.tool_call_mode || 'function_calling';
    document.getElementById('mr-preset-base-url').value = p.base_url || '';
    document.getElementById('mr-preset-api-key').value = '';
    document.getElementById('mr-preset-api-key').placeholder = p.api_key ? `已设置（${p.api_key}），留空不修改` : 'sk-...';
    document.getElementById('mr-preset-model').value = p.model || '';
    document.getElementById('mr-preset-reasoning-native').checked = p.reasoning_native === true;
    document.getElementById('mr-preset-reasoning-extra-body').value = p.reasoning_extra_body && typeof p.reasoning_extra_body === 'object'
      ? JSON.stringify(p.reasoning_extra_body, null, 2)
      : '';
    renderKeyValueEditor('mr-preset-params', p.params || {});
    document.getElementById('mr-preset-modal-title').textContent = `编辑 Preset: ${name}`;
  } else {
    nameInput.value = '';
    nameInput.disabled = false;
    document.getElementById('mr-preset-kind').value = 'openai';
    document.getElementById('mr-preset-api-protocol').value = 'chat_completions';
    document.getElementById('mr-preset-force-stream').checked = false;
    document.getElementById('mr-preset-anthropic-auth-mode').value = 'x_api_key';
    document.getElementById('mr-preset-tool-mode').value = 'function_calling';
    document.getElementById('mr-preset-base-url').value = '';
    document.getElementById('mr-preset-api-key').value = '';
    document.getElementById('mr-preset-api-key').placeholder = 'sk-...';
    document.getElementById('mr-preset-model').value = '';
    document.getElementById('mr-preset-reasoning-native').checked = false;
    document.getElementById('mr-preset-reasoning-extra-body').value = '';
    renderKeyValueEditor('mr-preset-params', {});
    document.getElementById('mr-preset-modal-title').textContent = '新建 Preset';
  }
  document.getElementById('mr-preset-modal').classList.add('open');
}
function closePresetModal() {
  document.getElementById('preset-import-json').value = '';
  document.getElementById('mr-preset-api-key').value = '';
  resetModelDiscovery();
  document.getElementById('mr-preset-modal').classList.remove('open');
  _mrEditingPresetName = null;
}

let _modelDiscoveryGeneration = 0;
function resetModelDiscovery() {
  _modelDiscoveryGeneration++;
  const select = document.getElementById('mr-discovered-models');
  select.replaceChildren();
  select.hidden = true;
  document.getElementById('mr-discover-button').disabled = false;
  document.getElementById('mr-discovery-status').textContent = '可从中转站获取模型，也可手动填写。';
}

async function discoverPresetModels() {
  const generation = ++_modelDiscoveryGeneration;
  const button = document.getElementById('mr-discover-button');
  const status = document.getElementById('mr-discovery-status');
  const select = document.getElementById('mr-discovered-models');
  const body = {
    base_url: document.getElementById('mr-preset-base-url').value.trim(),
    api_key: document.getElementById('mr-preset-api-key').value.trim(),
    preset_name: _mrEditingPresetName,
    api_protocol: document.getElementById('mr-preset-api-protocol').value,
    anthropic_auth_mode: document.getElementById('mr-preset-anthropic-auth-mode').value,
  };
  const unchanged = () => generation === _modelDiscoveryGeneration &&
    body.base_url === document.getElementById('mr-preset-base-url').value.trim() &&
    body.api_key === document.getElementById('mr-preset-api-key').value.trim() &&
    body.api_protocol === document.getElementById('mr-preset-api-protocol').value &&
    body.anthropic_auth_mode === document.getElementById('mr-preset-anthropic-auth-mode').value;
  select.hidden = true;
  button.disabled = true;
  status.textContent = '正在获取模型…';
  try {
    const result = await api('POST', '/model-presets/discover', body);
    if (!unchanged()) return;
    const hints = {
      unsupported: '此中转站未提供模型目录，请手动填写模型名。',
      unauthorized: '目录鉴权失败，请检查 API Key 或手动填写。',
      key_required: '地址已修改，请填写该地址的 API Key 后重新获取。',
      timeout: '获取超时，可重试或手动填写。',
      empty: '目录为空，请手动填写模型名。',
      invalid_response: '目录格式不兼容，请手动填写模型名。',
      connection_error: '无法连接，请检查地址或手动填写。',
      http_error: '目录请求失败，可重试或手动填写。',
    };
    if (result.status !== 'ok') { status.textContent = hints[result.status] || '无法获取目录，请手动填写。'; return; }
    select.replaceChildren(new Option('请选择模型（也可手填）', ''));
    for (const model of result.models) select.add(new Option(model, model));
    select.hidden = false;
    select.onchange = () => {
      if (!unchanged()) { resetModelDiscovery(); return; }
      if (select.value) document.getElementById('mr-preset-model').value = select.value;
    };
    status.textContent = `已获取 ${result.models.length} 个模型${result.has_more ? '（目录仅返回部分模型）' : ''}；选择后仍需保存，协议与工具能力请按中转站说明配置。`;
  } catch (e) {
    if (unchanged()) status.textContent = `获取失败：${e.message || e}；仍可手动填写。`;
  } finally {
    if (generation === _modelDiscoveryGeneration) {
      button.disabled = false;
      if (!unchanged()) { select.hidden = true; status.textContent = '连接信息已更改，请重新获取模型。'; }
    }
  }
}
async function submitPresetModal() {
  const nameInput = document.getElementById('mr-preset-name');
  const name = nameInput.value.trim();
  const previousName = _mrEditingPresetName;
  const errEl = document.getElementById('mr-preset-err');
  if (!name) { errEl.textContent = '名称不能为空'; return; }

  const body = {
    provider_kind: document.getElementById('mr-preset-kind').value,
    api_protocol: document.getElementById('mr-preset-api-protocol').value,
    force_stream: document.getElementById('mr-preset-force-stream').checked,
    anthropic_auth_mode: document.getElementById('mr-preset-anthropic-auth-mode').value,
    tool_call_mode: document.getElementById('mr-preset-tool-mode').value,
    base_url: document.getElementById('mr-preset-base-url').value.trim(),
    model: document.getElementById('mr-preset-model').value.trim(),
  };
  const apiKey = document.getElementById('mr-preset-api-key').value.trim();
  if (apiKey) body.api_key = apiKey;
  try {
    const params = readKeyValueEditor('mr-preset-params');
    if (Object.keys(params).length) body.params = params;
  } catch (e) { errEl.textContent = e.message; return; }
  body.reasoning_native = document.getElementById('mr-preset-reasoning-native').checked;
  const extraBodyRaw = document.getElementById('mr-preset-reasoning-extra-body').value.trim();
  if (extraBodyRaw) {
    let extraBody;
    try { extraBody = JSON.parse(extraBodyRaw); }
    catch (e) { errEl.textContent = t('routing.preset.invalid_extra_body', 'reasoning_extra_body 不是合法 JSON'); return; }
    if (!extraBody || Array.isArray(extraBody) || typeof extraBody !== 'object') {
      errEl.textContent = t('routing.preset.invalid_extra_body', 'reasoning_extra_body 必须是 JSON 对象');
      return;
    }
    body.reasoning_extra_body = extraBody;
  } else {
    body.reasoning_extra_body = {};
  }
  body.provider_kind = document.getElementById('mr-preset-kind').value;

  try {
    if (previousName && previousName !== name) {
      await api('POST', `/model-presets/presets/${encodeURIComponent(previousName)}/rename`, { new_name: name });
    }
    await api('PUT', `/model-presets/presets/${encodeURIComponent(name)}`, body);
    toast(`preset '${name}' 已保存`, 'ok');
    closePresetModal();
    loadModelRouting();
  } catch (e) {
    errEl.textContent = e.message;
  }
}

function addPresetParam() { addKeyValueRow('mr-preset-params'); }

function confirmDeletePreset(name) {
  _openAtConfirm(`删除 preset '${name}'`, '若仍被某个 routing profile 引用，或是唯一剩余的 preset，将会被拒绝。', async () => {
    try {
      await api('DELETE', `/model-presets/presets/${encodeURIComponent(name)}`);
      toast(`preset '${name}' 已删除`, 'ok');
      loadModelRouting();
    } catch (e) {
      toast('删除失败: ' + e.message, 'err');
    }
  });
}

function openProfileModal(name) {
  document.getElementById('mr-profile-err').textContent = '';
  const nameInput = document.getElementById('mr-profile-name');
  nameInput.value = name || '';
  nameInput.disabled = !!name;
  const existing = (name && _mrData.routing_profiles[name]) || {};
  const presetNames = Object.keys(_mrData.presets || {});
  const catsEl = document.getElementById('mr-profile-categories');
  catsEl.innerHTML = MR_CATEGORIES.map(cat => `
    <label class="field" style="margin-bottom:8px">
      <span>${cat} <span style="font-size:11px;color:var(--muted);font-weight:normal">—— ${MR_CATEGORY_DESC[cat] || ''}</span></span>
      <select id="mr-profile-cat-${cat}">
        <option value="">（不修改 / 沿用已有，缺省时自动回退用 chat）</option>
        ${presetNames.map(p => `<option value="${p}" ${existing[cat] === p ? 'selected' : ''}>${p}</option>`).join('')}
      </select>
    </label>
  `).join('');
  document.getElementById('mr-profile-modal').classList.add('open');
}
function closeProfileModal() {
  document.getElementById('mr-profile-modal').classList.remove('open');
}
async function submitProfileModal() {
  const name = document.getElementById('mr-profile-name').value.trim();
  const errEl = document.getElementById('mr-profile-err');
  if (!name) { errEl.textContent = '名称不能为空'; return; }
  const body = {};
  for (const cat of MR_CATEGORIES) {
    const v = document.getElementById(`mr-profile-cat-${cat}`).value;
    if (v) body[cat] = v;
  }
  if (!Object.keys(body).length) { errEl.textContent = '至少选择一个 category'; return; }
  try {
    await api('PUT', `/model-presets/routing-profiles/${encodeURIComponent(name)}`, body);
    toast(`routing profile '${name}' 已保存`, 'ok');
    closeProfileModal();
    loadModelRouting();
  } catch (e) {
    errEl.textContent = e.message;
  }
}

// ══════════════════════════════════════════════════════════
//  关系事实
// ══════════════════════════════════════════════════════════
