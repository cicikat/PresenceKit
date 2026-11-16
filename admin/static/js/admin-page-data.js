// Aggregate controller. Layout owns node movement; this module owns source reads/retry.
const AdminPageData = (() => {
  async function loadBehavior(sources = null) {
    const loaders = {
      'device-policy': () => loadScreenPeekSettings(),
      'output-settings': () => loadStickerConfig(),
      'coplay-config': () => _loadSetupCoplayGames({inline: true}),
      minecraft: () => loadMinecraftPage(),
      'listening-player': () => refreshListeningPlayer(),
      'mail-config': () => _loadSetupMail({inline: true}),
      'diary-config': () => _loadSetupDiary({inline: true}),
      'personal-settings': () => _loadSetupAnniversaries({inline: true}),
      'conversation-settings': () => loadConversationSettings(), 'observation-center': () => loadImeObservation(),
    };
    const mounted = Object.fromEntries(AdminPageLayout.behaviorPages.filter(source => AdminPageLayout.has(source) && (!sources || sources.includes(source)))
      .map(source => [source, loaders[source]]));
    const extras = [() => loadToolsPage(), () => loadHdsLocalSettings(), () => loadVideoCallPresence()];
    await Promise.all([loadSources(mounted, {protectInputs: true}), ...AdminPageLayout.behaviorCards.flatMap(([key, , source], index) =>
      AdminPageLayout.has(key) && (!sources || sources.includes(source)) ? [Promise.resolve().then(extras[index]).catch(error => {
        console.error('[admin] behavior section failed', key, error);
        throw error;
      })] : [])]);
  }
  async function loadSources(loaders, {protectInputs = false} = {}) {
    await Promise.all(Object.entries(loaders).map(async ([source, loader]) => {
      const root = document.getElementById('page-' + source);
      if (root.dataset.pageVisited === 'true') return;
      const host = AdminPageLayout.host(source);
      let fields;
      if (protectInputs && host) {
        fields = host.querySelector(':scope > [data-source-fields]');
        if (!fields) {
          fields = document.createElement('fieldset');
          fields.dataset.sourceFields = source;
          fields.style.cssText = 'border:0;margin:0;padding:0;min-width:0';
          fields.append(...host.childNodes);
          host.append(fields);
        }
        // Keep unloaded defaults from being edited/saved. Unwrap after the read
        // finishes so the original controls and their listeners retain identity.
        fields.disabled = true;
        fields.setAttribute('aria-busy', 'true');
      }
      try {
        await loader(); root.dataset.pageVisited = 'true';
        if (fields?.isConnected) fields.replaceWith(...fields.childNodes);
      }
      catch (error) {
        if (fields?.isConnected) fields.replaceWith(...fields.childNodes);
        if (!host) throw error;
        host.querySelector(`[data-source-error="${source}"]`)?.remove();
        const status = document.createElement('p'); status.dataset.sourceError = source; status.setAttribute('role', 'alert');
        status.textContent = t('common.load_failed', '加载失败：{error}', {error:error.message});
        const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'btn btn-ghost btn-sm';
        retry.textContent = t('common.refresh', '刷新');
        retry.addEventListener('click', async () => {
          status.remove(); await loadSources({[source]:loader}, {protectInputs});
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
  async function loadData(resume=false) {
    if (!resume || !document.getElementById('creation-character')) await loadCreationAssets();
    if (!resume || !document.getElementById('character-data-char')?.value) await initObserveCharacters('character-data-char', loadCharacterData);
    if (!(AdminUI.version === 'new')) return;
    await loadSources({
      'relationship-facts': loadRelationshipFactsPage,
      'user-data': loadUserDataPage,
      'memory-consolidation': () => initObserveCharacters('memory-consolidation-char', () => {loadMemoryConsolidationStatus(); loadMemoryHistoryReconciliation();}),
    });
  }
  async function loadNetwork() { await loadSources({'network-config': async () => { _bindProxyControls(); await loadProxy(); }}); }
  async function loadObservations(page,resume=false) {
    const primary = {'feature-center':loadFeatureCenter,'auth-tokens':()=>Promise.all([loadAuthTokens(),loadChannelToggles()]),status:loadStatus,'observe-vector':loadVector,'observe-prompt':loadObservePromptUidList,'observe-dream':loadObserveDream};
    if (!resume) await primary[page]();
    if (page === 'observe-vector') await loadFoodSettings();
    if (page === 'observe-dream') await loadObserveDrinking();
    if (!(AdminUI.version === 'new')) return;
    const loaders = {
      tools:loadToolsPage,'observe-runtime-signals':loadRuntimeSignals,'observe-api-contract':loadApiContractCheck,'observe-visual':loadObserveVisual,
      'observe-resource-completeness':loadResourceCompleteness, scheduler:loadScheduler,
      'observe-char-permissions':()=>initObserveCharacters('obs-charperm-char',loadCharPermissions),
      'observe-autonomy':loadObserveAutonomy,'observe-system-diagnosis':loadSystemDiagnosis,
      'observe-dream-prompt':loadObserveDreamPromptUidList,
      'generation-debug':()=>Promise.all([loadPromptAblation(),loadMcpDebugRequests(),loadLatestAutonomyPrompt()]),
      'observe-group-arbiter':initObserveGroupArbiter,'observe-spend':loadObserveSpend,
      'observe-provenance':loadProvenance,'observe-memory-summary':()=>initObserveCharacters('obs-memory-summary-char'),
      'observe-memory-events':()=>{},'observe-memory':()=>{},'observe-tools':loadObserveToolUidList,'call-records':loadUnifiedRecords,
      'observe-dream-operations':loadObserveDreamOperations,'observe-listening':loadObserveListening,
      'observe-mood':loadObserveMood,'observe-hidden':loadObserveHidden,
    };
    await loadSources(Object.fromEntries(AdminPageLayout.observationGroups[page].pages.map(source=>[source,loaders[source]])));
  }
  async function activate(current) {
      if (current === 'character-behavior') await loadBehavior();
      if (current === 'client-settings') await loadServiceCenter();
      if (current === 'model-routing') await loadModels();
      if (current === 'data-management') await loadData(true);
      if (current === 'setup') await loadNetwork();
      if (current === 'autonomy-settings') await Promise.all([loadSchedulerConfig(), loadHdsLocalSettings()]);
      if (AdminPageLayout.observationGroups[current]) await loadObservations(current,true);
  }
  return Object.freeze({loadBehavior, loadModels, loadData, loadNetwork, loadObservations, activate});
})();
