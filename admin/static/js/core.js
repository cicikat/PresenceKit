// ══════════════════════════════════════════════════════════
//  State
// ══════════════════════════════════════════════════════════
let TOKEN = localStorage.getItem('qq_admin_key') || '';
let BASE  = window.location.origin;
let _currentUser = null;
let _allUsers = [];
const ACTIVE_PAGE_SESSION_KEY = 'admin_active_page';
window._charName = '叶瑄';  // fallback; overwritten after login by _initCharName()

window.addEventListener('admin-language-changed', () => {
  const activePage = document.querySelector('.page.active');
  const appVisible = document.getElementById('app').style.display !== 'none';
  if (activePage && appVisible) {
    // Fragments contain their language-specific fallback markup. Reload the
    // active one so both static controls and API-backed views are rebuilt.
    goto(activePage.id.replace(/^page-/, ''), {reloadFragment: true});
  }
});


const _pageFragmentLoads = new Map();
const ADMIN_UI_FRAGMENT_VERSION = 'ime-awareness-2';

const ADMIN_PAGE_ALIASES = Object.freeze({memory: 'observe-memory'});

const ADMIN_PAGE_CONTEXT = Object.freeze({
  setup: {related: ['model-routing', 'character']},
  'runtime-config': {related: ['status', 'scheduler', 'model-routing']},
  'tts-config': {related: ['status', 'character', 'user-data']},
  character: {related: ['lorebook', 'tools']},
  lorebook: {related: ['character']},
  'dream-settings': {related: ['character']},
  'model-routing': {related: ['setup']},
  scheduler: {related: ['observe-autonomy']},
  tools: {related: ['mcp', 'character']},
  mcp: {related: ['tools']},
  'owner-turn-api': {related: ['auth-tokens', 'status']},
  'agent-runtime-browser': {related: ['tools', 'owner-turn-api']},
  'relationship-facts': {related: ['character', 'observe-memory']},
  'auth-tokens': {related: ['users']},
  status: {related: ['model-routing', 'auth-tokens']},
});

function decoratePageContext(page, container) {
  const context = ADMIN_PAGE_CONTEXT[page];
  if (!context || container.querySelector('.page-context')) return;
  const title = container.querySelector('.page-title');
  if (!title) return;
  const panel = document.createElement('section');
  panel.className = 'page-context';
  const purpose = document.createElement('p');
  purpose.className = 'page-context-purpose';
  purpose.textContent = t(`page_context.${page}.purpose`, 'Manage this feature and its effective configuration.');
  const source = document.createElement('p');
  source.className = 'page-context-source';
  source.textContent = t(`page_context.${page}.source`, 'Configuration source and effective scope are shown on this page.');
  const related = document.createElement('div');
  related.className = 'page-context-related';
  const label = document.createElement('span');
  label.textContent = t('page_context.related', 'Related settings');
  related.append(label);
  context.related.forEach(target => {
    const link = document.createElement('button');
    link.type = 'button';
    link.className = 'btn btn-ghost btn-sm';
    link.dataset.action = 'goto';
    link.dataset.actionArgs = JSON.stringify([target]);
    const navLabel = document.querySelector(`nav a[data-page="${target}"] [data-i18n]`);
    link.textContent = navLabel ? t(navLabel.dataset.i18n, navLabel.textContent) : target;
    related.append(link);
  });
  panel.append(purpose, source, related);
  title.insertAdjacentElement('afterend', panel);
  bindPageActions(panel);
}

function getRememberedPage() {
  try {
    const page = sessionStorage.getItem(ACTIVE_PAGE_SESSION_KEY);
    if (page && document.getElementById('page-' + page)) return page;
    sessionStorage.removeItem(ACTIVE_PAGE_SESSION_KEY);
  } catch (_error) { /* Storage can be unavailable in restricted browser contexts. */ }
  return null;
}

function rememberPage(page) {
  try {
    sessionStorage.setItem(ACTIVE_PAGE_SESSION_KEY, page);
  } catch (_error) { /* Page navigation must still work without storage access. */ }
}

function clearRememberedPage() {
  resetPageHistory();
  try {
    sessionStorage.removeItem(ACTIVE_PAGE_SESSION_KEY);
  } catch (_error) { /* Best effort only. */ }
}

function _actionArgs(element) {
  const raw = element.dataset.actionArgs;
  if (!raw) return [];
  try {
    const args = JSON.parse(raw);
    return Array.isArray(args) ? args : [];
  } catch (error) {
    console.error('[admin] invalid data-action-args', raw, error);
    return [];
  }
}

function _runAction(event) {
  const element = event.currentTarget;
  if (element.matches('a[data-page]')) event.preventDefault();
  const action = element.dataset.action;
  const args = _actionArgs(element);
  if (action === 'focus-element') {
    document.getElementById(args[0])?.click();
    return;
  }
  const fn = window[action];
  if (typeof fn !== 'function') {
    console.error('[admin] missing action handler', action);
    return;
  }
  fn(...args, element);
}

function bindPageActions(scope) {
  if (!scope) return;
  const actions = [
    ...(scope.matches?.('[data-action]') ? [scope] : []),
    ...scope.querySelectorAll('[data-action]'),
  ];
  actions.forEach(element => {
    if (element.dataset.actionBound === 'true') return;
    element.addEventListener('click', _runAction);
    element.dataset.actionBound = 'true';
  });
}

async function loadPageFragment(page, {reload = false} = {}) {
  const container = document.getElementById('page-' + page);
  if (!container) return container;
  if (reload) {
    _pageFragmentLoads.delete(page);
    delete container.dataset.pageLoaded;
    container.replaceChildren();
  }
  if (container.dataset.pageLoaded === 'true') return container;
  if (!_pageFragmentLoads.has(page)) {
    const request = fetch(`/static/pages/${encodeURIComponent(page)}.html?v=${ADMIN_UI_FRAGMENT_VERSION}`)
      .then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.text();
      })
      .then(html => {
        container.innerHTML = html;
        container.dataset.pageLoaded = 'true';
        window.AdminI18n?.applyI18n(container);
        bindPageActions(container);
        decoratePageContext(page, container);
        return container;
      })
      .catch(error => {
        _pageFragmentLoads.delete(page);
        console.error(`[admin] failed to load page fragment ${page}`, error);
        throw error;
      });
    _pageFragmentLoads.set(page, request);
  }
  return _pageFragmentLoads.get(page);
}

function bindShellActions() {
  bindPageActions(document.getElementById('auth-overlay'));
  bindPageActions(document.querySelector('nav'));
  bindPageActions(document.querySelector('main'));
  bindPageActions(document.getElementById('nav-menu-toggle'));
  bindPageActions(document.getElementById('nav-backdrop'));
}

const MOBILE_NAV_MEDIA = window.matchMedia('(max-width: 767px)');

function setSidebarOpen(open) {
  const shouldOpen = Boolean(open) && MOBILE_NAV_MEDIA.matches;
  const app = document.getElementById('app');
  const toggle = document.getElementById('nav-menu-toggle');
  const backdrop = document.getElementById('nav-backdrop');
  app.classList.toggle('admin-sidebar-open', shouldOpen);
  document.body.classList.toggle('admin-sidebar-open', shouldOpen);
  toggle.setAttribute('aria-expanded', String(shouldOpen));
  backdrop.setAttribute('aria-hidden', String(!shouldOpen));
  backdrop.tabIndex = shouldOpen ? 0 : -1;
}

function toggleSidebar() {
  setSidebarOpen(!document.getElementById('app').classList.contains('admin-sidebar-open'));
}

function closeSidebar() {
  setSidebarOpen(false);
}

function initMobileSidebar() {
  document.querySelector('nav').addEventListener('click', event => {
    if (event.target.closest('a[data-page]')) closeSidebar();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeSidebar();
  });
  MOBILE_NAV_MEDIA.addEventListener('change', () => closeSidebar());
  setSidebarOpen(false);
}


const PAGE_HISTORY_KEY = 'admin_page_history';
let _pageHistory = [];
let _navigationGeneration = 0;
try {
  const saved = JSON.parse(sessionStorage.getItem(PAGE_HISTORY_KEY) || '[]');
  if (Array.isArray(saved)) _pageHistory = saved.filter(item =>
    item && typeof item.page === 'string' && document.getElementById('page-' + item.page)
    && Number.isFinite(item.scroll) && item.scroll >= 0).slice(-100);
} catch (_error) { /* Navigation also works with unavailable or corrupt storage. */ }

function updatePageHistory() {
  document.getElementById('nav-back').disabled = _pageHistory.length < 2;
  try { sessionStorage.setItem(PAGE_HISTORY_KEY, JSON.stringify(_pageHistory)); }
  catch (_error) { /* Best effort only; page history remains available in memory. */ }
}

function resetPageHistory() {
  _navigationGeneration += 1;
  _pageHistory = [];
  updatePageHistory();
}

function goBackPage() {
  if (_pageHistory.length < 2) return;
  _pageHistory.pop();
  return goto(_pageHistory[_pageHistory.length - 1].page, {fromHistory: true});
}

async function goto(page, {reloadFragment = false, fromHistory = false} = {}) {
  page = ADMIN_PAGE_ALIASES[page] || page;
  const pageElement = document.getElementById('page-' + page);
  if (!pageElement) {
    console.error('[admin] unknown page', page);
    return;
  }
  const generation = ++_navigationGeneration;
  const main = document.querySelector('main');
  const current = _pageHistory[_pageHistory.length - 1];
  const samePage = current?.page === page;
  if (!fromHistory) {
    if (current && document.getElementById('page-' + current.page)?.dataset.pageVisited === 'true') current.scroll = main.scrollTop;
    if (!samePage) _pageHistory.push({page, scroll: 0});
    _pageHistory = _pageHistory.slice(-100);
  }
  updatePageHistory();
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('nav a').forEach(a => {
    a.classList.remove('active');
    a.removeAttribute('aria-current');
  });
  pageElement.classList.add('active');
  const navLink = document.querySelector(`nav a[data-page="${page}"]`);
  navLink?.classList.add('active');
  navLink?.setAttribute('aria-current', 'page');
  const group = navLink?.closest('.nav-group');
  if (group) setNavGroupExpanded(group.id.slice('navgroup-'.length), true);
  closeSidebar();
  rememberPage(page);
  if (page !== 'scheduler') _stopWatchStatusPoller();

  try {
    await loadPageFragment(page, {reload: reloadFragment});
  } catch (_error) {
    if (generation !== _navigationGeneration) return;
    const error = document.createElement('p');
    error.setAttribute('role', 'alert');
    error.textContent = t('nav.load_failed', 'Page could not load. Select it again to retry, or go back.');
    pageElement.replaceChildren(error);
    return;
  }
  // A slower fragment must never resume navigation after a newer click or logout.
  if (generation !== _navigationGeneration) return;

  const loaders = {
    guide: () => {},
    'call-records': loadUnifiedRecords,
    'autonomy-settings': loadAutonomySettings,
    'output-settings': loadStickerConfig,
    'device-policy': () => { loadScreenPeekSettings(); loadMetaMode(); },
    'network-config': () => { loadProxy(); loadRelaySettings(); },
    'personal-settings': async () => { _loadSetupAnniversaries(); await _ensurePronounUidOptions(); await loadUserPronoun(); },
    'coplay-config': _loadSetupCoplayGames,
    'diary-config': _loadSetupDiary,
    'mail-config': _loadSetupMail,
    'embedding-config': loadSetupEmbedding,
    'creation-center': loadCreationAssets,
    'observation-center': loadImeObservation,
    'operations-center': () => {},
    'feature-center': loadFeatureCenter,
    'service-center': loadServiceCenter,
    'role-bindings': loadRoleBindings,
    'conversation-settings': loadConversationSettings,
    'generation-debug': () => { loadPromptAblation(); loadMcpDebugRequests(); loadLatestAutonomyPrompt(); },
    setup:           loadSetupPage,
    'runtime-config': loadRuntimeConfig,
    'tts-config':     loadTtsConfig,
    status:          loadStatus,
    users:           () => { loadUsers(); loadBlacklist(); },
    logs:            loadLogs,
    'auth-tokens':   () => { loadAuthTokens(); loadChannelToggles(); },
    overview:        loadOverview,
    'model-routing': loadModelRouting,
    mcp:             loadMcpPage,
    tools:           loadToolsPage,
    'owner-turn-api': loadOwnerTurnApiPage,
    'agent-runtime-browser': loadBrowserRuntimePage,
    'relationship-facts': loadRelationshipFactsPage,
    character:       loadCharacterPage,
    lorebook:        () => { loadLorebook(); loadJbEntries(); },
    'dream-settings': loadDreamSettings,
    scheduler:       loadScheduler,
    'observe-existence': () => {},
    'observe-mood':    loadObserveMood,
    'observe-dream':   loadObserveDream,
    'observe-memory':  () => {},
    'observe-memory-events': () => {},
    'observe-hidden':  loadObserveHidden,
    'observe-chatlog': loadObserveChatlogDates,
    'observe-runtime': loadObserveRuntime,
    'observe-growth':  () => initObserveCharacters('obs-growth-char', loadObserveGrowth),
    'observe-visual':  loadObserveVisual,
    'observe-spend':   loadObserveSpend,
    'observe-group-arbiter': initObserveGroupArbiter,
    'observe-memory-summary': () => initObserveCharacters('obs-memory-summary-char'),
    'observe-prompt':  () => { loadObservePromptUidList(); },
    'observe-tools':   () => loadObserveToolUidList(),
    'observe-dream-prompt': () => loadObserveDreamPromptUidList(),
    'observe-dream-operations': loadObserveDreamOperations,
    'observe-trigger-catalog': () => loadTriggerCatalog(),
    'observe-vector':          () => loadVector(),
    'observe-provenance':      () => loadProvenance(),
    'observe-resource-completeness': () => loadResourceCompleteness(),
    'observe-api-contract':          () => loadApiContractCheck(),
    'observe-runtime-signals':       () => loadRuntimeSignals(),
    'observe-system-diagnosis':       loadSystemDiagnosis,
    'observe-autonomy':              loadObserveAutonomy,
    'observe-char-permissions':      () => initObserveCharacters('obs-charperm-char', loadCharPermissions),
    'user-data':                     loadUserDataPage,
  };
  // Returning to an already mounted page preserves its filters and form state.
  const resumePage = fromHistory && pageElement.dataset.pageVisited === 'true';
  if (resumePage && page === 'scheduler') _startWatchStatusPoller();
  if (!resumePage && loaders[page]) {
    loaders[page]();
  }
  pageElement.dataset.pageVisited = 'true';
  main.scrollTop = (fromHistory || samePage) ? (_pageHistory.at(-1)?.scroll || 0) : 0;
}

updatePageHistory();
window.addEventListener('pagehide', () => {
  if (_pageHistory.length) _pageHistory.at(-1).scroll = document.querySelector('main').scrollTop;
  updatePageHistory();
});
bindShellActions();
initMobileSidebar();

// ══════════════════════════════════════════════════════════
//  API helper
// ══════════════════════════════════════════════════════════
function authHeaders(extra) {
  return { 'Authorization': `Bearer ${TOKEN}`, 'Content-Type': 'application/json', ...extra };
}

async function api(method, path, body) {
  const opts = { method, headers: authHeaders() };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(BASE + path, opts);
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`HTTP ${r.status}: ${t}`);
  }
  return r.json();
}

const ANNIVERSARY_EDITOR_FIELDS = Object.freeze([
  ['key', 'key', 'text', 'Anniversary key'],
  ['month', 'month', 'number', 'Anniversary month'],
  ['day', 'day', 'number', 'Anniversary day'],
  ['year_start', 'year-start', 'number', 'Anniversary starting year'],
  ['prompt_zero', 'prompt-zero', 'text', 'Anniversary first-year prompt'],
  ['prompt_years', 'prompt-years', 'text', 'Anniversary later-years prompt'],
]);

function renderAnniversaryRow(value = {}, {removeAction = 'removeAnniversaryRow'} = {}) {
  const fields = ANNIVERSARY_EDITOR_FIELDS.map(([name, dataName, type, ariaLabel]) => {
    const rawValue = value[name] ?? '';
    const escapedValue = escapeHtml(String(rawValue));
    const placeholder = name === 'prompt_zero'
      ? 'prompt (first year)'
      : name === 'prompt_years' ? 'prompt (later years)' : name;
    const constraints = name === 'month'
      ? ' min="1" max="12"'
      : name === 'day' ? ' min="1" max="31"' : '';
    return `<input type="${type}" data-anniversary-field="${dataName}" data-${dataName} aria-label="${ariaLabel}" placeholder="${placeholder}"${constraints} value="${escapedValue}">`;
  }).join('');
  return `<div class="form-row" data-anniversary-row>${fields}<button type="button" class="btn btn-ghost btn-sm" data-action="${removeAction}">Remove</button></div>`;
}

function renderAnniversaryEditor(root, values = [], options = {}) {
  if (!root) return;
  root.innerHTML = (values.length ? values : [{}]).map(value => renderAnniversaryRow(value, options)).join('');
  bindPageActions(root);
}

function addAnniversaryEditorRow(root, options = {}) {
  if (!root) return;
  root.insertAdjacentHTML('beforeend', renderAnniversaryRow({}, options));
  bindPageActions(root);
}

function removeAnniversaryEditorRow(button) {
  const row = button?.closest('[data-anniversary-row]');
  if (!row) return false;
  row.remove();
  return true;
}

function readAnniversaryEditor(root, {onValidationError} = {}) {
  if (!root) return [];
  const result = [];
  for (const row of root.querySelectorAll('[data-anniversary-row]')) {
    const read = (name) => row.querySelector(`[data-anniversary-field="${name}"]`);
    const raw = Object.fromEntries(ANNIVERSARY_EDITOR_FIELDS.map(([name, dataName]) => [name, read(dataName)?.value || '']));
    const key = raw.key.trim();
    const month = Number(raw.month);
    const day = Number(raw.day);
    const hasValue = Object.values(raw).some(value => String(value).trim() !== '');
    if (!hasValue) continue;
    if (!key || !Number.isInteger(month) || !Number.isInteger(day)) {
      onValidationError?.();
      return null;
    }
    result.push({
      key,
      month,
      day,
      ...(raw.year_start.trim() ? {year_start: Number(raw.year_start)} : {}),
      ...(raw.prompt_zero.trim() ? {prompt_zero: raw.prompt_zero.trim()} : {}),
      ...(raw.prompt_years.trim() ? {prompt_years: raw.prompt_years.trim()} : {}),
    });
  }
  return result;
}

// Small structured editor for settings whose supported keys vary by provider.
function renderKeyValueEditor(id, values = {}, options = {}) {
  const root = document.getElementById(id);
  if (!root) return;
  root._kvOptions = options;
  const entries = Object.entries(values || {}).filter(([key]) => !options.exclude?.includes(key));
  const rows = entries.length ? entries : [['', '']];
  const labels = options.labels || {};
  const heading = options.labels
    ? `<div class="form-row kv-header"><span>${escapeHtml(labels.key || 'Name')}</span><span>${escapeHtml(labels.value || 'Value')}</span><span>${escapeHtml(labels.type || 'Type')}</span><span></span></div>`
    : '';
  root.innerHTML = heading + rows.map(([key, value]) => _renderKeyValueRow(key, value, options)).join('');
  root.querySelectorAll('[data-kv-row]').forEach((row, index) => {
    const value = entries[index]?.[1];
    const type = typeof value === 'number' ? 'number' : typeof value === 'boolean' ? 'boolean' : 'string';
    row.querySelector('[data-kv-type]').value = type;
    _syncKeyValueRowType(row);
  });
  root.querySelectorAll('[data-kv-type]').forEach(select => {
    select.addEventListener('change', () => _syncKeyValueRowType(select.closest('[data-kv-row]')));
  });
}

function _syncKeyValueRowType(row) {
  if (!row) return;
  const type = row.querySelector('[data-kv-type]')?.value;
  const text = row.querySelector('[data-kv-value]');
  const boolean = row.querySelector('[data-kv-boolean]');
  if (!text || !boolean) return;
  if (type === 'boolean') {
    if (/^(true|false)$/i.test(text.value.trim())) boolean.value = text.value.trim().toLowerCase();
    text.hidden = true;
    boolean.hidden = false;
  } else {
    if (text.hidden) text.value = boolean.value;
    text.hidden = false;
    boolean.hidden = true;
  }
}

function _renderKeyValueRow(key = '', value = '', options = {}) {
  const labels = options.labels || {};
  return `<div class="form-row" data-kv-row>
    <input type="text" data-kv-key aria-label="${escapeHtml(labels.key || 'Name')}" placeholder="${escapeHtml(options.keyPlaceholder || 'key')}" value="${escapeHtml(String(key))}">
    <input type="text" data-kv-value aria-label="${escapeHtml(labels.value || 'Value')}" placeholder="${escapeHtml(options.valuePlaceholder || 'value')}" value="${escapeHtml(value == null ? '' : String(value))}">
    <select data-kv-boolean aria-label="${escapeHtml(labels.value || 'Value')} boolean"><option value="true">true</option><option value="false">false</option></select>
    <select data-kv-type aria-label="${escapeHtml(labels.type || 'Type')}"><option value="string">text</option><option value="number">number</option><option value="boolean">true/false</option></select>
    <button type="button" class="btn btn-ghost btn-sm" data-action="removeKeyValueRow">Remove</button>
  </div>`;
}

function addKeyValueRow(id, options = undefined) {
  const root = document.getElementById(id);
  if (!root) return;
  const rowOptions = options || root._kvOptions || {};
  root.insertAdjacentHTML('beforeend', _renderKeyValueRow('', '', rowOptions));
  _syncKeyValueRowType(root.querySelector('[data-kv-row]:last-of-type'));
  root.querySelector('[data-kv-row]:last-of-type [data-kv-type]')?.addEventListener('change', event => _syncKeyValueRowType(event.target.closest('[data-kv-row]')));
  bindPageActions(root);
}

function removeKeyValueRow(button) {
  const row = button.closest('[data-kv-row]');
  const root = row?.parentElement;
  row?.remove();
  if (root && !root.querySelector('[data-kv-row]') && !root._kvOptions?.allowEmpty) addKeyValueRow(root.id);
}

function readKeyValueEditor(id) {
  const result = {};
  document.querySelectorAll(`#${CSS.escape(id)} [data-kv-row]`).forEach(row => {
    const key = row.querySelector('[data-kv-key]').value.trim();
    const raw = row.querySelector('[data-kv-value]').value;
    const type = row.querySelector('[data-kv-type]').value;
    if (!key) return;
    if (type === 'number') {
      const value = Number(raw);
      if (!Number.isFinite(value)) throw new Error(`${key} must be a number`);
      result[key] = value;
    } else if (type === 'boolean') {
      result[key] = row.querySelector('[data-kv-boolean]').value === 'true';
    } else result[key] = raw;
  });
  return result;
}

// ══════════════════════════════════════════════════════════
//  Toast
// ══════════════════════════════════════════════════════════
let _toastTimer;
function toast(msg, type = 'ok') {
  msg = window.AdminI18n ? AdminI18n.translateUiText(msg) : msg;
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `show ${type}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.className = '', 3200);
}

// ══════════════════════════════════════════════════════════
//  Status page
// ══════════════════════════════════════════════════════════
