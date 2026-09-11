let _toolsControl = null;
let _toolsTargetModel = '';
const _KNOWN_TOOL_CATEGORIES = ['info', 'desktop', 'memory', 'system', 'fs', 'phone_control', 'self_management', 'mcp'];

function _toolCurrentPreset() {
  return _toolsControl?.model_bindings?.[_toolsTargetModel] || '';
}

function _toolCheckedNames() {
  return [...document.querySelectorAll('[data-tool-exposure]:checked')].map(input => input.dataset.toolExposure);
}

function _toolPresetPayload(presets, binding = undefined) {
  const body = { tool_presets: presets };
  if (binding !== undefined && _toolsTargetModel) body.model_bindings = { [_toolsTargetModel]: binding || null };
  return body;
}

function _toolsPresetButtons() {
  const root = document.getElementById('tools-preset-buttons');
  if (!root || !_toolsControl) return;
  const active = _toolCurrentPreset();
  const buttons = ( _toolsControl.tool_presets || []).map(item => {
    const args = escapeHtml(JSON.stringify([item.name]));
    const klass = item.name === active ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm';
    return `<span style="display:inline-flex;gap:4px;margin:2px"><button class="${klass}" data-action="selectToolPreset" data-action-args="${args}">${escapeHtml(item.name)}</button><button class="btn btn-ghost btn-sm" data-action="editToolPreset" data-action-args="${args}" title="${escapeHtml(t('common.edit', '编辑'))}">✎</button><button class="btn btn-danger btn-sm" data-action="deleteToolPreset" data-action-args="${args}" title="${escapeHtml(t('common.delete', '删除'))}">×</button></span>`;
  }).join('');
  root.innerHTML = `<span style="font-size:12px;color:var(--muted);margin-right:6px">${escapeHtml(t('tools.preset', '工具预设'))}</span><button class="${active ? 'btn btn-ghost btn-sm' : 'btn btn-primary btn-sm'}" data-action="selectToolPreset" data-action-args='[""]'>${escapeHtml(t('tools.global_default', '全局默认'))}</button>${buttons || `<span style="font-size:12px;color:var(--muted);margin-left:6px">${escapeHtml(t('tools.no_presets', '尚无预设'))}</span>`}`;
  // These controls are rendered after the page fragment has received its
  // initial binding, so bind the newly-created action buttons explicitly.
  bindPageActions(root);
}

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
  const presetName = _toolCurrentPreset();
  const preset = (_toolsControl.tool_presets || []).find(item => item.name === presetName);
  const exposed = new Set(preset ? preset.tools : (_toolsControl.global_default_tools || []));
  const rows = (_toolsControl.tools || []).map(tool => {
    const frozen = tool.frozen === true;
    const execution = `<label class="checkbox-row"><input type="checkbox" data-tool="${escapeHtml(tool.name)}" ${tool.execution_enabled ? 'checked' : ''} ${frozen ? 'disabled' : ''} onchange="saveToolExecution(this.dataset.tool)"><span>${escapeHtml(frozen ? t('tools.frozen_execution', '冻结（需 Intiface opt-in）') : t('tools.execution_enabled', '全局执行'))}</span></label>`;
    const exposure = frozen ? `<span style="color:var(--muted)">${escapeHtml(t('tools.frozen', '冻结'))}</span>` : `<label class="checkbox-row"><input type="checkbox" data-tool-exposure="${escapeHtml(tool.name)}" ${exposed.has(tool.name) ? 'checked' : ''}><span>${escapeHtml(t('tools.expose', '在此模型中暴露'))}</span></label>`;
    return `<tr><td><strong><code>${escapeHtml(tool.name)}</code></strong><div style="font-size:12px;color:var(--muted)">${escapeHtml(_toolUiDescription(tool))}</div></td><td>${escapeHtml(_toolCategoryLabel(tool.category))}</td><td>${execution}</td><td>${exposure}</td></tr>`;
  }).join('');
  root.innerHTML = rows ? `<div class="tbl-wrap"><table><thead><tr><th>${escapeHtml(t('tools.name', '工具'))}</th><th>${escapeHtml(t('tools.category', '分类'))}</th><th>${escapeHtml(t('tools.execution', '执行'))}</th><th>${escapeHtml(t('tools.exposure', '模型暴露'))}</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<div class="empty">${escapeHtml(t('tools.empty', '没有已注册工具'))}</div>`;
  const count = document.getElementById('tools-exposure-count');
  if (count) {
    const n = exposed.size;
    count.textContent = t('tools.exposure_count', '{count} 个工具', { count: n });
    count.className = n > 16 ? 'badge badge-danger' : 'badge badge-success';
  }
}

function _renderToolsPage() {
  if (!_toolsControl) return;
  const selector = document.getElementById('tools-model-preset');
  if (!selector) return;
  if (!_toolsTargetModel || !(_toolsControl.model_presets || []).includes(_toolsTargetModel)) _toolsTargetModel = (_toolsControl.model_presets || [])[0] || '';
  selector.innerHTML = (_toolsControl.model_presets || []).map(name => `<option value="${escapeHtml(name)}" ${name === _toolsTargetModel ? 'selected' : ''}>${escapeHtml(name)}</option>`).join('');
  const note = document.getElementById('tools-binding-note');
  const mcpStatus = document.getElementById('tools-mcp-status');
  const intifaceStatus = document.getElementById('tools-intiface-status');
  const bound = _toolCurrentPreset();
  if (note) note.textContent = bound
    ? t('tools.bound_note', '当前模型绑定工具预设：{name}', { name: bound })
    : t('tools.global_note', '当前模型使用全局默认：可在此勾选并保存。');
  const saveButton = document.getElementById('tools-save-preset');
  if (saveButton) saveButton.textContent = bound
    ? t('tools.save_preset', '保存当前勾选')
    : t('tools.save_global_default', '保存全局默认');
  if (mcpStatus) mcpStatus.textContent = _toolsControl.mcp_enabled
    ? t('tools.mcp_status_enabled', 'MCP 全局状态：已启用（只读）')
    : t('tools.mcp_status_disabled', 'MCP 全局状态：未启用（只读）');
  if (intifaceStatus) intifaceStatus.textContent = _toolsControl.intiface_opt_in
    ? t('tools.intiface_enabled', 'Intiface 硬件能力：已启用（仍受所有者、危险模式与硬件安全闸保护）')
    : t('tools.intiface_frozen', 'Intiface 硬件能力：冻结（默认关闭；toy_* 工具不会进入聊天、自主唤醒或自主管理能力）');
  _toolsPresetButtons();
  _renderPathExposure();
  _renderToolsRegistry();
}

async function loadToolsPage() {
  loadXiaohongshuSettings();
  const root = document.getElementById('tools-registry-list');
  if (root) root.innerHTML = `<div class="loading">${escapeHtml(t('common.loading', '加载中…'))}</div>`;
  try { _toolsControl = await api('GET', '/settings/tools'); _renderToolsPage(); }
  catch (error) { if (root) root.innerHTML = `<div class="empty">${escapeHtml(t('common.load_failed', '加载失败: {error}', { error: error.message }))}</div>`; }
}

function showXiaohongshuSettings(data) {
  document.getElementById('xhs-enabled').checked = data.enabled;
  document.getElementById('xhs-reader-url').value = data.reader_url || '';
  document.getElementById('xhs-max-comments').value = data.max_comments;
  document.getElementById('xhs-max-images').value = data.max_images;
  document.getElementById('xhs-settings-status').textContent = !data.enabled ? '已关闭' :
    !data.configured ? '已开启，但尚未配置读取服务' : '配置已就绪；远端连接与登录尚未验证，仍需允许模型使用此工具。';
}

async function loadXiaohongshuSettings() {
  try { showXiaohongshuSettings(await api('GET', '/settings/xiaohongshu')); }
  catch (e) { document.getElementById('xhs-settings-status').textContent = '读取小红书设置失败'; }
}

async function saveXiaohongshuSettings() {
  try {
    showXiaohongshuSettings(await api('PUT', '/settings/xiaohongshu', {
      enabled: document.getElementById('xhs-enabled').checked,
      reader_url: document.getElementById('xhs-reader-url').value.trim(),
      max_comments: Number(document.getElementById('xhs-max-comments').value),
      max_images: Number(document.getElementById('xhs-max-images').value),
    }));
    toast('小红书设置已保存', 'ok');
  } catch (e) { document.getElementById('xhs-settings-status').textContent = `保存失败：${e.message || e}`; }
}

function changeToolsModelPreset(name) { _toolsTargetModel = name; _renderToolsPage(); }

async function selectToolPreset(name) {
  if (!_toolsTargetModel) return;
  try {
    _toolsControl = await api('PUT', '/settings/tools', _toolPresetPayload(undefined, name));
    toast(t('tools.binding_saved', '模型工具预设已切换并热更新'), 'ok');
    _renderToolsPage();
  } catch (error) { toast(error.message, 'err'); }
}

async function _saveNamedToolPreset(name, oldName = '') {
  const presets = (_toolsControl.tool_presets || []).filter(item => item.name !== oldName);
  presets.push({ name, tools: _toolCheckedNames() });
  _toolsControl = await api('PUT', '/settings/tools', _toolPresetPayload(presets, name));
  _renderToolsPage();
}

async function createToolPreset() {
  const name = prompt(t('tools.name_prompt', '请输入工具预设名称：'))?.trim();
  if (!name) return;
  try { await _saveNamedToolPreset(name); toast(t('tools.preset_saved', '工具预设已保存并应用到当前模型'), 'ok'); }
  catch (error) { toast(error.message, 'err'); }
}

async function saveToolPreset() {
  const name = _toolCurrentPreset();
  if (!name) return saveGlobalToolDefault();
  try { await _saveNamedToolPreset(name, name); toast(t('tools.preset_saved', '工具预设已保存并应用到当前模型'), 'ok'); }
  catch (error) { toast(error.message, 'err'); }
}

async function saveGlobalToolDefault() {
  try {
    _toolsControl = await api('PUT', '/settings/tools', { global_default_tools: _toolCheckedNames() });
    toast(t('tools.global_saved', '全局默认工具已保存并热更新'), 'ok');
    _renderToolsPage();
  } catch (error) { toast(error.message, 'err'); }
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

async function editToolPreset(name) {
  const next = prompt(t('tools.rename_prompt', '修改工具预设名称：'), name)?.trim();
  if (!next) return;
  try { await _saveNamedToolPreset(next, name); toast(t('tools.preset_saved', '工具预设已保存并应用到当前模型'), 'ok'); }
  catch (error) { toast(error.message, 'err'); }
}

async function deleteToolPreset(name) {
  if (!confirm(t('tools.delete_confirm', '删除工具预设“{name}”？', { name }))) return;
  try {
    const presets = (_toolsControl.tool_presets || []).filter(item => item.name !== name);
    _toolsControl = await api('PUT', '/settings/tools', _toolPresetPayload(presets));
    toast(t('tools.preset_deleted', '工具预设已删除'), 'ok');
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
