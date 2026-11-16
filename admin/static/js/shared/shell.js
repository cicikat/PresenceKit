// AR-03 single owner; classic function exports preserve existing HTML/domain consumers.
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


