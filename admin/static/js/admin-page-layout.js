// Move the original forms between pages; field identity and drafts survive UI switching.
const AdminPageLayout = (() => {
  const behaviorPages = ['agent-runtime-browser', 'device-policy', 'output-settings', 'coplay-config', 'minecraft', 'listening-player', 'mail-config', 'diary-config', 'personal-settings'];
  const modelPages = ['role-bindings', 'tts-config', 'local-model-runtime', 'embedding-config'];
  const dataPages = ['relationship-facts', 'user-data', 'observation-center', 'memory-consolidation'];
  const origins = [];
  const sections = new Map();
  const navOrigins = new Map();
  function isNew() { return AdminUI.version === 'new'; }
  function moduleKey(source) {
    return {'role-bindings':'model-role', 'tts-config':'model-stt', 'local-model-runtime':'model-stt', 'embedding-config':'model-embedding', 'network-config':'setup-network'}[source] || source;
  }
  function resolve(page) {
    if (!isNew()) return page;
    if (behaviorPages.includes(page)) return 'character-behavior';
    if (modelPages.includes(page)) return 'model-routing';
    if (dataPages.includes(page)) return 'data-management';
    return page === 'network-config' ? 'setup' : page;
  }
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
      link.hidden = isNew() && [...behaviorPages, ...modelPages, ...dataPages, 'network-config'].includes(link.dataset.page);
    });
    const behavior = document.querySelector('nav a[data-page="character-behavior"]');
    if (behavior) behavior.hidden = !isNew();
    const data = document.querySelector('nav a[data-page="data-management"]');
    if (data) data.hidden = !isNew();
    const modelLabel = document.querySelector('nav a[data-page="model-routing"] span');
    if (modelLabel) {
      modelLabel.dataset.i18n = isNew() ? 'nav.page.multimodal-routing' : 'nav.page.model-routing';
      modelLabel.textContent = t(modelLabel.dataset.i18n);
    }
    const modelTitle = document.querySelector('#page-model-routing>.page-title');
    if (modelTitle && modelLabel) {
      modelTitle.dataset.i18n = modelLabel.dataset.i18n; modelTitle.textContent = modelLabel.textContent;
    }
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
    for (const [page, group] of [['scheduler', 'observation'], ['runtime-config', 'features']]) {
      const link = document.querySelector(`nav a[data-page="${page}"]`);
      if (isNew() && link && !navOrigins.has(page)) {
        const anchor = document.createComment('navigation-origin'); link.before(anchor); navOrigins.set(page, anchor);
        document.getElementById('navgroup-' + group).append(link);
      } else if (!isNew() && navOrigins.has(page)) {
        navOrigins.get(page).replaceWith(link); navOrigins.delete(page);
      }
    }
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
    } else if (page === 'model-routing') {
      await Promise.all(modelPages.map(source => loadPageFragment(source, {reload})));
      if (!isNew() || sections.has('model-text')) return;
      const parent = document.getElementById('page-model-routing');
      const original = [...parent.children];
      const role = section(parent, 'model-role', designText('角色绑定与形象', 'Character bindings and appearance'));
      const text = section(parent, 'model-text', designText('文本模型', 'Text models'));
      const vision = section(parent, 'model-vision', designText('图像识别模型（VLM / OCR）', 'Image models (VLM / OCR)'));
      const stt = section(parent, 'model-stt', designText('语音识别（STT）', 'Speech recognition (STT)'));
      const tts = section(parent, 'model-tts', designText('角色声音与试听（TTS）', 'Character voice and preview (TTS)'));
      const embedding = section(parent, 'model-embedding', designText('记忆搜索模型（Embedding）', 'Memory search models (Embedding)'));
      let destination = vision;
      for (const node of original) {
        if (node.dataset.i18n === 'routing.text_heading') destination = text;
        if (node.matches('.card') && !node.classList.contains('admin-legacy-only')) move(node, destination);
      }
      const roleRoot = document.getElementById('page-role-bindings');
      for (const node of [...roleRoot.children]) if (node.matches('label,#role-binding-fields')) move(node, role);
      for (const card of [...document.getElementById('page-tts-config').children]) {
        if (card.matches('.card')) move(card, card.id === 'stt-config-card' ? stt : tts);
      }
      for (const card of [...document.getElementById('page-local-model-runtime').children]) if (card.matches('.card')) move(card, stt);
      for (const card of [...document.getElementById('page-embedding-config').children]) if (card.matches('.card')) move(card, embedding);
      document.getElementById('tts-char-select').closest('label').hidden = true;
    } else if (page === 'data-management') {
      await Promise.all(dataPages.map(source => loadPageFragment(source, {reload})));
      if (!isNew()) return;
      const parent = document.getElementById('page-data-management');
      for (const source of dataPages) {
        if (sections.has(source)) continue;
        const title = source === 'observation-center' ? t('ime.observation.title') : document.querySelector(`nav a[data-page="${source}"]`)?.textContent || source;
        const body = section(parent, source, title);
        const root = document.getElementById('page-' + source);
        if (source === 'user-data') move(document.getElementById('user-data-content'), body);
        else if (source === 'observation-center') move(document.getElementById('ime-reception-card'), body);
        else for (const node of [...root.children]) if (!node.matches('.page-title,.page-context,.settings-search')) move(node, body);
      }
    } else if (page === 'setup') {
      await loadPageFragment('network-config', {reload});
      if (!isNew() || sections.has('setup-network')) return;
      const body = section(document.getElementById('page-setup'), 'setup-network', designText('代理与手机连接', 'Proxy and mobile connection'));
      for (const node of [...document.getElementById('page-network-config').children]) if (!node.matches('.page-title,.page-context,.settings-search')) move(node, body);
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
    if (!isNew()) return;
    const body = sections.get(moduleKey(source));
    if (!body) return;
    const card = body.parentElement;
    if (card.dataset.pkCollapsed === 'true') card.querySelector(':scope > .card-header > .pk-collapse')?.click();
    card.scrollIntoView({block:'start'});
  }
  async function selected() {
    const current = document.querySelector('.page.active')?.id.replace(/^page-/, '');
    if (!current) return;
    const target = !isNew() && current === 'character-behavior' ? behaviorPages[0]
      : !isNew() && current === 'data-management' ? dataPages[0] : resolve(current);
    if (target !== current) return goto(target, {fromHistory:true});
    if (isNew() && ['model-routing', 'data-management', 'setup', 'autonomy-settings', 'scheduler'].includes(current)) {
      await prepare(current);
      if (current === 'model-routing') await loadModels();
      if (current === 'data-management') await loadData();
      if (current === 'setup') await loadNetwork();
      if (current === 'autonomy-settings') await Promise.all([loadSchedulerConfig(), loadHdsLocalSettings()]);
    }
  }
  async function loadSources(loaders) {
    await Promise.all(Object.entries(loaders).map(async ([source, loader]) => {
      const root = document.getElementById('page-' + source);
      if (root.dataset.pageVisited === 'true') return;
      try { await loader(); root.dataset.pageVisited = 'true'; }
      catch (error) {
        const host = sections.get(moduleKey(source));
        if (!host) throw error;
        host.querySelector(`[data-source-error="${source}"]`)?.remove();
        const status = document.createElement('p'); status.dataset.sourceError = source; status.setAttribute('role', 'alert');
        status.textContent = t('common.load_failed', '加载失败：{error}', {error:error.message});
        const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'btn btn-ghost btn-sm';
        retry.textContent = t('common.refresh', '刷新');
        retry.addEventListener('click', async () => {
          status.remove(); await loadSources({[source]:loader});
          const page = host.closest('.page'); decorateSettingsPanels(page.id.replace(/^page-/, ''), page); AdminUI.mount(page);
        });
        status.append(' ', retry); host.append(status);
      }
    }));
  }
  async function loadModels() {
    await loadSources({
      'role-bindings': loadRoleBindings,
      'tts-config': () => Promise.all([loadSttConfig(), loadSttVocabulary()]),
      'local-model-runtime': loadLocalModelRuntime,
      'embedding-config': loadSetupEmbedding,
    });
  }
  async function loadData() {
    await loadSources({
      'relationship-facts': loadRelationshipFactsPage,
      'user-data': loadUserDataPage,
      'observation-center': loadImeObservation,
      'memory-consolidation': () => initObserveCharacters('memory-consolidation-char', () => {loadMemoryConsolidationStatus(); loadMemoryHistoryReconciliation();}),
    });
  }
  async function loadNetwork() { await loadSources({'network-config': async () => { _bindProxyControls(); await loadProxy(); }}); }
  return {resolve, prepare, apply, restore, loadBehavior, loadModels, loadData, loadNetwork, selected, focus};
})();

window.addEventListener('admin-ui-version-changed', () => {
  AdminPageLayout.apply();
});
