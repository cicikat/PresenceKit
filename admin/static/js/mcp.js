let _mcpImport = null;
let _mcpConsoleData = null;
let _mcpConsolePending = null;
const MCP_EXPANDED_SERVERS_STORAGE_KEY = 'qq_admin_mcp_expanded_servers_v1';
const MCP_DEFAULT_HEADERS = Object.freeze({});

function _loadMcpExpandedServers() {
  try {
    const saved = JSON.parse(localStorage.getItem(MCP_EXPANDED_SERVERS_STORAGE_KEY) || '[]');
    return new Set(Array.isArray(saved) ? saved.filter(name => typeof name === 'string') : []);
  } catch (_) {
    return new Set();
  }
}

function _persistMcpExpandedServers() {
  try {
    localStorage.setItem(MCP_EXPANDED_SERVERS_STORAGE_KEY, JSON.stringify([..._mcpExpandedServers]));
  } catch (_) { /* 私密模式或受限存储时仍保持默认收起。 */ }
}

// 没有已保存状态的 server 默认收起。
const _mcpExpandedServers = _loadMcpExpandedServers();

function _mcpCsvValues(value) {
  return [...new Set(String(value || '').split(',').map(item => item.trim()).filter(Boolean))];
}

function _mcpHeaderEditorOptions() {
  return {
    allowEmpty: true,
    keyPlaceholder: t('mcp.header.name_placeholder', 'Authorization'),
    valuePlaceholder: t('mcp.header.value_placeholder', 'Bearer ${ENV_VAR}'),
    labels: {
      key: t('mcp.header.name', '名称 / Name'),
      value: t('mcp.header.value', '值 / Value'),
      type: t('mcp.header.type', '类型 / Type'),
    },
  };
}

function _mcpRaw(value, fallback = '') {
  const text = String(value ?? '').trim();
  return `<span class="i18n-raw">${escapeHtml(text || fallback)}</span>`;
}

function _mcpRemoteDescription(tool) {
  const description = String(tool?.description || '').trim();
  return `<small class="i18n-raw" style="display:block;color:var(--muted)"><span class="mcp-remote-label">${escapeHtml(t('mcp.remote_description_label', '服务器提供的原始描述 / Original description from server'))}</span>：${escapeHtml(description || t('mcp.remote_description_empty', '（服务器未提供描述 / server provided no description）'))}</small>`;
}

function _mcpDiscoveredDomains(server) {
  const domains = new Set();
  for (const tool of [...(server?.runtime?.tools || []), ...(server?.tool_states || [])]) {
    for (const domain of [...(tool.remote_domains || []), ...(tool.final_domains || [])]) {
      const value = String(domain || '').trim();
      if (value) domains.add(value);
    }
  }
  return [...domains].sort((left, right) => left.localeCompare(right));
}

function _mcpDomainChips(server, selector) {
  const selected = new Set(selector.domains || []);
  const domains = _mcpDiscoveredDomains(server);
  if (!domains.length) return `<small style="display:block;color:var(--muted)">${escapeHtml(t('mcp.metadata.no_discovered_domains', '暂未发现服务器领域；仍可手动填写文档中的领域。'))}</small>`;
  return `<div class="mcp-domain-chips" style="display:flex;gap:6px;flex-wrap:wrap;margin:6px 0">${domains.map(domain => `<label class="mcp-domain-chip"><input type="checkbox" data-mcp-domain-choice="${escapeHtml(server.name)}" value="${escapeHtml(domain)}" ${selected.has(domain) ? 'checked' : ''}><span class="i18n-raw">${escapeHtml(domain)}</span></label>`).join('')}</div>`;
}

function _renderMcpManualDomainChips(input, server) {
  const root = document.getElementById(`mcp-domain-manual-chips-${server.name}`);
  if (!root) return;
  const discovered = new Set(_mcpDiscoveredDomains(server));
  const manual = _mcpCsvValues(input.value).filter(domain => !discovered.has(domain));
  root.innerHTML = manual.length
    ? manual.map(domain => `<span class="mcp-domain-chip i18n-raw">${escapeHtml(domain)}</span>`).join('')
    : '';
}

function _bindMcpDomainSelector(section, server) {
  const input = section.querySelector(`#mcp-domain-selector-${CSS.escape(server.name)}`);
  if (!input) return;
  input.addEventListener('input', () => _renderMcpManualDomainChips(input, server));
  _renderMcpManualDomainChips(input, server);
}

function _mcpDraftFromForm() {
  const headers = readKeyValueEditor('mcp-import-headers');
  const draft = {
    name: document.getElementById('mcp-import-name').value.trim(),
    url: document.getElementById('mcp-import-url').value.trim(),
    transport: document.getElementById('mcp-import-transport').value,
    use_proxy: document.getElementById('mcp-import-use-proxy').checked,
    headers,
    enabled: document.getElementById('mcp-import-enabled').checked,
    tool_timeout_s: Number(document.getElementById('mcp-import-timeout').value || 30),
    allow_tools: [],
  };
  const namespace = document.getElementById('mcp-import-metadata-namespace').value.trim();
  if (namespace) {
    draft.metadata_mapping = {
      namespace,
      schema_versions: _mcpCsvValues(document.getElementById('mcp-import-metadata-versions').value)
        .map(value => /^\d+$/.test(value) ? Number(value) : value),
      schema_version_field: document.getElementById('mcp-import-metadata-version-field').value.trim() || 'schema_version',
      domains_field: document.getElementById('mcp-import-metadata-domains-field').value.trim() || 'domains',
      interaction_field: document.getElementById('mcp-import-metadata-interaction-field').value.trim() || 'interaction',
    };
  }
  return draft;
}

function _mcpSuggestion(tool) {
  return tool?.suggestion || { effect: null, source: 'unknown', status: 'confirmation_required', high_risk: false };
}

function _mcpEffectBadge(suggestion, policyStatus = '') {
  const effect = suggestion.effect || t('mcp.status.pending', '待确认');
  const source = suggestion.source === 'name_description'
    ? t('mcp.suggestion.name_description', '名称/描述建议')
    : suggestion.source || t('mcp.status.pending', '待确认');
  const risk = suggestion.high_risk ? ` ${t('mcp.status.high_risk', '高风险')}` : '';
  const pending = policyStatus && policyStatus !== 'confirmed' && policyStatus !== 'legacy_allowed'
    ? ` ${t('mcp.status.pending', '待确认')}` : '';
  return `<small style="display:block;color:${suggestion.high_risk ? 'var(--danger)' : 'var(--muted)'}">${escapeHtml(t('mcp.suggestion.summary', '建议 {effect} · {source}{risk}{pending}', {effect, source, risk, pending}))}</small>`;
}

function _mcpMetadataSummary(tool) {
  const remote = (tool?.remote_domains || []).join(', ');
  const finalDomains = (tool?.final_domains || []).join(', ');
  const version = tool?.metadata_schema_version == null ? '-' : String(tool.metadata_schema_version);
  const label = key => escapeHtml(t(key, key));
  return `<small style="display:block;color:var(--muted)">${label('mcp.metadata.remote_domains')}：${_mcpRaw(remote, t('mcp.metadata.unclassified', '未分类'))} · ${label('mcp.metadata.final_domains')}：${_mcpRaw(finalDomains, t('mcp.metadata.unclassified', '未分类'))} · ${label('mcp.metadata.interaction')}：${_mcpRaw(tool?.remote_interaction, 'unknown')} · ${label('mcp.metadata.status')}：${_mcpRaw(tool?.metadata_status, 'absent')} · ${label('mcp.metadata.schema_version')}：${_mcpRaw(version, '-')}</small>`;
}

function _mcpGroupTools(tools) {
  const groups = {};
  (tools || []).forEach(tool => {
    const prefix = String(tool.name || '').split('_')[0] || '其他';
    (groups[prefix] ||= []).push(tool);
  });
  return Object.entries(groups).map(([prefix, entries]) => `<div style="margin-top:8px"><strong class="i18n-raw" style="font-size:12px;color:var(--muted)">${escapeHtml(prefix)}</strong><div style="display:grid;gap:5px;margin-top:4px">${entries.map(tool => `<label class="checkbox-row"><input type="checkbox" data-mcp-import-tool="${escapeHtml(tool.name)}"><span><code class="i18n-raw">${escapeHtml(tool.name)}</code>${_mcpRemoteDescription(tool)}${_mcpEffectBadge(_mcpSuggestion(tool))}${_mcpMetadataSummary(tool)}</span></label>`).join('')}</div></div>`).join('');
}

async function loadMcpPage() {
  const serversEl = document.getElementById('mcp-servers');
  if (!serversEl) return;
  renderKeyValueEditor('mcp-import-headers', MCP_DEFAULT_HEADERS, _mcpHeaderEditorOptions());
  const hadContent = Boolean(serversEl.querySelector('[data-mcp-server-body], .empty'));
  const scrollY = window.scrollY;
  if (!hadContent) {
    serversEl.innerHTML = `<div class="loading">${escapeHtml(t('common.loading', '加载中…'))}</div>`;
  }
  try {
    const data = await api('GET', '/settings/mcp');
    document.getElementById('mcp-enabled').checked = !!data.enabled;
    serversEl.innerHTML = (data.servers || []).length
      ? (data.servers || []).map(_renderMcpServer).join('')
      : `<div class="empty">${escapeHtml(t('mcp.empty_servers', '尚未配置 MCP server。先填写 URL 并测试连接。'))}</div>`;
    document.querySelectorAll('[data-i18n="mcp.policy.help"]').forEach(element => {
      element.textContent = t('mcp.policy.help_v2', 'Mode guide: unrestricted is an administrator-selected unrestricted execution mode.');
    });
    _decorateMcpServerCards(serversEl, data.servers || []);
    bindPageActions(serversEl);
    _moveMcpSaveControls(serversEl);
    _setMcpConsoleData(data);
    await _loadMcpRecentCalls(data.servers || []);
    if (document.getElementById("mcp-llm-debug-enabled")) await loadMcpDebugRequests();
    if (hadContent) window.scrollTo(0, scrollY);
  } catch (e) { serversEl.innerHTML = `<div class="empty">${escapeHtml(e.message)}</div>`; }
}

function _mcpConsoleEligibleTools(server) {
  if (!server?.enabled || !server?.runtime?.connected) return [];
  return (server.tool_states || []).filter(tool => tool.allowlisted && tool.registered
    && (tool.policy_status === 'confirmed' || tool.policy_status === 'legacy_allowed'));
}

function _setMcpConsoleData(data) {
  _mcpConsoleData = data || null;
  _mcpConsolePending = null;
  const serverSelect = document.getElementById('mcp-console-server');
  if (!serverSelect) return;
  const servers = (_mcpConsoleData?.servers || []).filter(server => server.enabled && server.runtime?.connected);
  const selected = servers.some(server => server.name === serverSelect.value) ? serverSelect.value : (servers[0]?.name || '');
  serverSelect.innerHTML = servers.length
    ? servers.map(server => `<option value="${escapeHtml(server.name)}">${escapeHtml(server.name)}</option>`).join('')
    : `<option value="">${escapeHtml(t('mcp.console.no_server', '没有可用的已连接 MCP server。'))}</option>`;
  serverSelect.value = selected;
  _renderMcpConsoleTool();
  bindPageActions(document.getElementById('mcp-console-card'));
}

function _selectedMcpConsoleTool() {
  const serverName = document.getElementById('mcp-console-server')?.value || '';
  const toolName = document.getElementById('mcp-console-tool')?.value || '';
  const server = (_mcpConsoleData?.servers || []).find(item => item.name === serverName);
  return { server, tool: _mcpConsoleEligibleTools(server).find(item => item.name === toolName) };
}

function _renderMcpConsoleTool() {
  const serverSelect = document.getElementById('mcp-console-server');
  const toolSelect = document.getElementById('mcp-console-tool');
  const info = document.getElementById('mcp-console-tool-info');
  const schema = document.getElementById('mcp-console-schema');
  const invoke = document.getElementById('mcp-console-invoke');
  if (!serverSelect || !toolSelect || !info || !schema || !invoke) return;
  const server = (_mcpConsoleData?.servers || []).find(item => item.name === serverSelect.value);
  const tools = _mcpConsoleEligibleTools(server);
  const selected = tools.some(tool => tool.name === toolSelect.value) ? toolSelect.value : (tools[0]?.name || '');
  toolSelect.innerHTML = tools.length
    ? tools.map(tool => `<option value="${escapeHtml(tool.name)}">${escapeHtml(tool.name)}</option>`).join('')
    : `<option value="">${escapeHtml(t('mcp.console.no_tool', '该 server 没有可调用工具。'))}</option>`;
  toolSelect.value = selected;
  const tool = tools.find(item => item.name === selected);
  info.textContent = tool
    ? `${t('mcp.console.effect', 'effect')}: ${tool.effect || 'unclassified'}${tool.require_confirm ? ` · ${t('mcp.console.confirmation_required', 'confirmation required')}` : ''}\n` +
      `${t('mcp.console.domains', 'domains')}: ${(tool.final_domains || []).join(', ') || 'unclassified'} · ${t('mcp.console.interaction', 'interaction')}: ${tool.remote_interaction || 'unknown'}`
    : t('mcp.console.no_tool', '该 server 没有可调用工具。');
  schema.textContent = JSON.stringify(tool?.parameter_summary || {}, null, 2);
  renderKeyValueEditor('mcp-console-arguments', Object.fromEntries((tool?.parameter_summary?.properties || []).map(item => [item.name, ''])));
  invoke.disabled = !_mcpConsoleData?.enabled || !tool;
}

function changeMcpConsoleServer() {
  _mcpConsolePending = null;
  _renderMcpConsoleTool();
  clearMcpConsole();
}

function changeMcpConsoleTool() {
  _mcpConsolePending = null;
  clearMcpConsole();
  _renderMcpConsoleTool();
}

function _showMcpConsoleResult(text) {
  document.getElementById('mcp-console-result').textContent = text || '';
}

function clearMcpConsole() {
  _mcpConsolePending = null;
  document.getElementById('mcp-console-confirmation').hidden = true;
  document.getElementById('mcp-console-confirm-message').textContent = '';
  _showMcpConsoleResult('');
}

async function refreshMcpConsole() {
  try {
    _setMcpConsoleData(await api('GET', '/settings/mcp'));
    toast(t('mcp.console.refreshed', 'MCP 工具状态已刷新'), 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

async function invokeMcpConsole() {
  const { server, tool } = _selectedMcpConsoleTool();
  if (!server || !tool) return;
  let arguments_;
  try {
    arguments_ = readKeyValueEditor('mcp-console-arguments');
  } catch (e) {
    _showMcpConsoleResult(`${t('mcp.console.arguments_error', '参数 JSON 无效：')}${e.message}`);
    return;
  }
  clearMcpConsole();
  try {
    const result = await api('POST', '/settings/mcp/console/invoke', { server: server.name, tool: tool.name, arguments: arguments_ });
    if (result.status === 'confirmation_required') {
      _mcpConsolePending = result;
      document.getElementById('mcp-console-confirm-message').textContent = result.confirmation_message || '';
      document.getElementById('mcp-console-confirmation').hidden = false;
      _showMcpConsoleResult(`audit_id: ${result.audit_id}`);
      return;
    }
    _showMcpConsoleResult(`audit_id: ${result.audit_id}\n\n${result.result || t('mcp.console.no_result', '调用完成，无文本结果。')}`);
  } catch (e) { _showMcpConsoleResult(`${t('mcp.console.invoke_error', '调用被拒绝或失败：')}${e.message}`); }
}

function addMcpHeader() { addKeyValueRow('mcp-import-headers', _mcpHeaderEditorOptions()); }
function addMcpConsoleArgument() { addKeyValueRow('mcp-console-arguments'); }

async function confirmMcpConsole() {
  if (!_mcpConsolePending?.confirmation_id) return;
  const confirmationId = _mcpConsolePending.confirmation_id;
  clearMcpConsole();
  try {
    const result = await api('POST', '/settings/mcp/console/confirm', { confirmation_id: confirmationId });
    _showMcpConsoleResult(`audit_id: ${result.audit_id}\n\n${result.result || t('mcp.console.no_result', '调用完成，无文本结果。')}`);
  } catch (e) { _showMcpConsoleResult(`${t('mcp.console.confirm_error', '确认调用失败：')}${e.message}`); }
}

function _moveMcpSaveControls(root) {
  root.querySelectorAll('[data-action="saveMcpServer"]').forEach(button => {
    const header = button.closest('section')?.querySelector('.card-header');
    const enabledControl = header?.querySelector('[id^="mcp-server-enabled-"]')?.closest('label');
    if (!header || !enabledControl) return;
    button.style.margin = '0 5px 0 auto';
    enabledControl.before(button);
  });
}

async function loadMcpDebugRequests() {
  const out = document.getElementById('mcp-llm-debug-entries');
  if (!out) return;
  try {
    const [settings, snapshots] = await Promise.all([
      api('GET', '/llm-debug-requests'),
      api('GET', '/observability/llm-debug-requests?limit=10'),
    ]);
    document.getElementById('mcp-llm-debug-enabled').checked = !!settings.enabled;
    document.getElementById('mcp-llm-debug-keep-days').value = String(settings.keep_days || 1);
    out.textContent = (snapshots.entries || []).length
      ? (snapshots.entries || []).map(entry => JSON.stringify(entry, null, 2)).join('\n\n')
      : t('mcp.llm_debug.empty', '暂无请求快照。开启后完成一次模型调用，再刷新此处。');
  } catch (e) {
    out.textContent = t('mcp.llm_debug.load_error', '读取调试快照失败: {error}', { error: e.message });
  }
}

async function saveMcpDebugRequests() {
  try {
    const keep_days = Number(document.getElementById('mcp-llm-debug-keep-days').value || 1);
    await api('PUT', '/llm-debug-requests', {
      enabled: document.getElementById('mcp-llm-debug-enabled').checked,
      keep_days,
    });
    toast(t('mcp.llm_debug.saved', 'LLM 请求调试设置已保存'), 'ok');
    if (document.getElementById("mcp-llm-debug-enabled")) await loadMcpDebugRequests();
  } catch (e) { toast(t('mcp.llm_debug.save_error', '保存调试设置失败: {error}', { error: e.message }), 'err'); }
}

async function clearMcpDebugRequests() {
  if (!confirm(t('mcp.llm_debug.clear_confirm', '确定清理所有 LLM 请求调试快照吗？此操作不可恢复。'))) return;
  try {
    await api('DELETE', '/observability/llm-debug-requests');
    toast(t('mcp.llm_debug.cleared', 'LLM 请求调试快照已清理'), 'ok');
    if (document.getElementById("mcp-llm-debug-enabled")) await loadMcpDebugRequests();
  } catch (e) { toast(t('mcp.llm_debug.clear_error', '清理调试快照失败: {error}', { error: e.message }), 'err'); }
}

function _mcpServerTools(name) {
  return [...document.querySelectorAll(`[data-mcp-server="${name}"]:checked`)].map(el => el.value);
}

function _mcpPresetButtons(server) {
  const active = server.active_tool_preset || '';
  const args = escapeHtml(JSON.stringify([server.name, '']));
  const custom = `<button class="btn btn-ghost btn-sm" data-action="selectMcpToolPreset" data-action-args="${args}">${escapeHtml(t('mcp.preset.custom', '自定义'))}</button>`;
  const presets = (server.tool_presets || []).map(preset => {
    const actionArgs = escapeHtml(JSON.stringify([server.name, preset.name]));
    const klass = preset.name === active ? 'btn btn-primary btn-sm' : 'btn btn-ghost btn-sm';
    return `<button class="${klass}" data-action="selectMcpToolPreset" data-action-args="${actionArgs}">${escapeHtml(preset.name)}</button>`;
  }).join('');
  return `<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center"><span style="font-size:12px;color:var(--muted)">${escapeHtml(t('mcp.preset.selected', '工具预设'))}</span>${custom}${presets || `<span style="font-size:12px;color:var(--muted)">${escapeHtml(t('mcp.preset.none', '还没有预设'))}</span>`}</div>`;
}

function _mcpPolicyControl(server, tool, allowlisted) {
  const state = (server.tool_states || []).find(item => item.name === tool.name) || {};
  const suggestion = state.suggestion || _mcpSuggestion(tool);
  const badge = _mcpEffectBadge(suggestion, state.policy_status || '');
  if (!allowlisted) return badge;
  const selected = state.policy?.effect || state.effect || suggestion.effect || 'write';
  const options = ['read', 'write', 'actuate', 'emergency', 'unrestricted'].map(effect =>
    `<option value="${effect}" ${effect === selected ? 'selected' : ''}>${escapeHtml(t(`mcp.policy.effect.${effect}`, effect))}</option>`
  ).join('');
  const args = escapeHtml(JSON.stringify([server.name, tool.name]));
  const selectId = `mcp-policy-effect-${server.name}-${tool.name}`;
  const confirmId = `mcp-policy-confirm-${server.name}-${tool.name}`;
  const requireConfirm = state.policy?.require_confirm === true || state.require_confirm === true;
  return `${badge}<span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:4px"><span style="font-size:12px;color:var(--muted)">${escapeHtml(t('mcp.policy.mode', '模式'))}</span><select id="${escapeHtml(selectId)}" style="max-width:116px">${options}</select><label class="checkbox-row"><input type="checkbox" id="${escapeHtml(confirmId)}" ${requireConfirm ? 'checked' : ''}><span>${escapeHtml(t('mcp.policy.require_confirm', '每次执行前确认'))}</span></label><button class="btn btn-ghost btn-sm" data-action="saveMcpToolPolicy" data-action-args="${args}">${escapeHtml(t('mcp.policy.save_mode', '保存模式'))}</button></span>`;
}

function _mcpMetadataControl(server, tool) {
  const state = (server.tool_states || []).find(item => item.name === tool.name) || {};
  const override = state.metadata_override || {};
  const mode = override.mode || 'remote';
  const domains = (override.domains || []).join(', ');
  const args = escapeHtml(JSON.stringify([server.name, tool.name]));
  const modeId = `mcp-metadata-mode-${server.name}-${tool.name}`;
  const domainsId = `mcp-metadata-domains-${server.name}-${tool.name}`;
  const options = [
    ['remote', t('mcp.metadata.use_remote', '使用远端分类')],
    ['override', t('mcp.metadata.use_override', '本地覆盖分类')],
    ['ignore', t('mcp.metadata.ignore_remote', '忽略远端分类')],
  ].map(([value, label]) => `<option value="${value}" ${value === mode ? 'selected' : ''}>${escapeHtml(label)}</option>`).join('');
  const yesNo = value => value ? t('common.yes', '是') : t('common.no', '否');
  const stateLabel = key => escapeHtml(t(key, key));
  const stateBadges = [
    `${stateLabel('mcp.state.discovered')}: ${yesNo(state.discovered)}`,
    `${stateLabel('mcp.state.authorized')}: ${yesNo(state.authorized)}`,
    `${stateLabel('mcp.state.session_exposed')}: ${yesNo(state.session_exposed)}`,
    `${stateLabel('mcp.state.remote_category')}: ${_mcpRaw((state.remote_domains || []).join(', '), t('mcp.metadata.unclassified', '未分类'))}`,
    `${stateLabel('mcp.state.final_category')}: ${_mcpRaw((state.final_domains || []).join(', '), t('mcp.metadata.unclassified', '未分类'))}`,
    `${stateLabel('mcp.state.classification_status')}: ${_mcpRaw(state.metadata_status, 'absent')}`,
  ].join(' · ');
  return `${_mcpMetadataSummary({
    remote_domains: state.remote_domains, final_domains: state.final_domains,
    remote_interaction: state.remote_interaction, metadata_status: state.metadata_status,
    metadata_schema_version: state.metadata_schema_version,
  })}<small style="display:block;color:var(--muted)">${stateBadges}</small><span style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:4px"><select id="${escapeHtml(modeId)}" aria-label="${escapeHtml(t('mcp.metadata.override_mode', '分类处理方式'))}" style="max-width:190px">${options}</select><input type="text" id="${escapeHtml(domainsId)}" value="${escapeHtml(domains)}" placeholder="calendar, health" aria-label="${escapeHtml(t('mcp.metadata.override_domains', '本地领域覆盖'))}" style="max-width:220px"><button class="btn btn-ghost btn-sm" data-action="saveMcpMetadataOverride" data-action-args="${args}">${escapeHtml(t('mcp.metadata.save', '保存分类'))}</button></span>`;
}

function _mcpMetadataMappingControls(server) {
  const mapping = server.metadata_mapping || {};
  const selector = server.domain_selector || {};
  const serverName = escapeHtml(server.name);
  const domains = (selector.domains || []).join(', ');
  return `<details class="admin-inline-041" style="margin-top:10px"><summary>${escapeHtml(t('mcp.metadata.mapping_editor', '分类映射与筛选'))}</summary><p class="admin-inline-144">${escapeHtml(t('mcp.metadata.mapping_help', '部分 server 会在 metadata 里声明领域。映射只告诉 PresenceKit 如何读取字段；普通 MCP 或没有 schema 的 server 不需要配置。远端分类不会授予权限。'))}</p><div class="form-row"><label class="field"><span><span>${escapeHtml(t('mcp.metadata.namespace', '命名空间'))}</span> <code>namespace</code></span><input type="text" id="mcp-metadata-namespace-${serverName}" value="${escapeHtml(mapping.namespace || '')}" placeholder="io.example/tool"></label><label class="field"><span><span>${escapeHtml(t('mcp.metadata.schema_versions', '支持的 schema 版本'))}</span> <code>schema_versions</code></span><input type="text" id="mcp-metadata-versions-${serverName}" value="${escapeHtml((mapping.schema_versions || [1]).join(', '))}"></label></div><div class="form-row"><label class="field"><span><span>${escapeHtml(t('mcp.metadata.schema_version_field', '版本字段'))}</span> <code>schema_version_field</code></span><input type="text" id="mcp-metadata-version-field-${serverName}" value="${escapeHtml(mapping.schema_version_field || 'schema_version')}"></label><label class="field"><span><span>${escapeHtml(t('mcp.metadata.domains_field', '领域字段'))}</span> <code>domains_field</code></span><input type="text" id="mcp-metadata-domains-field-${serverName}" value="${escapeHtml(mapping.domains_field || 'domains')}"></label><label class="field"><span><span>${escapeHtml(t('mcp.metadata.interaction_field', '交互字段'))}</span> <code>interaction_field</code></span><input type="text" id="mcp-metadata-interaction-field-${serverName}" value="${escapeHtml(mapping.interaction_field || 'interaction')}"></label></div><div class="form-row"><label class="field"><span>${escapeHtml(t('mcp.metadata.selector_domains', '筛选 domains / Domains'))}</span>${_mcpDomainChips(server, selector)}<div id="mcp-domain-manual-chips-${serverName}" class="mcp-domain-chips" style="display:flex;gap:6px;flex-wrap:wrap"></div><input type="text" id="mcp-domain-selector-${serverName}" value="${escapeHtml(domains)}" placeholder="calendar, health, files, hardware" aria-describedby="mcp-domain-selector-help-${serverName}"></label><label class="checkbox-row"><input id="mcp-domain-include-unclassified-${serverName}" type="checkbox" ${selector.include_unclassified !== false ? 'checked' : ''}><span>${escapeHtml(t('mcp.metadata.include_unclassified', '包含未分类工具'))}</span></label></div><small id="mcp-domain-selector-help-${serverName}" style="display:block;color:var(--muted)">${escapeHtml(t('mcp.metadata.selector_hint', '筛选只会收窄已授权的 MCP 工具；留空表示不筛选；它不能扩大权限。'))} ${escapeHtml(t('mcp.metadata.domain_examples', '示例：calendar、health、files、hardware。'))}</small><small style="display:block;color:var(--muted)">${escapeHtml(t('mcp.metadata.include_unclassified_help', '关闭“包含未分类工具”会排除没有最终领域的工具；开启则保留它们，但不会改变授权。'))}</small></details>`;
}

function _mcpBulkControls(server, toolCount) {
  const connected = !!server.runtime?.connected;
  const available = connected && toolCount > 0;
  const disabled = available ? '' : ' disabled';
  const defaultArgs = escapeHtml(JSON.stringify([server.name, 'default']));
  const unrestrictedArgs = escapeHtml(JSON.stringify([server.name, 'unrestricted']));
  const reason = available
    ? ''
    : `<small style="display:block;color:var(--muted);margin-top:5px">${escapeHtml(
      connected
        ? t('mcp.bulk.no_tools', 'No tools discovered')
        : t('mcp.bulk.not_connected', 'Server is not connected'),
    )}</small>`;
  return `<div data-mcp-bulk-server="${escapeHtml(server.name)}" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px"><button class="btn btn-primary btn-sm" data-action="bulkAuthorizeMcpServer" data-action-args="${defaultArgs}"${disabled}>${escapeHtml(t('mcp.bulk.default', 'Authorize all with defaults'))}</button><button class="btn btn-danger btn-sm" data-action="bulkAuthorizeMcpServer" data-action-args="${unrestrictedArgs}"${disabled}>${escapeHtml(t('mcp.bulk.unrestricted', 'Authorize all unrestricted'))}</button>${reason}</div>`;
}

function _decorateMcpServerCards(root, servers) {
  (servers || []).forEach(server => {
    const section = root.querySelector(`#mcp-server-enabled-${server.name}`)?.closest('section');
    if (!section) return;
    const header = section.querySelector('.card-header');
    if (header) header.insertAdjacentHTML('afterend', _mcpBulkControls(server, (server.runtime || {}).tools?.length || 0));
    _bindMcpDomainSelector(section, server);
    section.querySelectorAll('option[value="unrestricted"]').forEach(option => {
      option.textContent = t('mcp.policy.effect.unrestricted', 'unrestricted · unrestricted execution');
    });
  });
}

function _mcpReloadToastKind(status) {
  return status === 'reloaded' ? 'ok' : 'err';
}

function _mcpReloadToastText(status, {reloaded, connectionFailed, restartRequired}) {
  if (status === 'connection_failed') return connectionFailed;
  if (status === 'restart_required') return restartRequired;
  return reloaded;
}

async function bulkAuthorizeMcpServer(name, mode) {
  if (mode === 'unrestricted' && !confirm(t(
    'mcp.bulk.unrestricted_confirm',
    'Authorize every currently discovered tool without per-tool confirmation? This writes unrestricted policies and enables retries for explicitly idempotent calls.',
  ))) return;
  const controls = [...document.querySelectorAll('[data-mcp-bulk-server]')]
    .filter(element => element.dataset.mcpBulkServer === name)
    .flatMap(element => [...element.querySelectorAll('button')]);
  controls.forEach(button => { button.disabled = true; });
  try {
    const result = await api('PATCH', `/settings/mcp/${encodeURIComponent(name)}`, { bulk_authorize: mode });
    const status = _mcpReloadToastKind(result.reload_status);
    const reloadText = _mcpReloadToastText(result.reload_status, {
      reloaded: t('mcp.bulk.reloaded', 'Hot reload completed.'),
      connectionFailed: t('mcp.bulk.connection_failed', '配置已保存并热重载，但未能连接。'),
      restartRequired: t('mcp.bulk.restart_required', 'Configuration saved; restart required.'),
    });
    const modeText = mode === 'unrestricted'
      ? t('mcp.bulk.unrestricted', 'Authorize all unrestricted')
      : t('mcp.bulk.default', 'Authorize all with defaults');
    toast(`${modeText}: ${t('mcp.bulk.completed', 'Processed {count} tools.', { count: result.processed_count || 0 })} ${reloadText}`, status);
    await loadMcpPage();
  } catch (e) {
    controls.forEach(button => { button.disabled = false; });
    toast(e.message, 'err');
  }
}

function _renderMcpServer(server) {
  const runtime = server.runtime || {};
  const tools = runtime.tools || [];
  const allow = new Set(server.allow_tools || []);
  const exposedCount = (server.tool_states || []).filter(tool => tool.session_exposed).length;
  const status = runtime.connected
    ? `<span class="badge badge-success">${escapeHtml(t('mcp.status.connected', '已连接'))}</span>`
    : runtime.last_init_ok === false
      ? `<span class="badge badge-danger">${escapeHtml(t('mcp.status.connection_failed', '连接失败'))}</span>`
      : `<span class="badge badge-warn">${escapeHtml(t('mcp.status.disconnected', '未连接'))}</span>`;
  const initError = runtime.last_init_error ? `<div style="color:var(--danger);font-size:12px;margin-top:6px">${escapeHtml(runtime.last_init_error)}</div>` : '';
  const grouped = Object.entries((tools || []).reduce((out, tool) => {
    const prefix = String(tool.name || '').split('_')[0] || '其他'; (out[prefix] ||= []).push(tool); return out;
  }, {})).map(([prefix, entries]) => `<div style="margin-top:9px"><strong class="i18n-raw" style="font-size:12px;color:var(--muted)">${escapeHtml(prefix)}</strong>${entries.map(tool => `<label class="checkbox-row" style="margin-top:5px"><input type="checkbox" data-mcp-server="${escapeHtml(server.name)}" value="${escapeHtml(tool.name)}" ${allow.has(tool.name) ? 'checked' : ''}><span><code class="i18n-raw">${escapeHtml(tool.name)}</code>${_mcpRemoteDescription(tool)}${_mcpPolicyControl(server, tool, allow.has(tool.name))}${_mcpMetadataControl(server, tool)}<small id="mcp-call-${escapeHtml(server.name)}-${escapeHtml(tool.name)}" style="display:block;color:var(--muted)">${escapeHtml(t('mcp.calls.loading', '调用记录加载中…'))}</small></span></label>`).join('')}</div>`).join('');
  const exposureWarn = exposedCount > 20 ? `<p style="font-size:12px;color:var(--danger);margin:8px 0">${escapeHtml(t('mcp.exposure_warning', '当前会暴露 {count} 个工具，超过单次暴露 ≤20 的安全红线；请收窄白名单或 domain selector。', {count: exposedCount}))}</p>` : '';
  const actionArgs = escapeHtml(JSON.stringify([server.name]));
  const collapsed = !_mcpExpandedServers.has(server.name);
  const collapseArgs = escapeHtml(JSON.stringify([server.name]));
  const saveLabel = escapeHtml(t('mcp.save_server', '设置'));
  const reconnectLabel = escapeHtml(t('mcp.reconnect', '重新连接'));
  const deleteLabel = escapeHtml(t('mcp.delete_server', '删除 server'));
  const proxyControl = server.is_local_url
    ? `<span style="font-size:12px;color:var(--muted)">${escapeHtml(t('mcp.proxy_direct', '本地地址：始终直连'))}</span>`
    : `<label class="checkbox-row" style="margin-top:8px"><input type="checkbox" id="mcp-server-use-proxy-${escapeHtml(server.name)}" ${server.use_proxy ? 'checked' : ''}><span>${escapeHtml(t('mcp.proxy_label', '远程 MCP 使用全局代理'))}</span></label>`;
  const presets = (server.tool_presets || []).map(preset => {
    const editArgs = escapeHtml(JSON.stringify([server.name, preset.name]));
    return `<div style="display:flex;gap:6px;align-items:center;margin-top:5px"><code>${escapeHtml(preset.name)}</code><span style="font-size:12px;color:var(--muted)">${preset.tools.length} ${escapeHtml(t('mcp.preset.tools', '个工具'))}</span><button class="btn btn-ghost btn-sm" data-action="editMcpToolPreset" data-action-args="${editArgs}">${escapeHtml(t('common.edit', '修改'))}</button><button class="btn btn-danger btn-sm" data-action="deleteMcpToolPreset" data-action-args="${editArgs}">${escapeHtml(t('common.delete', '删除'))}</button></div>`;
  }).join('');
  return `<section class="card" data-mcp-presets="${escapeHtml(JSON.stringify(server.tool_presets || []))}" style="background:var(--bg);margin:0 0 12px"><div class="card-header"><div style="display:flex;gap:8px;align-items:center"><button class="btn btn-ghost btn-sm" title="${escapeHtml(t('mcp.collapse', '展开/收起'))}" data-action="toggleMcpServerCollapsed" data-action-args="${collapseArgs}">${collapsed ? '▸' : '▾'}</button><h3>${escapeHtml(server.name)} ${status}</h3></div><label class="checkbox-row"><input type="checkbox" id="mcp-server-enabled-${escapeHtml(server.name)}" ${server.enabled ? 'checked' : ''}><span>${escapeHtml(t('common.enable', '启用'))}</span></label></div>${_mcpPresetButtons(server)}<div data-mcp-server-body style="display:${collapsed ? 'none' : 'block'};margin-top:10px"><div style="font-size:12px;color:var(--muted);word-break:break-all">${escapeHtml(server.url || server.transport)} · ${escapeHtml(t('mcp.timeout', '超时'))} ${Number(server.tool_timeout_s || 30)}s</div>${proxyControl}${Object.keys(server.headers || {}).length ? `<div style="font-size:12px;color:var(--muted);margin-top:5px">${escapeHtml(t('mcp.headers', '请求头'))}：${escapeHtml(Object.keys(server.headers).join(', '))}</div>` : ''}${_mcpMetadataMappingControls(server)}${initError}<p style="font-size:12px;color:var(--warn);margin:10px 0">${escapeHtml(server.require_local_policy ? t('mcp.allowlist.strict_hint', 'Strict mode: an empty allowlist authorizes no tools.') : t('mcp.allowlist.legacy_hint', 'Legacy mode: an empty allowlist allows all tools; select the smallest explicit allowlist.'))}${escapeHtml(t('mcp.metadata.local_policy_notice', '远端分类不授予权限，执行和确认由本地 policy 控制。'))}</p>${exposureWarn}${grouped || `<div class="empty">${escapeHtml(t('mcp.no_discovered_tools', '尚未发现工具；可切换启用状态以重连。'))}</div>`}<div style="margin-top:12px"><strong>${escapeHtml(t('mcp.preset.manage', '工具预设'))}</strong>${presets || `<div style="font-size:12px;color:var(--muted);margin-top:5px">${escapeHtml(t('mcp.preset.none', '还没有预设'))}</div>`}<button class="btn btn-ghost btn-sm" style="margin-top:8px" data-action="createMcpToolPreset" data-action-args="${actionArgs}">+ ${escapeHtml(t('mcp.preset.create', '新增预设'))}</button></div><div style="display:flex;gap:8px;margin-top:12px"><button class="btn btn-primary btn-sm" data-action="saveMcpServer" data-action-args="${actionArgs}">${saveLabel}</button><button class="btn btn-ghost btn-sm" data-action="reconnectMcpServer" data-action-args="${actionArgs}">${reconnectLabel}</button><button class="btn btn-danger btn-sm" data-action="deleteMcpServer" data-action-args="${actionArgs}">${deleteLabel}</button></div></div></section>`;
}

function toggleMcpServerCollapsed(name) {
  _mcpExpandedServers.has(name) ? _mcpExpandedServers.delete(name) : _mcpExpandedServers.add(name);
  _persistMcpExpandedServers();
  const card = document.getElementById(`mcp-server-enabled-${name}`)?.closest('section');
  if (!card) return;
  const expanded = _mcpExpandedServers.has(name);
  const body = card.querySelector('[data-mcp-server-body]');
  const button = card.querySelector('[data-action="toggleMcpServerCollapsed"]');
  if (body) body.style.display = expanded ? 'block' : 'none';
  if (button) button.textContent = expanded ? '▾' : '▸';
}

async function _saveMcpToolPresets(name, presets, active_tool_preset = undefined) {
  const body = { tool_presets: presets };
  if (active_tool_preset !== undefined) body.active_tool_preset = active_tool_preset;
  await api('PATCH', `/settings/mcp/${encodeURIComponent(name)}`, body);
  await loadMcpPage();
}

async function selectMcpToolPreset(name, presetName) {
  try { await _saveMcpToolPresets(name, undefined, presetName); toast(t('mcp.preset.selected_ok', '工具预设已切换并热重载'), 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}

function _mcpPresetsFromCard(name) {
  const card = document.getElementById(`mcp-server-enabled-${name}`)?.closest('section');
  return JSON.parse(card?.dataset.mcpPresets || '[]');
}

async function saveMcpToolPolicy(name, toolName) {
  try {
    const data = await api('GET', '/settings/mcp');
    const server = (data.servers || []).find(item => item.name === name);
    if (!server) throw new Error('MCP server 不存在');
    const state = (server.tool_states || []).find(item => item.name === toolName) || {};
    const effectControl = document.getElementById(`mcp-policy-effect-${name}-${toolName}`);
    const confirmControl = document.getElementById(`mcp-policy-confirm-${name}-${toolName}`);
    const effect = effectControl?.value || state.suggestion?.effect || 'write';
    const policy = { ...(server.tool_policy || {}) };
    policy[toolName] = { ...(state.policy || {}), effect };
    if (effect === 'unrestricted' || effect === 'emergency') {
      if (effect === 'unrestricted') policy[toolName].idempotent = true;
      policy[toolName].require_confirm = false;
    } else {
      policy[toolName].require_confirm = !!confirmControl?.checked;
    }
    const result = await api('PATCH', `/settings/mcp/${encodeURIComponent(name)}`, { tool_policy: policy });
    toast(_mcpReloadToastText(result.reload_status, {
      reloaded: t('mcp.policy.saved', '工具模式已保存并热重载'),
      connectionFailed: t('mcp.policy.connection_failed', '模式已保存并热重载，但未能连接'),
      restartRequired: t('mcp.policy.restart_required', '模式已保存，需要重启服务'),
    }), _mcpReloadToastKind(result.reload_status));
    await loadMcpPage();
  } catch (e) { toast(e.message, 'err'); }
}

async function saveMcpMetadataOverride(name, toolName) {
  try {
    const data = await api('GET', '/settings/mcp');
    const server = (data.servers || []).find(item => item.name === name);
    if (!server) throw new Error('MCP server 不存在');
    const mode = document.getElementById(`mcp-metadata-mode-${name}-${toolName}`)?.value || 'remote';
    const domains = _mcpCsvValues(document.getElementById(`mcp-metadata-domains-${name}-${toolName}`)?.value);
    const overrides = { ...(server.metadata_overrides || {}) };
    if (mode === 'remote') delete overrides[toolName];
    else if (mode === 'ignore') overrides[toolName] = { mode: 'ignore' };
    else {
      if (!domains.length) throw new Error(t('mcp.metadata.override_required', '本地覆盖至少需要一个 domain'));
      overrides[toolName] = { mode: 'override', domains };
    }
    const result = await api('PATCH', `/settings/mcp/${encodeURIComponent(name)}`, { metadata_overrides: overrides });
    toast(_mcpReloadToastText(result.reload_status, {
      reloaded: t('mcp.metadata.saved', '分类已保存并热重载'),
      connectionFailed: t('mcp.metadata.connection_failed', '分类已保存并热重载，但未能连接'),
      restartRequired: t('mcp.metadata.restart_required', '分类已保存，需要重启服务'),
    }), _mcpReloadToastKind(result.reload_status));
    await loadMcpPage();
  } catch (e) { toast(e.message, 'err'); }
}

async function createMcpToolPreset(name) {
  const presetName = prompt(t('mcp.preset.name_prompt', '请输入预设名称：'))?.trim();
  if (!presetName) return;
  const presets = _mcpPresetsFromCard(name);
  presets.push({ name: presetName, tools: _mcpServerTools(name) });
  try { await _saveMcpToolPresets(name, presets); toast(t('mcp.preset.created', '工具预设已保存'), 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}

async function editMcpToolPreset(name, oldName) {
  const presets = _mcpPresetsFromCard(name);
  const preset = presets.find(item => item.name === oldName);
  if (!preset) return;
  const newName = prompt(t('mcp.preset.rename_prompt', '修改预设名称：'), oldName)?.trim();
  if (!newName) return;
  preset.name = newName;
  preset.tools = _mcpServerTools(name);
  try { await _saveMcpToolPresets(name, presets, newName); toast(t('mcp.preset.updated', '工具预设已更新'), 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}

async function deleteMcpToolPreset(name, presetName) {
  if (!confirm(t('mcp.preset.delete_confirm', '删除工具预设“{name}”？', { name: presetName }))) return;
  const presets = _mcpPresetsFromCard(name).filter(item => item.name !== presetName);
  try { await _saveMcpToolPresets(name, presets, ''); toast(t('mcp.preset.deleted', '工具预设已删除'), 'ok'); }
  catch (e) { toast(e.message, 'err'); }
}

async function _loadMcpRecentCalls(servers) {
  await Promise.all((servers || []).flatMap(server => ((server.runtime || {}).tools || []).map(async tool => {
    const target = document.getElementById(`mcp-call-${server.name}-${tool.name}`);
    if (!target) return;
    try {
      const data = await getMcpRecentCalls(`mcp__${server.name}__${tool.name}`, 1);
      const entry = (data.entries || [])[0];
      target.textContent = entry
        ? t('mcp.calls.recent', '最近调用：{status} · {duration}ms', {status: t(entry.ok ? 'mcp.calls.success' : 'mcp.calls.failed', entry.ok ? '成功' : '失败'), duration: entry.duration_ms})
        : t('mcp.calls.empty', '暂无调用记录');
    } catch (_) { target.textContent = t('mcp.calls.unavailable', '调用记录不可用'); }
  })));
}

async function getMcpRecentCalls(caller, limit = 1) {
  return api('GET', `/observability/api-calls?caller=${encodeURIComponent(caller)}&limit=${Math.max(1, Math.min(limit, 30))}`);
}

async function saveMcpEnabled() {
  const checkbox = document.getElementById('mcp-enabled');
  try {
    const result = await api('PATCH', '/settings/mcp', { enabled: checkbox.checked });
    if (checkbox && result && typeof result.enabled === 'boolean') checkbox.checked = result.enabled;
    toast(t('mcp.enabled_saved', 'MCP 总开关已热同步'), 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

async function testMcpImport() {
  const out = document.getElementById('mcp-import-result');
  try {
    const draft = _mcpDraftFromForm();
    out.innerHTML = `<div class="loading">${escapeHtml(t('mcp.test_loading', '连接测试中…'))}</div>`;
    const data = await api('POST', '/settings/mcp/test', draft);
    _mcpImport = { draft, tools: data.tools || [] };
    const warning = _mcpImport.tools.length > 20 ? `<p style="color:var(--danger);margin-bottom:8px">${escapeHtml(t('mcp.test_warning', '⚠ 发现 {count} 个工具，超过单次暴露 ≤20 的安全红线；请只勾选必要工具。', { count: _mcpImport.tools.length }))}</p>` : '';
    out.innerHTML = `<p style="color:var(--success);margin-bottom:8px">${escapeHtml(t('mcp.test_success', '连接成功，发现 {count} 个工具。请选择要公开的最小集合：', { count: _mcpImport.tools.length }))}</p>${warning}${_mcpGroupTools(_mcpImport.tools)}`;
    document.getElementById('mcp-import-save').disabled = false;
  } catch (e) { _mcpImport = null; document.getElementById('mcp-import-save').disabled = true; out.innerHTML = `<div style="color:var(--danger)">${escapeHtml(e.message)}</div>`; }
}

async function importMcpServer() {
  if (!_mcpImport) return;
  const allow = [...document.querySelectorAll('[data-mcp-import-tool]:checked')].map(el => el.dataset.mcpImportTool);
  try {
    const result = await api('POST', '/settings/mcp/import', { ..._mcpImport.draft, allow_tools: allow });
    toast(_mcpReloadToastText(result.reload_status, {
      reloaded: t('mcp.imported', 'MCP server 已导入，已补齐默认本地策略'),
      connectionFailed: t('mcp.import_connection_failed', 'MCP server 已导入并热重载，但未能连接'),
      restartRequired: t('mcp.import_restart_required', 'MCP server 已导入，需要重启服务'),
    }), _mcpReloadToastKind(result.reload_status)); _mcpImport = null; document.getElementById('mcp-import-save').disabled = true; document.getElementById('mcp-import-result').innerHTML = ''; loadMcpPage();
  } catch (e) { toast(e.message, 'err'); }
}

async function saveMcpServer(name) {
  const enabled = document.getElementById(`mcp-server-enabled-${name}`).checked;
  const toolInputs = [...document.querySelectorAll(`[data-mcp-server="${name}"]`)];
  const proxyControl = document.getElementById(`mcp-server-use-proxy-${name}`);
  const body = { enabled };
  // A disabled server has no runtime-discovered checkboxes. Do not turn that
  // absence into an empty persisted whitelist when it is being re-enabled.
  if (toolInputs.length) body.allow_tools = toolInputs.filter(el => el.checked).map(el => el.value);
  if (proxyControl) body.use_proxy = proxyControl.checked;
  const namespace = document.getElementById(`mcp-metadata-namespace-${name}`)?.value.trim() || '';
  body.metadata_mapping = namespace ? {
    namespace,
    schema_versions: _mcpCsvValues(document.getElementById(`mcp-metadata-versions-${name}`)?.value)
      .map(value => /^\d+$/.test(value) ? Number(value) : value),
    schema_version_field: document.getElementById(`mcp-metadata-version-field-${name}`)?.value.trim() || 'schema_version',
    domains_field: document.getElementById(`mcp-metadata-domains-field-${name}`)?.value.trim() || 'domains',
    interaction_field: document.getElementById(`mcp-metadata-interaction-field-${name}`)?.value.trim() || 'interaction',
  } : null;
  const chipDomains = [...document.querySelectorAll(`[data-mcp-domain-choice="${CSS.escape(name)}"]:checked`)]
    .map(input => input.value);
  const manualDomains = document.getElementById(`mcp-domain-selector-${name}`)?.value || '';
  const selectorDomains = _mcpCsvValues([...chipDomains, manualDomains].join(', '));
  body.domain_selector = selectorDomains.length ? {
    domains: selectorDomains,
    include_unclassified: document.getElementById(`mcp-domain-include-unclassified-${name}`)?.checked !== false,
  } : null;
  try { const result = await api('PATCH', `/settings/mcp/${encodeURIComponent(name)}`, body); toast(_mcpReloadToastText(result.reload_status, {
    reloaded: t('mcp.server_saved', '{name} 已热重载', { name }),
    connectionFailed: t('mcp.server_connection_failed', '{name} 已热重载，但未能连接', { name }),
    restartRequired: t('mcp.server_restart_required', '{name} 已保存，需要重启服务', { name }),
  }), _mcpReloadToastKind(result.reload_status)); loadMcpPage(); }
  catch (e) { toast(e.message, 'err'); }
}

async function reconnectMcpServer(name) {
  const card = document.getElementById(`mcp-server-enabled-${name}`)?.closest('section');
  const button = card?.querySelector('[data-action="reconnectMcpServer"]');
  if (button) button.disabled = true;
  try {
    const result = await api('POST', `/settings/mcp/${encodeURIComponent(name)}/reconnect`);
    toast(_mcpReloadToastText(result.reload_status, {
      reloaded: t('mcp.server_saved', '{name} 已热重载', { name }),
      connectionFailed: t('mcp.server_connection_failed', '{name} 已热重载，但未能连接', { name }),
      restartRequired: t('mcp.server_restart_required', '{name} 已保存，需要重启服务', { name }),
    }), _mcpReloadToastKind(result.reload_status));
    await loadMcpPage();
  } catch (e) {
    toast(e.message, 'err');
    if (button) button.disabled = false;
  }
}

async function deleteMcpServer(name) {
  const question = t('mcp.delete_confirm', '确定删除 MCP server “{name}”吗？这会立即断开连接并移除它的工具。', { name });
  if (!confirm(question)) return;
  try {
    await api('DELETE', `/settings/mcp/${encodeURIComponent(name)}`);
    toast(t('mcp.delete_success', 'MCP server “{name}”已删除', { name }), 'ok');
    loadMcpPage();
  } catch (e) { toast(e.message, 'err'); }
}

// ══════════════════════════════════════════════════════════
//  Routing
// ══════════════════════════════════════════════════════════
