// AR-03 single owner; classic function exports preserve existing HTML/domain consumers.
let _toastTimer;
function toast(msg, type = 'ok') {
  msg = window.AdminI18n ? AdminI18n.translateUiText(msg) : msg;
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `show ${type}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.className = '', 3200);
}
