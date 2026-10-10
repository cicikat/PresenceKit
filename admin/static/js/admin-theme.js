'use strict';
(() => {
  const key = 'presencekit_admin_theme';
  let theme = 'day';
  const paletteKey = 'presencekit_admin_night_palette';
  let palette = 'charcoal';
  try { if (localStorage.getItem(key) === 'night') theme = 'night'; } catch (_) {}
  try { if (localStorage.getItem(paletteKey) === 'purple') palette = 'purple'; } catch (_) {}
  function apply() {
    document.body.dataset.adminTheme = theme;
    document.body.dataset.adminPalette = palette;
    const button = document.getElementById('admin-theme-toggle');
    if (!button) return;
    const english = document.documentElement.lang.startsWith('en');
    const select = document.getElementById('admin-night-palette');
    select.value = palette;
    select.setAttribute('aria-label', english ? 'Night palette' : '夜间配色');
    document.getElementById('admin-night-palette-label').textContent = english ? 'Night palette' : '夜间配色';
    select.options[0].textContent = english ? 'Charcoal' : '参考深灰';
    select.options[1].textContent = english ? 'Deep purple' : '深紫';
    const label = english
      ? (theme === 'day' ? 'Switch to night mode' : 'Switch to day mode')
      : (theme === 'day' ? '切换夜间模式' : '切换日间模式');
    button.setAttribute('aria-label', label);
    button.title = label;
    button.firstElementChild.textContent = theme === 'day' ? '☾' : '☀';
  }
  apply();
  document.getElementById('admin-night-palette').addEventListener('change', event => {
    palette = event.target.value === 'purple' ? 'purple' : 'charcoal';
    try { localStorage.setItem(paletteKey, palette); } catch (_) {}
    apply();
  });
  document.getElementById('admin-theme-toggle').addEventListener('click', () => {
    theme = theme === 'day' ? 'night' : 'day';
    try { localStorage.setItem(key, theme); } catch (_) {}
    apply();
  });
  window.addEventListener('admin-language-changed', apply);
})();
