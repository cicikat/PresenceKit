// Presentation adapter: moves the existing controls, never mounts a second form.
const AdminUI = (() => {
  const key = 'presencekit_admin_ui';
  let version = 'new';
  try { if (localStorage.getItem(key) === 'legacy') version = 'legacy'; } catch (_) {}
  let observer = null;
  let transition = Promise.resolve();
  const components = AdminPresentation.components;
  function mount(root) {
    if (version !== 'new') return;

    AdminPresentation.enhance(root);
  }
  function refresh() {
    if (version !== 'new') return;
    AdminPresentation.sweep();
    document.querySelectorAll('.page[data-page-loaded="true"]').forEach(mount);
  }
  function observe() {
    if (observer || !document.getElementById('app')) return;
    observer = observeAdminChanges(roots => {
      if (version !== 'new') return;
      const cards = new Set();
      roots.forEach(root => {
        const element = root.nodeType === Node.TEXT_NODE ? root.parentElement : root;
        if (!element?.closest?.('.page')) return;
        mount(element);
        const card = element.closest('.card');
        if (card) cards.add(card);
      });
      cards.forEach(AdminPresentation.decorate);
      AdminPresentation.sweep();
    }, {attributes:true,attributeFilter:['style']});
  }
  function apply() {
    document.body.classList.toggle('pk-admin', version === 'new');
    if (version === 'new') {refresh(); observe();}
    else {
      observer?.disconnect(); observer = null;
      document.querySelectorAll('[data-pk-tone],[data-pk-surface]').forEach(node => {
        delete node.dataset.pkTone; delete node.dataset.pkSurface;
      });
      AdminPresentation.dispose(document.getElementById('app'));
    }
    const select = document.getElementById('admin-ui-version'); if (select) select.value = version;
    window.dispatchEvent(new Event('admin-ui-version-changed'));
  }
  function select(value) {
    version = value === 'new' ? 'new' : 'legacy';
    try { localStorage.setItem(key, version); } catch (_) {}
    apply();
    transition = AdminPageLayout.selected();
    transition.catch(error => console.error('[admin] page layout failed', error));
    return transition;
  }
  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('admin-ui-version')?.addEventListener('change', e => select(e.target.value)); apply();
  });
  return {apply, select, mount, components, whenSettled: () => transition, get version() { return version; }};
})();
