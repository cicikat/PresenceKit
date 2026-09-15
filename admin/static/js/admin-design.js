// Shared presentation only. Configuration and runtime decisions belong to the backend.
function designText(zh, en) { return window.AdminI18n?.getLanguage() === 'en' ? en : zh; }

function decorateSettingsPanels(page, root) {
  const pages = new Set(['model-routing', 'scheduler', 'autonomy-settings', 'embedding-config', 'mail-config', 'tts-config', 'network-config', 'device-policy', 'conversation-settings', 'output-settings', 'tools', 'mcp', 'runtime-config', 'coplay-config', 'diary-config']);
  if (!pages.has(page) || !root?.isConnected) return;
  const pageTitle = root.querySelector('.page-title');
  const navigationLabel = document.querySelector(`nav a[data-page="${page}"] [data-i18n]`);
  if (pageTitle && navigationLabel) {
    pageTitle.textContent = navigationLabel.textContent;
    pageTitle.dataset.i18n = navigationLabel.dataset.i18n;
  }
  if (!root.querySelector('.settings-search')) {
    const search = document.createElement('input'); search.type = 'search'; search.className = 'settings-search';
    search.placeholder = designText('查找功能、参数或函数…','Find a feature, parameter or function…');
    search.setAttribute('aria-label',search.placeholder);
    search.addEventListener('input',()=>{
      const query = search.value.trim().toLowerCase();
      root.querySelectorAll('details.settings-disclosure').forEach(details=>{
        const match = query && details.textContent.toLowerCase().includes(query);
        details.classList.toggle('settings-match',Boolean(match));
        if (match) details.open = true;
      });
    });
    root.querySelector('.page-title')?.insertAdjacentElement('afterend',search);
  }
  for (const editor of root.querySelectorAll('.vision-editor')) {
    if (editor.querySelector(':scope > .settings-footer')) continue;
    const footer = document.createElement('div'); footer.className = 'settings-footer';
    editor.querySelectorAll(':scope > .card-header button').forEach(button=>footer.append(button));
    if (footer.children.length) editor.append(footer);
  }
  for (const card of root.querySelectorAll('.card')) {
    if (card.querySelector(':scope > details.settings-disclosure')) continue;
    // Do not hide plain switches, lists, or already expandable connection editors.
    const fields = card.querySelectorAll('input:not([type=checkbox]):not([type=hidden]), select, textarea');
    if (fields.length < 2 || card.querySelector('.vision-editor, .card, details')) continue;
    const header = card.querySelector(':scope > .card-header');
    const title = header?.querySelector('h3') || card.querySelector(':scope > h3');
    if (!title) continue;
    const details = document.createElement('details'); details.className = 'settings-disclosure';
    const summary = document.createElement('summary');
    const heading = document.createElement('div'); heading.className = 'settings-disclosure-heading';
    heading.append(title);
    const subtitle = document.createElement('p'); subtitle.className = 'settings-current';
    const updateSummary = () => {
      const visibleValues = [...fields].filter(el => el.id && el.tagName !== 'TEXTAREA' && el.type !== 'password' && !/key|token|secret|password|url|host|email|owner/i.test(el.id) && el.value && !el.hidden).slice(0, 2);
      subtitle.textContent = visibleValues.map(el => {
        const label = el.closest('label')?.querySelector('span')?.textContent.trim() || el.id;
        const value = el.tagName === 'SELECT' ? el.selectedOptions[0]?.textContent : el.value;
        return `${label}: ${String(value || '').slice(0, 70)}`;
      }).join(' · ') || designText('展开填写具体参数', 'Expand to edit parameters');
    };
    updateSummary(); heading.append(subtitle); summary.append(heading);
    const functions = [...new Set([...card.querySelectorAll('[data-action]')].map(el=>el.dataset.action).filter(name=>name.startsWith('save')))];
    if (functions.length) {
      const names = document.createElement('small'); names.className = 'settings-functions'; names.textContent = functions.join(' · '); heading.append(names);
    }
    // Existing server-backed status nodes remain live, with their original IDs.
    for (const state of header?.querySelectorAll(':scope > span') || []) summary.append(state);
    const body = document.createElement('div'); body.className = 'settings-disclosure-body';
    const actions = document.createElement('div'); actions.className = 'settings-footer';
    for (const button of header?.querySelectorAll('button') || []) actions.append(button);
    if (header && !header.textContent.trim() && !header.querySelector('input,select')) header.remove();
    while (card.firstChild) body.append(card.firstChild);
    if (actions.children.length) body.append(actions);
    details.append(summary, body); card.append(details);
    details.addEventListener('toggle', updateSummary);
    body.addEventListener('change', updateSummary);
    // Browser find and keyboard search can expose collapsed parameter forms.
  }
}

function chainReason(reason) {
  const labels = {
    no_connected_mcp_server:['外部工具服务尚未连接','External tool servers are not connected'],
    no_enabled_mcp_server:['尚未启用外部工具服务','No external tool server enabled'],
    no_chat_preset:['尚未配置可用的聊天模型连接','No resolved chat model connection'],
    no_active_channel:['尚无在线消息通道','No active message channel'],
    scheduler_not_running:['调度器尚未运行','Scheduler is not running'],
    chat_preset_not_function_calling_or_unavailable:['聊天模型不可用或不支持工具调用','Chat preset unavailable or does not support tool calls'],
  };
  return labels[reason] ? designText(...labels[reason]) : t('overview.reason.'+reason,reason);
}

function stateCell(value) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return `<span class="${value ? 'state-pass' : 'state-stop'}">${value ? designText('是', 'Yes') : designText('否', 'No')}</span>`;
  return escapeHtml(typeof value === 'object' ? JSON.stringify(value) : String(value));
}
function renderChainTable(rows) {
  const labels = {tool_loop:['多步工具调用','Tool loop'],mcp:['外部工具','External tools'],self_capability:['自主管理','Self management'],autonomy:['自主活动','Autonomy'],scheduler:['调度器','Scheduler'],channels:['消息通道','Channels'],model_routing:['模型路由','Model routing'],embedding:['语义检索','Embedding'],tts:['语音合成','Speech'],hardware_intiface:['硬件连接','Hardware']};
  const headers = [['功能 / 对应函数','Feature / function'],['配置值','Configured'],['生效值','Effective'],['运行判断','Runtime'],['阻断 / 来源','Blocker / source'],['操作','Actions']];
  return `<div class="tbl-wrap"><table class="chain-table"><thead><tr>${headers.map(pair=>`<th>${designText(...pair)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => {
    const good = row.runtime_status === 'enabled';
    const bad = ['disabled','unavailable','blocked','error'].includes(row.runtime_status);
    const reason = row.blocking_reason ? chainReason(row.blocking_reason) : '—';
    return `<tr><td><strong>${escapeHtml(labels[row.id] ? designText(...labels[row.id]) : row.id)}</strong><small>${escapeHtml(row.runtime_consumer || '—')}</small></td><td>${stateCell(row.configured_value)}</td><td>${stateCell(row.effective_value)}</td><td class="${good?'state-pass':bad?'state-stop':''}">${escapeHtml(t('overview.status.'+row.runtime_status, row.runtime_status || '—'))}</td><td>${escapeHtml(reason)}<small>${escapeHtml(row.override_source || '—')}${row.restart_required ? ' · '+designText('需重启','Restart required') : ''}</small></td><td>${row.edit_page ? centerLink(row.edit_page, designText('设置','Settings')) : '—'}</td></tr>`;
  }).join('')}</tbody></table></div>`;
}
async function loadChainOverview(root) {
  if (!root) return;
  const request = root._chainRequest = (root._chainRequest || 0) + 1;
  root.textContent = designText('读取链路状态…','Loading chain state…');
  try {
    const data = await api('GET','/admin/control-center/effective-state');
    if (!Array.isArray(data.features)) throw new Error(designText('状态响应不完整','Incomplete state response'));
    if (!root.isConnected || root._chainRequest !== request) return;
    const blockers = data.features.filter(row=>['unavailable','blocked'].includes(row.runtime_status) && row.blocking_reason);
    root.innerHTML = (blockers.length ? `<aside class="configuration-notice"><strong>${designText('以下环节需要配置或排查','These stages need configuration or attention')}</strong>${blockers.map(row=>`<p>${escapeHtml(chainReason(row.blocking_reason))} ${row.edit_page?centerLink(row.edit_page,designText('前往设置','Open settings')):''}</p>`).join('')}</aside>` : '') + renderChainTable(data.features);
    bindPageActions(root);
  } catch (error) {
    if (root.isConnected && root._chainRequest === request) root.textContent = designText('状态读取失败，不代表功能关闭：','State unavailable; this does not mean disabled: ') + error.message;
  }
}
function mountChainOverview(page, root) {
  if (!['observation-center','tools'].includes(page)) return;
  let section = root.querySelector('.chain-overview');
  if (!section) {
    section = document.createElement('section'); section.className = 'card chain-overview';
    const heading = document.createElement('h3'); heading.textContent = designText('链路状态 · 从配置定位阻断','Chain state · locate configuration blockers');
    const hint = document.createElement('p'); hint.textContent = designText('绿色表示该项运行判断通过，红色表示关闭或阻断；— 表示无数据或不适用。模型路由通过不等于上游连通，实际调用见调用记录。','Green means the runtime check passes; red means disabled or blocked. — means unavailable or not applicable. Resolved routing is not an upstream connectivity test; see call records.');
    const content = document.createElement('div'); content.className = 'chain-content'; content.setAttribute('aria-live','polite');
    const footer = document.createElement('div'); footer.className = 'settings-footer';
    const refresh = document.createElement('button'); refresh.className = 'btn btn-ghost'; refresh.textContent = designText('刷新链路状态','Refresh chain state'); refresh.onclick = ()=>loadChainOverview(content);
    footer.append(refresh); section.append(heading,hint,content,footer);
    root.querySelector('.page-title').insertAdjacentElement('afterend',section);
  }
  return loadChainOverview(section.querySelector('.chain-content'));
}

function importPresetJson() {
  const input = document.getElementById('preset-import-json');
  const result = document.getElementById('preset-import-result');
  try {
    if (input.value.length > 100000) throw new Error();
    const parsed = JSON.parse(input.value);
    const data = parsed.connection || parsed;
    if (!data || Array.isArray(data) || typeof data !== 'object') throw new Error();
    const aliases = {base_url:'base-url',baseURL:'base-url',api_key:'api-key',apiKey:'api-key',model:'model',provider_kind:'kind',api_protocol:'api-protocol',tool_call_mode:'tool-mode',anthropic_auth_mode:'anthropic-auth-mode'};
    const changes = new Map();
    for (const [key, suffix] of Object.entries(aliases)) {
      if (!Object.hasOwn(data,key)) continue;
      if (typeof data[key] !== 'string') throw new Error();
      const field = document.getElementById('mr-preset-'+suffix);
      if (field.tagName === 'SELECT' && ![...field.options].some(option=>option.value===data[key])) throw new Error();
      if (suffix==='base-url' && data[key] && !/^https?:\/\//i.test(data[key])) throw new Error();
      if (changes.has(field) && changes.get(field)!==data[key]) throw new Error();
      changes.set(field,data[key]);
    }
    if (data.params !== undefined && (!data.params || Array.isArray(data.params) || typeof data.params !== 'object')) throw new Error();
    // The existing visual editor supports scalar parameters only; reject nested data
    // before changing any field rather than silently coercing it to a string.
    if (data.params && Object.entries(data.params).some(([key,value])=>['__proto__','constructor','prototype'].includes(key) || !['string','number','boolean'].includes(typeof value))) throw new Error();
    if (!changes.size && data.params === undefined) throw new Error();
    for (const [field,value] of changes) field.value = value;
    if (data.params !== undefined) renderKeyValueEditor('mr-preset-params',data.params);
    if (typeof data.reasoning_native === 'boolean') document.getElementById('mr-preset-reasoning-native').checked = data.reasoning_native;
    if (data.reasoning_extra_body !== undefined) {
      if (data.reasoning_extra_body && typeof data.reasoning_extra_body === 'object' && !Array.isArray(data.reasoning_extra_body)) {
        document.getElementById('mr-preset-reasoning-extra-body').value = JSON.stringify(data.reasoning_extra_body, null, 2);
      } else if (data.reasoning_extra_body === null || data.reasoning_extra_body === '') {
        document.getElementById('mr-preset-reasoning-extra-body').value = '';
      } else {
        throw new Error();
      }
    }
    bindPageActions(document.getElementById('mr-preset-params'));
    resetModelDiscovery(); input.value = '';
    result.textContent = designText('已填入表单，请核对后保存。未识别字段不会导入。','Form filled. Review and save; unrecognized fields are ignored.');
  } catch (_) { result.textContent = designText('无法导入：请检查 JSON 对象、字段类型、协议选项和 HTTP(S) 地址。表单未修改。','Cannot import: check the JSON object, field types, protocol and HTTP(S) URL. Form unchanged.'); }
}
