let _toolsControl = null;
let _openedNotebookQuote = '';
let _openedNotebookScope = '';
function notebookAuthor(author) { return author === 'user' ? t('quotes.user', '用户') : t('quotes.character', '角色'); }

function notebookScope() {
  const charId = document.getElementById('quotes-char').value.trim();
  if (!charId) throw Error(t('quotes.need_char', '请填写角色 ID'));
  return `char_id=${encodeURIComponent(charId)}`;
}

async function loadQuoteNotebook() {
  try {
    const data = await api('GET', `/observability/quotes?${notebookScope()}&query=${encodeURIComponent(document.getElementById('quotes-query').value)}`);
    _openedNotebookQuote = '';
    document.getElementById('quotes-choice').innerHTML = data.items.map(item => `<option value="${escapeHtml(item.quote_id)}">${escapeHtml(notebookAuthor(item.author))} · ${escapeHtml(new Date(item.message_ts * 1000).toLocaleString())} · ${escapeHtml(item.preview)}</option>`).join('');
    document.getElementById('quotes-detail').textContent = `${data.total} ${t('quotes.count', '条收藏（本页最多20条）')}`;
    document.getElementById('quotes-note').value = '';
  } catch (error) { document.getElementById('quotes-detail').textContent = error.message; }
}

async function openNotebookQuote() {
  try {
    const id = document.getElementById('quotes-choice').value;
    if (!id) return;
    const data = await api('GET', `/observability/quotes/${encodeURIComponent(id)}?${notebookScope()}`);
    _openedNotebookQuote = id;
    _openedNotebookScope = notebookScope();
    const memories = data.related_memories.map(item => `<p>${escapeHtml(new Date(item.occurred_at * 1000).toLocaleString())} · ${escapeHtml(item.text)}</p>`).join('') || `<p>${escapeHtml(t('quotes.no_related', '未关联召回'))}</p>`;
    document.getElementById('quotes-detail').innerHTML = `<p>${escapeHtml(notebookAuthor(data.author))} · ${escapeHtml(new Date(data.message_ts * 1000).toLocaleString())}</p><blockquote style="white-space:pre-wrap">${escapeHtml(data.text)}</blockquote><p>${escapeHtml(t('quotes.saved_at', '收藏时间'))}: ${escapeHtml(new Date(data.saved_at * 1000).toLocaleString())}</p><p>${escapeHtml(data.source_available ? t('quotes.source_ok', '原消息仍可查询') : t('quotes.source_missing', '原消息已不可用；此处为收藏快照'))}</p>${memories}`;
    document.getElementById('quotes-note').value = data.note;
  } catch (error) { document.getElementById('quotes-detail').textContent = error.message; }
}

const _KNOWN_TOOL_CATEGORIES = ['info', 'self', 'schedule', 'life', 'desktop', 'memory', 'system', 'fs', 'phone_control', 'self_management', 'mcp'];

function _toolExposureCategories() {
  const categories = new Set(_KNOWN_TOOL_CATEGORIES);
  for (const tool of (_toolsControl?.tools || [])) categories.add(tool.category);
  for (const path of ['path_a', 'path_c']) {
    for (const category of (_toolsControl?.path_exposure?.[path]?.categories || [])) categories.add(category);
  }
  return [...categories].filter(Boolean).sort();
}

function _toolCategoryLabel(category) {
  const label = t(`tools.category.${category}`, category);
  return `${label} \`${category}\``;
}

function _toolUiDescription(tool) {
  // This is presentation-only. The canonical description in the registered
  // schema remains untouched and is still sent to the model/tool loop.
  return t(`tools.description.${tool.name}`, tool.description || '');
}

function _renderPathExposure() {
  const root = document.getElementById('tools-path-exposure');
  if (!root || !_toolsControl) return;
  const categories = _toolExposureCategories();
  const pathLabels = {
    path_a: t('tools.path_a', 'Path A (预探针)'),
    path_c: t('tools.path_c', 'Path C (工具循环)'),
  };
  root.innerHTML = ['path_a', 'path_c'].map(path => {
    const selected = new Set(_toolsControl.path_exposure?.[path]?.categories || []);
    const checks = categories.map(category => `<label class="checkbox-row" style="margin:2px 10px 2px 0"><input type="checkbox" data-tool-path-category="${path}" data-tool-category="${escapeHtml(category)}" ${selected.has(category) ? 'checked' : ''}><span>${escapeHtml(_toolCategoryLabel(category))}</span></label>`).join('');
    return `<div style="padding:10px 0;border-top:1px solid var(--border)"><strong>${pathLabels[path]}</strong><div style="display:flex;flex-wrap:wrap;margin-top:6px">${checks || `<span style="color:var(--muted);font-size:12px">${escapeHtml(t('tools.no_categories', '没有可配置的工具类别'))}</span>`}</div></div>`;
  }).join('');
}

function _renderToolsRegistry() {
  const root = document.getElementById('tools-registry-list');
  if (!root || !_toolsControl) return;
  const rows = (_toolsControl.tools || []).filter(tool => !tool.frozen).map(tool => {
    const frozen = tool.frozen === true;
    const execution = `<span class="badge" data-tool-state="${escapeHtml(tool.name)}" data-enabled="${tool.execution_enabled}">${escapeHtml(tool.execution_enabled ? t("common.enabled", "已开启") : t("common.disabled", "已关闭"))}</span>`;
    return `<tr><td><strong><code>${escapeHtml(tool.name)}</code></strong><div style="font-size:12px;color:var(--muted)">${escapeHtml(_toolUiDescription(tool))}</div></td><td>${escapeHtml(_toolCategoryLabel(tool.category))}</td><td>${execution}</td></tr>`;
  }).join('');
  root.innerHTML = rows ? `<div class="tbl-wrap"><table><thead><tr><th>${escapeHtml(t('tools.name', '工具'))}</th><th>${escapeHtml(t('tools.category', '分类'))}</th><th>${escapeHtml(t('tools.execution', '执行'))}</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="empty">${escapeHtml(t('tools.empty', '没有已注册工具'))}</div>`;
  const count = document.getElementById('tools-exposure-count');
  if (count) {
    const n = (_toolsControl.tools || []).length;
    count.textContent = t('tools.exposure_count', '{count} 个工具', { count: n });
    count.className = n > 16 ? 'badge badge-danger' : 'badge badge-success';
  }
}

function refreshToolExecutionStates(tools) {
  for (const tool of tools || []) {
    const current=_toolsControl?.tools?.find(row=>row.name===tool.name);
    if(current) current.execution_enabled=tool.execution_enabled;
    document.querySelectorAll('[data-tool-state]').forEach(node=>{
      if(node.dataset.toolState!==tool.name) return;
      node.dataset.enabled=String(tool.execution_enabled);
      node.textContent=tool.execution_enabled?t('common.enabled','已开启'):t('common.disabled','已关闭');
    });
  }
}

function _renderToolsPage() {
  if (!_toolsControl) return;
  const weatherProxy = document.getElementById('tools-weather-use-proxy');
  if (weatherProxy) weatherProxy.checked = _toolsControl.weather?.use_proxy === true;
  const weatherStatus = document.getElementById('tools-weather-status');
  if (weatherStatus) weatherStatus.textContent = _toolsControl.weather?.use_proxy
    ? t('tools.weather_proxy_on', '当前天气请求走全局代理')
    : t('tools.weather_proxy_off', '当前天气请求直连，不走全局代理');
  _renderPathExposure();
  _renderToolsRegistry();
}

async function loadToolsPage() {
  loadFoodSettings();
  loadXiaohongshuSettings();
  const root = document.getElementById('tools-registry-list');
  if (root) root.innerHTML = `<div class="loading">${escapeHtml(t('common.loading', '加载中…'))}</div>`;
  try { _toolsControl = await api('GET', '/settings/tools'); _renderToolsPage(); }
  catch (error) { if (root) root.innerHTML = `<div class="empty">${escapeHtml(t('common.load_failed', '加载失败: {error}', { error: error.message }))}</div>`; }
}

// One controller and one API client for both presentations.
let _foodSnapshot = null;
const _foodBusy = new Set();
function foodStatus(message, state = 'success') {
  const node = document.getElementById('food-status');
  node.textContent = message; node.dataset.state = state;
}
async function foodOperation(name, work, target = 'food-status') {
  if (_foodBusy.size) return false;
  _foodBusy.add(name);
  const buttons = [...document.querySelectorAll('#food-module button')];
  buttons.forEach(b => b.disabled = true);
  const node = document.getElementById(target);
  node.textContent = '处理中…'; node.dataset.state = 'loading'; node.setAttribute('aria-busy', 'true');
  try { await work(); return true; }
  catch (error) { node.textContent = error.message; node.dataset.state = 'error'; return false; }
  finally {
    _foodBusy.delete(name); node.setAttribute('aria-busy', 'false');
    buttons.forEach(b => b.disabled = _foodBusy.size > 0);
  }
}
function renderFoodRecords() {
  const root = document.getElementById('food-records');
  if (!root || !_foodSnapshot || root.dataset.state === 'loading' || root.dataset.state === 'error') return;
  const value = _foodSnapshot;
  const stats = `<div class="pk-stats">待处理 ${Number(value.pending)} · 失败 ${Number(value.failed)} · 项目 ${Number(value.total)}</div><p>${escapeHtml(value.count_semantics)}</p>`;
  if (AdminUI.version === 'legacy') {
    root.innerHTML = stats + `<pre>${escapeHtml(JSON.stringify({items: value.items, tastes: value.tastes}, null, 2))}</pre>`;
    return;
  }
  const records = (items) => items.map(item => `<article class="pk-record"><strong>${escapeHtml(item.name)}</strong> · ${escapeHtml(({like:'喜欢',dislike:'不喜欢'})[item.evaluation] || '未明确评价')}<small>已记录吃过 ${Number(item.recorded_eaten_count)} 次 · ${escapeHtml(new Date(item.last_evidence_at * 1000).toLocaleString())}</small><p>${escapeHtml(item.evaluation_quote || item.temporary_refusal_quote || '')}</p></article>`).join('') || '<p class="pk-empty">暂无记录</p>';
  root.innerHTML = stats + '<h4>食品与店名（本页最多 20 项）</h4>' + records(value.items) + '<h4>口味记录（最多 12 项）</h4>' + records(value.tastes);
}
window.addEventListener('admin-ui-version-changed', renderFoodRecords);
async function loadFoodSettings() {
  await foodOperation('settings', async () => {
    const value = await api('GET', '/settings/food-memory');
    foodStatus(`${value.effective ? '已生效' : '已关闭或路由未就绪'} · food_extract → ${value.route.effective_preset || '未配置'} (${value.route.source})`);
  });
}
function foodScope() {
  const charId = document.getElementById('food-char').value.trim();
  if (!charId) throw new Error('请填写角色 ID');
  return `char_id=${encodeURIComponent(charId)}`;
}
async function loadFoodRecords() {
  await foodOperation('query', async () => {
    const scope = foodScope();
    const query = document.getElementById('food-query').value.trim();
    _foodSnapshot = await api('GET', `/observability/food-memory?${scope}&query=${encodeURIComponent(query)}`);
    document.getElementById('food-records').dataset.state = 'success'; renderFoodRecords();
  }, 'food-records');
}
function showXiaohongshuSettings(data) {
  document.getElementById('xhs-enabled').checked = data.enabled;
  document.getElementById('xhs-local-service').checked = !!data.local_service;
  document.getElementById('xhs-reader-url').value = data.reader_url || '';
  document.getElementById('xhs-max-comments').value = data.max_comments;
  document.getElementById('xhs-max-images').value = data.max_images;
  document.getElementById('xhs-settings-status').textContent = !data.enabled ? t('xhs.disabled', '已关闭') :
    !data.configured ? t('xhs.no_address', '已开启，但尚未配置读取服务') : t('xhs.configured', '配置已保存；仍需允许模型使用此工具。服务和登录状态见下方。');
  changeXiaohongshuMode();
  showXiaohongshuRuntime(data.local_runtime || {});
}

function changeXiaohongshuMode() {
  const local = document.getElementById('xhs-local-service').checked;
  const address = document.getElementById('xhs-reader-url');
  address.readOnly = local;
  if (local) address.value = 'http://127.0.0.1:18060';
  document.getElementById('xhs-local-controls').hidden = !local;
}

function showXiaohongshuRuntime(data) {
  const state = data.state || 'not_started';
  const login = data.login_status || 'not_checked';
  document.getElementById('xhs-runtime-status').textContent = [
    data.installed ? t('xhs.installed', '已安装') : t('xhs.not_installed', '尚未安装'),
    t(`xhs.state.${state}`, state), t(`xhs.login.${login}`, login),
    data.install_state === 'installing' ? t('xhs.installing', '正在安装，请稍后刷新') : '',
    data.install_error ? t('xhs.install_failed', '安装失败，请检查 Go 版本和 GitHub 网络连接，或按文档执行安装命令') : '',
    !data.installer_available && !data.installed ? t('xhs.go_required', '后端未找到 Go，请先安装 Go 并重启后端') : '',
  ].filter(Boolean).join(' · ');
  document.getElementById('xhs-install').disabled = !!data.installed || data.install_state === 'installing' || !data.installer_available || !data.supported;
  for (const id of ['xhs-login', 'xhs-check-login']) document.getElementById(id).disabled = !['running', 'reused'].includes(state);
}

async function refreshXiaohongshuStatus() {
  try { showXiaohongshuRuntime((await api('GET', '/settings/xiaohongshu')).local_runtime || {}); }
  catch (e) { document.getElementById('xhs-operation-status').textContent = e.message; }
}

async function installXiaohongshu() {
  try { showXiaohongshuRuntime(await api('POST', '/settings/xiaohongshu/install')); }
  catch (e) { document.getElementById('xhs-operation-status').textContent = e.message; }
}

async function _xiaohongshuLogin(action) {
  const status = document.getElementById('xhs-operation-status');
  const img = document.getElementById('xhs-qrcode');
  status.textContent = t('xhs.checking', '正在连接小红书，请稍候…');
  img.hidden = true;
  img.removeAttribute('src');
  try {
    const data = await api('POST', `/settings/xiaohongshu/login/${action}`);
    status.textContent = t(`xhs.login.${data.login_status}`, data.login_status);
    if (data.img && /^data:image\/png;base64,/.test(data.img)) {
      img.src = data.img;
      img.hidden = false;
      status.textContent = t('xhs.scan_hint', '用小红书 App 扫码并确认，4 分钟内有效；完成后点击“我已扫码 / 检查登录”。');
      setTimeout(() => { if (img.src === data.img) { img.hidden = true; img.removeAttribute('src'); } }, 240000);
    }
    await refreshXiaohongshuStatus();
  } catch (e) { status.textContent = t('xhs.login_failed', '登录检查失败，请检查小红书网络连接或稍后重试') + ': ' + e.message; }
}

function loginXiaohongshu() { return _xiaohongshuLogin('qrcode'); }
function checkXiaohongshuLogin() { return _xiaohongshuLogin('status'); }

async function loadXiaohongshuSettings() {
  try { showXiaohongshuSettings(await api('GET', '/settings/xiaohongshu')); }
  catch (e) { document.getElementById('xhs-settings-status').textContent = t('xhs.load_failed', '读取小红书设置失败'); }
}

async function saveXiaohongshuSettings() {
  try {
    showXiaohongshuSettings(await api('PUT', '/settings/xiaohongshu', {
      enabled: document.getElementById('xhs-enabled').checked,
      local_service: document.getElementById('xhs-local-service').checked,
      reader_url: document.getElementById('xhs-reader-url').value.trim(),
      max_comments: Number(document.getElementById('xhs-max-comments').value),
      max_images: Number(document.getElementById('xhs-max-images').value),
    }));
    toast(t('xhs.saved', '小红书设置已保存，服务状态将在数秒内更新'), 'ok');
  } catch (e) { document.getElementById('xhs-settings-status').textContent = `保存失败：${e.message || e}`; }
}

async function savePathExposure() {
  const exposure = {};
  for (const path of ['path_a', 'path_c']) {
    exposure[path] = {
      categories: [...document.querySelectorAll(`[data-tool-path-category="${path}"]:checked`)]
        .map(input => input.dataset.toolCategory),
    };
  }
  try {
    _toolsControl = await api('PUT', '/settings/tools', { exposure });
    toast(t('tools.path_saved', 'Path A/Path C categories saved; QQ, desktop, and mobile share the same Path A settings.'), 'ok');
    _renderToolsPage();
  } catch (error) { toast(error.message, 'err'); }
}

async function saveToolExecution(name) {
  const input = document.querySelector(`[data-tool="${CSS.escape(name)}"]`);
  if (!input) return;
  try {
    _toolsControl = await api('PUT', '/settings/tools', { execution_enabled: { [name]: input.checked } });
    toast(t('tools.execution_saved', '全局执行开关已热更新'), 'ok');
    _renderToolsPage();
  } catch (error) { toast(error.message, 'err'); await loadToolsPage(); }
}

async function saveWeatherProxy() {
  const input = document.getElementById('tools-weather-use-proxy');
  if (!input) return;
  try {
    _toolsControl = await api('PUT', '/settings/tools', { weather: { use_proxy: input.checked } });
    toast(t('tools.weather_saved', '天气直连设置已热更新'), 'ok');
    _renderToolsPage();
  } catch (error) {
    toast(error.message, 'err');
    await loadToolsPage();
  }
}
