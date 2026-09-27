const SC_TRIGGER_LABELS = {
  morning_greeting: 'dynamic.scheduler.morning',
  night_reminder: 'dynamic.scheduler.bedtime',
  random_message: 'dynamic.scheduler.daytime',
  hr_high: 'dynamic.scheduler.heart_high',
  hr_critical: 'dynamic.scheduler.heart_critical',
  sleep_end: 'dynamic.scheduler.sleep_ended',
  weather_alert: 'dynamic.scheduler.weather',
  period_reminder: 'dynamic.scheduler.period',
  diary_reminder: 'dynamic.scheduler.diary_missing',
  diary_inject: 'dynamic.scheduler.diary_injection',
  daily_journal: 'dynamic.scheduler.daily_journal',
  diary_share_reminder: 'dynamic.scheduler.diary_share',
};

const SC_FIELD_LABELS = {
  enabled: 'scheduler.field.enabled', morning_greeting: 'dynamic.scheduler.morning', night_reminder: 'scheduler.field.bedtime',
  random_message: 'dynamic.scheduler.daytime', daily_journal: 'dynamic.scheduler.daily_journal', period_reminder: 'dynamic.scheduler.period',
  diary_reminder: 'scheduler.field.diary_reminder', diary_inject: 'dynamic.scheduler.diary_injection', presence_nag: 'scheduler.field.presence_nag',
};

function _scText(key, fallback, params) { return t(key, fallback, params); }
function _scTriggerLabel(key) {
  const translationKey = SC_TRIGGER_LABELS[key];
  return translationKey ? _scText(translationKey, key) : key;
}

const SC_BOOL_FIELDS = [
  'enabled', 'morning_greeting', 'night_reminder', 'random_message',
  'daily_journal', 'period_reminder', 'diary_reminder', 'diary_inject',
  'presence_nag',
];

async function loadProactiveLedger() {
  const el = document.getElementById('sc-ledger-body');
  if (!el) return;
  try {
    const d = await api('GET', '/scheduler/proactive-ledger');
    const gapH = (d.effective_gap_seconds / 3600).toFixed(2);
    const nextIn = d.next_allowed_in_seconds > 0 ? _fmtSec(d.next_allowed_in_seconds) : _scText('dynamic.scheduler.can_send', '现在可发');
    const budgetPct = d.daily_budget ? Math.round(100 * d.daily_count / d.daily_budget) : 0;
    const recentRows = (d.recent || []).slice().reverse().map(r => {
      const ts = r.ts ? new Date(r.ts * 1000).toLocaleTimeString() : '';
      return `<tr>
        <td style="color:var(--muted);font-size:12px">${ts}</td>
        <td>${escapeHtml(r.trigger_name || '')}</td>
        <td style="color:var(--muted)">${escapeHtml(r.gist || '')}</td>
      </tr>`;
    }).join('');
    el.innerHTML = `
      <table style="width:100%;font-size:13px;border-collapse:collapse;margin-bottom:8px">
        <tr>
          <td style="padding:6px 0;color:var(--muted);width:140px">生效全局间隔</td>
          <td style="font-weight:600">${gapH} 小时</td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:var(--muted)">下次可发</td>
          <td style="font-weight:600">${nextIn}</td>
        </tr>
        <tr>
          <td style="padding:6px 0;color:var(--muted)">今日已发 / 预算</td>
          <td style="font-weight:600">${d.daily_count} / ${d.daily_budget}（${d.daily_logical_day || ''}）</td>
        </tr>
      </table>
      <div style="height:4px;background:var(--border);border-radius:2px;overflow:hidden;margin-bottom:12px">
        <div style="height:100%;background:var(--accent);border-radius:2px;width:${Math.max(0, Math.min(100, budgetPct))}%"></div>
      </div>
      <div class="tbl-wrap"><table>
        <tr><th>${_scText('scheduler.column.time', '时间')}</th><th>${_scText('scheduler.column.trigger', '触发器')}</th><th>${_scText('scheduler.column.summary', '内容摘要')}</th></tr>
        ${recentRows || `<tr><td colspan="3" class="empty">${_scText('dynamic.scheduler.no_records', '暂无记录')}</td></tr>`}
      </table></div>`;
  } catch (e) {
    el.innerHTML = `<div class="empty">${escapeHtml(_scText('dynamic.scheduler.load_failed', '加载失败: {error}', {error: e.message}))}</div>`;
  }
}

let _scConfig = {};
async function loadSchedulerConfig() { try { _scConfig = await api('GET','/scheduler/config'); document.getElementById('sc-config-switches').innerHTML = SC_BOOL_FIELDS.map(k => `<label class="checkbox-row"><input type="checkbox" data-sc-bool="${k}" ${_scConfig[k] ? 'checked' : ''}><span>${_scText(SC_FIELD_LABELS[k], k)}</span></label>`).join(''); document.getElementById('sc-presence-minutes').value=_scConfig.presence_nag_minutes||60; document.getElementById('sc-gap-hours').value=_scConfig.global_proactive_min_gap_hours||1.5; document.getElementById('sc-daily-proactive').value=_scConfig.max_daily_proactive||16; } catch(e){toast(_scText('dynamic.scheduler.read_failed','读取调度配置失败: {error}',{error:e.message}),'err');} }
async function saveSchedulerConfig() { const body={presence_nag_minutes:Number(document.getElementById('sc-presence-minutes').value),global_proactive_min_gap_hours:Number(document.getElementById('sc-gap-hours').value),max_daily_proactive:Number(document.getElementById('sc-daily-proactive').value)}; document.querySelectorAll('[data-sc-bool]').forEach(el=>body[el.dataset.scBool]=el.checked); try{await api('PUT','/scheduler/config',body);toast(_scText('dynamic.scheduler.saved','调度配置已保存'),'ok');loadSchedulerConfig();}catch(e){toast(_scText('common.save_failed','保存失败: {error}',{error:e.message}),'err');} }
async function loadRelaySettings(){try{const d=await api('GET','/settings/relay');document.getElementById('relay-base-url').value=d.relay_base_url||'';document.getElementById('relay-topic').value=d.relay_topic||'';document.getElementById('relay-token').value='';document.getElementById('relay-token').placeholder=d.relay_token?t('status.relay.configured','已配置（{value}），留空保留',{value:d.relay_token}):t('status.relay.unconfigured','未配置');}catch(e){toast(t('status.relay.load_error','读取中继失败: {error}',{error:e.message}),'err');}}
async function saveRelaySettings(){const body={relay_base_url:document.getElementById('relay-base-url').value.trim(),relay_topic:document.getElementById('relay-topic').value.trim()};const token=document.getElementById('relay-token').value.trim();if(token)body.relay_token=token;try{await api('PUT','/settings/relay',body);toast(t('status.relay.saved','中继配置已保存'),'ok');loadRelaySettings();}catch(e){toast(t('common.save_failed','保存失败: {error}',{error:e.message}),'err');}}
async function loadScheduler() {
  loadSchedulerConfig();
  loadHdsLocalSettings();
  _startWatchStatusPoller();
  loadProactiveLedger();
  try {
    const statusRes = await api('GET', '/scheduler/status');

    const triggers = statusRes.triggers || {};
    const rows = Object.entries(triggers).map(([key, v]) => {
      const pct = v.ready ? 100 : Math.round((1 - v.remaining_sec / (v.cooldown_sec || 1)) * 100);
      const badge = v.ready
        ? `<span class="badge badge-success">${_scText('dynamic.scheduler.ready', '就绪')}</span>`
        : `<span class="badge badge-warn">${escapeHtml(_scText('dynamic.scheduler.cooldown', '冷却中 {duration}', {duration: _fmtSec(v.remaining_sec)}))}</span>`;
      const enabledBadge = v.enabled === false
        ? `<span class="badge badge-danger" style="font-size:10px">${_scText('dynamic.scheduler.disabled', '已禁用')}</span>`
        : '';
      return `<tr>
        <td>${escapeHtml(_scTriggerLabel(key))}${key in SC_TRIGGER_LABELS ? `（${escapeHtml(key)}）` : ''} ${enabledBadge}</td>
        <td style="font-size:12px;color:var(--muted)">${escapeHtml(v.last_triggered || _scText('dynamic.scheduler.never', '从未'))}</td>
        <td>${badge}</td>
        <td style="min-width:120px">
          <div style="height:4px;background:var(--border);border-radius:2px;overflow:hidden">
            <div style="height:100%;background:var(--accent);border-radius:2px;width:${Math.max(0,Math.min(100,pct))}%"></div>
          </div>
        </td>
      </tr>`;
    }).join('');
    document.getElementById('sc-status-table').innerHTML =
      `<div class="tbl-wrap"><table>
        <tr><th>${_scText('scheduler.column.trigger', '触发器')}</th><th>${_scText('scheduler.column.last_trigger', '上次触发')}</th><th>${_scText('common.status', '状态')}</th><th>${_scText('scheduler.column.cooldown', '冷却进度')}</th></tr>
        ${rows || `<tr><td colspan="4" class="empty">${_scText('dynamic.scheduler.no_triggers', '暂无触发器数据')}</td></tr>`}
      </table></div>`;

  } catch(e) {
    toast(_scText('dynamic.scheduler.load_failed', '加载调度器数据失败: {error}', {error: e.message}), 'err');
  }
}

let _hdsInterfaces = [];
async function loadHdsLocalSettings() {
  const enabled = document.getElementById('hds-enabled');
  if (!enabled) return;
  try {
    const [settings, status] = await Promise.all([
      api('GET', '/settings/hds-local'), api('GET', '/watch/hds-local'),
    ]);
    _hdsInterfaces = settings.interfaces || [];
    enabled.checked = settings.enabled;
    document.getElementById('hds-port').value = settings.port;
    const selection = document.getElementById('hds-interface');
    selection.innerHTML = `<option value="">${escapeHtml(_scText('scheduler.hds.all_interfaces', '全部本机网卡'))}</option>` +
      _hdsInterfaces.map(item => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)} · ${escapeHtml(item.address)}</option>`).join('');
    selection.value = settings.interface || '';
    document.getElementById('hds-source-mode').value = settings.source_mode;
    document.getElementById('hds-subnets').value = (settings.allowed_subnets || []).join('\n');
    _showHdsMode();
    _showHdsAddress(settings.port);
    document.getElementById('hds-effective').textContent = settings.restart_required
      ? _scText('scheduler.hds.restart', '设置已保存；接收监听变更需要重启后端。')
      : settings.effective_listening
        ? _scText('scheduler.hds.listening', '接收服务正在监听。')
        : _scText('scheduler.hds.stopped', '接收服务未启动。');
    const latest = status.latest;
    document.getElementById('hds-latest').textContent = latest
      ? _scText('scheduler.hds.latest', '最近心率：{value} bpm，时间：{time}；已留存 {count} 条。', {value: latest.value, time: new Date(latest.received_at * 1000).toLocaleString(), count: status.sample_count_retained})
      : _scText('scheduler.hds.no_samples', '尚未收到 HDS 心率样本。');
  } catch (e) { toast(_scText('scheduler.hds.load_failed', '读取 HDS 设置失败：{error}', {error: e.message}), 'err'); }
}

function _showHdsMode() {
  const manual = document.getElementById('hds-source-mode')?.value === 'manual';
  document.getElementById('hds-subnets-row').style.display = manual ? '' : 'none';
  document.getElementById('hds-interface').disabled = manual;
}

function _showHdsAddress(port) {
  const selected = document.getElementById('hds-interface')?.value || '';
  const urls = _hdsInterfaces.filter(item => !selected || item.name === selected)
    .map(item => `http://${item.address}:${port}/`);
  document.getElementById('hds-url').textContent = urls.length
    ? _scText('scheduler.hds.address', '手表 HDS 中填写：{urls}', {urls: urls.join('  ·  ')})
    : _scText('scheduler.hds.no_address', '未发现可用的局域网 IPv4 地址。');
}

async function copyHdsAddress() {
  const selected = document.getElementById('hds-interface')?.value || '';
  if (!selected) {
    toast(_scText('scheduler.hds.choose_interface', '先选择手表所在网卡'), 'warn');
    return;
  }
  const item = _hdsInterfaces.find(entry => entry.name === selected);
  if (!item) return;
  const url = `http://${item.address}:${document.getElementById('hds-port').value}/`;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(url);
    } else {
      const field = document.createElement('textarea');
      field.value = url;
      document.body.appendChild(field);
      field.select();
      const copied = document.execCommand('copy');
      field.remove();
      if (!copied) throw new Error('copy unavailable');
    }
    toast(_scText('scheduler.hds.copied', '地址已复制'), 'ok');
  } catch (error) { toast(_scText('scheduler.hds.copy_failed', '无法复制地址，请手动选择文本'), 'warn'); }
}

async function saveHdsLocalSettings() {
  const body = {
    enabled: document.getElementById('hds-enabled').checked,
    port: Number(document.getElementById('hds-port').value),
    source_mode: document.getElementById('hds-source-mode').value,
    interface: document.getElementById('hds-interface').value,
    allowed_subnets: document.getElementById('hds-subnets').value.split(/\s+/).filter(Boolean),
  };
  try {
    await api('PUT', '/settings/hds-local', body);
    toast(_scText('scheduler.hds.saved', 'HDS 设置已保存'), 'ok');
    await loadHdsLocalSettings();
  } catch (e) { toast(_scText('common.save_failed', '保存失败: {error}', {error:e.message}), 'err'); }
}

document.addEventListener('change', event => {
  if (event.target?.id === 'hds-source-mode') _showHdsMode();
  if (event.target?.id === 'hds-interface' || event.target?.id === 'hds-port') _showHdsAddress(document.getElementById('hds-port').value);
});

function _fmtSec(s) {
  if (s < 60)  return _scText('scheduler.duration.seconds', '{count} 秒', {count: s});
  if (s < 3600) return _scText('scheduler.duration.minutes', '{count} 分钟', {count: Math.round(s / 60)});
  return _scText('scheduler.duration.hours', '{count} 小时', {count: (s / 3600).toFixed(1)});
}

async function scTrigger(name) {
  try {
    const d = await api('POST', `/scheduler/trigger/${name}`);
    toast(d.message, 'ok');
    setTimeout(loadScheduler, 500);
  } catch(e) { toast(_scText('common.trigger_failed', '触发失败: {error}', {error: e.message}), 'err'); }
}

async function testWatchEvent(type) {
  const body = { type };
  if (type === 'heart_rate') {
    const val = parseInt(document.getElementById('sc-hr-value').value);
    if (isNaN(val)) { toast(_scText('dynamic.scheduler.invalid_heart_rate', '请输入有效的心率值'), 'warn'); return; }
    body.value = val;
  }
  try {
    const opts = { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) };
    const r = await fetch(BASE + '/watch/event', opts);
    const d = await r.json();
    toast(d.message || _scText('dynamic.scheduler.sent', '已发送'), 'ok');
    setTimeout(loadWatchStatus, 300);
  } catch(e) { toast(_scText('common.send_failed', '发送失败: {error}', {error: e.message}), 'err'); }
}

let _watchStatusTimer = null;

async function loadWatchStatus() {
  loadHdsLocalStatus();
  try {
    const d = await api('GET', '/watch/status');
    const hr    = document.getElementById('ws-hr');
    const sleep = document.getElementById('ws-sleep');
    const last  = document.getElementById('ws-last');
    if (!hr) return;

    if (!d || !d.event_type) {
      // 保持暂无数据，不清空
      return;
    }

    if (d.event_type === 'heart_rate') {
      hr.textContent = d.value ? _scText('scheduler.heart_rate', '{value} 次/分', {value: d.value}) : _scText('common.no_data', '暂无数据');
      hr.style.color = d.value > 120 ? 'var(--danger)' : d.value > 100 ? 'var(--warn)' : 'var(--accent)';
    }

    if (d.event_type === 'sleep_end') {
      const start = d.sleep_start || '—';
      const end   = d.sleep_end_time || '—';
      const dur = d.duration_minutes
        ? _scText('scheduler.duration.compact', '{hours} 小时 {minutes} 分钟', {
          hours: Math.floor(d.duration_minutes / 60),
          minutes: Math.round(d.duration_minutes % 60),
        })
        : '—';
      sleep.textContent = _scText('dynamic.scheduler.awake_summary', '已醒 · 入睡 {sleep} → 起床 {wake} · 共 {duration}', {sleep: start, wake: end, duration: dur});
      sleep.style.color = 'var(--success)';
    }

    last.textContent = `${_scTriggerLabel(d.event_type)} · ${d.timestamp || ''}`;
  } catch(e) { /* 静默失败 */ }
}

async function loadHdsLocalStatus() {
  const el = document.getElementById('hds-latest');
  if (!el) return;
  try {
    const status = await api('GET', '/watch/hds-local');
    const latest = status.latest;
    el.textContent = latest
      ? _scText('scheduler.hds.latest', '最近心率：{value} bpm，时间：{time}；已留存 {count} 条。', {value: latest.value, time: new Date(latest.received_at * 1000).toLocaleString(), count: status.sample_count_retained})
      : _scText('scheduler.hds.no_samples', '尚未收到 HDS 心率样本。');
  } catch (e) { /* 状态轮询静默失败 */ }
}

// 调度器页面激活时启动 Watch 状态自动刷新
function _startWatchStatusPoller() {
  if (_watchStatusTimer) return;
  loadWatchStatus();
  _watchStatusTimer = setInterval(loadWatchStatus, 30000);
}
function _stopWatchStatusPoller() {
  if (_watchStatusTimer) { clearInterval(_watchStatusTimer); _watchStatusTimer = null; }
}

// ══════════════════════════════════════════════════════════
//  群聊蒸馏
// ══════════════════════════════════════════════════════════
async function runGroupDistill() {
  const group_id = document.getElementById('distill-group-id').value.trim();
  if (!group_id) { toast('请输入群号', 'warn'); return; }
  const ta = document.getElementById('distill-result');
  ta.value = '蒸馏中，请稍候…';
  try {
    const d = await api('POST', '/group-distill', { group_id });
    ta.value = d.summary || '（无结果）';
    toast('蒸馏完成', 'ok');
  } catch(e) {
    ta.value = '失败：' + e.message;
    toast('蒸馏失败：' + e.message, 'err');
  }
}

// ══════════════════════════════════════════════════════════
//  观测页：情绪·花园
// ══════════════════════════════════════════════════════════
