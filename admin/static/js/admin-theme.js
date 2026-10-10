'use strict';
(() => {
  const key = 'presencekit_admin_theme';
  let theme = 'day';
  try { if (localStorage.getItem(key) === 'night') theme = 'night'; } catch (_) {}
  function apply() {
    document.body.dataset.adminTheme = theme;
    const button = document.getElementById('admin-theme-toggle');
    if (!button) return;
    const english = document.documentElement.lang.startsWith('en');
    const label = english
      ? (theme === 'day' ? 'Switch to night mode' : 'Switch to day mode')
      : (theme === 'day' ? '切换夜间模式' : '切换日间模式');
    button.setAttribute('aria-label', label);
    button.title = label;
    button.firstElementChild.textContent = theme === 'day' ? '☾' : '☀';
  }
  apply();
  document.getElementById('admin-theme-toggle').addEventListener('click', () => {
    theme = theme === 'day' ? 'night' : 'day';
    try { localStorage.setItem(key, theme); } catch (_) {}
    apply();
  });
  window.addEventListener('admin-language-changed', apply);
})();
