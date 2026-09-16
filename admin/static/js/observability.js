async function loadObserveMood() {
  const moodEl   = document.getElementById('obs-mood-raw');
  const gardenEl = document.getElementById('obs-garden-raw');
  moodEl.textContent = gardenEl.textContent = '加载中…';
  try {
    const d = await api('GET', '/mood/state');
    moodEl.textContent = JSON.stringify(d, null, 2);
  } catch(e) {
    moodEl.textContent = '加载失败：' + e.message;
  }
  try {
    const d = await api('GET', '/garden/state');
    gardenEl.textContent = JSON.stringify(d, null, 2);
  } catch(e) {
    gardenEl.textContent = '加载失败：' + e.message;
  }
}

// ══════════════════════════════════════════════════════════
//  观测页：梦境状态
// ══════════════════════════════════════════════════════════
async function loadObserveDream() {
  const rawEl   = document.getElementById('obs-dream-raw');
  const statsEl = document.getElementById('obs-dream-stats');
  const statusEl = document.getElementById('obs-dream-status');
  rawEl.textContent = statsEl.textContent = '加载中…';
  try {
    const d = await api('GET', '/dream/state');
    statusEl.textContent = '状态：' + (d.status || '—');
    rawEl.textContent = JSON.stringify(d, null, 2);
  } catch(e) {
    rawEl.textContent = '加载失败：' + e.message;
  }
  try {
    const s = await api('GET', '/dream/stats');
    statsEl.textContent = JSON.stringify(s, null, 2);
  } catch(e) {
    statsEl.textContent = '加载失败：' + e.message;
  }
}

// ══════════════════════════════════════════════════════════
//  观测页：记忆探查（短期记忆）
// ══════════════════════════════════════════════════════════
async function loadObserveMemory() {
  const uid = (document.getElementById('obs-mem-uid').value || '').trim();
  const el  = document.getElementById('obs-memory-raw');
  if (!uid) { el.textContent = '请输入 uid（用户 QQ 号）'; return; }
  el.textContent = '加载中…';
  try {
    const d = await api('GET', `/memory/${encodeURIComponent(uid)}/short-term`);
    el.textContent = JSON.stringify(d, null, 2);
  } catch(e) {
    el.textContent = '加载失败：' + e.message;
  }
}

// ══════════════════════════════════════════════════════════
//  观测页：Memory Event 证据账本
// ══════════════════════════════════════════════════════════
function _memoryEventScope() {
  const uid = (document.getElementById('event-query-uid')?.value || '').trim();
  const charId = (document.getElementById('event-query-char-id')?.value || '').trim();
  if (!uid || !charId) throw new Error('请输入用户 ID 和角色 ID');
  return {uid, charId};
}

function _memoryEventId() {
  const eventId = (document.getElementById('event-query-event-id')?.value || '').trim();
  if (!eventId) throw new Error('请输入事件 ID');
  return eventId;
}

function _memoryEventResult() {
  return document.getElementById('event-query-result');
}

function _showMemoryEventResult(value) {
  const output = _memoryEventResult();
  if (output) output.textContent = JSON.stringify(value, null, 2);
}

function _memoryEventParams(scope) {
  return new URLSearchParams({uid: scope.uid, char_id: scope.charId, realm: 'reality'});
}

async function loadMemoryEventSearch() {
  const output = _memoryEventResult();
  if (output) output.textContent = '加载中…';
  try {
    const scope = _memoryEventScope();
    const params = _memoryEventParams(scope);
    const text = (document.getElementById('event-query-text')?.value || '').trim();
    if (text) params.set('q', text);
    _showMemoryEventResult(await api('GET', `/memory-events/search?${params}`));
  } catch (error) {
    if (output) output.textContent = `加载失败：${error.message}`;
  }
}

async function loadMemoryEvent() {
  const output = _memoryEventResult();
  if (output) output.textContent = '加载中…';
  try {
    const scope = _memoryEventScope();
    const eventId = _memoryEventId();
    _showMemoryEventResult(await api('GET', `/memory-events/${encodeURIComponent(eventId)}?${_memoryEventParams(scope)}`));
  } catch (error) {
    if (output) output.textContent = `加载失败：${error.message}`;
  }
}

async function loadMemoryEventWindow() {
  const output = _memoryEventResult();
  if (output) output.textContent = '加载中…';
  try {
    const scope = _memoryEventScope();
    const eventId = _memoryEventId();
    const params = _memoryEventParams(scope);
    params.set('before', document.getElementById('event-query-before')?.value || '10');
    params.set('after', document.getElementById('event-query-after')?.value || '10');
    _showMemoryEventResult(await api('GET', `/memory-events/${encodeURIComponent(eventId)}/window?${params}`));
  } catch (error) {
    if (output) output.textContent = `加载失败：${error.message}`;
  }
}

async function loadMemoryEventRelated() {
  const output = _memoryEventResult();
  if (output) output.textContent = '加载中…';
  try {
    const scope = _memoryEventScope();
    const eventId = _memoryEventId();
    _showMemoryEventResult(await api('GET', `/memory-events/${encodeURIComponent(eventId)}/related?${_memoryEventParams(scope)}`));
  } catch (error) {
    if (output) output.textContent = `加载失败：${error.message}`;
  }
}

async function loadMemoryEventQueryTrace() {
  const output = _memoryEventResult();
  if (output) output.textContent = '加载中…';
  try {
    const scope = _memoryEventScope();
    _showMemoryEventResult(await api('GET', `/memory-events/query-trace?${_memoryEventParams(scope)}`));
  } catch (error) {
    if (output) output.textContent = `加载失败：${error.message}`;
  }
}

async function tombstoneMemoryEvent() {
  const output = _memoryEventResult();
  if (output) output.textContent = t('common.loading', '处理中...');
  try {
    const scope = _memoryEventScope();
    const eventId = _memoryEventId();
    const confirmed = window.confirm(t(
      'memory_events.tombstone_confirm',
      '这会清空该事件的文本与媒体引用，并保留墓碑和关系边。物理删除当前不可用。继续？',
    ));
    if (!confirmed) return;
    _showMemoryEventResult(await api('DELETE', `/memory-events/${encodeURIComponent(eventId)}?${_memoryEventParams(scope)}`));
  } catch (error) {
    if (output) output.textContent = `${t('memory_events.operation_failed', '操作失败')}：${error.message}`;
  }
}

async function loadMemoryEventMigrationStatus() {
  const output = _memoryEventResult();
  if (output) output.textContent = t('common.loading', '加载中...');
  try {
    const scope = _memoryEventScope();
    const params = new URLSearchParams({uid: scope.uid, char_id: scope.charId});
    _showMemoryEventResult(await api('GET', `/observability/memory-event-migration?${params}`));
  } catch (error) {
    if (output) output.textContent = `${t('common.load_failed', '加载失败')}：${error.message}`;
  }
}

// ══════════════════════════════════════════════════════════
//  观测页：隐性状态
// ══════════════════════════════════════════════════════════
async function loadObserveHidden() {
  const el = document.getElementById('obs-hidden-raw');
  el.textContent = '加载中…';
  try {
    const d = await api('GET', '/debug/user-hidden-state');
    el.textContent = JSON.stringify(d, null, 2);
  } catch(e) {
    el.textContent = '加载失败：' + e.message;
  }
}

// ══════════════════════════════════════════════════════════
//  观测页：聊天日志
// ══════════════════════════════════════════════════════════
async function loadObserveChatlogDates() {
  const sel = document.getElementById('obs-chatlog-date');
  sel.innerHTML = '<option value="">— 加载中… —</option>';
  try {
    const d = await api('GET', '/chat-log/dates');
    const dates = d.dates || d || [];
    sel.innerHTML = '<option value="">— 选择日期 —</option>' +
      dates.map(dt => `<option value="${escapeHtml(dt)}">${escapeHtml(dt)}</option>`).join('');
  } catch(e) {
    sel.innerHTML = '<option value="">加载失败</option>';
    toast('聊天日志日期加载失败：' + e.message, 'err');
  }
}

async function _fetchChatArtifactResponse(artifactId, preview) {
  const id = String(artifactId || '').trim();
  if (!/^[A-Fa-f0-9]{32}$/.test(id)) throw new Error('invalid artifact id');
  const path = `/chat/artifacts/${encodeURIComponent(id)}${preview ? '/preview' : ''}`;
  const r = await fetch(BASE + path, { headers: { Authorization: `Bearer ${TOKEN}` } });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(`HTTP ${r.status}: ${text}`);
  }
  return r;
}

async function downloadObserveChatArtifact(artifactId, filename) {
  try {
    const r = await _fetchChatArtifactResponse(artifactId, false);
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = String(filename || 'artifact.txt').replace(/[\\/:*?"<>|]/g, '_');
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    toast(t('observe.chat_artifacts.download_failed', '下载失败：{error}', {error: e.message}), 'err');
  }
}

async function previewObserveChatArtifact(artifactId) {
  const host = document.getElementById('obs-artifacts-preview');
  if (!host) return;
  host.hidden = false;
  host.textContent = t('common.loading', '加载中…');
  try {
    const r = await _fetchChatArtifactResponse(artifactId, true);
    const html = await r.text();
    host.replaceChildren();
    const iframe = document.createElement('iframe');
    iframe.setAttribute('sandbox', '');
    iframe.setAttribute('referrerpolicy', 'no-referrer');
    iframe.setAttribute('title', t('observe.chat_artifacts.preview', '预览'));
    iframe.style.cssText = 'width:100%;min-height:280px;border:1px solid var(--border);background:#fff';
    iframe.srcdoc = html;
    host.appendChild(iframe);
  } catch (e) {
    host.textContent = t('observe.chat_artifacts.preview_failed', '预览失败：{error}', {error: e.message});
  }
}

async function loadObserveChatArtifacts() {
  const uid = (document.getElementById('obs-artifacts-uid')?.value || '').trim();
  const charId = (document.getElementById('obs-artifacts-char')?.value || '').trim();
  const el = document.getElementById('obs-artifacts-body');
  const previewHost = document.getElementById('obs-artifacts-preview');
  if (previewHost) {
    previewHost.hidden = true;
    previewHost.replaceChildren();
  }
  if (!el) return;
  if (!uid || !charId) {
    el.innerHTML = `<div class="empty">${escapeHtml(t('observe.chat_artifacts.need_scope', '输入用户 ID 和角色 ID 后查看。'))}</div>`;
    return;
  }
  el.innerHTML = `<div class="loading">${escapeHtml(t('common.loading', '加载中…'))}</div>`;
  try {
    const query = new URLSearchParams({ uid, char_id: charId, limit: '50' });
    const d = await api('GET', `/observability/chat-artifacts?${query}`);
    const items = d.items || [];
    if (!items.length) {
      el.innerHTML = `<div class="empty">${escapeHtml(t('observe.chat_artifacts.empty', '该范围暂无产物文件。'))}</div>`;
      return;
    }
    const rows = items.map(item => {
      const id = String(item.id || '');
      const filename = String(item.filename || '');
      const mime = escapeHtml(item.mime || '');
      const size = escapeHtml(String(item.size ?? 0));
      const created = item.created_at ? new Date(item.created_at * 1000).toLocaleString() : '—';
      const args = escapeHtml(JSON.stringify([id, filename]));
      const previewable = item.previewable === true || /\.(html?|md|txt|json|csv|ya?ml|xml|toml|css)$/i.test(filename);
      const preview = previewable
        ? ` <button type="button" class="btn btn-ghost btn-sm" data-action="previewObserveChatArtifact" data-action-args="${escapeHtml(JSON.stringify([id]))}">${escapeHtml(t('observe.chat_artifacts.preview', '预览'))}</button>`
        : '';
      return `<div style="padding:10px 14px;border-top:1px solid var(--border);font-size:12px"><div><code>${escapeHtml(filename)}</code> · ${size} B · ${escapeHtml(created)}</div><div style="color:var(--muted);margin-top:4px">${mime} · ${escapeHtml(id)}</div><div style="margin-top:6px"><button type="button" class="btn btn-ghost btn-sm" data-action="downloadObserveChatArtifact" data-action-args="${args}">${escapeHtml(t('observe.chat_artifacts.download', '下载'))}</button>${preview}</div></div>`;
    }).join('');
    el.innerHTML = `<div class="admin-inline-086">${escapeHtml(t('observe.chat_artifacts.count', '共 {count} 个文件', {count: String(items.length)}))} · ${escapeHtml(d.retention || '')}</div>${rows}`;
    bindPageActions(el);
  } catch (e) {
    el.innerHTML = `<div class="empty">${escapeHtml(t('observe.chat_artifacts.load_failed', '加载失败：{error}', {error: e.message}))}</div>`;
  }
}

async function loadObserveChatlogDay() {
  const date = document.getElementById('obs-chatlog-date').value;
  const el   = document.getElementById('obs-chatlog-body');
  if (!date) { el.textContent = '请选择日期'; return; }
  el.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const d = await api('GET', `/chat-log/${encodeURIComponent(date)}`);
    const items = d.messages || d.entries || d || [];
    if (!items.length) { el.innerHTML = '<div class="empty">该日期暂无记录</div>'; return; }
    el.innerHTML = items.map(m => {
      const role = escapeHtml(m.role || m.speaker || '');
      const content = escapeHtml(m.content || m.text || '');
      const ts = m.timestamp ? `<span style="font-size:11px;color:var(--muted)">[${escapeHtml(m.timestamp)}]</span> ` : '';
      const roleColor = role === 'assistant' ? 'var(--accent)' : 'var(--text)';
      return `<div class="i18n-raw" style="margin-bottom:6px;padding:6px 10px;border-radius:4px;background:var(--bg-secondary)">
        ${ts}<span style="font-weight:600;color:${roleColor}">${role}</span>: ${content}
      </div>`;
    }).join('');
  } catch(e) {
    el.innerHTML = `<div class="empty">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

// ══════════════════════════════════════════════════════════
//  观测页：运行时内部态
// ══════════════════════════════════════════════════════════
async function loadObserveRuntime() {
  const ids = ['queue','dlq','pending','locks','channels','mood'];
  ids.forEach(id => {
    const el = document.getElementById('obs-rt-' + id);
    if (el) el.textContent = '加载中…';
  });
  try {
    const d = await api('GET', '/observe/runtime');
    const set = (id, val) => {
      const el = document.getElementById('obs-rt-' + id);
      if (el) el.textContent = typeof val === 'object' ? JSON.stringify(val, null, 2) : String(val);
    };
    // slow_queue
    const q = d.slow_queue || {};
    set('queue', q.error ? `读取失败: ${q.error}` :
      `积压任务: ${q.queue_size ?? '?'}\nworker 存活: ${q.worker_alive ?? '?'}\n当前任务: ${q.current_task_type ?? '(空闲)'}`);
    // DLQ
    const dlq = d.dead_letter_queue || {};
    if (dlq.error) {
      set('dlq', `读取失败: ${dlq.error}`);
    } else {
      const recent = (dlq.recent || []).map(f =>
        `  ${f.task_type}  ${f.failed_at ? f.failed_at.slice(0,19).replace('T',' ') : ''}`
      ).join('\n');
      set('dlq', `文件总数: ${dlq.count ?? '?'}\n最近:\n${recent || '  (空)'}`);
    }
    // pending_perception
    const pp = d.pending_perception || {};
    set('pending', pp.error ? `读取失败: ${pp.error}` :
      `未提交感知: ${pp.count ?? '?'}\n最旧: ${pp.oldest ? pp.oldest.slice(0,19).replace('T',' ') : '(无)'}`);
    // locks
    const lk = d.locks || {};
    set('locks', lk.error ? `读取失败: ${lk.error}` :
      `uid_lock 持有: ${JSON.stringify(lk.uid_locks_held ?? [])}\nglobal_lock 持有: ${JSON.stringify(lk.global_locks_held ?? [])}\nconv_lock 持有: ${JSON.stringify(lk.conversation_locks_held ?? [])}`);
    // channels
    const ch = d.channels || {};
    set('channels', ch.error ? `读取失败: ${ch.error}` :
      `活跃通道: ${JSON.stringify(ch.active ?? [])}`);
    // mood
    const md = d.mood || {};
    set('mood', md.error ? `读取失败: ${md.error}` :
      `${md.mood_text || '(无)'}\n${md.mood_raw ? JSON.stringify(md.mood_raw, null, 2) : ''}`);
  } catch(e) {
    ids.forEach(id => {
      const el = document.getElementById('obs-rt-' + id);
      if (el) el.textContent = '加载失败：' + e.message;
    });
  }
}

// ══════════════════════════════════════════════════════════
//  内置唤醒状态（独立 autonomy job/run store，不是 scheduler proposal）
// ══════════════════════════════════════════════════════════
async function loadObserveAutonomy() {
  const overviewEl = document.getElementById('autonomy-overview');
  const runsEl = document.getElementById('autonomy-runs');
  if (!overviewEl) return;
  try {
    const [status, effectiveState, config, runs, tools, opportunities] = await Promise.all([
      api('GET', '/admin/autonomy/status'), api('GET', '/admin/autonomy/effective-state'),
      api('GET', '/admin/autonomy/config'),
      api('GET', '/admin/autonomy/runs'), api('GET', '/admin/autonomy/tools'),
      api('GET', '/observability/autonomy-opportunities?limit=100'),
    ]);
    _renderAutonomyOverview(overviewEl, status, config, effectiveState);
    _renderAutonomyRuns(runsEl, runs.runs || []);
    _renderAutonomyFunnel(document.getElementById('autonomy-funnel'), opportunities.funnel || {});
    const host = document.getElementById('autonomy-tools');
    _renderAutonomyTools(host, tools.tools || []);

  } catch (e) { overviewEl.innerHTML = `<div class="empty">读取唤醒状态失败：${escapeHtml(e.message)}</div>`; }
}

function _renderAutonomyFunnel(host, funnel) {
  if (!host) return;
  const windows = [['24h', '近 24 小时'], ['7d', '近 7 天']];
  const labels = {
    no_candidate: 'No candidate', producer_matched: 'Producer matched',
    signal_queued: '信号入队', opportunity_created: '机会创建', admission_allowed: '准入通过',
    blocked_user_active: '用户活跃阻断', daily_budget: '每日预算', minimum_interval: '最小间隔',
    expired: '过期', talk_unavailable: 'Talk 不可用', evaluated_silent: '模型静默',
    tools_only: '仅工具', talk_gate_rejected: 'Talk gate 拒绝', talk_sent: '已发送',
  };
  const keys = Object.keys(labels);
  host.innerHTML = windows.map(([key, title]) => {
    const data = funnel[key] || {};
    const totals = data.totals || {};
    const rows = Object.entries(data.by_source || {});
    const totalLine = keys.filter(name => totals[name]).map(name => `${labels[name]} ${totals[name]}`).join('；') || '暂无可用记录';
    const sourceTable = rows.length ? `<table><thead><tr><th>来源</th>${keys.map(name => `<th>${escapeHtml(labels[name])}</th>`).join('')}</tr></thead><tbody>${rows.map(([source, counts]) => `<tr><td>${escapeHtml(source)}</td>${keys.map(name => `<td>${Number(counts[name] || 0)}</td>`).join('')}</tr>`).join('')}</tbody></table>` : '';
    return `<section class="autonomy-funnel-window"><strong>${escapeHtml(title)}</strong><p class="autonomy-hint">${escapeHtml(totalLine)}</p>${sourceTable}</section>`;
  }).join('');
}

async function loadSelfManagement() {
  const capabilities = document.getElementById('self-management-capabilities');
  const audit = document.getElementById('self-management-audit');
  if (!capabilities || !audit) return;
  try {
    const data = await api('GET', '/admin/self-management');
    const rows = data.capabilities || [];
    capabilities.innerHTML = `<table><thead><tr><th>能力</th><th>可用</th><th>用户授权</th><th>代理状态</th><th>锁定</th><th>操作</th></tr></thead><tbody>${rows.map(row => {
      const id = escapeHtml(row.capability_id);
      const grant = row.grant || {};
      const locked = !!row.locked;
      return `<tr><td>${id}</td><td>${row.system_available ? '是' : '否'}</td><td>${grant.allowed ? (grant.mutable_by_agent ? '可修改' : '仅用户可改') : '未授权'}<br><small>${escapeHtml(JSON.stringify(grant.constraints || {}))}</small></td><td>${escapeHtml(String(row.agent_selected_state ?? '默认'))}</td><td>${locked ? '已锁定' : '开放'}</td><td class="actions"><button class="btn btn-ghost btn-sm" data-action="selfManagementChange" data-action-args='["grant","${id}"]'>授权</button><button class="btn btn-ghost btn-sm" data-action="selfManagementChange" data-action-args='["revoke","${id}"]'>撤销授权</button><button class="btn btn-ghost btn-sm" data-action="selfManagementChange" data-action-args='["${locked ? 'unlock' : 'lock'}","${id}"]'>${locked ? '解锁' : '锁定'}</button><button class="btn btn-ghost btn-sm" data-action="selfManagementChange" data-action-args='["restore","${id}"]'>恢复</button><button class="btn btn-ghost btn-sm" data-action="selfManagementChange" data-action-args='["undo","${id}"]'>撤销上一步</button></td></tr>`;
    }).join('')}</tbody></table>`;
    bindPageActions(capabilities);
    const events = data.audit || [];
    audit.innerHTML = `<table><thead><tr><th>时间</th><th>来源</th><th>能力</th><th>修订号</th><th>代理值</th><th>生效值</th><th>操作</th><th>结果</th><th>原因</th><th>运行 / 任务</th></tr></thead><tbody>${events.map(event => `<tr><td>${escapeHtml(_autonomyTime(event.timestamp))}</td><td>${escapeHtml(event.source || event.actor || '')}</td><td>${escapeHtml(event.capability_id || '')}</td><td>${escapeHtml(`${event.revision_before ?? '-'} -> ${event.revision_after ?? '-'}`)}</td><td>${escapeHtml(`${event.old_value ?? '-'} -> ${event.new_value ?? '-'}`)}</td><td>${escapeHtml(`${event.old_effective_value ?? '-'} -> ${event.new_effective_value ?? '-'}`)}</td><td>${escapeHtml(event.action_id || '')}</td><td>${escapeHtml(event.result || '')}</td><td>${escapeHtml(event.reason || '')}</td><td>${escapeHtml([event.run_id, event.job_id].filter(Boolean).join(' / '))}</td></tr>`).join('')}</tbody></table>`;
  } catch (error) {
    capabilities.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

async function selfManagementChange(action, capabilityId) {
  const reason = window.prompt('请输入此次用户覆盖的原因：', '用户覆盖');
  if (!reason) return;
  let path, body;
  if (action === 'grant' || action === 'revoke') {
    const constraints = capabilityId === 'autonomy.min_interval_seconds' ? {minimum: 60, maximum: 86400} : {};
    path = '/admin/self-management/grants'; body = {capability_id: capabilityId, allowed: action === 'grant', mutable_by_agent: action === 'grant', constraints, reason};
  } else if (action === 'lock' || action === 'unlock') {
    path = '/admin/self-management/locks'; body = {capability_id: capabilityId, locked: action === 'lock', reason};
  } else if (action === 'restore') {
    path = '/admin/self-management/restore'; body = {capability_id: capabilityId, reason};
  } else {
    path = '/admin/self-management/undo'; body = {capability_id: capabilityId, reason};
  }
  try { await api('POST', path, body); await loadSelfManagement(); toast('自主管理能力已更新', 'ok'); }
  catch (error) { toast(`自主管理能力更新失败：${error.message}`, 'err'); }
}

function _autonomyTime(value) {
  if (!value) return '—';
  const date = new Date(Number(value) * 1000);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
}

function _autonomyStateLabel(status) {
  return { '空闲': '空闲', '排队': '已有活动等待处理', '运行': '正在进行自主活动', '冷却': '等待下一次可评估时间', '熔断': '连续失败后暂时暂停' }[status] || status || '未知';
}

function _autonomyTalkLabel(talk) {
  const reasons = {
    suppressed_unanswered_cap: '等待用户回复后再试', suppressed_dnd: '勿扰时段',
    suppressed_proactive_off: '主动发言已关闭', suppressed_daily_budget: '今日主动发言预算已用完',
    blocked_dream: '梦境中', blocked_dream_uncertain: '梦境状态未确认',
    gap_not_elapsed: '距离上次主动消息太近', daily_budget_exceeded: '今日主动发言预算已用完',
  };
  if (!talk?.available) return `暂不可用${talk?.reason ? `：${reasons[talk.reason] || '当前条件不允许'}` : ''}`;
  return '可主动发言';
}

function _renderAutonomyOverview(host, status, config, effectiveState) {
  const daily = status.daily || {};
  const talk = status.talk || {};
  const last = status.last_run || {};
  const effective = effectiveState?.proactive || {};
  const state = effective.reason ? `${effective.state}（${effective.reason}）` : _autonomyStateLabel(status.runtime_state);
  const stateClass = ['disabled', 'blocked', 'unavailable'].includes(effective.state) ? 'autonomy-state-off' : ['cooled_down', 'queued'].includes(effective.state) ? 'autonomy-state-attention' : 'autonomy-state-running';
  const cards = [
    ['内置唤醒', effective.state || (config.enabled ? 'enabled' : 'disabled'), effective.reason || '统一生效状态'],
    ['当前状态', state, status.current_run_id ? `当前运行：${String(status.current_run_id).slice(0, 8)}` : '没有正在进行的活动'],
    ['下一次间隔检查', _autonomyTime(status.next_due_at), config.interval?.enabled ? '按上一次完成评估计算' : '间隔唤醒未启用'],
    ['今日评估', `${Number(effectiveState?.daily_evaluation_budget?.used ?? daily.evaluations ?? 0)} / ${Number(effectiveState?.daily_evaluation_budget?.effective_value ?? config.daily_evaluation_budget ?? 0)}`, `工具 ${Number(daily.tools || 0)} 次；Talk ${Number(daily.talks || 0)} 次`],
    ['Talk 状态', effectiveState?.talk?.effective ? '可主动发言' : (effectiveState?.talk?.gate_reason || _autonomyTalkLabel(talk)), `gate=${effectiveState?.talk?.gate_mode || talk.mode || 'unknown'}`],
    ['最近一次运行', _autonomyRunLabel(last), last.finished_at ? _autonomyTime(last.finished_at) : '尚无记录'],
  ];
  host.innerHTML = `<div class="autonomy-overview-grid">${cards.map(([label, value, detail], index) => `<div class="stat"><div class="val ${index === 1 ? stateClass : ''}">${escapeHtml(value)}</div><div class="lbl">${escapeHtml(label)}</div><div class="detail">${escapeHtml(detail)}</div></div>`).join('')}</div>`;
}

function _autonomyRunLabel(run) {
  const disposition = String(run?.disposition || '');
  const labels = {
    completed_no_op: '安静结束', completed_tools_only: '完成工具活动', completed_talk_sent: '已主动发言', completed_tools_and_talk_sent: '活动后已发言',
    talk_canceled: '取消发言', talk_soft_blocked_then_canceled: '因时机不佳取消发言', talk_soft_blocked_then_sent: '确认后已发言',
    blocked_dream: '梦境中，未运行', blocked_dream_uncertain: '梦境状态不确定，未运行', blocked_user_active: '用户正在互动，未运行',
    suppressed_unanswered_cap: '等待用户回复', suppressed_dnd: '勿扰时段，未发言', suppressed_proactive_off: '主动功能已关闭', suppressed_daily_budget: '今日预算用完',
    canceled_by_user_activity: '用户开始互动，已停止', timeout: '运行超时', tool_failed: '工具未完成', tool_outcome_unknown: '工具结果不明确', llm_failed: '判断服务失败', circuit_open: '熔断保护中', expired: '已过期', duplicate: '重复机会已合并', lease_lost: '运行租约已失效',
  };
  return labels[disposition] || (disposition ? disposition.replaceAll('_', ' ') : '尚无记录');
}

function _renderAutonomyToolsLegacy(host, tools) {
  // This is intentionally driven by the effective autonomy allowlist. It does
  // not name or hide MCP tools by hand: inactive/unavailable entries simply
  // are not part of the current autonomy surface.
  const active = tools.filter(item => item.enabled);
  if (!active.length) {
    host.innerHTML = '<div class="empty">当前没有已启用的自主工具。角色仍可安静结束或在允许时主动 Talk。</div>';
    return;
  }
  host.innerHTML = `<table><thead><tr><th>工具</th><th>来源</th><th>动作类型</th><th>风险</th><th>可重试</th></tr></thead><tbody>${active.map(item => `<tr><td>${escapeHtml(item.name)}</td><td>${escapeHtml(item.source)}</td><td>${escapeHtml(item.effect === 'read' ? '读取' : '受限写入')}</td><td>${escapeHtml(item.risk === 'high' ? '高（当前不会自动执行）' : '低')}</td><td>${item.idempotent ? '可安全重试' : '失败后停止并记录'}</td></tr>`).join('')}</tbody></table>`;
}

function _renderAutonomyTools(host, tools) {
  const cell = value => escapeHtml(value === null || value === undefined || value === '' ? '-' : String(value));
      host.innerHTML = `<table><thead><tr><th>工具</th><th>来源</th><th>全局开关</th><th>已注册</th><th>已连接</th><th>MCP 策略</th><th>MCP 明确授权</th><th>自主管理授权</th><th>自主管理生效值</th><th>代理选择</th><th>自主白名单</th><th>动作类型</th><th>危险/确认</th><th>最终状态</th><th>拒绝原因</th></tr></thead><tbody>${tools.map(item => `<tr><td>${cell(item.name)}</td><td>${cell(item.source)}</td><td>${cell(item.global_enabled)}</td><td>${cell(item.registered)}</td><td>${cell(item.mcp_server_connected)}</td><td>${cell(item.mcp_policy)}</td><td>${cell(item.mcp_explicit)}</td><td>${cell(item.self_capability_granted)}</td><td>${cell(item.self_capability_effective)}</td><td>${cell(item.agent_selected_state)}</td><td>${cell(item.autonomy_allowlist)}</td><td>${cell(item.effect)}</td><td>${cell(item.dangerous || item.require_confirm)}</td><td>${cell(item.execution_allowed)}</td><td>${cell(item.denial_reason)}</td></tr>`).join('')}</tbody></table>`;
}

function _renderAutonomyRuns(host, runs) {
  if (!runs.length) {
    host.innerHTML = '<div class="empty">还没有运行记录。可以用上方“排队一次测试”验证流程。</div>';
    return;
  }
  host.innerHTML = `<table><thead><tr><th>完成时间</th><th>触发方式</th><th>结果</th><th>执行过的工具</th><th>Talk</th><th>耗时</th></tr></thead><tbody>${runs.map(run => {
    const duration = run.finished_at && run.started_at ? `${Math.max(0, Number(run.finished_at) - Number(run.started_at)).toFixed(1)} 秒` : '—';
    const talk = run.talk_sent ? '已发送' : run.talk_soft_blocked ? '时机不佳，未发送' : '未尝试';
    return `<tr><td>${escapeHtml(_autonomyTime(run.finished_at || run.started_at))}</td><td>${escapeHtml({manual:'手动测试', overflow:'Overflow', schedule:'定时', interval:'间隔'}[run.source] || run.source || '—')}</td><td><span class="badge ${String(run.disposition || '').includes('completed') || run.talk_sent ? 'badge-success' : 'badge-warn'}">${escapeHtml(_autonomyRunLabel(run))}</span></td><td class="autonomy-run-tools">${escapeHtml((run.tool_names || []).join('、') || '无')}</td><td>${escapeHtml(talk)}</td><td>${escapeHtml(duration)}</td></tr>`;
  }).join('')}</tbody></table>`;
}

async function loadAutonomyPrompt(runId) {
  const host = document.getElementById('autonomy-prompt');
  if (!host || !runId) return;
  host.innerHTML = '<div class="loading">Loading...</div>';
  try {
    const data = await api('GET', `/admin/autonomy/runs/${encodeURIComponent(runId)}/prompt`);
    const text = (data.messages || []).map(item => `[${item.role}${item._layer ? ` / ${item._layer}` : ''}]\n${item.content || ''}`).join('\n\n');
    host.innerHTML = `<pre class="autonomy-prompt-view">${escapeHtml(text || 'No snapshot for this run.')}</pre>`;
  } catch (error) {
    host.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

async function saveAutonomyConfig() {
  try {
    const weekdays = document.getElementById('autonomy-schedule-weekdays').value.split(',').map(x => x.trim()).filter(Boolean).map(Number);
    const start = document.getElementById('autonomy-window-start').value;
    const end = document.getElementById('autonomy-window-end').value;
    const config = await api('PATCH', '/admin/autonomy/config', {
      enabled: document.getElementById('autonomy-enabled').checked,
      talk_enabled: document.getElementById('autonomy-talk').checked,
      daily_evaluation_budget: Number(document.getElementById('autonomy-daily').value),
      min_interval_seconds: Number(document.getElementById('autonomy-min-interval').value),
      interval: {enabled: document.getElementById('autonomy-interval-enabled').checked, seconds: Number(document.getElementById('autonomy-interval').value)},
      overflow: {enabled: document.getElementById('autonomy-overflow-enabled').checked, threshold: Number(document.getElementById('autonomy-overflow-threshold').value)},
      schedule: {enabled: document.getElementById('autonomy-schedule-enabled').checked, time: document.getElementById('autonomy-schedule-time').value, timezone: document.getElementById('autonomy-schedule-timezone').value || 'local', weekdays, window: start && end ? [start, end] : [], restart_miss_policy: document.getElementById('autonomy-miss-policy').value},
    });
    await loadAutonomySettings();
    const enabled = config.enabled ? '已启用内置唤醒' : '已关闭内置唤醒';
    const overflow = config.overflow?.enabled ? `溢出信号已启用（阈值 ${config.overflow.threshold}）` : '溢出信号已关闭';
    const schedule = config.schedule?.enabled ? `定时唤醒已启用（${config.schedule.time}）` : '定时唤醒已关闭';
    const interval = config.interval?.enabled ? `间隔唤醒已启用（${config.interval.seconds} 秒）` : '间隔唤醒已关闭';
    toast(`${enabled}；${overflow}；${schedule}；${interval}`, 'ok');
  } catch (e) { toast('保存失败：' + e.message, 'err'); }
}

async function enqueueAutonomyTest() {
  try { const r = await api('POST', '/admin/autonomy/test-enqueue', {source: document.getElementById('autonomy-test-source').value}); toast(`已排队一次测试（任务 ${String(r.job_id).slice(0, 8)}）`, 'ok'); if(document.getElementById('page-observe-autonomy')?.classList.contains('active'))await loadObserveAutonomy(); }
  catch (e) { toast('排队失败：' + e.message, 'err'); }
}

window.loadObserveChatArtifacts = loadObserveChatArtifacts;
window.downloadObserveChatArtifact = downloadObserveChatArtifact;
window.previewObserveChatArtifact = previewObserveChatArtifact;
window.loadObserveAutonomy = loadObserveAutonomy;
window.loadSelfManagement = loadSelfManagement;
window.selfManagementChange = selfManagementChange;
window.saveAutonomyConfig = saveAutonomyConfig;
window.enqueueAutonomyTest = enqueueAutonomyTest;

// ── 检视器当前快照（导出 MD 用）──
let _obsPromptCurrent      = null;
let _obsDreamPromptCurrent = null;
let _obsProbeCurrent       = null;

// ══════════════════════════════════════════════════════════
//  五类只读观测面板（由桌面客户端迁入）
// ══════════════════════════════════════════════════════════
async function initObserveCharacters(selectId, afterLoad) {
  const sel = document.getElementById(selectId);
  if (!sel) return;
  try {
    const d = await api('GET', '/characters');
    const characters = d.characters || d.items || [];
    const activeId = d.active_id || d.active || '';
    sel.innerHTML = characters.map(c => `<option value="${escapeHtml(c.id || '')}"${c.id === activeId ? ' selected' : ''}>${escapeHtml(c.label || c.id || '')}</option>`).join('');
    if (afterLoad) await afterLoad();
  } catch(e) {
    if (afterLoad) await afterLoad();
  }
}

function observeTime(value) {
  if (!value) return '—';
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function observeEmpty(label) {
  return `<div class="empty">${escapeHtml(label || t('common.no_data', '暂无数据'))}</div>`;
}
function observeBar(value, max) {
  const ratio = max > 0 ? Math.max(0, Math.min(1, Number(value || 0) / max)) : 0;
  return `<div style="height:6px;background:var(--border);border-radius:4px;overflow:hidden;margin-top:5px"><div style="width:${ratio*100}%;height:100%;background:var(--accent);border-radius:4px"></div></div>`;
}

async function loadObserveGrowth() {
  const el = document.getElementById('obs-growth-content');
  const charId = document.getElementById('obs-growth-char')?.value || '';
  if (!el || !charId) { if(el) el.innerHTML = observeEmpty('暂无角色'); return; }
  el.innerHTML = observeEmpty('加载中…');
  try {
    const root = await api('GET', `/growth/interests?char_id=${encodeURIComponent(charId)}`);
    const interests = root.interests || [];
    if (!interests.length) { el.innerHTML = observeEmpty('尚未启用成长记录'); return; }
    const practice = await api('GET', '/growth/practice-log');
    const practiceCount = (practice.entries || []).filter(x => !x.char_id || x.char_id === charId).length;
    const bundles = await Promise.all(interests.map(async interest => {
      const id = String(interest.id || interest.interest_id || interest.name || '');
      if (!id) return {interest, id, works:[], notes:[]};
      const qs = `?char_id=${encodeURIComponent(charId)}`;
      const [works, notes] = await Promise.all([
        api('GET', `/growth/works/${encodeURIComponent(id)}${qs}`),
        api('GET', `/growth/notes/${encodeURIComponent(id)}${qs}`),
      ]);
      return {interest, id, works:works.entries || [], notes:notes.entries || []};
    }));
    el.className = '';
    el.innerHTML = bundles.map(({interest,id,works,notes}) => {
      const scores = (interest.recent_scores || works.map(x => x.score)).map(Number).filter(Number.isFinite);
      const latest = scores.length ? scores[scores.length-1] : 0;
      const first = scores.length ? scores[0] : latest;
      const trend = latest > first + .01 ? '↗' : latest < first - .01 ? '↘' : '→';
      const level = Math.max(0, Math.min(5, Math.round(Number(interest.level || 0))));
      return `<div style="padding:14px 0;border-bottom:1px solid var(--border)">
        <div style="display:flex;justify-content:space-between"><strong>${escapeHtml(interest.name || id)}</strong><span>${trend}</span></div>
        <div style="color:var(--accent);letter-spacing:1px">${'★'.repeat(level)}${'☆'.repeat(5-level)}</div>${observeBar(latest,10)}
        <h4 style="margin:14px 0 5px">作品时间轴</h4>${works.length ? works.slice().reverse().map(w => { const f=String(w.file||w.filename||''); return `<div style="padding:5px 0"><button class="btn btn-ghost btn-sm" onclick="loadObserveGrowthWork(this)" data-char="${escapeHtml(charId)}" data-interest="${escapeHtml(id)}" data-file="${escapeHtml(f)}">${escapeHtml(observeTime(w.date || w.ts))} · ${escapeHtml(w.title || f || '练习作品')}${w.score != null ? ` · ${escapeHtml(String(w.score))} 分` : ''}</button><pre class="obs-growth-work" style="display:none;white-space:pre-wrap"></pre></div>`; }).join('') : observeEmpty()}
        <h4 style="margin:14px 0 5px">技巧笔记</h4>${notes.length ? notes.map(n => `<div style="padding:4px 0">${escapeHtml(n.text || n.note || n.content || JSON.stringify(n))}${n.hits != null ? ` <span class="badge">命中 ${escapeHtml(String(n.hits))}</span>` : ''}</div>`).join('') : observeEmpty()}
      </div>`;
    }).join('') + `<div style="padding-top:12px;color:var(--muted)">练习日志：${practiceCount}</div>`;
  } catch(e) { el.innerHTML = observeEmpty('加载失败：' + e.message); }
}

async function loadObserveGrowthWork(button) {
  const pre = button.parentElement.querySelector('.obs-growth-work');
  if (pre.style.display !== 'none') { pre.style.display='none'; return; }
  pre.style.display='block'; pre.textContent='加载中…';
  try {
    const d = await api('GET', `/growth/works/${encodeURIComponent(button.dataset.interest)}/${encodeURIComponent(button.dataset.file)}?char_id=${encodeURIComponent(button.dataset.char)}`);
    pre.textContent = d.content || '（空文件）';
  } catch(e) { pre.textContent='加载失败：'+e.message; }
}

async function loadObserveVisual() {
  const el=document.getElementById('obs-visual-content'); if(!el)return;
  const input=document.getElementById('obs-visual-date');
  if (!input.value) input.value = new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
  el.innerHTML=observeEmpty('加载中…');
  try {
    const d=await api('GET',`/perception/visual-trace?date=${encodeURIComponent(input.value)}`), rows=d.entries||[];
    if(!rows.length){el.innerHTML=observeEmpty('当日暂无视觉观测');return;}
    const heat=Array(24).fill(0), drops={};
    rows.forEach(r=>{if(r.dropped)drops[r.dropped]=(drops[r.dropped]||0)+1;else if(r.ts)heat[new Date(r.ts*1000).getHours()]++;});
    const max=Math.max(...heat,1), captions=rows.filter(r=>r.caption).slice(-5).reverse();
    el.className=''; el.innerHTML=`<h4>24 小时时段热力</h4><div style="display:grid;grid-template-columns:repeat(12,1fr);gap:5px">${heat.map((n,h)=>`<div title="${h}:00 · ${n}" style="padding:8px 2px;text-align:center;border-radius:4px;background:color-mix(in srgb,var(--accent) ${Math.round(n/max*80+8)}%,transparent)">${h}</div>`).join('')}</div><h4 style="margin-top:18px">丢弃原因</h4>${Object.keys(drops).length?Object.entries(drops).map(([k,v])=>`<span class="badge" style="margin-right:6px">${escapeHtml(k)}: ${v}</span>`).join(''):observeEmpty()}<h4 style="margin-top:18px">最近观察描述</h4>${captions.length?captions.map(r=>`<div style="padding:7px 0;border-bottom:1px solid var(--border)">${escapeHtml(observeTime(r.ts))} · ${escapeHtml(r.caption)}</div>`).join(''):observeEmpty()}`;
  }catch(e){el.innerHTML=observeEmpty('加载失败：'+e.message);}
}

async function loadObserveSpend(){
  const el=document.getElementById('obs-spend-content'); if(!el)return; el.innerHTML='<div class="card empty">加载中…</div>';
  try{
    const [budget,ledger,mandates]=await Promise.all([api('GET','/spend/budget'),api('GET','/spend/ledger'),api('GET','/spend/mandates')]);
    const mandateRows=mandates.entries||[], ledgerRows=ledger.entries||[];
    el.innerHTML=`<div class="card"><h3>额度用量</h3><div>今日：${budget.daily_used||0} / ${budget.daily_cap||'—'}</div>${observeBar(budget.daily_used,budget.daily_cap)}<div style="margin-top:12px">本月：${budget.monthly_used||0} / ${budget.monthly_cap||'—'}</div>${observeBar(budget.monthly_used,budget.monthly_cap)}</div><div class="card"><h3>支出意向单</h3>${mandateRows.length?mandateRows.slice().reverse().map(r=>`<div style="padding:8px 0;border-bottom:1px solid var(--border)"><strong>${escapeHtml(r.payee||r.action||r.mandate_id||'—')}</strong> <span class="badge">${escapeHtml(r.status||'—')}</span><div>${escapeHtml(String(r.amount||r.max_price||0))} ${escapeHtml(r.currency||'')}</div></div>`).join(''):observeEmpty()}<div style="margin-top:10px;color:var(--muted)">安全门未落地前保持只读，不提供确认或拒绝操作。</div></div><div class="card"><h3>台账流水</h3>${ledgerRows.length?ledgerRows.slice().reverse().map(r=>`<div style="padding:8px 0;border-bottom:1px solid var(--border)">${escapeHtml(observeTime(r.ts))} · ${escapeHtml(r.payee||r.action||'—')} · ${escapeHtml(String(r.amount||0))} ${escapeHtml(r.currency||'')} <span class="badge">${escapeHtml(r.status||'—')}</span></div>`).join(''):observeEmpty()}</div>`;
  }catch(e){el.innerHTML=`<div class="card">${observeEmpty('加载失败：'+e.message)}</div>`;}
}

async function runSpendCheck(){
  const button=document.querySelector('[data-action="runSpendCheck"]');
  if(button)button.disabled=true;
  try{
    await api('POST','/spend/check');
    toast(t('dynamic.observe.spend_check_ok','余额检查已完成'),'ok');
    await loadObserveSpend();
  }catch(e){
    toast(t('dynamic.observe.spend_check_failed','余额检查失败：{error}').replace('{error}',e.message),'err');
  }finally{
    if(button)button.disabled=false;
  }
}

let _observeGroupSummaries = {};

function _observeGroupCharacterLabel(groupId, charId) {
  const roster = _observeGroupSummaries[groupId]?.roster || [];
  const member = roster.find(item => item.char_id === charId);
  return member?.label || charId || '—';
}

function _isStagePromptForGroup(snapshot, groupId) {
  const origin = snapshot?.origin || {};
  return origin.origin === 'stage' && origin.group_id === groupId;
}

async function _loadRecentPromptSnapshots(uid, limit=10) {
  if (!uid) return [];
  const first = await api('GET', `/observe/prompt-layers/${encodeURIComponent(uid)}?n=0`);
  if (!first.snapshot) return [];
  const count = Math.min(limit, Number(first.total_snapshots || 1));
  const rest = await Promise.all(
    Array.from({length: Math.max(0, count - 1)}, (_, i) =>
      api('GET', `/observe/prompt-layers/${encodeURIComponent(uid)}?n=${i + 1}`)
    )
  );
  return [first, ...rest].map(item => item.snapshot).filter(Boolean);
}

async function _loadRecentDreamPromptSnapshots(uid, limit=5) {
  if (!uid) return [];
  const first = await api('GET', `/observe/dream-prompt/${encodeURIComponent(uid)}?n=0`);
  if (!first.snapshot) return [];
  const count = Math.min(limit, Number(first.total_snapshots || 1));
  const rest = await Promise.all(
    Array.from({length: Math.max(0, count - 1)}, (_, i) =>
      api('GET', `/observe/dream-prompt/${encodeURIComponent(uid)}?n=${i + 1}`)
    )
  );
  return [first, ...rest].map(item => item.snapshot).filter(Boolean);
}

function _renderGroupDreamPromptSnapshot(groupId, snapshot) {
  const origin = snapshot.origin || {};
  const speaker = _observeGroupCharacterLabel(groupId, origin.char_id);
  const sceneTags = (snapshot.scene_tags || []).map(tag => `<code style="font-size:10px;background:#1d3a6e;color:#93c5fd;padding:1px 4px;border-radius:3px;margin-right:3px">${escapeHtml(tag)}</code>`).join('') || '—';
  const infoLine = `<div style="font-size:12px;color:var(--muted);display:flex;gap:16px;flex-wrap:wrap;padding:2px 0 8px">
    <span>${escapeHtml(t('group.dream_prompt_world', '世界'))}：${escapeHtml(snapshot.world_id || '?')}</span>
    <span>${escapeHtml(t('group.dream_prompt_lucid', '模式'))}：${escapeHtml(snapshot.lucid_mode || '?')}</span>
    <span>token：${Number(snapshot.total_tokens || 0).toLocaleString()}</span>
    <span>scene_tags：${sceneTags}</span>
  </div>`;
  const layerHtml = (snapshot.layers || []).length ? snapshot.layers.map(layer => {
    const injected = !!layer.injected;
    const state = injected
      ? `<span class="badge">${escapeHtml(t('group.prompt_kept', '保留'))}</span>`
      : `<span class="badge badge-danger">${escapeHtml(t('group.dream_prompt_not_injected', '未注入'))}</span>`;
    const content = injected
      ? (layer.content || t('group.prompt_empty_layer', '（空层）'))
      : t('group.dream_prompt_layer_skipped', '本层未注入（禁用/无内容）');
    return `<details style="margin:6px 0;padding:7px 9px;background:var(--bg-secondary);border-radius:6px">
      <summary style="cursor:pointer">${escapeHtml(layer.label || '?')} · ${Number(layer.chars || 0).toLocaleString()} ${escapeHtml(t('group.prompt_chars', '字'))} ${state}</summary>
      <pre style="font-size:11px;white-space:pre-wrap;word-break:break-all;max-height:260px;overflow:auto;margin:8px 0 0">${escapeHtml(content)}</pre>
    </details>`;
  }).join('') : observeEmpty(t('group.prompt_no_layers', '本轮没有可展示的层'));
  const userMsgHtml = snapshot.user_message ? `<div style="margin-top:8px;padding:7px 9px;background:var(--bg-secondary);border-radius:6px;border-left:3px solid var(--success)">
    <div style="font-size:11px;color:var(--muted)">${escapeHtml(t('group.dream_prompt_instruction', '发言指令'))}</div>
    <pre style="font-size:11px;white-space:pre-wrap;word-break:break-all;margin:4px 0 0">${escapeHtml(snapshot.user_message)}</pre>
  </div>` : '';
  const replyHtml = snapshot.llm_output != null ? `<div style="margin-top:8px;padding:7px 9px;background:var(--bg-secondary);border-radius:6px;border-left:3px solid var(--accent)">
    <div style="font-size:11px;color:var(--muted)">${escapeHtml(t('group.dream_prompt_reply', '角色回复'))}</div>
    <pre style="font-size:11px;white-space:pre-wrap;word-break:break-all;margin:4px 0 0">${escapeHtml(snapshot.llm_output)}</pre>
  </div>` : '';
  return `<details style="padding:9px 0;border-bottom:1px solid var(--border)">
    <summary style="cursor:pointer"><strong>${escapeHtml(speaker)}</strong> · dream_id: ${escapeHtml(snapshot.dream_id || '—')} · ${escapeHtml(observeTime(snapshot.captured_at))}</summary>
    <div style="padding:8px 0 0 18px">${infoLine}${layerHtml}${userMsgHtml}${replyHtml}</div>
  </details>`;
}

function _renderPrivateExchangePair(groupId, relation, log) {
  const charA = relation.char_a;
  const charB = relation.char_b;
  const labelA = _observeGroupCharacterLabel(groupId, charA);
  const labelB = _observeGroupCharacterLabel(groupId, charB);
  const entries = log.entries || [];
  const latest = entries.length ? observeTime(entries[entries.length - 1].ts) : '—';
  const body = entries.length
    ? entries.map(entry => `<div style="padding:7px 0;border-bottom:1px solid var(--border)">
        <span style="color:var(--muted);font-size:11px">${escapeHtml(observeTime(entry.ts))}</span>
        <strong style="margin-left:8px">${escapeHtml(_observeGroupCharacterLabel(groupId, entry.speaker_id))}</strong>
        <div class="i18n-raw" style="margin-top:3px;white-space:pre-wrap;word-break:break-word">${escapeHtml(entry.content || '')}</div>
      </div>`).join('')
    : observeEmpty(t('group.private_none', '这两位还没私下聊过'));
  return `<details style="padding:9px 0;border-bottom:1px solid var(--border)">
    <summary style="cursor:pointer"><strong>${escapeHtml(labelA)} ↔ ${escapeHtml(labelB)}</strong> · ${escapeHtml(t('group.private_last', '最近往来 {time}', {time: latest}))}</summary>
    <div style="padding:8px 0 0 18px">${body}</div>
  </details>`;
}

function _renderPromptLayersHtml(snapshot) {
  const present = new Set((snapshot.layers || []).map(layer => layer.layer));
  const layers = [
    ...(snapshot.layers || []),
    ...(snapshot.removed_layers || [])
      .filter(layer => !present.has(layer))
      .map(layer => ({layer, chars: 0, content: '', pruned: true})),
  ];
  return layers.length ? layers.map(layer => {
    const pruned = !!layer.pruned;
    const state = pruned
      ? `<span class="badge badge-danger">${escapeHtml(t('group.prompt_pruned', '被裁'))}</span>`
      : `<span class="badge">${escapeHtml(t('group.prompt_kept', '保留'))}</span>`;
    const content = pruned && !layer.content
      ? t('group.prompt_content_pruned', '内容已在捕获前裁掉')
      : (layer.content || t('group.prompt_empty_layer', '（空层）'));
    return `<details style="margin:6px 0;padding:7px 9px;background:var(--bg-secondary);border-radius:6px">
      <summary style="cursor:pointer">${escapeHtml(layer.layer || '?')} · ${Number(layer.chars || 0).toLocaleString()} ${escapeHtml(t('group.prompt_chars', '字'))} ${state}</summary>
      <pre style="font-size:11px;white-space:pre-wrap;word-break:break-all;max-height:260px;overflow:auto;margin:8px 0 0">${escapeHtml(content)}</pre>
    </details>`;
  }).join('') : observeEmpty(t('group.prompt_no_layers', '本轮没有可展示的层'));
}

function _renderGroupPromptSnapshot(groupId, snapshot) {
  const origin = snapshot.origin || {};
  const speaker = _observeGroupCharacterLabel(groupId, origin.speaker);
  return `<details style="padding:9px 0;border-bottom:1px solid var(--border)">
    <summary style="cursor:pointer"><strong>${escapeHtml(speaker)}</strong> · ${escapeHtml(origin.round_id || '—')} · ${escapeHtml(observeTime(snapshot.captured_at))}</summary>
    <div style="padding:8px 0 0 18px">${_renderPromptLayersHtml(snapshot)}</div>
  </details>`;
}

function _isPrivatePromptForGroup(snapshot, groupId) {
  const origin = snapshot?.origin || {};
  if (origin.origin !== 'private_exchange') return false;
  const roster = new Set((_observeGroupSummaries[groupId]?.roster || []).map(item => item.char_id));
  const pair = origin.pair || [];
  return pair.length === 2 && pair.every(charId => roster.has(charId));
}

function _renderPrivatePromptSnapshot(groupId, snapshot) {
  const origin = snapshot.origin || {};
  const pairLabel = (origin.pair || []).map(charId => _observeGroupCharacterLabel(groupId, charId)).join(' ↔ ');
  const speaker = _observeGroupCharacterLabel(groupId, origin.speaker);
  return `<details style="padding:9px 0;border-bottom:1px solid var(--border)">
    <summary style="cursor:pointer"><strong>${escapeHtml(pairLabel)}</strong> · ${escapeHtml(t('group.private_prompt_speaker', '发言：{name}', {name: speaker}))} · ${escapeHtml(observeTime(snapshot.captured_at))}</summary>
    <div style="padding:8px 0 0 18px">${_renderPromptLayersHtml(snapshot)}</div>
  </details>`;
}

async function initObserveGroupArbiter(){
  const sel=document.getElementById('obs-arbiter-group');
  try{const groups=await api('GET','/group/list');_observeGroupSummaries=Object.fromEntries((groups||[]).map(g=>[g.group_id,g]));sel.innerHTML=(groups||[]).map(g=>`<option value="${escapeHtml(g.group_id)}">${escapeHtml(g.title||g.group_id)}</option>`).join('');await loadObserveGroupArbiter();}catch(e){document.getElementById('obs-arbiter-content').innerHTML=observeEmpty(t('group.load_failed','加载失败：{error}',{error:e.message}));}
}

async function loadObserveGroupArbiter(){
  const el=document.getElementById('obs-arbiter-content'), id=document.getElementById('obs-arbiter-group')?.value||''; if(!el)return;
  if(!id){el.innerHTML=observeEmpty(t('group.no_groups','暂无群聊，创建群聊后可查看仲裁轨迹'));return;} el.innerHTML=observeEmpty(t('common.loading','加载中…'));
  try{
    const flagRequest = _featureFlags.private_exchange
      ? Promise.resolve(_featureFlags)
      : api('GET','/settings/feature-flags').then(data => (_featureFlags = data.flags || {}));
    const [trace,relations,scheduler,flags]=await Promise.all([
      api('GET',`/group/${encodeURIComponent(id)}/arbiter-trace`),
      api('GET',`/group/${encodeURIComponent(id)}/relations`),
      api('GET','/scheduler/config'),
      flagRequest,
    ]), rows=Array.isArray(trace)?trace:(trace.entries||[]), rels=relations.relations||[];
    const privateLogs = await Promise.all(rels.map(async relation => {
      const query = `char_a=${encodeURIComponent(relation.char_a)}&char_b=${encodeURIComponent(relation.char_b)}&limit=50`;
      try { return await api('GET', `/relations/private-log?${query}`); }
      catch (error) { return {entries: [], error: error.message}; }
    }));
    const snapshots = await _loadRecentPromptSnapshots(String(scheduler.owner_id || '').trim(), 10);
    const groupSnapshots = snapshots.filter(snapshot => _isStagePromptForGroup(snapshot, id));
    const privatePromptSnapshots = snapshots.filter(snapshot => _isPrivatePromptForGroup(snapshot, id));
    const dreamSnapshots = await _loadRecentDreamPromptSnapshots(String(scheduler.owner_id || '').trim(), 5);
    const groupDreamSnapshots = dreamSnapshots.filter(snapshot => _isStagePromptForGroup(snapshot, id));
    const privateEnabled = !!flags.private_exchange?.enabled;
    const privateBadge = `<span class="badge ${privateEnabled?'badge-success':'badge-danger'}" style="margin-left:7px">private_exchange: ${escapeHtml(privateEnabled?t('common.enabled','已启用'):t('common.disabled','未启用'))}</span>`;
    const privateHtml = rels.length
      ? rels.map((relation,index) => privateLogs[index]?.error
          ? observeEmpty(t('group.private_pair_failed','{pair} 加载失败：{error}',{pair:`${relation.char_a} ↔ ${relation.char_b}`,error:privateLogs[index].error}))
          : _renderPrivateExchangePair(id, relation, privateLogs[index])).join('')
      : observeEmpty(t('group.private_no_relations','当前群还没有可观测的角色关系 pair'));
    const promptHtml = groupSnapshots.length
      ? groupSnapshots.map(snapshot => _renderGroupPromptSnapshot(id, snapshot)).join('')
      : observeEmpty(t('group.prompt_none','最近 10 轮没有当前群的 Prompt 快照；群聊跑一轮后刷新'));
    const privatePromptHtml = privatePromptSnapshots.length
      ? privatePromptSnapshots.map(snapshot => _renderPrivatePromptSnapshot(id, snapshot)).join('')
      : observeEmpty(t('group.private_prompt_none','最近 10 轮没有当前群角色间的私下往来 Prompt 快照；触发一次私下往来后刷新'));
    const dreamPromptHtml = groupDreamSnapshots.length
      ? groupDreamSnapshots.map(snapshot => _renderGroupDreamPromptSnapshot(id, snapshot)).join('')
      : observeEmpty(t('group.dream_prompt_none','最近 5 轮没有当前群的梦境 Prompt 快照；群聊梦境跑一轮后刷新'));
    const echoCutBadge = `<span class="badge">${escapeHtml(t('group.echo_cut','回声截断'))}</span>`;
    const silentRoundBadge = `<span class="badge">${escapeHtml(t('group.silent_round','静默轮次'))}</span>`;
    el.className='';el.innerHTML=`<h4>${escapeHtml(t('group.trace','仲裁轨迹'))}</h4>${rows.length?rows.slice().reverse().map(r=>`<div style="padding:10px 0;border-bottom:1px solid var(--border)"><div>${escapeHtml(observeTime(r.ts))} · ${escapeHtml(t('group.phase','阶段'))} ${escapeHtml(r.phase||'—')} ${r.echo_cut?echoCutBadge:''} ${r.silent_round?silentRoundBadge:''}</div>${(r.candidates||[]).map(c=>`<div style="margin-top:6px">${escapeHtml(_observeGroupCharacterLabel(id,c.char_id))} · ${Number(c.total||0).toFixed(2)}${observeBar(Number(c.total||0),1.5)}</div>`).join('')}</div>`).join(''):observeEmpty(t('group.no_trace','暂无仲裁轨迹'))}<h4 style="margin-top:18px">${escapeHtml(t('group.impressions','角色双向印象'))}</h4>${rels.length?rels.map(r=>{const labelA=_observeGroupCharacterLabel(id,r.char_a),labelB=_observeGroupCharacterLabel(id,r.char_b);return `<div style="padding:9px 0;border-bottom:1px solid var(--border)"><strong>${escapeHtml(labelA)} ↔ ${escapeHtml(labelB)}</strong><div>${escapeHtml(labelA)} → ${escapeHtml(labelB)}：${escapeHtml(r.a_of_b?.summary||'—')} (${Number(r.a_of_b?.valence||0).toFixed(2)})</div><div>${escapeHtml(labelB)} → ${escapeHtml(labelA)}：${escapeHtml(r.b_of_a?.summary||'—')} (${Number(r.b_of_a?.valence||0).toFixed(2)})</div></div>`;}).join(''):observeEmpty(t('group.no_impressions','暂无角色双向印象'))}<h4 style="margin-top:18px">${escapeHtml(t('group.private','角色私下往来'))} ${privateBadge}</h4>${privateHtml}<h4 style="margin-top:18px">${escapeHtml(t('group.private_prompt','私下往来 Prompt 检视'))} <span style="font-size:12px;font-weight:400;color:var(--muted)">${escapeHtml(t('group.private_prompt_subtitle','最近 10 轮，仅显示当前群成员间的 pair'))}</span></h4>${privatePromptHtml}<h4 style="margin-top:18px">${escapeHtml(t('group.prompt','群聊 Prompt 检视'))} <span style="font-size:12px;font-weight:400;color:var(--muted)">${escapeHtml(t('group.prompt_subtitle','最近 10 轮，仅显示当前群'))}</span></h4>${promptHtml}<h4 style="margin-top:18px">${escapeHtml(t('group.dream_prompt','群聊梦境 Prompt 检视'))} <span style="font-size:12px;font-weight:400;color:var(--muted)">${escapeHtml(t('group.dream_prompt_subtitle','最近 5 轮，仅显示当前群 · 与现实 Prompt 相互独立'))}</span></h4>${dreamPromptHtml}`;
  }catch(e){el.innerHTML=observeEmpty(t('group.load_failed','加载失败：{error}',{error:e.message}));}
}

async function loadObserveMemorySummary(){
  const el=document.getElementById('obs-memory-summary-content'),uid=document.getElementById('obs-memory-summary-uid').value.trim(),charId=document.getElementById('obs-memory-summary-char')?.value||'';if(!uid){el.innerHTML=observeEmpty('请输入用户 UID');return;}el.innerHTML=observeEmpty('加载中…');
  try{const cq=charId?`?char_id=${encodeURIComponent(charId)}`:'',[digest,recall]=await Promise.all([api('GET',`/memory/digest/${encodeURIComponent(uid)}${cq}`),api('GET',`/debug/recall?uid=${encodeURIComponent(uid)}${charId?`&char_id=${encodeURIComponent(charId)}`:''}`)]),rows=recall.records||[];
    el.className='';el.innerHTML=`<h4>${escapeHtml(t('dynamic.memory_summary.archive','时期摘要归档'))}</h4>${digest.content?`<pre style="white-space:pre-wrap;max-height:360px;overflow:auto">${escapeHtml(digest.content)}</pre>`:observeEmpty()}<h4 style="margin-top:18px">${escapeHtml(t('dynamic.memory_summary.recall_trace','召回轨迹'))}</h4>${rows.length?rows.slice().reverse().map(r=>{const counts=Object.entries(r).filter(([k,v])=>k.endsWith('_hits')&&Array.isArray(v)).map(([k,v])=>`<span class="badge" style="margin-right:5px">${escapeHtml(k)}: ${v.length}</span>`).join('');return `<div style="padding:9px 0;border-bottom:1px solid var(--border)"><div class="i18n-raw">${escapeHtml(observeTime(r.ts))} · ${escapeHtml(r.query||r.message_excerpt||r.mood||'—')}</div><div style="margin-top:5px">${counts}</div>${r.time_range||r.parsed_time_range?`<div style="color:var(--muted);margin-top:4px">${escapeHtml(t('dynamic.memory_summary.time_range','时间过滤范围：{range}',{range:JSON.stringify(r.time_range||r.parsed_time_range)}))}</div>`:''}</div>`;}).join(''):observeEmpty()}`;
  }catch(e){el.innerHTML=observeEmpty('加载失败：'+e.message);}
}

// ══════════════════════════════════════════════════════════
//  观测页：Prompt 层检视器
// ══════════════════════════════════════════════════════════
async function loadObservePromptUidList() {
  const listEl = document.getElementById('obs-prompt-uid-list');
  try {
    const d = await api('GET', '/observe/prompt-layers');
    const uids = d.uids || [];
    if (!uids.length) {
      listEl.textContent = '暂无快照（发送一条消息后刷新）';
    } else {
      listEl.innerHTML = '有快照的 uid：' + uids.map(u =>
        `<a href="#" style="margin-left:8px;color:var(--accent)" onclick="document.getElementById('obs-prompt-uid').value='${escapeHtml(u)}';loadObservePrompt();return false">${escapeHtml(u)}</a>`
      ).join('');
    }
  } catch(e) {
    listEl.textContent = '加载失败：' + e.message;
  }
}

async function loadObservePrompt() {
  const uid = (document.getElementById('obs-prompt-uid').value || '').trim();
  const n   = parseInt(document.getElementById('obs-prompt-n').value || '0', 10);
  const summaryCard  = document.getElementById('obs-prompt-summary-card');
  const summaryEl    = document.getElementById('obs-prompt-summary');
  const layersListEl = document.getElementById('obs-prompt-layers-list');
  if (!uid) { layersListEl.innerHTML = '<div style="color:var(--muted)">请输入 uid 后点击「查看」</div>'; return; }
  layersListEl.innerHTML = '<div style="color:var(--muted)">加载中…</div>';
  summaryCard.style.display = 'none';
  try {
    const d = await api('GET', `/observe/prompt-layers/${encodeURIComponent(uid)}?n=${n}`);
    if (!d.snapshot) {
      layersListEl.innerHTML = `<div style="color:var(--muted)">uid ${escapeHtml(uid)} 暂无快照，发一条消息后刷新。</div>`;
      return;
    }
    const snap = d.snapshot;
    _obsPromptCurrent = { snap, uid };
    const SOFT = snap.soft_warn_threshold || 15000;
    const HARD = snap.hard_trigger_threshold || 20000;
    const est  = snap.token_estimate || 0;

    // ── 总览卡 ──
    summaryCard.style.display = '';
    const origin = snap.origin || {};
    const isProactive = origin.origin === 'proactive';
    const isDesktop   = origin.origin === 'desktop';
    const isStage     = origin.origin === 'stage';
    const isPrivate   = origin.origin === 'private_exchange';
    const originBadge = isProactive
      ? `<span style="font-size:11px;background:#1a3a1a;color:#86efac;padding:2px 8px;border-radius:10px;margin-left:8px;font-weight:600">主动 · ${escapeHtml(origin.trigger_name||'')}</span>`
      : isDesktop
        ? `<span style="font-size:11px;background:#1e3a5f;color:#93c5fd;padding:2px 8px;border-radius:10px;margin-left:8px">桌宠</span>`
        : isStage
          ? `<span style="font-size:11px;background:#3b2257;color:#c4b5fd;padding:2px 8px;border-radius:10px;margin-left:8px">群聊 · ${escapeHtml(origin.speaker||'')}</span>`
          : isPrivate
            ? `<span style="font-size:11px;background:#422006;color:#fcd34d;padding:2px 8px;border-radius:10px;margin-left:8px">私下往来 · ${escapeHtml(origin.speaker||'')}</span>`
            : `<span style="font-size:11px;background:#2d2d2d;color:#9ca3af;padding:2px 8px;border-radius:10px;margin-left:8px">用户</span>`;
    document.getElementById('obs-prompt-ts').innerHTML =
      `${snap.captured_at ? snap.captured_at.slice(0,19).replace('T',' ') : ''}${originBadge} · 第 ${d.n+1}/${d.total_snapshots} 轮`;
    const statusColor = est > HARD ? '#ef4444' : est > SOFT ? '#f59e0b' : 'var(--accent)';
    let summaryHtml =
      `<div>字符估算：<strong style="color:${statusColor}">${est.toLocaleString()}</strong>` +
      ` &nbsp;软警戒 ${SOFT.toLocaleString()} &nbsp;触发裁剪 ${HARD.toLocaleString()}</div>` +
      `<div style="margin-top:4px">激活 tags：${(snap.active_tags||[]).map(t=>`<code style="font-size:11px;background:var(--bg-secondary);padding:1px 4px;border-radius:3px">${escapeHtml(t)}</code>`).join(' ') || '(无)'}</div>` +
      (snap.removed_layers && snap.removed_layers.length
        ? `<div style="margin-top:4px;color:#ef4444">被裁层：${snap.removed_layers.map(l=>`<code style="font-size:11px">${escapeHtml(l)}</code>`).join(' ')}</div>`
        : '<div style="margin-top:4px;color:var(--muted)">无裁剪</div>') +
      (snap.ablated_layers && snap.ablated_layers.length
        ? `<div style="margin-top:4px;color:#a78bfa">已消融层：${snap.ablated_layers.map(l=>`<code style="font-size:11px">${escapeHtml(l)}</code>`).join(' ')}</div>`
        : '');
    if (isProactive) {
      const sq = (origin.search_query||'').trim();
      const sp = (origin.seed_prompt||'').trim();
      summaryHtml += `<div style="margin-top:10px;padding:10px;background:#0f2410;border-radius:6px;border:1px solid #166534">
        <div style="font-size:12px;font-weight:600;color:#86efac;margin-bottom:6px">主动触发详情</div>
        <div style="font-size:12px;margin-bottom:4px"><span style="color:var(--muted)">触发器：</span><code style="background:var(--bg-secondary);padding:1px 5px;border-radius:3px">${escapeHtml(origin.trigger_name||'')}</code></div>
        <div style="font-size:12px;margin-bottom:4px"><span style="color:var(--muted)">召回锚点 (search_query)：</span><span style="color:#fde68a">${sq ? escapeHtml(sq.slice(0,200)) : '<em style="color:var(--muted)">（与 seed_prompt 相同）</em>'}</span></div>
        <div style="font-size:12px"><span style="color:var(--muted)">种子 prompt：</span><pre style="margin:4px 0 0;font-size:11px;white-space:pre-wrap;word-break:break-all;background:var(--bg-secondary);padding:6px;border-radius:4px;max-height:120px;overflow:auto">${escapeHtml(sp.slice(0,600))}${sp.length>600?'\n…':''}
</pre></div>
      </div>`;
    }
    summaryEl.innerHTML = summaryHtml;

    // token 进度条
    const pct = Math.min(100, est / HARD * 100);
    document.getElementById('obs-prompt-bar').style.width = pct + '%';
    document.getElementById('obs-prompt-bar-soft').style.left = Math.min(100, SOFT/HARD*100) + '%';
    document.getElementById('obs-prompt-bar-hard').style.left = '100%';

    // ── 层级列表 ──
    // One logical layer can consist of several message records (notably
    // mes_example/history). Show it once, preserving order and separating the
    // original records with newlines in the expanded view.
    const groupedLayers = new Map();
    (snap.layers || []).forEach(lyr => {
      const key = lyr.layer || '?';
      const group = groupedLayers.get(key);
      if (group) {
        group.position_end = lyr.position;
        group.chars += lyr.chars || 0;
        group.est_tokens += lyr.est_tokens || 0;
        group.pruned = group.pruned && !!lyr.pruned;
        group.content = [group.content, lyr.content].filter(Boolean).join('\n\n');
      } else {
        groupedLayers.set(key, {...lyr, position_end: lyr.position});
      }
    });
    const layers = [...groupedLayers.values()];
    const totalChars = layers.reduce((s,l) => s + (l.chars||0), 0) || 1;

    function _provBadge(prov) {
      if (!prov) return '<span style="font-size:10px;background:#374151;color:#9ca3af;padding:1px 5px;border-radius:8px;margin-left:5px">常驻</span>';
      const mode = prov.mode || 'always';
      if (mode === 'always')  return '<span style="font-size:10px;background:#374151;color:#9ca3af;padding:1px 5px;border-radius:8px;margin-left:5px">常驻</span>';
      if (mode === 'tagged')  return '<span style="font-size:10px;background:#1d3a6e;color:#93c5fd;padding:1px 5px;border-radius:8px;margin-left:5px">标签召回</span>';
      if (mode === 'scored')  return '<span style="font-size:10px;background:#3b2257;color:#c4b5fd;padding:1px 5px;border-radius:8px;margin-left:5px">打分召回</span>';
      return '';
    }
    function _provDetail(prov) {
      if (!prov || prov.mode === 'always') return '';
      if (prov.mode === 'tagged') {
        const matched = (prov.matched_tags || []).join(', ') || '(无命中)';
        const checked = (prov.triggers_checked || []).join(', ') || '';
        return `<div style="margin-top:6px;font-size:11px;color:#93c5fd">
          <span style="color:var(--muted)">命中 tags：</span>${escapeHtml(matched)}<br>
          <span style="color:var(--muted)">检查集：</span><span style="color:var(--muted)">${escapeHtml(checked)}</span>
        </div>`;
      }
      if (prov.mode === 'scored') {
        const q = prov.rag_query || '';
        let extra = '';
        if (prov.source) {
          extra += `<br><span style="color:var(--muted)">来源：</span>${escapeHtml(prov.source)}`;
        }
        const hits = prov.hits || [];
        if (hits.length) {
          const hitLines = hits.map(h => {
            const url = Array.isArray(h) ? h[0] : (h.url || '');
            const dist = Array.isArray(h) ? h[1] : (h.dist ?? h.distance);
            return `&nbsp;&nbsp;• ${escapeHtml(String(url))} (dist=${dist})`;
          }).join('<br>');
          extra += `<br><span style="color:var(--muted)">命中：</span><br>${hitLines}`;
        }
        return `<div style="margin-top:6px;font-size:11px;color:#c4b5fd">
          <span style="color:var(--muted)">RAG 查询：</span>${escapeHtml(q.slice(0,120))}${q.length>120?'…':''}${extra}
        </div>`;
      }
      return '';
    }

    layersListEl.innerHTML = layers.map(lyr => {
      const prunedBadge = lyr.pruned
        ? '<span style="font-size:11px;background:#ef4444;color:#fff;padding:1px 6px;border-radius:10px;margin-left:6px">被裁</span>'
        : '';
      const pct2 = ((lyr.chars||0)/totalChars*100).toFixed(1);
      const barColor = lyr.pruned ? '#ef4444' : 'var(--accent)';
      const contentId = `obs-prompt-content-${lyr.position}`;
      const prov = lyr.provenance;
      return `
        <div class="card" style="margin-bottom:8px;opacity:${lyr.pruned?'0.5':'1'}">
          <div style="display:flex;align-items:center;gap:8px;padding:10px 14px;cursor:pointer" onclick="togglePromptLayer('${contentId}')">
            <span style="font-size:11px;color:var(--muted);width:34px;text-align:right">${lyr.position_end !== lyr.position ? `${lyr.position}–${lyr.position_end}` : lyr.position}</span>
            <span style="font-weight:600;font-size:13px;flex:1">${escapeHtml(lyr.layer||'?')}${prunedBadge}${_provBadge(prov)}</span>
            <span style="font-size:12px;color:var(--muted)">${(lyr.chars||0).toLocaleString()} 字 / ~${Math.round(lyr.est_tokens||0)} tok</span>
            <span style="font-size:12px;color:var(--muted);width:42px;text-align:right">${pct2}%</span>
            <span style="font-size:11px">▶</span>
          </div>
          <div style="padding:0 14px 2px">
            <div style="height:4px;background:var(--bg-secondary);border-radius:2px;overflow:hidden">
              <div style="height:100%;width:${pct2}%;background:${barColor}"></div>
            </div>
          </div>
          <div id="${contentId}" style="display:none;padding:0 14px 12px;margin-top:8px">
            ${_provDetail(prov)}
            <pre style="font-size:11px;white-space:pre-wrap;word-break:break-all;background:var(--bg-secondary);padding:10px;border-radius:4px;max-height:300px;overflow:auto;color:var(--text);margin-top:6px">${escapeHtml((lyr.content||'').slice(0,3000))}${(lyr.content||'').length>3000?'\n… (截断)':''}</pre>
          </div>
        </div>`;
    }).join('');

    // ── LLM 实际输出 ──
    if (snap.llm_output != null) {
      layersListEl.innerHTML += `
        <div class="card" style="margin-top:16px;border:1px solid var(--accent2)">
          <div style="padding:10px 14px;font-weight:600;font-size:13px;border-bottom:1px solid var(--border)">
            LLM 实际输出 <span style="font-size:11px;color:var(--muted);font-weight:400">（本轮生成原文，含 guard 清洗前）</span>
          </div>
          <div style="padding:10px 14px">
            <pre style="font-size:12px;white-space:pre-wrap;word-break:break-all;background:var(--bg-secondary);padding:10px;border-radius:4px;max-height:400px;overflow:auto;color:var(--text)">${escapeHtml((snap.llm_output||'').slice(0,5000))}${(snap.llm_output||'').length>5000?'\n… (截断)':''}</pre>
          </div>
        </div>`;
    }

  } catch(e) {
    layersListEl.innerHTML = `<div style="color:#ef4444">加载失败：${escapeHtml(e.message)}</div>`;
    summaryCard.style.display = 'none';
  }
}

function togglePromptLayer(contentId) {
  const el = document.getElementById(contentId);
  if (!el) return;
  el.style.display = el.style.display === 'none' ? '' : 'none';
}

// ══════════════════════════════════════════════════════════
//  观测页：层级消融开关（CC 任务 23 · B7）
// ══════════════════════════════════════════════════════════
let _promptAblationKnownLayers = [];

async function loadPromptAblation() {
  const el = document.getElementById('obs-ablation-list');
  el.textContent = '加载中…';
  try {
    const d = await api('GET', '/prompt-ablation');
    _promptAblationKnownLayers = d.known_layers || [];
    const alwaysOn = new Set(d.always_on || []);
    const disabled = new Set(d.disabled_layers || []);
    const rows = _promptAblationKnownLayers.map(({layer, desc}) => {
      const isAlwaysOn = alwaysOn.has(layer);
      const isOff = disabled.has(layer);
      const warnHtml = layer === '9_history'
        ? '<span style="color:#ef4444;font-size:11px;margin-left:6px">关闭短期历史将严重改变行为</span>'
        : '';
      return `<label style="display:flex;align-items:center;gap:8px;padding:4px 0;${isAlwaysOn?'opacity:0.5':''}">
        <input type="checkbox" class="obs-ablation-toggle" data-layer="${escapeHtml(layer)}"
          ${isOff && !isAlwaysOn ? 'checked' : ''} ${isAlwaysOn ? 'disabled' : ''}>
        <code style="font-size:11px;background:var(--bg-secondary);padding:1px 5px;border-radius:3px">${escapeHtml(layer)}</code>
        <span style="font-size:12px;color:var(--muted)">${escapeHtml(desc||'')}</span>
        ${isAlwaysOn ? '<span style="font-size:11px;color:var(--muted)">（不可消融）</span>' : ''}
        ${warnHtml}
      </label>`;
    }).join('');
    const perceptionOff = !!d.perception_block_disabled;
    const perceptionRow = `<label style="display:flex;align-items:center;gap:8px;padding:8px 0;border-top:1px solid var(--border);margin-top:6px">
      <input type="checkbox" id="obs-ablation-perception" ${perceptionOff ? 'checked' : ''}>
      <code style="font-size:11px;background:var(--bg-secondary);padding:1px 5px;border-radius:3px">perception_block</code>
      <span style="font-size:12px;color:var(--muted)">感知槽位（嵌在 1_system_prompt 内，独立子开关）</span>
    </label>`;
    el.innerHTML = rows + perceptionRow;
  } catch(e) {
    el.textContent = '加载失败：' + e.message;
  }
}

async function savePromptAblation() {
  const checks = document.querySelectorAll('.obs-ablation-toggle:checked');
  const disabled_layers = Array.from(checks).map(c => c.dataset.layer);
  const perception_block_disabled = document.getElementById('obs-ablation-perception').checked;
  try {
    await api('PUT', '/prompt-ablation', { disabled_layers, perception_block_disabled });
    toast('已生效，下一轮对话起作用', 'ok');
    loadPromptAblation();
  } catch(e) {
    toast('保存失败：' + e.message, 'err');
  }
}

async function loadOutputSegmentEnforce() {
  try {
    const d = await api('GET', '/output-segment-enforce');
    document.getElementById('obs-segment-enforce-enabled').checked = !!d.enabled;
    document.getElementById('obs-segment-enforce-min-len').value = String(d.min_len || 40);
  } catch(e) {
    toast('读取生成后段落兜底失败：' + e.message, 'err');
  }
}

async function saveOutputSegmentEnforce() {
  const enabled = document.getElementById('obs-segment-enforce-enabled').checked;
  const min_len = parseInt(document.getElementById('obs-segment-enforce-min-len').value || '40', 10);
  try {
    await api('PUT', '/output-segment-enforce', { enabled, min_len });
    toast('生成后段落兜底已更新，下一轮对话起作用', 'ok');
    loadOutputSegmentEnforce();
  } catch(e) {
    toast('保存失败：' + e.message, 'err');
  }
}

// ══════════════════════════════════════════════════════════
//  观测页：召回溯源（recall_trace，CC 任务 23 · A3）
// ══════════════════════════════════════════════════════════
async function loadObserveRecallTrace() {
  const uid = (document.getElementById('obs-prompt-uid').value || '').trim();
  const date = document.getElementById('obs-recall-date').value || '';
  const n = parseInt(document.getElementById('obs-recall-n').value || '5', 10);
  const el = document.getElementById('obs-recall-list');
  if (!uid) { el.innerHTML = '<div style="color:var(--muted)">请先在上方输入 uid</div>'; return; }
  el.innerHTML = '<div style="color:var(--muted)">加载中…</div>';
  try {
    const qs = new URLSearchParams({ n: String(n) });
    if (date) qs.set('date', date);
    const d = await api('GET', `/observe/recall/${encodeURIComponent(uid)}?${qs.toString()}`);
    const records = d.records || [];
    if (!records.length) {
      el.innerHTML = `<div style="color:var(--muted)">${escapeHtml(d.date||'')} 暂无召回溯源记录</div>`;
      return;
    }
    el.innerHTML = records.slice().reverse().map((r, idx) => {
      const contentId = `obs-recall-content-${idx}`;
      const fmtHits = (hits) => (hits || []).map(h => Array.isArray(h) ? `${h[0]} (${h[1]})` : JSON.stringify(h)).join(', ') || '(无)';
      const mood = r.mood || {};
      return `<div class="card" style="margin-bottom:8px">
        <div style="display:flex;align-items:center;gap:8px;padding:8px 12px;cursor:pointer" onclick="togglePromptLayer('${contentId}')">
          <span style="font-size:12px;color:var(--muted)">${escapeHtml((r.ts||'').replace('T',' '))}</span>
          <span style="font-size:12px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(r.query||'')}</span>
          <span style="font-size:11px">▶</span>
        </div>
        <div id="${contentId}" style="display:none;padding:0 12px 12px;font-size:12px;line-height:1.8">
          <div><span style="color:var(--muted)">episodic_hits：</span>${fmtHits(r.episodic_hits)}</div>
          <div><span style="color:var(--muted)">episodic_fallback（${r.episodic_fallback_used?'已用':'未用'}）：</span>${fmtHits(r.episodic_fallback_hits)}</div>
          <div><span style="color:var(--muted)">event_log_hits：</span>${fmtHits(r.event_log_hits)}</div>
          <div><span style="color:var(--muted)">lore_hits：</span>${escapeHtml(JSON.stringify(r.lore_hits||[]))}</div>
          <div><span style="color:var(--muted)">semantic_hits（X2 向量通道）：</span>${fmtHits(r.semantic_hits)}</div>
          <div><span style="color:var(--muted)">web_recall_hits（X3）：</span>${fmtHits(r.web_recall_hits)}</div>
          <div><span style="color:var(--muted)">mood：</span>${escapeHtml(mood.current||'')} (${mood.intensity ?? ''})</div>
        </div>
      </div>`;
    }).join('');
  } catch(e) {
    el.innerHTML = `<div style="color:#ef4444">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

function exportSnapshotMd(kind) {
  function fmtTs() {
    const n = new Date(), p = v => String(v).padStart(2,'0');
    return `${n.getFullYear()}${p(n.getMonth()+1)}${p(n.getDate())}-${p(n.getHours())}${p(n.getMinutes())}`;
  }
  function triggerDownload(filename, content) {
    const blob = new Blob([content], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
  }
  function safeSlug(s) { return (s||'').replace(/[^a-zA-Z0-9_\-]/g,'_'); }
  function blockquote(text) {
    return (text||'').split('\n').map(l => '> ' + l).join('\n');
  }

  if (kind === 'prompt') {
    if (!_obsPromptCurrent) { alert('请先加载一条快照'); return; }
    const { snap, uid } = _obsPromptCurrent;
    const origin = snap.origin || {};
    const originTag = origin.origin === 'proactive'
      ? `proactive-${origin.trigger_name||'unknown'}`
      : (origin.origin || 'user');
    const filename = `prompt_${safeSlug(uid)}_${safeSlug(originTag)}_${fmtTs()}.md`;

    const SOFT = snap.soft_warn_threshold || 15000;
    const HARD = snap.hard_trigger_threshold || 20000;
    const est  = snap.token_estimate || 0;
    const layers = snap.layers || [];
    const totalChars = layers.reduce((s,l) => s + (l.chars||0), 0) || 1;

    let md = `# Prompt 快照 — ${originTag}\n`;
    md += `- uid: ${uid}\n`;
    md += `- 捕获时间: ${snap.captured_at || ''}\n`;
    md += `- 来源: ${origin.origin || ''}`;
    if (origin.origin === 'proactive') md += ` · trigger=${origin.trigger_name||''}`;
    md += '\n';
    if ((origin.seed_prompt||'').trim()) md += `- 种子 prompt: ${origin.seed_prompt}\n`;
    if ((origin.search_query||'').trim()) md += `- 召回锚 search_query: ${origin.search_query}\n`;
    md += `- token 估算: ${est}（软警戒 ${SOFT} / 触发裁剪 ${HARD} / 目标 18000）\n`;
    md += `- 激活 tags: ${(snap.active_tags||[]).join(', ') || '(无)'}\n`;
    md += `- 被裁层: ${(snap.removed_layers||[]).join(', ') || '(无)'}\n`;
    md += '\n## 层级（按 position）\n\n';

    for (const lyr of layers) {
      const pct = ((lyr.chars||0)/totalChars*100).toFixed(1);
      const prov = lyr.provenance;
      const mode = prov ? (prov.mode || 'always') : 'always';
      const status = lyr.pruned ? '被裁' : '已注入';
      md += `### [${lyr.position}] ${lyr.layer || '?'}  · ${mode}  · ${lyr.chars||0}字/${Math.round(lyr.est_tokens||0)}tok (${pct}%)\n`;
      md += `- 状态: ${status}\n`;
      if (prov && mode !== 'always') {
        let provStr = `mode=${mode}`;
        if (prov.matched_tags && prov.matched_tags.length) provStr += ` 命中tag=${prov.matched_tags.join(',')}`;
        if (prov.rag_query) provStr += ` rag_query=${prov.rag_query}`;
        md += `- provenance: ${provStr}\n`;
      }
      md += '\n';
      if (lyr.content) md += blockquote(lyr.content) + '\n';
      md += '\n';
    }

    if (snap.user_message) md += `## 用户消息\n${snap.user_message}\n\n`;
    if (snap.llm_output != null) md += `## LLM 实际输出\n${snap.llm_output}\n`;

    triggerDownload(filename, md);

  } else if (kind === 'dream') {
    if (!_obsDreamPromptCurrent) { alert('请先加载一条快照'); return; }
    const { snap: s, uid } = _obsDreamPromptCurrent;
    const filename = `dream_${safeSlug(uid)}_${safeSlug(s.world_id||'unknown')}_${fmtTs()}.md`;

    const layers = s.layers || [];
    const totalTok = layers.reduce((a,l) => a + (l.tokens||0), 0) || 1;

    let md = `# 梦境 Prompt 快照 — ${s.dream_id || '?'}\n`;
    md += `- uid: ${uid}\n`;
    md += `- 捕获时间: ${s.captured_at || ''}\n`;
    md += `- dream_id: ${s.dream_id || ''}\n`;
    md += `- 世界: ${s.world_id || ''}\n`;
    md += `- 模式: ${s.lucid_mode || ''} / dream_mode: ${s.dream_mode || ''}\n`;
    md += `- scene_tags: ${(s.scene_tags || []).join(', ') || '(无)'}\n`;
    md += `- 历史轮数: ${s.history_turns || 0}\n`;
    md += `- token 合计: ${s.total_tokens || 0}\n`;
    md += '\n## 层级\n\n';

    for (const lyr of layers) {
      const pct = lyr.injected ? ((lyr.tokens||0)/totalTok*100).toFixed(1) : '0.0';
      const flags = (lyr.flags||[]).join(' ');
      const status = lyr.injected ? '已注入' : '未注入';
      md += `### ${lyr.label || '?'}  · ${lyr.chars||0}字/${lyr.tokens||0}tok (${pct}%)\n`;
      md += `- 状态: ${status}${flags ? ' ' + flags : ''}\n`;
      if (lyr.note) md += `- note: ${lyr.note}\n`;
      md += '\n';
      if (lyr.content && lyr.injected) md += blockquote(lyr.content) + '\n';
      md += '\n';
    }

    if (s.user_message) md += `## 用户消息\n${s.user_message}\n\n`;
    if (s.llm_output != null) md += `## 梦境 LLM 回复\n${s.llm_output}\n`;

    triggerDownload(filename, md);

  } else if (kind === 'probe') {
    if (!_obsProbeCurrent) { alert('请先加载一条快照'); return; }
    const { snap: s, uid } = _obsProbeCurrent;
    const pathKind = s.is_fast_path ? 'fast' : 'llm';
    const filename = `probe_${safeSlug(uid)}_${pathKind}_${fmtTs()}.md`;

    let md = `# 探针快照 — ${uid}\n`;
    md += `- 捕获时间: ${s.captured_at || ''}\n`;
    md += `- 路径: ${s.is_fast_path ? 'Fast-Path（跳过探针 LLM）' : 'LLM 探针'}\n`;
    md += `- 用户消息: ${s.user_message || ''}\n\n`;

    if (s.is_fast_path) {
      md += `## Fast-Path 决策\n`;
      md += `- 命中工具: ${s.matched_tool || ''}\n`;
      md += `- 命中关键词: ${s.matched_keyword || ''}\n`;
      md += `- 风险: ${s.fast_path_risk ?? ''}\n`;
    } else {
      md += `## LLM 探针决策\n`;
      md += `- 暴露路径: ${s.exposure_path || 'path_a'} (${s.exposure_source || 'unknown'})\n`;
      md += `- 暴露类别: ${(s.exposure_categories || []).join(', ')}\n`;
      md += `- 可用工具: ${(s.tools_available||[]).join(', ')}\n`;
      const tcs = (s.tool_calls && s.tool_calls.length)
        ? s.tool_calls.map(tc => `${tc.name||'?'}(${JSON.stringify(tc.arguments||{})})`).join(', ')
        : '(无工具调用)';
      md += `- 解析 tool_calls: ${tcs}\n\n`;

      if (s.probe_system) md += `## 探针 System Prompt\n${s.probe_system}\n\n`;

      const ctx = s.probe_context || [];
      if (ctx.length) {
        md += `## 注入上下文（${ctx.length} 条）\n\n`;
        for (const m of ctx) md += `**[${m.role||'?'}]** ${m.content||''}\n\n`;
      }

      if (s.probe_response_raw) md += `## 探针原始返回\n${s.probe_response_raw}\n\n`;
    }

    const results = s.tool_results || [];
    if (results.length) {
      md += `## 工具执行结果\n\n`;
      for (const r of results) {
        md += `### ${r.name||'?'}${r.has_side_effect ? ' (副作用)' : ''}\n`;
        md += `- 参数: ${JSON.stringify(r.arguments||{})}\n`;
        md += `- 结果: ${r.result||'(无返回)'}\n\n`;
      }
    }

    triggerDownload(filename, md);
  }
}

// ══════════════════════════════════════════════════════════
//  工具观测（observe-tools）
// ══════════════════════════════════════════════════════════

async function loadObserveToolUidList() {
  const listEl = document.getElementById('obs-tools-uid-list');
  try {
    const category = document.getElementById('obs-tools-category').value;
    const d = await api('GET', category === 'probe' ? '/observe/probe' : '/observability/tool-traces');
    const uids = d.uids || [];
    listEl.innerHTML = uids.length
      ? (category === 'probe' ? '有探针快照的 uid：' : t('observe.tools.uid_list', '有执行痕迹的 uid：')) + uids.map(uid =>
        `<a href="#" style="margin-left:8px;color:var(--accent)" onclick="document.getElementById('obs-tools-uid').value='${escapeHtml(uid)}';loadObserveTools();return false">${escapeHtml(uid)}</a>`
      ).join('')
      : t('observe.tools.empty_uid', '暂无工具执行痕迹。');
  } catch (e) {
    listEl.textContent = t('observe.tools.load_failed', '加载失败：{error}', {error: e.message});
  }
}

async function _loadObserveToolMcpLedger(entries) {
  const callers = [...new Set((entries || [])
    .filter(entry => entry.category === 'mcp' && String(entry.tool || '').startsWith('mcp__'))
    .map(entry => entry.tool))];
  if (!callers.length) return '';
  const rows = await Promise.all(callers.map(async caller => {
    try {
      const data = await getMcpRecentCalls(caller, 3);
      return (data.entries || []).map(entry => ({ caller, entry }));
    } catch (_) {
      return [];
    }
  }));
  const flat = rows.flat();
  if (!flat.length) return `<div class="empty">${escapeHtml(t('observe.tools.mcp_ledger_empty', 'MCP 调用总账暂无对应记录。'))}</div>`;
  return `<div class="card" style="margin-top:12px"><div class="card-header"><h3>${escapeHtml(t('observe.tools.mcp_ledger', 'MCP 调用总账'))}</h3><span style="font-size:12px;color:var(--muted)">${escapeHtml(t('observe.tools.mcp_ledger_hint', '与 MCP 管理页复用同一只读总账'))}</span></div>${flat.map(({caller, entry}) => `<div style="padding:8px 14px;border-top:1px solid var(--border);font-size:12px"><code>${escapeHtml(caller)}</code> · ${escapeHtml(entry.ok ? t('observe.tools.ok', '成功') : t('observe.tools.failed', '失败'))} · ${escapeHtml(String(entry.duration_ms ?? '?'))}ms</div>`).join('')}</div>`;
}

async function loadObserveTools() {
  const uid = (document.getElementById('obs-tools-uid').value || '').trim();
  const category = document.getElementById('obs-tools-category').value;
  const limit = document.getElementById('obs-tools-limit').value || '30';
  const el = document.getElementById('obs-tools-content');
  if (!uid) { el.innerHTML = `<div class="empty">${escapeHtml(t('observe.tools.need_uid', '请输入 uid 后查看。'))}</div>`; return; }
  el.innerHTML = `<div class="loading">${escapeHtml(t('common.loading', '加载中…'))}</div>`;
  if (category === 'probe') {
    await loadObserveProbe(el, uid);
    return;
  }
  try {
    const query = new URLSearchParams({ limit });
    if (category) query.set('category', category);
    const d = await api('GET', `/observability/tool-traces/${encodeURIComponent(uid)}?${query}`);
    const summary = Object.entries(d.categories || {}).map(([name, count]) => `<span class="badge" style="margin-right:6px">${escapeHtml(name)} ${escapeHtml(String(count))}</span>`).join('') || '—';
    const entries = d.entries || [];
    const rows = entries.length ? entries.map(entry => {
      const statusClass = entry.status === 'ok' ? 'badge-success' : entry.status === 'failed' ? 'badge-danger' : 'badge-warn';
      const ts = entry.ts ? new Date(entry.ts * 1000).toLocaleString() : '—';
      return `<div style="padding:10px 14px;border-top:1px solid var(--border);font-size:12px"><div><code>${escapeHtml(entry.tool || '?')}</code> <span class="badge">${escapeHtml(entry.category || 'unknown')}</span> <span class="badge">${escapeHtml(entry.provider || 'builtin')}</span> <span class="badge">${escapeHtml(entry.execution_path || 'other')}</span> <span class="badge ${statusClass}">${escapeHtml(entry.status || '?')}</span> <span style="color:var(--muted)">${escapeHtml(entry.origin || '?')} · ${escapeHtml(ts)}</span></div>${entry.args_digest ? `<div style="margin-top:4px;color:var(--muted)">${escapeHtml(t('observe.tools.args', '参数：'))}${escapeHtml(entry.args_digest)}</div>` : ''}${entry.result_digest ? `<div style="margin-top:3px">${escapeHtml(t('observe.tools.result', '结果摘要：'))}${escapeHtml(entry.result_digest)}</div>` : ''}</div>`;
    }).join('') : `<div class="empty">${escapeHtml(t('observe.tools.empty', '该筛选条件下暂无执行痕迹。'))}</div>`;
    el.innerHTML = `<div class="card"><div class="card-header"><h3>${escapeHtml(t('observe.tools.recent', '最近工具执行'))}</h3><span style="font-size:12px;color:var(--muted)">${escapeHtml(t('observe.tools.summary', '全部类目：'))}${summary}</span></div>${rows}</div>`;
    if (!category || category === 'mcp') el.innerHTML += await _loadObserveToolMcpLedger(entries);
  } catch (e) {
    el.innerHTML = `<div class="empty">${escapeHtml(t('observe.tools.load_failed', '加载失败：{error}', {error: e.message}))}</div>`;
  }
}

// ══════════════════════════════════════════════════════════
//  探针观测（observe-probe）
// ══════════════════════════════════════════════════════════

async function loadObserveProbe(el = document.getElementById('obs-probe-content'), uidOverride = '') {
  const uid = uidOverride || (document.getElementById('obs-probe-uid')?.value || '').trim();
  const n   = uidOverride ? 0 : parseInt(document.getElementById('obs-probe-n')?.value || '0', 10);
  if (!uid) { el.innerHTML = '<div style="color:var(--muted)">请输入 uid 后点击「查看」</div>'; return; }
  el.innerHTML = '<div style="color:var(--muted)">加载中…</div>';
  try {
    const d = await api('GET', `/observe/probe/${encodeURIComponent(uid)}?n=${n}`);
    if (!d.snapshot) {
      el.innerHTML = `<div style="color:var(--muted)">uid ${escapeHtml(uid)} 暂无快照，聊天后刷新。</div>`;
      return;
    }
    const s = d.snapshot;
    _obsProbeCurrent = { snap: s, uid };
    const ts = s.captured_at ? s.captured_at.slice(0,19).replace('T',' ') : '';
    const _chColor = s.channel === 'qq' ? '#2563eb' : s.channel === 'desktop' ? '#059669' : '#6b7280';
    const _chBadge = s.channel ? `<span style="font-size:10px;background:${_chColor};color:#fff;padding:1px 6px;border-radius:8px;margin-left:8px;vertical-align:middle">${escapeHtml(s.channel)}</span>` : '';

    let html = `<div style="font-size:12px;color:var(--muted);margin-bottom:12px">${ts} · 第 ${d.n+1}/${d.total_snapshots} 轮${_chBadge}</div>`;

    // ── 决策卡 ──
    if (s.is_fast_path) {
      html += `<div class="card" style="margin-bottom:12px;border-left:3px solid var(--warn)">
        <div style="padding:10px 14px">
          <div style="font-weight:600;margin-bottom:6px">Fast-Path（跳过探针 LLM）</div>
          <div style="font-size:13px">
            命中工具：<code style="background:var(--bg-secondary);padding:1px 5px;border-radius:3px">${escapeHtml(s.matched_tool||'')}</code>
            &nbsp; 关键词：<code style="background:var(--bg-secondary);padding:1px 5px;border-radius:3px">${escapeHtml(s.matched_keyword||'')}</code>
            &nbsp; 风险：<code style="background:var(--bg-secondary);padding:1px 5px;border-radius:3px">${escapeHtml(String(s.fast_path_risk??''))}</code>
          </div>
          <div style="font-size:12px;color:var(--muted);margin-top:6px">用户消息：${escapeHtml((s.user_message||'').slice(0,200))}</div>
        </div>
      </div>`;
    } else {
      // LLM probe 路径
      const toolCallsStr = (s.tool_calls && s.tool_calls.length)
        ? s.tool_calls.map(tc => `${escapeHtml(tc.name||'?')}(${escapeHtml(JSON.stringify(tc.arguments||{}))})`).join(', ')
        : '(无工具调用)';
      html += `<div class="card" style="margin-bottom:12px">
        <div style="padding:10px 14px;font-weight:600;border-bottom:1px solid var(--border)">LLM 探针决策</div>
        <div style="padding:10px 14px;font-size:13px">
          <div><span style="color:var(--muted)">解析出的 tool_calls：</span> <code style="background:var(--bg-secondary);padding:2px 6px;border-radius:3px">${toolCallsStr}</code></div>
          <div style="margin-top:6px"><span style="color:var(--muted)">暴露路径：</span><code style="background:var(--bg-secondary);padding:2px 6px;border-radius:3px">${escapeHtml(s.exposure_path || 'path_a')}</code> ${escapeHtml(s.exposure_source || 'unknown')}</div>
          <div style="margin-top:6px"><span style="color:var(--muted)">暴露类别：</span>${(s.exposure_categories||[]).map(c=>`<code style="font-size:11px;background:var(--bg-secondary);padding:1px 4px;margin-right:4px">${escapeHtml(c)}</code>`).join('')}</div>
          <div style="margin-top:6px"><span style="color:var(--muted)">可用工具：</span> ${(s.tools_available||[]).map(t=>`<code style="font-size:11px;background:var(--bg-secondary);padding:1px 4px;border-radius:3px;margin-right:4px">${escapeHtml(t)}</code>`).join('')}</div>
          <div style="margin-top:6px"><span style="color:var(--muted)">用户消息：</span>${escapeHtml((s.user_message||'').slice(0,300))}</div>
        </div>
      </div>`;

      // 探针 prompt 展开
      html += `<div class="card" style="margin-bottom:12px">
        <div style="padding:8px 14px;display:flex;align-items:center;justify-content:space-between;cursor:pointer;border-bottom:1px solid var(--border)" onclick="togglePromptLayer('obs-probe-sys')">
          <span style="font-size:13px;font-weight:600">探针 System Prompt</span><span>▶</span>
        </div>
        <div id="obs-probe-sys" style="display:none;padding:10px 14px">
          <pre style="font-size:11px;white-space:pre-wrap;word-break:break-all;background:var(--bg-secondary);padding:8px;border-radius:4px;max-height:250px;overflow:auto;color:var(--text)">${escapeHtml((s.probe_system||'').slice(0,3000))}</pre>
        </div>
      </div>`;

      // 上下文
      const ctx = s.probe_context || [];
      if (ctx.length) {
        html += `<div class="card" style="margin-bottom:12px">
          <div style="padding:8px 14px;display:flex;align-items:center;justify-content:space-between;cursor:pointer;border-bottom:1px solid var(--border)" onclick="togglePromptLayer('obs-probe-ctx')">
            <span style="font-size:13px;font-weight:600">注入的对话上下文（${ctx.length} 条）</span><span>▶</span>
          </div>
          <div id="obs-probe-ctx" style="display:none;padding:10px 14px">
            ${ctx.map(m=>`<div style="margin-bottom:6px"><span style="font-size:11px;color:var(--muted)">[${escapeHtml(m.role||'')}]</span> <span style="font-size:12px">${escapeHtml((m.content||'').slice(0,300))}</span></div>`).join('')}
          </div>
        </div>`;
      }

      // 原始返回
      if (s.probe_response_raw) {
        html += `<div class="card" style="margin-bottom:12px">
          <div style="padding:8px 14px;display:flex;align-items:center;justify-content:space-between;cursor:pointer;border-bottom:1px solid var(--border)" onclick="togglePromptLayer('obs-probe-raw')">
            <span style="font-size:13px;font-weight:600">探针原始返回</span><span>▶</span>
          </div>
          <div id="obs-probe-raw" style="display:none;padding:10px 14px">
            <pre style="font-size:11px;white-space:pre-wrap;word-break:break-all;background:var(--bg-secondary);padding:8px;border-radius:4px;max-height:200px;overflow:auto;color:var(--text)">${escapeHtml((s.probe_response_raw||'').slice(0,2000))}</pre>
          </div>
        </div>`;
      }
    }

    // ── 工具执行结果 ──
    const results = s.tool_results || [];
    if (results.length) {
      html += `<div class="card" style="margin-bottom:12px">
        <div style="padding:10px 14px;font-weight:600;border-bottom:1px solid var(--border)">工具执行结果</div>
        ${results.map(r => `
          <div style="padding:8px 14px;border-bottom:1px solid var(--border)">
            <div style="font-size:13px;font-weight:600">${escapeHtml(r.name||'?')}
              ${r.has_side_effect ? '<span style="font-size:10px;background:#7c3aed;color:#fff;padding:1px 5px;border-radius:8px;margin-left:4px">副作用</span>' : ''}
            </div>
            <div style="font-size:11px;color:var(--muted);margin-top:2px">参数：${escapeHtml(JSON.stringify(r.arguments||{}))}</div>
            <div style="font-size:12px;margin-top:4px;background:var(--bg-secondary);padding:6px 8px;border-radius:4px">${escapeHtml((r.result||'(无返回)').slice(0,500))}</div>
          </div>`
        ).join('')}
      </div>`;
    }

    el.innerHTML = html;
  } catch(e) {
    el.innerHTML = `<div style="color:#ef4444">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

// ══════════════════════════════════════════════════════════
//  梦境 Prompt 检视（observe-dream-prompt）
// ══════════════════════════════════════════════════════════

async function loadObserveDreamPromptUidList() {
  const listEl = document.getElementById('obs-dream-prompt-uid-list');
  loadDreamPromptAblation();
  try {
    const d = await api('GET', '/observe/dream-prompt');
    const uids = d.uids || [];
    if (!uids.length) {
      listEl.textContent = '暂无快照（进行一次梦境对话后刷新）';
    } else {
      listEl.innerHTML = '有快照的 uid：' + uids.map(u =>
        `<a href="#" style="margin-left:8px;color:var(--accent)" onclick="document.getElementById('obs-dream-prompt-uid').value='${escapeHtml(u)}';loadObserveDreamPrompt();return false">${escapeHtml(u)}</a>`
      ).join('');
    }
  } catch(e) {
    listEl.textContent = '加载失败：' + e.message;
  }
}

async function loadObserveDreamPrompt() {
  const uid = (document.getElementById('obs-dream-prompt-uid').value || '').trim();
  const n   = parseInt(document.getElementById('obs-dream-prompt-n').value || '0', 10);
  const el  = document.getElementById('obs-dream-prompt-content');
  if (!uid) { el.innerHTML = '<div style="color:var(--muted)">请输入 uid 后点击「查看」</div>'; return; }
  el.innerHTML = '<div style="color:var(--muted)">加载中…</div>';
  try {
    const d = await api('GET', `/observe/dream-prompt/${encodeURIComponent(uid)}?n=${n}`);
    if (!d.snapshot) {
      el.innerHTML = `<div style="color:var(--muted)">uid ${escapeHtml(uid)} 暂无梦境快照，进行一次梦境对话后刷新。</div>`;
      return;
    }
    const s = d.snapshot;
    _obsDreamPromptCurrent = { snap: s, uid };
    const ts = s.captured_at ? s.captured_at.slice(0,19).replace('T',' ') : '';
    const scenarioExcluded = (s.layers || [])
      .filter(layer => !layer.injected && layer.note === 'scenario_profile')
      .map(layer => layer.label)
      .filter(Boolean);

    // ── 总览 ──
    const sceneTags = (s.scene_tags || []).map(t => `<code style="font-size:11px;background:#1d3a6e;color:#93c5fd;padding:1px 4px;border-radius:3px;margin-right:3px">${escapeHtml(t)}</code>`).join('') || '(无)';
    const ablated = (s.ablated_layers || []).map(layer => `<code style="font-size:11px">${escapeHtml(layer)}</code>`).join(' ');
    let html = `
      <div style="font-size:12px;color:var(--muted);margin-bottom:12px">${ts} · 第 ${d.n+1}/${d.total_snapshots} 轮 · dream_id: ${escapeHtml(s.dream_id||'?')}</div>
      <div class="card" style="margin-bottom:12px">
        <div style="padding:10px 14px;display:flex;gap:24px;font-size:13px;flex-wrap:wrap">
          <span><span style="color:var(--muted)">世界：</span><strong>${escapeHtml(s.world_id||'?')}</strong></span>
          <span><span style="color:var(--muted)">模式：</span><strong>${escapeHtml(s.lucid_mode||'?')}</strong></span>
          <span><span style="color:var(--muted)">dream_mode：</span><strong>${escapeHtml(s.dream_mode||'?')}</strong></span>
          <span><span style="color:var(--muted)">${escapeHtml(t('observe.dream_prompt.profile', 'Prompt profile'))}：</span><strong>${escapeHtml(s.prompt_profile||'?')} / ${escapeHtml(s.prompt_profile_version||'?')}</strong></span>
          <span><span style="color:var(--muted)">历史轮数：</span><strong>${s.history_turns||0}</strong></span>
          <span><span style="color:var(--muted)">token 合计：</span><strong>${(s.total_tokens||0).toLocaleString()}</strong></span>
        </div>
        <div style="padding:4px 14px 10px;font-size:13px"><span style="color:var(--muted)">scene_tags：</span>${sceneTags}</div>
        ${ablated ? `<div style="padding:0 14px 10px;color:#a78bfa;font-size:12px">已消融层：${ablated}</div>` : ''}
        ${scenarioExcluded.length ? `<div style="padding:0 14px 10px;color:var(--muted);font-size:12px">${escapeHtml(t('observe.dream_prompt.profile_excluded', 'Scenario excluded layers'))}：${scenarioExcluded.map(layer => `<code style="font-size:11px;margin-right:4px">${escapeHtml(layer)} · DISABLED/scenario_profile</code>`).join('')}</div>` : ''}
      </div>`;

    // ── 层列表 ──
    const layers = s.layers || [];
    const totalTok = layers.reduce((a,l)=>a+(l.tokens||0),0) || 1;
    html += layers.map((lyr, i) => {
      const injected = lyr.injected;
      const flags = (lyr.flags||[]).join(' ');
      const pct = injected ? ((lyr.tokens||0)/totalTok*100).toFixed(1) : '0.0';
      const barColor = !injected ? '#374151' : (lyr.label||'').includes('D4.5') ? '#7c3aed' : 'var(--accent)';
      const contentId = `obs-dp-layer-${i}`;
      const flagBadge = flags ? `<span style="font-size:10px;background:#374151;color:#9ca3af;padding:1px 5px;border-radius:8px;margin-left:4px">${escapeHtml(flags)}</span>` : '';
      const noteStr = lyr.note ? `<span style="font-size:11px;color:var(--muted);margin-left:6px">${escapeHtml(lyr.note)}</span>` : '';
      return `
        <div class="card" style="margin-bottom:6px;opacity:${injected?'1':'0.4'}">
          <div style="display:flex;align-items:center;gap:8px;padding:8px 14px;cursor:pointer" onclick="togglePromptLayer('${contentId}')">
            <span style="font-size:12px;font-weight:700;width:60px;color:var(--accent)">${escapeHtml(lyr.label||'?')}</span>
            <span style="flex:1;font-size:12px">${injected ? '' : '<span style="color:var(--muted)">[未注入]</span> '}${flagBadge}${noteStr}</span>
            <span style="font-size:11px;color:var(--muted)">${injected?(lyr.chars||0).toLocaleString()+' 字 / '+((lyr.tokens||0))+' tok':''}</span>
            <span style="font-size:11px;color:var(--muted);width:36px;text-align:right">${injected?pct+'%':''}</span>
            <span style="font-size:11px">${injected?'▶':''}</span>
          </div>
          ${injected ? `<div style="padding:0 14px 2px"><div style="height:3px;background:var(--bg-secondary);border-radius:2px;overflow:hidden"><div style="height:100%;width:${pct}%;background:${barColor}"></div></div></div>` : ''}
          <div id="${contentId}" style="display:none;padding:0 14px 10px;margin-top:4px">
            ${lyr.content ? `<pre style="font-size:11px;white-space:pre-wrap;word-break:break-all;background:var(--bg-secondary);padding:10px;border-radius:4px;max-height:300px;overflow:auto;color:var(--text);margin-top:4px">${escapeHtml((lyr.content||'').slice(0,3000))}${(lyr.content||'').length>3000?'\n… (截断)':''}</pre>` : '<div style="font-size:11px;color:var(--muted);padding:4px 0">（本层无内容/禁用）</div>'}
          </div>
        </div>`;
    }).join('');

    // ── 用户消息 & LLM 输出 ──
    if (s.user_message) {
      html += `<div class="card" style="margin-top:12px;border-left:3px solid var(--success)">
        <div style="padding:10px 14px"><span style="font-size:12px;color:var(--muted)">用户消息：</span>
        <pre style="font-size:12px;white-space:pre-wrap;word-break:break-all;margin-top:4px;color:var(--text)">${escapeHtml(s.user_message.slice(0,500))}</pre></div>
      </div>`;
    }
    if (s.llm_output != null) {
      html += `<div class="card" style="margin-top:8px;border-left:3px solid var(--accent)">
        <div style="padding:10px 14px"><span style="font-size:12px;color:var(--muted)">梦境 LLM 回复：</span>
        <pre style="font-size:12px;white-space:pre-wrap;word-break:break-all;background:var(--bg-secondary);padding:8px;border-radius:4px;max-height:400px;overflow:auto;margin-top:4px;color:var(--text)">${escapeHtml((s.llm_output||'').slice(0,5000))}${(s.llm_output||'').length>5000?'\n…(截断)':''}</pre></div>
      </div>`;
    }

    el.innerHTML = html;
  } catch(e) {
    el.innerHTML = `<div style="color:#ef4444">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

let _dreamPromptAblationKnownLayers = [];

async function loadDreamPromptAblation() {
  const el = document.getElementById('obs-dream-ablation-list');
  if (!el) return;
  el.textContent = t('common.loading', '加载中…');
  try {
    const d = await api('GET', '/dream-prompt-ablation');
    _dreamPromptAblationKnownLayers = d.known_layers || [];
    const alwaysOn = new Set(d.always_on || []);
    const disabled = new Set(d.disabled_layers || []);
    el.innerHTML = _dreamPromptAblationKnownLayers.map(({layer, desc}) => {
      const protectedLayer = alwaysOn.has(layer);
      const enabled = protectedLayer || !disabled.has(layer);
      const localizedDesc = t(`observe.dream_prompt.layer.${layer}`, desc || layer);
      const status = protectedLayer
        ? t('observe.dream_prompt.layer_unablatable', '不可消融')
        : enabled
          ? t('observe.dream_prompt.layer_enabled', '已启用')
          : t('observe.dream_prompt.layer_disabled', '已关闭');
      const historyWarning = layer === 'D9_dream_history'
        ? `<span style="color:#ef4444;font-size:11px;margin-left:6px">${escapeHtml(t('observe.dream_prompt.history_warning', '关闭梦境历史将显著改变连续性；仅用于对照测试'))}</span>`
        : '';
      return `<label style="display:flex;align-items:center;gap:8px;padding:4px 0;${protectedLayer?'opacity:0.5':''}">
        <input type="checkbox" class="obs-dream-ablation-toggle" data-layer="${escapeHtml(layer)}" data-ablatable="${protectedLayer ? 'false' : 'true'}"
          ${enabled ? 'checked' : ''} ${protectedLayer ? 'disabled' : ''}
          aria-label="${escapeHtml(t('observe.dream_prompt.enable_layer', '启用此层：{layer}', {layer}))}"
          onchange="updateDreamPromptAblationState(this)">
        <span style="font-size:11px;color:var(--text)">${escapeHtml(t('observe.dream_prompt.enable_label', '启用此层'))}</span>
        <code style="font-size:11px;background:var(--bg-secondary);padding:1px 5px;border-radius:3px">${escapeHtml(layer)}</code>
        <span style="flex:1;font-size:12px;color:var(--muted)">${escapeHtml(localizedDesc)}</span>
        <span data-ablation-status style="font-size:11px;color:${protectedLayer ? 'var(--muted)' : enabled ? 'var(--success)' : '#ef4444'}">${escapeHtml(status)}</span>
        ${historyWarning}
      </label>`;
    }).join('');
  } catch (e) {
    el.textContent = t('common.load_failed', '加载失败：{error}').replace('{error}', e.message);
  }
}

function updateDreamPromptAblationState(input) {
  if (!input || input.disabled || input.dataset.ablatable === 'false') return;
  const status = input.closest('label')?.querySelector('[data-ablation-status]');
  if (!status) return;
  status.textContent = input.checked
    ? t('observe.dream_prompt.layer_enabled', '已启用')
    : t('observe.dream_prompt.layer_disabled', '已关闭');
  status.style.color = input.checked ? 'var(--success)' : '#ef4444';
}

async function saveDreamPromptAblation() {
  const disabled_layers = Array.from(document.querySelectorAll('.obs-dream-ablation-toggle'))
    .filter(item => !item.checked && item.dataset.ablatable !== 'false')
    .map(item => item.dataset.layer);
  try {
    await api('PUT', '/dream-prompt-ablation', {disabled_layers});
    toast(t('observe.dream_prompt.ablation_saved', '已生效，下一轮梦境对话起作用'), 'ok');
    loadDreamPromptAblation();
  } catch (e) {
    toast(t('common.save_failed', '保存失败：{error}').replace('{error}', e.message), 'err');
  }
}

// ══════════════════════════════════════════════════════════
//  触发器目录（observe-trigger-catalog）
// ══════════════════════════════════════════════════════════

const SCHEDULER_CATALOG_LABELS = {
  birthday: '生日问候', diary_reminder: '日记缺失提醒', diary_share_reminder: '日记分享提醒',
  dream_exit: '出梦问候', festival: '节日问候', garden_bloom: '花园开花',
  garden_handle_gift: '处理花园赠礼', garden_handle_self: '处理自留花朵',
  garden_harvest_expired: '处理过期采收', garden_vase_wilted: '处理花瓶枯萎',
  holiday_boost: '节假日加强问候', hr_critical: '心率危急关心', hr_high: '心率偏高关心',
  letter_writer: '写信问候', morning_greeting: '早安问候', night_reminder: '晚安提醒',
  overflow: '运行信号溢出处理', period_reminder: '生理期关心', presence_nag: '存在感提醒',
  practice_help: '练习协助', random_message: '随机日间消息', reminders: '待办提醒',
  sleep_end: '睡醒关心', spontaneous_recall: '自发记忆召回', timenode: '节气提醒',
  topic_followup: '话题跟进', watch_hr_critical: '心率危急关心', watch_hr_high: '心率偏高关心',
  watch_sleep_end: '睡醒关心', weather_alert: '天气提醒', weather_alert_heavy: '恶劣天气提醒',
  weather_alert_light: '轻度天气提醒', daily_journal: '每日手账',
};

function schedulerCatalogLabel(name) {
  const purpose = SCHEDULER_CATALOG_LABELS[name];
  return purpose ? `${purpose}（${name}）` : `${name}（用途待补充）`;
}

async function loadTriggerCatalog() {
  const el = document.getElementById('trigger-catalog-content');
  if (!el) return;
  el.innerHTML = '<div style="color:var(--muted)">加载中…</div>';
  try {
    const d = await api('GET', '/observe/trigger-catalog');
    const proposers = d.proposers || [];
    if (!proposers.length) {
      el.innerHTML = '<div style="color:var(--muted)">没有已注册的提议器。</div>';
      return;
    }
    let html = '';
    for (const p of proposers) {
      const tnames = p.trigger_names || [];
      const samples = p.samples || {};
      const hasSample = tnames.some(t => samples[t] != null);
      html += `<div class="card" style="margin-bottom:12px">
        <div class="card-header" style="padding:10px 14px">
          <h3 style="font-size:14px;margin:0">${escapeHtml(schedulerCatalogLabel(p.name))}</h3>
          ${hasSample ? '<span style="font-size:11px;background:#1a3a1a;color:#86efac;padding:1px 6px;border-radius:8px;margin-left:8px">有样本</span>' : '<span style="font-size:11px;background:#2d2d2d;color:#9ca3af;padding:1px 6px;border-radius:8px;margin-left:8px">暂无样本</span>'}
        </div>`;
      for (const tname of tnames) {
        const s = samples[tname];
        const tsStr = s ? (s.captured_at||'').slice(0,19).replace('T',' ') : '';
        const sq = s ? (s.search_query||'').trim() : '';
        const sp = s ? (s.seed_prompt||'').trim() : '';
        const out = s ? (s.llm_output||null) : null;
        const tok = s ? (s.token_estimate||0) : 0;
        const contentId = `tc-${escapeHtml(p.name)}-${escapeHtml(tname)}`.replace(/[^a-zA-Z0-9-]/g,'_');
        html += `
          <div style="padding:8px 14px;border-top:1px solid var(--border)">
            <div style="display:flex;align-items:center;gap:8px;cursor:pointer" onclick="togglePromptLayer('${contentId}')">
              <span style="font-size:12px;background:var(--bg-secondary);padding:2px 6px;border-radius:4px">${escapeHtml(schedulerCatalogLabel(tname))}</span>
              ${s ? `<span style="font-size:11px;color:var(--muted)">${tsStr} · ${tok.toLocaleString()} 字</span>` : '<span style="font-size:11px;color:var(--muted)">暂无样本</span>'}
              ${s ? '<span style="font-size:11px;color:var(--muted)">▶</span>' : ''}
            </div>
            ${s ? `<div id="${contentId}" style="display:none;margin-top:8px;padding-left:8px">
              <div style="font-size:12px;margin-bottom:4px"><span style="color:var(--muted)">召回锚点（搜索条件）：</span><span style="color:#fde68a">${sq ? escapeHtml(sq.slice(0,200)) : '<em style="color:var(--muted)">（与种子 prompt 相同）</em>'}</span></div>
              <div style="font-size:12px;margin-bottom:6px"><span style="color:var(--muted)">种子 prompt：</span><pre style="margin:4px 0 0;font-size:11px;white-space:pre-wrap;word-break:break-all;background:var(--bg-secondary);padding:6px;border-radius:4px;max-height:100px;overflow:auto">${escapeHtml(sp.slice(0,600))}${sp.length>600?'\n…':''}</pre></div>
              ${out != null ? `<div style="font-size:12px"><span style="color:var(--muted)">LLM 回复：</span><pre style="margin:4px 0 0;font-size:11px;white-space:pre-wrap;word-break:break-all;background:var(--bg-secondary);padding:6px;border-radius:4px;max-height:100px;overflow:auto;color:var(--text)">${escapeHtml((out||'').slice(0,600))}${(out||'').length>600?'\n…':''}</pre></div>` : ''}
            </div>` : ''}
          </div>`;
      }
      html += '</div>';
    }
    el.innerHTML = html;
  } catch(e) {
    el.innerHTML = `<div style="color:#ef4444">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

// ══════════════════════════════════════════════════════════
//  资源完整性/功能状态检查（observe-resource-completeness，2026-07-25）
// ══════════════════════════════════════════════════════════

function _runtimeSignalText(kind, value) {
  return t(`runtime_signals.${kind}.${value}`, value);
}

function _runtimeSignalTime(value) {
  if (!value) return '—';
  return new Date(Number(value) * 1000).toLocaleString();
}

async function loadRuntimeSignals() {
  const el = document.getElementById('runtime-signals-content');
  if (!el) return;
  el.innerHTML = `<div class="loading">${escapeHtml(t('runtime_signals.loading'))}</div>`;
  try {
    const data = await api('GET', '/observability/runtime-signals');
    const summary = data.summary || {};
    const signals = data.signals || [];
    let html = `<div class="runtime-signal-summary">
      <span class="badge badge-success">${escapeHtml(t('runtime_signals.status.ok'))} × ${Number(summary.ok || 0)}</span>
      <span class="badge badge-warn">${escapeHtml(t('runtime_signals.status.attention'))} × ${Number(summary.attention || 0)}</span>
      <span class="runtime-signal-scope">${escapeHtml(t('runtime_signals.process_scope'))}: ${escapeHtml(_runtimeSignalTime(data.started_at))}</span>
    </div>`;
    if (!signals.length) {
      el.innerHTML = html + `<div class="empty">${escapeHtml(t('runtime_signals.empty'))}</div>`;
      return;
    }
    html += `<div class="card tbl-wrap"><table><thead><tr>
      <th>${escapeHtml(t('runtime_signals.category'))}</th>
      <th>${escapeHtml(t('runtime_signals.signal'))}</th>
      <th>${escapeHtml(t('runtime_signals.state'))}</th>
      <th>${escapeHtml(t('runtime_signals.count'))}</th>
      <th>${escapeHtml(t('runtime_signals.contexts'))}</th>
      <th>${escapeHtml(t('runtime_signals.last_seen'))}</th>
      <th>${escapeHtml(t('runtime_signals.context'))}</th>
    </tr></thead><tbody>`;
    for (const signal of signals) {
      const attention = signal.status === 'attention';
      const context = Object.entries(signal.latest_context || {})
        .map(([key, value]) => `${key}=${String(value)}`)
        .join(', ') || '—';
      html += `<tr>
        <td>${escapeHtml(_runtimeSignalText('category', signal.category || 'uncategorized'))}</td>
        <td>${escapeHtml(_runtimeSignalText('code', signal.code || 'unknown'))}</td>
        <td><span class="badge ${attention ? 'badge-warn' : 'badge-success'}">${escapeHtml(t(`runtime_signals.status.${attention ? 'attention' : 'ok'}`))}</span></td>
        <td>${Number(signal.count || 0)}</td>
        <td>${Number(signal.unique_contexts || 0)}</td>
        <td>${escapeHtml(_runtimeSignalTime(signal.last_seen))}</td>
        <td class="runtime-signal-context">${escapeHtml(context)}</td>
      </tr>`;
    }
    el.innerHTML = html + '</tbody></table></div>';
  } catch (error) {
    el.innerHTML = `<div class="empty runtime-signal-error">${escapeHtml(t('runtime_signals.load_error', { error: error.message }))}</div>`;
  }
}

const _RC_STATUS_BADGE = {
  ok:            { bg: '#1a3a1a', color: '#86efac', text: '正常' },
  off:           { bg: '#2d2d2d', color: '#9ca3af', text: '开关关闭' },
  missing_asset: { bg: '#3a2a1a', color: '#fbbf24', text: '缺素材' },
  unknown:       { bg: '#3a1a1a', color: '#f87171', text: '检查异常' },
};

async function loadResourceCompleteness() {
  const el = document.getElementById('resource-completeness-content');
  if (!el) return;
  el.innerHTML = '<div style="color:var(--muted)">加载中…</div>';
  try {
    const d = await api('GET', '/observability/resource-completeness');
    const checks = d.checks || [];
    const gaps = d.known_gaps || [];
    const summary = d.summary || {};

    let html = '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">';
    for (const [status, count] of Object.entries(summary)) {
      const b = _RC_STATUS_BADGE[status] || { bg: '#2d2d2d', color: '#9ca3af', text: status };
      html += `<span style="font-size:12px;background:${b.bg};color:${b.color};padding:3px 10px;border-radius:10px">${escapeHtml(b.text)} × ${count}</span>`;
    }
    html += '</div>';

    html += '<div class="card"><table style="width:100%;border-collapse:collapse">';
    html += `<thead><tr style="text-align:left;font-size:12px;color:var(--muted)">
      <th style="padding:8px 12px">功能</th><th style="padding:8px 12px">状态</th><th style="padding:8px 12px">详情</th>
    </tr></thead><tbody>`;
    for (const c of checks) {
      const b = _RC_STATUS_BADGE[c.status] || { bg: '#2d2d2d', color: '#9ca3af', text: c.status };
      html += `<tr style="border-top:1px solid var(--border)">
        <td style="padding:8px 12px;font-size:13px">${escapeHtml(c.label)}</td>
        <td style="padding:8px 12px"><span style="font-size:11px;background:${b.bg};color:${b.color};padding:2px 8px;border-radius:8px">${escapeHtml(b.text)}</span></td>
        <td style="padding:8px 12px;font-size:12px;color:var(--muted)">${escapeHtml(c.detail || '')}</td>
      </tr>`;
    }
    html += '</tbody></table></div>';

    if (gaps.length) {
      html += '<h3 style="font-size:14px;margin:18px 0 8px">已知功能缺口（人工记录，随实现推进摘除）</h3>';
      html += '<div class="card">';
      for (const g of gaps) {
        html += `<div style="padding:10px 14px;border-top:1px solid var(--border)">
          <div style="font-size:13px"><b>${escapeHtml(g.label)}</b> <span style="font-size:11px;color:var(--muted)">来源: ${escapeHtml(g.source)}</span></div>
          <div style="font-size:12px;color:var(--muted);margin-top:4px">${escapeHtml(g.detail)}</div>
        </div>`;
      }
      html += '</div>';
    }

    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = `<div style="color:#ef4444">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

// ══════════════════════════════════════════════════════════
//  API 契约检查（observe-api-contract，2026-07-25）
// ══════════════════════════════════════════════════════════

async function loadApiContractCheck() {
  const el = document.getElementById('api-contract-check-content');
  if (!el) return;
  el.innerHTML = '<div style="color:var(--muted)">加载中…</div>';
  try {
    const d = await api('GET', '/observability/api-contract-check');

    if (!d.frontend_available) {
      el.innerHTML = `<div class="card" style="padding:14px">
        <div style="font-size:13px;color:var(--muted)">${escapeHtml(d.detail || '前端仓库不可用，跳过对比')}</div>
        <div style="font-size:12px;color:var(--muted);margin-top:8px">后端仍可产出 ${Object.keys(d.backend_producible || {}).length} 种 action type，见下方（无对比对象）。</div>
      </div>`;
      return;
    }

    const brokenSet = new Set(d.broken || []);
    const statusBadge = d.status === 'ok'
      ? '<span style="font-size:12px;background:#1a3a1a;color:#86efac;padding:3px 10px;border-radius:10px">契约一致</span>'
      : `<span style="font-size:12px;background:#3a1a1a;color:#f87171;padding:3px 10px;border-radius:10px">发现漂移 × ${brokenSet.size}</span>`;

    let html = `<div style="margin-bottom:10px">${statusBadge} <span style="font-size:11px;color:var(--muted);margin-left:8px">前端仓库: ${escapeHtml(d.frontend_repo_path || '')}</span></div>`;

    html += '<div class="card"><table style="width:100%;border-collapse:collapse">';
    html += `<thead><tr style="text-align:left;font-size:12px;color:var(--muted)">
      <th style="padding:8px 12px">type</th><th style="padding:8px 12px">前端是否认识</th><th style="padding:8px 12px">产出来源</th>
    </tr></thead><tbody>`;
    for (const [type, sources] of Object.entries(d.backend_producible || {})) {
      const isBroken = brokenSet.has(type);
      html += `<tr style="border-top:1px solid var(--border)">
        <td style="padding:8px 12px;font-size:13px"><code>${escapeHtml(type)}</code></td>
        <td style="padding:8px 12px">${isBroken
          ? '<span style="font-size:11px;background:#3a1a1a;color:#f87171;padding:2px 8px;border-radius:8px">不认识（漂移）</span>'
          : '<span style="font-size:11px;background:#1a3a1a;color:#86efac;padding:2px 8px;border-radius:8px">认识</span>'}</td>
        <td style="padding:8px 12px;font-size:12px;color:var(--muted)">${(sources || []).map(escapeHtml).join('<br>')}</td>
      </tr>`;
    }
    html += '</tbody></table></div>';

    if ((d.frontend_only || []).length) {
      html += `<div style="font-size:12px;color:var(--muted);margin-top:12px">前端认识但本模块未扫到后端产出（不代表有问题，可能走别的推送路径）: ${d.frontend_only.map(escapeHtml).join(', ')}</div>`;
    }

    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = `<div style="color:#ef4444">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

// ══════════════════════════════════════════════════════════
//  角色权限（observe-char-permissions，2026-07-25）
// ══════════════════════════════════════════════════════════

function _charPermUid() {
  const el = document.getElementById('obs-charperm-uid');
  return el ? el.value.trim() : '';
}

function _charPermCharId() {
  const el = document.getElementById('obs-charperm-char');
  return el ? el.value : '';
}

async function loadCharPermissions() {
  const el = document.getElementById('char-permissions-content');
  if (!el) return;
  const charId = _charPermCharId();
  if (!charId) { el.innerHTML = '<div style="color:var(--muted)">未选中角色</div>'; return; }
  const uid = _charPermUid();
  el.innerHTML = '<div style="color:var(--muted)">加载中…</div>';
  try {
    const qs = uid ? `?char_id=${encodeURIComponent(charId)}&uid=${encodeURIComponent(uid)}` : `?char_id=${encodeURIComponent(charId)}`;
    const d = await api('GET', '/observability/character-permissions' + qs);

    let html = `<div style="margin-bottom:12px;font-size:12px;color:var(--muted)">
      当前危险模式: <b style="color:${d.current_mode === 'danger' ? '#fbbf24' : '#86efac'}">${escapeHtml(d.current_mode)}</b>
      · tool loop: ${d.tool_loop_enabled ? '已开启' : '已关闭'}
      · 暴露面来源: ${escapeHtml(d.tool_categories_source || '')}
    </div>`;

    for (const cat of (d.categories || [])) {
      const modeBadge = cat.mode_restricted
        ? (cat.currently_blocked_by_mode
            ? '<span style="font-size:11px;background:#3a1a1a;color:#f87171;padding:2px 8px;border-radius:8px">受危险模式闸门·当前拦截</span>'
            : '<span style="font-size:11px;background:#1a3a1a;color:#86efac;padding:2px 8px;border-radius:8px">受危险模式闸门·当前放行</span>')
        : '<span style="font-size:11px;background:#2d2d2d;color:#9ca3af;padding:2px 8px;border-radius:8px">不受危险模式约束</span>';
      html += `<div class="card" style="margin-bottom:10px">
        <div class="card-header" style="padding:10px 14px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <h3 style="font-size:14px;margin:0">${escapeHtml(cat.category)}</h3>
          ${cat.exposed_to_probe ? '<span style="font-size:11px;color:var(--muted)">探针可见</span>' : ''}
          ${cat.exposed_to_tool_loop ? '<span style="font-size:11px;color:var(--muted)">tool loop 可见</span>' : ''}
          ${modeBadge}
          <button class="btn btn-ghost btn-sm" style="margin-left:auto" onclick="testCharPermissionLink('${cat.category}')">测试这条链路</button>
        </div>
        <div style="padding:8px 14px;font-size:12px;color:var(--muted)">
          ${(cat.tools || []).map(t => `<span style="display:inline-block;margin:2px 6px 2px 0;padding:2px 8px;border-radius:8px;background:${t.config_enabled && !t.excluded_by_char ? 'var(--bg-secondary)' : '#2d1a1a'};${t.dangerous ? 'color:#fbbf24' : ''}">${escapeHtml(t.name)}${t.dangerous ? ' ⚠' : ''}${t.excluded_by_char ? '（被角色排除）' : (t.config_enabled ? '' : '（配置关闭）')}</span>`).join('') || '（该类目下无注册工具）'}
        </div>
      </div>`;
    }

    if (d.identity_consolidation) {
      const ic = d.identity_consolidation;
      html += `<div class="card" style="margin-bottom:10px">
        <div class="card-header" style="padding:10px 14px;display:flex;align-items:center;gap:8px">
          <h3 style="font-size:14px;margin:0">身份固化管线（角色改自己的记忆文件）</h3>
          <button class="btn btn-ghost btn-sm" style="margin-left:auto" onclick="testCharPermissionLink('identity_consolidation')">测试这条链路</button>
        </div>
        <div style="padding:8px 14px;font-size:12px;color:var(--muted)">
          identity.yaml: ${ic.identity_file_exists ? `存在，最后修改 ${escapeHtml(ic.identity_file_mtime_human || '')}` : '尚不存在（角色还没固化过身份）'}<br>
          ${ic.would_consolidate_now ? '<span style="color:#fbbf24">当前已满足固化阈值，下一次慢队列处理时会真正触发</span>' : '当前未达固化阈值（不代表链路坏了，只是还没攒够）'}
        </div>
      </div>`;
    } else {
      html += `<div style="font-size:12px;color:var(--muted);margin-bottom:10px">填写上方 uid 可查看该用户的身份固化管线状态。</div>`;
    }

    html += `<div id="char-perm-test-result"></div>`;
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = `<div style="color:#ef4444">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

async function testCharPermissionLink(link) {
  const charId = _charPermCharId();
  const uid = _charPermUid();
  if (link === 'identity_consolidation' && !uid) {
    toast('测试身份固化管线需要先填 uid', 'error');
    return;
  }
  const resultEl = document.getElementById('char-perm-test-result');
  if (resultEl) resultEl.innerHTML = '<div style="color:var(--muted);margin-top:8px">测试中…</div>';
  try {
    const d = await api('POST', '/observability/character-permissions/test', {
      link, char_id: charId, uid: uid || 'admin_probe',
    });
    const badge = d.ok
      ? '<span style="font-size:12px;background:#1a3a1a;color:#86efac;padding:3px 10px;border-radius:10px">通</span>'
      : '<span style="font-size:12px;background:#3a1a1a;color:#f87171;padding:3px 10px;border-radius:10px">不通</span>';
    let html = `<div class="card" style="margin-top:8px;padding:12px 14px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">${badge}
        <span style="font-size:12px;color:var(--muted)">${d.executed ? '（已真实执行）' : '（仅就绪检查，未实际执行）'}</span>
      </div>
      <div style="font-size:13px">${escapeHtml(d.detail || '')}</div>`;
    if (d.checklist) {
      html += '<div style="margin-top:8px">' + d.checklist.map(c =>
        `<div style="font-size:12px;color:${c.pass ? '#86efac' : '#f87171'}">${c.pass ? '✓' : '✗'} ${escapeHtml(c.item)}</div>`
      ).join('') + '</div>';
    }
    html += '</div>';
    if (resultEl) resultEl.innerHTML = html;
  } catch (e) {
    if (resultEl) resultEl.innerHTML = `<div style="color:#ef4444;margin-top:8px">测试请求失败：${escapeHtml(e.message)}</div>`;
  }
}

// ══════════════════════════════════════════════════════════
//  梦境设定页（dream-settings）
// ══════════════════════════════════════════════════════════

async function loadSystemDiagnosis() {
  const el = document.getElementById('system-diagnosis-content');
  if (!el) return;
  el.innerHTML = '<div style="color:var(--muted)">加载中…</div>';
  try {
    const [resource, runtime, trigger, contract] = await Promise.all([
      api('GET', '/observability/resource-completeness'),
      api('GET', '/observability/runtime-signals'),
      api('GET', '/observe/trigger-catalog'),
      api('GET', '/observability/api-contract-check'),
    ]);

    const summary = resource.summary || {};
    const runtimeSummary = runtime.summary || {};
    const proposers = trigger.proposers || [];
    const broken = new Set(contract.broken || []);

    let html = '<div class="diagnosis-band">';
    html += `<span class="badge badge-success">资源 ${Number(summary.ok || 0)}</span>`;
    html += `<span class="badge badge-warn">关注 ${Number(runtimeSummary.attention || 0)}</span>`;
    html += `<span class="badge badge-success">触发器 ${proposers.length}</span>`;
    html += `<span class="badge ${broken.size ? 'badge-warn' : 'badge-success'}">契约漂移 ${broken.size}</span>`;
    html += '</div>';

    html += '<div class="card tbl-wrap"><table><thead><tr>';
    html += '<th>检查层级</th><th>项目</th><th>状态</th><th>说明</th></tr></thead><tbody>';
    for (const row of resource.checks || []) {
      html += `<tr><td>静态资源</td><td>${escapeHtml(row.label || '')}</td><td>${escapeHtml(row.status || '')}</td><td>${escapeHtml(row.detail || '')}</td></tr>`;
    }
    for (const row of (runtime.signals || []).slice(0, 8)) {
      const detail = `${Number(row.count || 0)} events; ${Number(row.unique_contexts || 0)} contexts`;
      html += `<tr><td>当前进程</td><td>${escapeHtml(row.code || '')}</td><td>${escapeHtml(row.status || '')}</td><td>${escapeHtml(detail)}</td></tr>`;
    }
    for (const entry of proposers.slice(0, 8)) {
      const sampleCount = (entry.trigger_names || []).filter(name => trigger.samples?.[name] != null).length;
      html += `<tr><td>调度链路</td><td>${escapeHtml(entry.name || '')}</td><td>${escapeHtml((entry.trigger_names || []).join(', '))}</td><td>${sampleCount} samples observed</td></tr>`;
    }
    for (const [type, sources] of Object.entries(contract.backend_producible || {})) {
      html += `<tr><td>跨仓源码契约</td><td><code>${escapeHtml(type)}</code></td><td>${broken.has(type) ? '漂移' : '一致'}</td><td>${escapeHtml((sources || []).join(', '))}</td></tr>`;
    }
    html += '</tbody></table></div>';
    el.innerHTML = html;
  } catch (e) {
    el.innerHTML = `<div style="color:#ef4444">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

async function loadAutonomySettings() {
  void loadSelfManagement();
  try { const config=await api("GET", "/admin/autonomy/config");
    document.getElementById('autonomy-enabled').checked = !!config.enabled;
    document.getElementById('autonomy-talk').checked = !!config.talk_enabled;
    document.getElementById('autonomy-daily').value = config.daily_evaluation_budget || 1;
    document.getElementById('autonomy-min-interval').value = config.min_interval_seconds || 0;
    document.getElementById('autonomy-interval').value = (config.interval || {}).seconds || 60;
    document.getElementById('autonomy-interval-enabled').checked = !!(config.interval || {}).enabled;
    document.getElementById('autonomy-overflow-enabled').checked = !!(config.overflow || {}).enabled;
    document.getElementById('autonomy-overflow-threshold').value = (config.overflow || {}).threshold || 1.6;
    const schedule = config.schedule || {};
    document.getElementById('autonomy-schedule-enabled').checked = !!schedule.enabled;
    document.getElementById('autonomy-schedule-time').value = schedule.time || '12:00';
    document.getElementById('autonomy-schedule-timezone').value = schedule.timezone || 'local';
    document.getElementById('autonomy-schedule-weekdays').value = (schedule.weekdays || []).join(',');
    document.getElementById('autonomy-window-start').value = (schedule.window || [])[0] || '';
    document.getElementById('autonomy-window-end').value = (schedule.window || [])[1] || '';
    document.getElementById('autonomy-miss-policy').value = schedule.restart_miss_policy || 'skip';

  } catch(error) { toast("读取失败："+error.message,"err"); }
}

async function loadLatestAutonomyPrompt() {
  const host=document.getElementById('autonomy-prompt');
  try {const data=await api('GET','/admin/autonomy/runs?limit=1');if(data.runs?.length)await loadAutonomyPrompt(data.runs[0].id);}
  catch(error){centerError(host,error);}
}
