// Parser-blocking head script: restore the canvas before CSS/content can paint.
(() => {
  let night = false;
  let purple = false;
  try {
    night = localStorage.getItem('presencekit_admin_theme') === 'night';
    purple = localStorage.getItem('presencekit_admin_night_palette') === 'purple';
  } catch (_) {}
  const root = document.documentElement;
  root.dataset.adminTheme = night ? 'night' : 'day';
  root.dataset.adminPalette = purple ? 'purple' : 'charcoal';
  root.style.colorScheme = night ? 'dark' : 'light';
  if (night) root.style.backgroundColor = purple ? '#17151e' : '#14171b';
})();
