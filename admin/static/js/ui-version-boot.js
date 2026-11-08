// Early synchronous boot: set pk-admin before first paint so the new UI
// does not flash the legacy cascade while later scripts load.
document.body.dataset.adminTheme = document.documentElement.dataset.adminTheme || 'day';
document.body.dataset.adminPalette = document.documentElement.dataset.adminPalette || 'charcoal';
try { if (localStorage.getItem('presencekit_admin_ui') !== 'legacy') document.body.classList.add('pk-admin'); } catch (_) { document.body.classList.add('pk-admin'); }
