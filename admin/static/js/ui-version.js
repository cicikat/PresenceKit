// Presentation adapter: moves the existing controls, never mounts a second form.
const AdminUI = (() => {
  const key = 'presencekit_admin_ui';
  let version = 'legacy';
  try { if (localStorage.getItem(key) === 'new') version = 'new'; } catch (_) {}
  let layout = null;
  const components = Object.freeze({card: 'pk-card', accordion: 'pk-accordion', button: 'pk-button', input: 'pk-input', select: 'pk-select', switch: 'pk-switch', statusBadge: 'pk-status', formField: 'pk-field'});
  function enhance(root) {
    if (!root || layout?.root === root) return;
    const legacyChildren = [...root.childNodes];
    const disclosure = root.querySelector(':scope > details.settings-disclosure');
    const nodes = disclosure ? [disclosure.querySelector('h3'), ...disclosure.querySelector('.settings-disclosure-body').children] : [...root.children];
    const anchors = nodes.map(n => { const anchor = document.createComment('ui-origin'); n.before(anchor); return [n, anchor]; });
    const shell = document.createElement('div');
    shell.className = 'pk-ui';
    const hero = document.createElement('header'); hero.className = 'pk-hero';
    hero.append(nodes[0], nodes[1]); shell.append(hero);
    const cards = [];
    ['自动记录', '吃食清单', '更正口味'].forEach((title, i) => {
      const card = document.createElement('details'); card.className = components.card + ' ' + components.accordion; card.open = true;
      const summary = document.createElement('summary'); summary.textContent = `0${i + 1} · ${title}`;
      const body = document.createElement('div'); body.className = 'pk-body';
      card.append(summary, body); shell.append(card); cards.push(body);
    });
    nodes.slice(2,5).forEach(n => cards[0].append(n));
    nodes.slice(5,10).forEach(n => cards[1].append(n));
    nodes.slice(10).forEach(n => cards[2].append(n));
    const changes = [];
    shell.querySelectorAll('input, select, button, label, [role="status"]').forEach(n => {
      const cls = n.tagName === 'BUTTON' ? components.button : n.tagName === 'SELECT' ? components.select : n.tagName === 'LABEL' ? components.formField : n.type === 'checkbox' ? components.switch : n.matches('[role="status"]') ? components.statusBadge : components.input;
      n.classList.add(cls); changes.push([n, cls]);
    });
    root.replaceChildren(shell); layout = {root, nodes, shell, changes, anchors, legacyChildren};
  }
  function apply() {
    document.body.classList.toggle('pk-admin', version === 'new');
    const root = document.getElementById('food-module');
    if (version === 'new') enhance(root);
    else if (layout) {
      layout.root.replaceChildren(...layout.legacyChildren);
      layout.anchors.forEach(([n, anchor]) => anchor.replaceWith(n));
      layout.changes.forEach(([n, cls]) => n.classList.remove(cls));
      layout.shell.remove(); layout = null;
    }
    const select = document.getElementById('admin-ui-version'); if (select) select.value = version;
    window.dispatchEvent(new Event('admin-ui-version-changed'));
  }
  function select(value) {
    version = value === 'new' ? 'new' : 'legacy';
    try { localStorage.setItem(key, version); } catch (_) {}
    apply();
  }
  document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('admin-ui-version')?.addEventListener('change', e => select(e.target.value)); apply();
  });
  return {apply, select, components, get version() { return version; }};
})();
