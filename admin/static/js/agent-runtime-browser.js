let _browserRuntimeState = null;
const _browserRuntimeRequests = new Map();

function _browserRuntimeRenderStatus(data) {
  _browserRuntimeState = data;
  const status = document.getElementById('browser-runtime-status');
  if (!status) return;
  status.innerHTML = `<span>effective: <strong>${escapeHtml(data.effective_state || 'unknown')}</strong></span><span>worker: ${data.worker_alive ? 'alive' : 'offline'}</span><span>adapter: ${data.adapter_available ? 'available' : 'unavailable'}</span><span>allowlist: ${Number(data.allowed_domain_count || 0)}</span><span>upload/download: ${data.upload_enabled ? 'on' : 'off'} / ${data.download_enabled ? 'on' : 'off'}</span>`;
  document.getElementById('browser-enabled').checked = Boolean(data.enabled);
  document.getElementById('browser-download').checked = Boolean(data.download_enabled);
  document.getElementById('browser-upload').checked = Boolean(data.upload_enabled);
}

async function loadBrowserRuntimePage() {
  try {
    _browserRuntimeRenderStatus(await api('GET', '/settings/agent-runtime-browser'));
    const cfg = await api('GET', '/settings/agent-runtime-browser');
    document.getElementById('browser-domains').value = (cfg.allowed_domains || []).join('\n');
    document.getElementById('browser-max-chars').value = cfg.limits?.max_page_chars || 12000;
    document.getElementById('browser-max-links').value = cfg.limits?.max_links || 30;
    document.getElementById('browser-timeout').value = cfg.limits?.worker_timeout_seconds || 60;
    await loadBrowserRuntimeTasks();
  } catch (error) { toast(`浏览器配置读取失败：${error.message || error}`, 'err'); }
}

async function saveBrowserRuntimeSettings() {
  try {
    const result = await api('PUT', '/settings/agent-runtime-browser', {
      enabled: document.getElementById('browser-enabled').checked,
      allowed_domains: document.getElementById('browser-domains').value.split(/\r?\n|,/).map(value => value.trim()).filter(Boolean),
      download_enabled: document.getElementById('browser-download').checked,
      upload_enabled: document.getElementById('browser-upload').checked,
      max_page_chars: Number(document.getElementById('browser-max-chars').value),
      max_links: Number(document.getElementById('browser-max-links').value),
      worker_timeout_seconds: Number(document.getElementById('browser-timeout').value),
    });
    _browserRuntimeRenderStatus(result);
    toast('浏览器策略已保存并重新加载 worker', 'ok');
  } catch (error) { toast(`浏览器策略保存失败：${error.message || error}`, 'err'); }
}

async function loadBrowserRuntimeTasks() {
  const root = document.getElementById('browser-task-list');
  if (!root) return;
  try {
    const data = await api('GET', '/settings/agent-runtime-browser/tasks');
    root.innerHTML = (data.entries || []).map(task => `<div class="card" style="margin-top:8px"><div><strong>${escapeHtml(task.status || 'unknown')}</strong> <code>${escapeHtml(task.task_id || '')}</code></div><div class="mono">attempts: ${Number(task.attempt_count || 0)}${task.error_code ? ` · error: ${escapeHtml(task.error_code)}` : ''}</div>${task.status === 'waiting_confirm' ? `<button class="btn btn-primary btn-sm" data-action="confirmBrowserRuntimeTask" data-action-args='["${escapeHtml(task.task_id || '')}"]'>确认并执行</button>` : ''}</div>`).join('') || '<div class="loading">暂无任务</div>';
    bindPageActions(root);
  } catch (error) { root.textContent = `任务读取失败：${error.message || error}`; }
}

async function submitBrowserRuntimeTask() {
  const url = document.getElementById('browser-task-url').value.trim();
  const operation = document.getElementById('browser-task-operation').value;
  const params = {};
  if (['click', 'fill', 'select'].includes(operation)) params.selector = document.getElementById('browser-task-selector').value.trim();
  if (['fill', 'select'].includes(operation)) params.value = document.getElementById('browser-task-value').value.trim();
  if (!url) return;
  try {
    const request = { url, operation, idempotency_key: `admin-browser-${Date.now()}`, params };
    const result = await api('POST', '/settings/agent-runtime-browser/tasks', request);
    if (result.receipt?.task_id) _browserRuntimeRequests.set(result.receipt.task_id, request);
    document.getElementById('browser-task-message').textContent = result.receipt?.status === 'waiting_confirm' ? '任务已暂停，等待人工确认。' : `任务状态：${result.receipt?.status || result.status || 'submitted'}`;
    await loadBrowserRuntimeTasks();
  } catch (error) { document.getElementById('browser-task-message').textContent = `提交失败：${error.message || error}`; }
}

async function confirmBrowserRuntimeTask(taskId) {
  const request = _browserRuntimeRequests.get(taskId);
  if (!request) { toast('当前页面未保留该任务的执行参数，请重新提交。', 'err'); return; }
  try { await api('POST', `/settings/agent-runtime-browser/tasks/${encodeURIComponent(taskId)}/confirm`, { ...request, confirmed: true }); await loadBrowserRuntimeTasks(); } catch (error) { toast(`确认失败：${error.message || error}`, 'err'); }
}
