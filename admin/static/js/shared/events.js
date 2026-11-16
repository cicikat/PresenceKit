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

const AdminActions = (() => {
  const bound = new WeakSet();
  function targets(scope) {
    return [...(scope.matches?.('[data-action]') ? [scope] : []), ...scope.querySelectorAll('[data-action]')];
  }
  return Object.freeze({
    enhance(scope) {
      if (!scope) return;
      targets(scope).forEach(element => {
        if (bound.has(element)) return;
        element.addEventListener('click', _runAction);
        bound.add(element);
        element.dataset.actionBound = 'true';
      });
    },
    dispose(scope) {
      if (!scope) return;
      targets(scope).forEach(element => {
        element.removeEventListener('click', _runAction);
        bound.delete(element);
        delete element.dataset.actionBound;
      });
    },
  });
})();

function bindPageActions(scope) {
  AdminActions.enhance(scope);
}
/* The bridge above is the only data-action/window lookup. Domain actions keep
   their current names while fragment consumers migrate; a copied data marker
   cannot suppress binding on a new node. */
