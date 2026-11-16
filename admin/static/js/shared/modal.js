// One keyboard/focus owner for existing modal DOM. Business close callbacks keep
// secret clearing, validation and permission semantics in their domain.
const AdminModal = (() => {
  const sessions = new WeakMap();
  const controls = root => [...root.querySelectorAll('button,input,select,textarea,a[href],[tabindex]')]
    .filter(node => !node.disabled && node.tabIndex >= 0 && node.getClientRects().length);
  function close(root) {
    if (!root) return;
    const session = sessions.get(root);
    root.classList.remove('open');
    if (!session) return;
    root.removeEventListener('keydown', session.keydown);
    sessions.delete(root);
    if (session.previous?.isConnected) session.previous.focus({preventScroll:true});
  }
  function open(root, onClose) {
    if (!root) return;
    root.classList.add('open');
    if (sessions.has(root)) return;
    const previous = document.activeElement;
    const keydown = event => {
      if (event.key === 'Escape') {event.preventDefault(); onClose();}
      if (event.key !== 'Tab') return;
      const fields = controls(root);
      if (!fields.length) {event.preventDefault(); return;}
      const target = event.shiftKey ? fields.at(-1) : fields[0];
      if (!root.contains(document.activeElement) || document.activeElement === (event.shiftKey ? fields[0] : fields.at(-1))) {
        event.preventDefault(); target.focus();
      }
    };
    sessions.set(root, {previous, keydown});
    root.addEventListener('keydown', keydown);
    controls(root)[0]?.focus({preventScroll:true});
  }
  return Object.freeze({open, close});
})();
