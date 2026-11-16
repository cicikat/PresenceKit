// ══════════════════════════════════════════════════════════
//  State
// ══════════════════════════════════════════════════════════
let TOKEN = '';
try { TOKEN = localStorage.getItem('qq_admin_key') || ''; } catch (_) {}
let BASE  = window.location.origin;
let _currentUser = null;
let _allUsers = [];
const ACTIVE_PAGE_SESSION_KEY = 'admin_active_page';
window._charName = '叶瑄';  // fallback; overwritten after login by _initCharName()

let _promptLanguageReady = false;
let _promptLanguageSaving = false;
window.saveAdminPromptLanguage = async (uiLanguage) => {
  // Before sign-in only the local UI language changes. Authenticated changes
  // persist first, so an error never advertises an unapplied prompt language.
  if (!_promptLanguageReady) return;
  if (_promptLanguageSaving) throw new Error('A language change is already being saved.');
  _promptLanguageSaving = true;
  try {
    const language = uiLanguage === 'en' ? 'en' : 'zh';
    const result = await api('POST', '/settings/prompt-language', {language});
    if (result.effective_language !== language) throw new Error('Prompt language was not applied.');
  } finally {
    _promptLanguageSaving = false;
  }
};

async function loadAdminPromptLanguage(options = {}) {
  _promptLanguageReady = false;
  const result = await api('GET', '/settings/prompt-language', undefined, options);
  if (options.signal?.aborted) return;
  await AdminI18n.setLanguage(result.effective_language === 'en' ? 'en' : 'zh-CN', {persistPrompt: false});
  _promptLanguageReady = true;
}

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
const ADMIN_UI_FRAGMENT_VERSION = 'finance-330-i18n';

const ADMIN_PAGE_ALIASES = Object.freeze({overview:'setup', users:'character', 'operations-center':'status', memory: 'observe-memory', 'creation-center':'data-management', 'observe-existence':'observe-dream'});

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

// Shared classic scripts provide the documented compatibility exports.
// Bootstrap runs after those services, before domain scripts initialize.
