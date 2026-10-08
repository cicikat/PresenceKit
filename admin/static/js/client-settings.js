let _clientFlags = {};
let _clientWechatSettings = null;

async function loadClientWechatSettings() {
  const state = document.getElementById('wechat-state');
  if (!state) return;
  try {
    const s = await api('GET', '/settings/wechat');
    _clientWechatSettings = s;
    document.getElementById('wechat-enabled').checked = Boolean(s.enabled);
    document.getElementById('wechat-proactive').checked = Boolean(s.proactive_enabled);
    for (const key of ['base_url', 'account_id', 'owner_sender_id']) {
      document.getElementById('wechat-' + key).value = s[key] || '';
    }
    state.textContent = `${s.effective_state} · 桥密钥：${s.credential_configured ? '已配置' : '未配置'} · ${s.last_error || ''}`;
    document.getElementById('wechat-counters').textContent = JSON.stringify(s.counters || {}, null, 2);
  } catch (error) { state.textContent = error.message; }
}

async function saveClientWechatSettings() {
  if (!_clientWechatSettings) return toast('请先刷新连接设置', 'err');
  try {
    const body = {
      transport: _clientWechatSettings.transport,
      enabled: document.getElementById('wechat-enabled').checked,
      proactive_enabled: document.getElementById('wechat-proactive').checked,
    };
    for (const key of ['base_url', 'account_id', 'owner_sender_id']) {
      body[key] = document.getElementById('wechat-' + key).value.trim();
    }
    // Preserve transport-specific connection fields when using an older adapter.
    if (_clientWechatSettings.ws_url !== undefined) body.ws_url = _clientWechatSettings.ws_url;
    await api('PUT', '/settings/wechat', body);
    toast('已保存', 'ok');
    await loadClientSettings();
  } catch (error) { toast(error.message, 'err'); }
}

async function loadClientSettings() {
  try {
    const [clients, features] = await Promise.all([
      api('GET', '/settings/clients'), api('GET', '/settings/feature-flags'),
    ]);
    _clientFlags = features.flags;
    for (const name of ['qq', 'wechat', 'desktop', 'mobile']) {
      document.getElementById('client-' + name).checked =
        Boolean(clients.clients[name]?.enabled ?? _clientFlags[name]?.enabled);
    }
    document.getElementById('client-qq-host').value = clients.qq_host;
    document.getElementById('client-qq-port').value = clients.qq_port;
    document.getElementById('client-state').textContent =
      `电脑：${clients.clients.desktop.effective_state} · 手机：${clients.clients.mobile.effective_state} · 微信：${_clientFlags.wechat?.effective_state} · QQ：${clients.standalone_mode ? 'standalone 模式阻止启动' : '开关及连接重启后生效'}`;
    document.getElementById('client-address').textContent = `当前管理面地址：${location.origin}（客户端使用可达地址；服务监听端口 ${clients.admin_port}，修改 config.yaml 的 admin.port 后重启）`;
    await Promise.all([loadClientWechatSettings(), loadRelaySettings(), loadQzoneSettings()]);
  } catch (error) {
    document.getElementById('client-state').textContent = error.message;
  }
}

async function toggleClientChannel(name) {
  const input = document.getElementById('client-' + name);
  input.disabled = true;
  try {
    const result = name === 'qq' || name === 'wechat'
      ? await api('PUT', '/settings/feature-flags', {flags: {[name]: input.checked}})
      : await api('PUT', '/settings/clients', {[name]: input.checked});
    toast(result.restart_required?.length ? '已保存，重启后端后生效' : '已保存并生效', 'ok');
  } catch (error) { toast(error.message, 'err'); }
  finally { input.disabled = false; await loadClientSettings(); }
}

async function saveClientConnection() {
  try {
    const result = await api('PUT', '/settings/clients', {
      qq_host: document.getElementById('client-qq-host').value.trim(),
      qq_port: Number(document.getElementById('client-qq-port').value),
    });
    toast(result.restart_required ? '已保存，重启后端后生效' : '已保存', 'ok');
    await loadClientSettings();
  } catch (error) { toast(error.message, 'err'); }
}

let qzoneEventsSupported = false;
async function loadQzoneSettings() {
  const root = document.getElementById('qzone-state');
  if (!root) return;
  try {
    const s = await api('GET', '/settings/qzone');
    qzoneEventsSupported = typeof s.events_enabled === 'boolean';
    document.getElementById('qzone-enabled').checked = s.enabled;
    document.getElementById('qzone-write-enabled').checked = s.write_enabled;
    document.getElementById('qzone-events-enabled').checked = s.events_enabled;
    document.getElementById('qzone-replies-enabled').checked = s.replies_enabled;
    document.getElementById('qzone-autonomy-interactions').checked = s.autonomy_interactions_enabled;
    document.getElementById('qzone-watched-users').value = (s.watched_user_ids || []).join(', ');
    document.getElementById('qzone-poll-interval').value = s.poll_interval_seconds || 120;
    const e = s.events || {};
    document.getElementById('qzone-events-state').textContent = `事件：${e.last_code || '未扫描'} · 有效关注 ${(s.effective_watched_user_ids || []).join(', ') || '未绑定主用户 QQ'} · 入队 ${e.queued || 0} · 重复 ${e.duplicates || 0} · ${e.partial ? '部分扫描/评论不可用' : '有界扫描'} · 最近成功 ${e.last_success_at ? new Date(e.last_success_at * 1000).toLocaleString() : '无'}`;
    if (!qzoneEventsSupported) document.getElementById('qzone-events-state').textContent = '后端尚未加载空间事件模块，请正常重启后端后设置。';
    document.getElementById('qzone-base-url').value = s.base_url;
    document.getElementById('qzone-account').value = s.account_id;
    document.getElementById('qzone-characters').value = s.allowed_char_ids.join(', ');
    document.getElementById('qzone-token').value = '';
    root.textContent = `状态：${s.effective_state} · Token：${s.credential_configured ? '已配置' : '未配置'} · 调用 ${s.calls} / 失败 ${s.failures}`;
  } catch (error) { root.textContent = error.message; }
}

async function saveQzoneSettings() {
  if (!qzoneEventsSupported) { toast('请先正常重启后端加载空间事件模块，再保存设置', 'err'); return; }
  try {
    await api('PUT', '/settings/qzone', {
      enabled: document.getElementById('qzone-enabled').checked,
      write_enabled: document.getElementById('qzone-write-enabled').checked,
      events_enabled: document.getElementById('qzone-events-enabled').checked,
      replies_enabled: document.getElementById('qzone-replies-enabled').checked,
      autonomy_interactions_enabled: document.getElementById('qzone-autonomy-interactions').checked,
      watched_user_ids: document.getElementById('qzone-watched-users').value.split(/[,，]/).map(v => v.trim()).filter(Boolean),
      poll_interval_seconds: Number(document.getElementById('qzone-poll-interval').value),
      base_url: document.getElementById('qzone-base-url').value.trim(),
      access_token: document.getElementById('qzone-token').value.trim(),
      account_id: document.getElementById('qzone-account').value.trim(),
      allowed_char_ids: document.getElementById('qzone-characters').value.split(/[,，]/).map(v => v.trim()).filter(Boolean),
    });
    toast('空间设置已保存并热生效', 'ok');
    await loadQzoneSettings();
  } catch (error) { toast(error.message, 'err'); }
}

async function probeQzone() {
  try {
    const result = await api('POST', '/settings/qzone/probe', {});
    toast(result.code, result.ok ? 'ok' : 'err');
    await loadQzoneSettings();
  } catch (error) { toast(error.message, 'err'); }
}

async function loginQzoneCookie() {
  const input = document.getElementById('qzone-cookie');
  try {
    const result = await api('POST', '/settings/qzone/login-cookie', {cookie: input.value});
    toast(result.code, result.ok ? 'ok' : 'err');
    await loadQzoneSettings();
  } catch (error) { toast(error.message, 'err'); }
  finally { input.value = ''; }
}

async function syncQzoneNapcatCookie() {
  try {
    const result = await api('POST', '/settings/qzone/sync-napcat-cookie', {});
    toast(result.code, result.ok ? 'ok' : 'err');
    await loadQzoneSettings();
  } catch (error) { toast(error.message, 'err'); }
}
