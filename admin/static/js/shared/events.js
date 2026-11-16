// AR-03 single owner; classic function exports preserve existing HTML/domain consumers.
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

