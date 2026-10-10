// Move the original forms between pages; field identity and drafts survive UI switching.
const AdminPageLayout = (() => {
  const behaviorPages = ['agent-runtime-browser', 'device-policy', 'output-settings', 'coplay-config', 'minecraft', 'listening-player', 'mail-config', 'diary-config', 'personal-settings'];
  const origins = [];
  const sections = new Map();
  let navOrigin = null;
  function isNew() { return AdminUI.version === 'new'; }
  function resolve(page) { return isNew() && behaviorPages.includes(page) ? 'character-behavior' : page; }
  function move(node, target) {
    const anchor = document.createComment('page-layout-origin');
    node.before(anchor); origins.push([node, anchor]); target.append(node);
  }
  function section(parent, key, title) {
    if (sections.has(key)) return sections.get(key);
    const card = document.createElement('section'); card.className = 'card behavior-section';
    const heading = document.createElement('div'); heading.className = 'card-header';
    const h = document.createElement('h2'); h.textContent = title;
    const body = document.createElement('div'); body.className = 'behavior-section-body';
    heading.append(h); card.append(heading, body); parent.append(card); sections.set(key, body);
    return body;
  }
  function navigation() {
    document.querySelectorAll('nav a[data-page]').forEach(link => {
      link.hidden = isNew() && behaviorPages.includes(link.dataset.page);
    });
    const behavior = document.querySelector('nav a[data-page="character-behavior"]');
    if (behavior) behavior.hidden = !isNew();
    const voiceLabel = document.querySelector('nav a[data-page="tts-config"] span');
    if (voiceLabel) {
      voiceLabel.dataset.i18n = isNew() ? 'stt.title' : 'nav.page.tts-config';
      voiceLabel.textContent = t(voiceLabel.dataset.i18n, isNew() ? '语音识别（STT）' : '语音合成与声音（TTS、STT）');
    }
    const voiceTitle = document.querySelector('#page-tts-config>.page-title');
    if (voiceTitle && voiceLabel) {
      voiceTitle.dataset.i18n = voiceLabel.dataset.i18n; voiceTitle.textContent = voiceLabel.textContent;
    }
    const voiceHint = document.querySelector('#page-tts-config>.admin-source-hint');
    if (voiceHint) {
      voiceHint.dataset.i18n = isNew() ? 'tts_config.stt_source_hint' : 'tts_config.source_hint';
      voiceHint.textContent = t(voiceHint.dataset.i18n);
    }
    const scheduler = document.querySelector('nav a[data-page="scheduler"]');
    if (isNew() && scheduler && !navOrigin) {
      navOrigin = document.createComment('scheduler-nav-origin'); scheduler.before(navOrigin);
      document.getElementById('navgroup-observation').prepend(scheduler);
    } else if (!isNew() && navOrigin) { navOrigin.replaceWith(scheduler); navOrigin = null; }
  }
  function restore() {
    for (const [node, anchor] of origins.splice(0).reverse()) {
      if (anchor.isConnected) anchor.replaceWith(node);
    }
    for (const body of sections.values()) body.parentElement.remove();
    sections.clear();
    document.getElementById('tts-char-select')?.closest('label')?.removeAttribute('hidden');
    navigation();
  }
  async function prepare(page, reload = false) {
    if (!isNew()) return;
    if (page === 'scheduler') {
      await loadPageFragment('autonomy-settings');
      return prepare('autonomy-settings');
    }
    if (page === 'tts-config') {
      await loadPageFragment('role-bindings');
      return prepare('role-bindings');
    }
    if (page === 'character-behavior') {
      await Promise.all(behaviorPages.map(source => loadPageFragment(source, {reload})));
      if (!isNew()) return;
      const parent = document.getElementById('page-character-behavior');
      for (const source of behaviorPages) {
        if (sections.has(source)) continue;
        const root = document.getElementById('page-' + source);
        decorateSettingsPanels(source, root);
        const title = source === 'personal-settings' ? t('settings_center.user_profile_and_anniversaries', '用户资料与纪念日')
          : document.querySelector(`nav a[data-page="${source}"]`)?.textContent || root.querySelector('.page-title')?.textContent || source;
        const body = section(parent, source, title);
        for (const node of [...root.children]) {
          if (!node.matches('.page-title,.page-context,.settings-search')) move(node, body);
        }
      }
    } else if (page === 'autonomy-settings') {
      await loadPageFragment('scheduler', {reload});
      if (!isNew() || sections.has('scheduler-config')) return;
      const body = section(document.getElementById('page-autonomy-settings'), 'scheduler-config', designText('例行触发与发言预算', 'Routine triggers and speaking budget'));
      move(document.getElementById('scheduler-config-card'), body);
      move(document.getElementById('hds-config-card'), body);
    } else if (page === 'role-bindings') {
      await loadPageFragment('tts-config', {reload});
      if (!isNew() || sections.has('role-voice')) return;
      const body = section(document.getElementById('page-role-bindings'), 'role-voice', designText('角色声音与试听', 'Character voice and preview'));
      for (const card of [...document.getElementById('page-tts-config').children]) {
        if (card.matches('.card') && card.id !== 'stt-config-card') move(card, body);
      }
      document.getElementById('tts-char-select').closest('label').hidden = true;
    }
    AdminUI.apply();
  }
  async function loadBehavior() {
    const loaders = {
      'agent-runtime-browser': loadBrowserRuntimePage,
      'device-policy': () => Promise.all([loadScreenPeekSettings(), loadMetaMode()]),
      'output-settings': loadStickerConfig,
      'coplay-config': _loadSetupCoplayGames,
      minecraft: loadMinecraftPage,
      'listening-player': refreshListeningPlayer,
      'mail-config': _loadSetupMail,
      'diary-config': _loadSetupDiary,
      'personal-settings': () => Promise.all([_loadSetupAnniversaries(), (async () => {await _ensurePronounUidOptions(); await loadUserPronoun();})()]),
    };
    await Promise.all(behaviorPages.map(async source => {
      const root = document.getElementById('page-' + source);
      if (root.dataset.pageVisited === 'true') return;
      await loaders[source](); root.dataset.pageVisited = 'true';
    }));
  }
  function apply() {
    if (!isNew()) restore(); else navigation();
  }
  function focus(source) {
    if (!isNew() || !behaviorPages.includes(source)) return;
    const body = sections.get(source);
    if (!body) return;
    const card = body.parentElement;
    if (card.dataset.pkCollapsed === 'true') card.querySelector(':scope > .card-header > .pk-collapse')?.click();
    card.scrollIntoView({block:'start'});
  }
  async function selected() {
    const current = document.querySelector('.page.active')?.id.replace(/^page-/, '');
    if (!current) return;
    const target = !isNew() && current === 'character-behavior' ? behaviorPages[0] : resolve(current);
    if (target !== current) return goto(target, {fromHistory:true});
    if (isNew() && ['role-bindings', 'autonomy-settings', 'scheduler', 'tts-config'].includes(current)) {
      await prepare(current);
      if (current === 'role-bindings') await loadRoleBindingVoice();
      if (current === 'autonomy-settings') await Promise.all([loadSchedulerConfig(), loadHdsLocalSettings()]);
    }
  }
  return {resolve, prepare, apply, restore, loadBehavior, selected, focus};
})();

window.addEventListener('admin-ui-version-changed', () => {
  AdminPageLayout.apply();
});
