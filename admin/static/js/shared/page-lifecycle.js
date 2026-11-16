// AR-03 single owner; classic function exports preserve existing HTML/domain consumers.
async function loadPageFragment(page, {reload = false} = {}) {
  const container = document.getElementById('page-' + page);
  if (!container) return container;
  const pending = _pageFragmentLoads.get(page);
  if (pending) {
    if (!reload) return pending;
    // Finish the in-flight fetch before replacing the fragment it owns.
    await pending.catch(() => {});
  }
  if (reload) {
    delete container.dataset.pageLoaded;
    delete container.dataset.pageVisited;
    container.replaceChildren();
  }
  if (container.dataset.pageLoaded === 'true') return container;
  if (!_pageFragmentLoads.has(page)) {
    const request = withAdminDeadline(async signal => {
        const response = await fetch(`/static/pages/${encodeURIComponent(page)}.html?v=${ADMIN_UI_FRAGMENT_VERSION}`, {signal});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.text();
      }, 8000)
      .then(html => {
        container.innerHTML = html;
        container.dataset.pageLoaded = 'true';
        AdminUI.mount(container);
        window.AdminI18n?.applyI18n(container);
        bindPageActions(container);
        return container;
      })
      .catch(error => {
        console.error(`[admin] failed to load page fragment ${page}`, error);
        throw error;
      }).finally(() => _pageFragmentLoads.delete(page));
    _pageFragmentLoads.set(page, request);
  }
  return _pageFragmentLoads.get(page);
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
  const requestedPage = page;
  page = AdminPageLayout.resolve(ADMIN_PAGE_ALIASES[page] || page);
  if (reloadFragment) AdminPageLayout.restore();
  const pageElement = document.getElementById('page-' + page);
  if (!pageElement) {
    console.error('[admin] unknown page', page);
    return;
  }
  const generation = ++_navigationGeneration;
  const main = document.querySelector('main');
  const previousScroll = main.scrollTop;
  const current = _pageHistory[_pageHistory.length - 1];
  const samePage = current?.page === page;
  if (!fromHistory) {
    if (current && document.getElementById('page-' + current.page)?.dataset.pageVisited === 'true') current.scroll = previousScroll;
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

  try {
    await loadPageFragment(page, {reload: reloadFragment});
    if (generation !== _navigationGeneration) return;
    await AdminPageLayout.prepare(page, reloadFragment);
  } catch (_error) {
    if (generation !== _navigationGeneration) return;
    pageElement.querySelector(':scope > [data-page-load-error]')?.remove();
    const error = document.createElement('p');
    error.dataset.pageLoadError = '';
    error.setAttribute('role', 'alert');
    error.textContent = t('nav.load_failed', 'Page could not load. Select it again to retry, or go back.');
    pageElement.append(error);
    return;
  }
  // A slower fragment must never resume navigation after a newer click or logout.
  if (generation !== _navigationGeneration) return;
  pageElement.querySelector(':scope > [data-page-load-error]')?.remove();

  const loaders = AdminPageRegistry;
  // Returning to an already mounted page preserves its filters and form state.
  const resumePage = fromHistory && pageElement.dataset.pageVisited === 'true';
  pageElement.querySelector(':scope > [data-page-data-error]')?.remove();
  if (!resumePage && loaders[page]) {
    Promise.resolve().then(() => loaders[page]()).then(() => {
      if (generation !== _navigationGeneration) return;
      decorateSettingsPanels(page, pageElement);
      AdminUI.mount(pageElement);
    }).catch(error => {
      console.error('[admin] page load failed', error);
      if (generation !== _navigationGeneration) return;
      const status = document.createElement('p');
      status.dataset.pageDataError = '';
      status.setAttribute('role', 'alert');
      status.textContent = t('common.load_failed', '加载失败：{error}', {error: error.message}) + ' ';
      const retry = document.createElement('button');
      retry.type = 'button'; retry.className = 'btn btn-ghost btn-sm';
      retry.textContent = t('common.refresh', '刷新');
      retry.addEventListener('click', () => goto(page));
      status.append(retry); pageElement.prepend(status);
    });
  }
  mountChainOverview(page, pageElement);
  pageElement.dataset.pageVisited = 'true';
  const targetScroll = (fromHistory || samePage) ? (_pageHistory.at(-1)?.scroll || 0) : 0;
  // Avoid forcing synchronous layout of a newly mounted page just to set an
  // already-zero scroll position. Nonzero history restoration still runs.
  if (previousScroll || targetScroll) main.scrollTop = targetScroll;
  AdminPageLayout.focus(requestedPage);
}

updatePageHistory();
window.addEventListener('pagehide', () => {
  if (_pageHistory.length) _pageHistory.at(-1).scroll = document.querySelector('main').scrollTop;
  updatePageHistory();
});



bindShellActions();
initMobileSidebar();
