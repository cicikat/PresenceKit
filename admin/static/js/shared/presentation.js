// Idempotent presentation of existing cards and semantic signals. No observer or data loading.
const AdminPresentation = (() => {
  const folds = new Map();
  const foldState = new WeakMap();
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
    if (!root?.isConnected) return;
    const cards = [...(root.matches?.('.card') ? [root] : []), ...root.querySelectorAll('.card')];
    cards.forEach(card => {
      // Existing details keep their own keyboard behavior and open state.
      // The food pilot already owns its three native accordion cards.
      if (card.closest('.pk-ui') || card.matches('details')
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
  function dispose(root) {
    for (const [card, entry] of folds) if (!root || root === card || root.contains(card)) removeFold(card, entry);
    if (root) [root, ...root.querySelectorAll('[data-pk-tone],[data-pk-surface]')].forEach(node => {
      delete node.dataset.pkTone; delete node.dataset.pkSurface;
    });
  }
  function sweep() { for (const [card, entry] of folds) if (!card.isConnected) removeFold(card, entry); }
  return Object.freeze({enhance(root) { decorate(root); normalizeSignals(root); }, decorate, dispose, sweep, components});
})();
