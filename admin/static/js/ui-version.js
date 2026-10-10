// Presentation adapter: moves the existing controls, never mounts a second form.
const AdminUI = (() => {
  const key = 'presencekit_admin_ui';
  let version = 'legacy';
  try { if (localStorage.getItem(key) === 'new') version = 'new'; } catch (_) {}
  let layout = null;
  const folds = new Map();
  const foldState = new WeakMap();
  let observer = null;
  let scheduled = false;
  const pendingRoots = new Set();
  // Map the known dark-theme signal palette to tokens without rewriting legacy inline styles.
  // CSS variable-based colors already inherit tokens; media and authored color inputs are excluded.
  const tones = new Map([
    ['rgb(134, 239, 172)', 'success'], ['rgb(239, 68, 68)', 'error'],
    ['rgb(248, 113, 113)', 'error'], ['rgb(252, 165, 165)', 'error'],
    ['rgb(156, 163, 175)', 'muted'], ['rgb(147, 197, 253)', 'accent'],
    ['rgb(167, 139, 250)', 'accent'], ['rgb(196, 181, 253)', 'accent'],
    ['rgb(251, 191, 36)', 'warn'], ['rgb(252, 211, 77)', 'warn'], ['rgb(253, 230, 138)', 'warn'],
  ]);
  const surfaces = new Map([
    ['rgb(26, 58, 26)', 'success'], ['rgb(15, 36, 16)', 'success'],
    ['rgb(58, 26, 26)', 'error'], ['rgb(66, 32, 6)', 'warn'],
    ['rgb(29, 58, 110)', 'accent'], ['rgb(30, 58, 95)', 'accent'], ['rgb(59, 34, 87)', 'accent'],
    ['rgb(45, 45, 45)', 'field'], ['rgb(55, 65, 81)', 'field'], ['rgb(124, 58, 237)', 'strong'],
  ]);
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
  function foldLabel(expanded) {
    const english = window.AdminI18n?.getLanguage() === 'en';
    return expanded ? (english ? 'Collapse' : '折叠') : (english ? 'Expand' : '展开');
  }
  function updateFold(card, entry) {
    const expanded = !foldState.get(card);
    card.dataset.pkCollapsed = String(!expanded);
    const label = foldLabel(expanded);
    const marker = expanded ? '▾' : '▸';
    if (entry.button.textContent !== marker) entry.button.textContent = marker;
    entry.button.setAttribute('aria-label', label);
    entry.button.title = label;
    entry.button.setAttribute('aria-expanded', String(expanded));
  }
  function removeFold(card, entry) {
    entry.button.remove();
    entry.heading.removeEventListener('click', entry.headingClick);
    entry.heading.classList.remove('pk-card-heading');
    delete card.dataset.pkCollapsed;
    folds.delete(card);
  }
  function decorate(root) {
    if (version !== 'new' || !root?.isConnected) return;
    const cards = [...(root.matches?.('.card') ? [root] : []), ...root.querySelectorAll('.card')];
    cards.forEach(card => {
      // Existing details keep their own keyboard behavior and open state.
      // The food pilot already owns its three native accordion cards.
      if (card.closest('.pk-ui') || card.id === 'food-module' || card.matches('details')
          || card.querySelector('[data-action="toggleMcpServerCollapsed"]')) return;
      const entry = folds.get(card);
      if (card.querySelector(':scope > details.settings-disclosure')) {
        if (entry) removeFold(card, entry);
        return;
      }
      // Let the existing legacy decorator finish these settings cards first.
      const page = card.closest('.page')?.id.replace(/^page-/, '');
      if (isSettingsDisclosureCandidate(page, card)) return;
      const heading = card.querySelector(':scope > .card-header,:scope > .admin-card-header,:scope > h2,:scope > h3,:scope > h4');
      if (!heading) return;
      if (entry?.heading === heading && entry.button.isConnected) {updateFold(card, entry); return;}
      if (entry) removeFold(card, entry);
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'pk-collapse';
      heading.classList.add('pk-card-heading');
      // No wrapping or replacement: selectors, handlers, draft fields and canvases retain identity.
      if (/^H[234]$/.test(heading.tagName)) card.insertBefore(button, heading);
      else heading.insertBefore(button, heading.firstChild);
      const toggle = () => {
        foldState.set(card, !foldState.get(card)); updateFold(card, next);
      };
      const headingClick = event => {
        if (!event.target.closest('button,a,input,select,textarea,label,[contenteditable]')) toggle();
      };
      const next = {heading, button, headingClick}; folds.set(card, next); updateFold(card, next);
      button.addEventListener('click', toggle);
      heading.addEventListener('click', headingClick);
    });
  }
  function mount(root) {
    if (version !== 'new') return;
    enhance(document.getElementById('food-module'));
    decorate(root);
    normalizeSignals(root);
  }
  function normalizeSignals(root) {
    if (!root?.isConnected) return;
    const nodes = [...(root.matches?.('[style]') ? [root] : []), ...root.querySelectorAll('[style]')];
    nodes.forEach(node => {
      if (node.matches('input,textarea,canvas,svg,img,video') || node.closest('svg')) return;
      const surface = surfaces.get(node.style.backgroundColor);
      const tone = tones.get(node.style.color) || (surface && surface !== 'strong' && node.style.color === 'rgb(255, 255, 255)' ? 'text' : null);
      if (surface) node.dataset.pkSurface = surface; else delete node.dataset.pkSurface;
      if (tone) node.dataset.pkTone = tone; else delete node.dataset.pkTone;
    });
  }
  function refresh() {
    scheduled = false;
    if (version !== 'new') return;
    for (const [card, entry] of folds) if (!card.isConnected) removeFold(card, entry);
    document.querySelectorAll('.page[data-page-loaded="true"]').forEach(mount);
  }
  function observe() {
    if (observer || !document.getElementById('app')) return;
    observer = new MutationObserver(records => {
      records.forEach(record => {
        const root = record.target.closest?.('.page');
        if (root) pendingRoots.add(root);
      });
      if (!scheduled && pendingRoots.size) {
        scheduled = true;
        queueMicrotask(() => {
          scheduled = false;
          if (version === 'new') pendingRoots.forEach(mount);
          pendingRoots.clear();
          for (const [card, entry] of folds) if (!card.isConnected) removeFold(card, entry);
        });
      }
    });
    observer.observe(document.getElementById('app'), {childList:true,subtree:true,attributes:true,attributeFilter:['style']});
  }
  function apply() {
    document.body.classList.toggle('pk-admin', version === 'new');
    const root = document.getElementById('food-module');
    if (version === 'new') {enhance(root); refresh(); observe();}
    else {
      observer?.disconnect(); observer = null;
      pendingRoots.clear();
      document.querySelectorAll('[data-pk-tone],[data-pk-surface]').forEach(node => {
        delete node.dataset.pkTone; delete node.dataset.pkSurface;
      });
      for (const [card, entry] of folds) removeFold(card, entry);
      if (layout) {
        layout.root.replaceChildren(...layout.legacyChildren);
        layout.anchors.forEach(([n, anchor]) => anchor.replaceWith(n));
        layout.changes.forEach(([n, cls]) => n.classList.remove(cls));
        layout.shell.remove(); layout = null;
      }
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
  return {apply, select, mount, components, get version() { return version; }};
})();
