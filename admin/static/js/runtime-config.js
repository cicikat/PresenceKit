function loadRuntimeConfig() {
  loadEventShadowRecallSettings();
  loadStateAuthoritySettings();
  loadEventContextObserverSettings();
}

async function loadWechatSettings() {
  const root = document.getElementById('wechat-state');
  if (!root) return;
  try {
    const s = await api('GET', '/settings/wechat');
    document.getElementById('wechat-enabled').checked = s.enabled;
    document.getElementById('wechat-proactive').checked = s.proactive_enabled;
    ['base_url', 'account_id', 'owner_sender_id'].forEach(key => {
      document.getElementById('wechat-' + key).value = s[key] || '';
    });
    root.textContent = `${s.effective_state} · credential: ${s.credential_configured ? 'configured' : 'missing'} · ${s.last_error || ''}`;
    document.getElementById('wechat-counters').textContent = JSON.stringify(s.counters || {}, null, 2);
  } catch (error) { root.textContent = error.message; }
}

async function saveWechatSettings() {
  try {
    await api('PUT', '/settings/wechat', {
      enabled: document.getElementById('wechat-enabled').checked,
      proactive_enabled: document.getElementById('wechat-proactive').checked,
      transport: 'openclaw_weixin',
      base_url: document.getElementById('wechat-base_url').value.trim(),
      account_id: document.getElementById('wechat-account_id').value.trim(),
      owner_sender_id: document.getElementById('wechat-owner_sender_id').value.trim(),
    });
    toast(t('common.saved', '已保存'), 'ok');
    await loadWechatSettings();
  } catch (error) { toast(error.message, 'err'); }
}

function _stateAuthorityList(id) {
  return [...new Set((document.getElementById(id)?.value || '')
    .split(/[\n,]/).map(value => value.trim()).filter(Boolean))];
}

async function loadStateAuthoritySettings() {
  const enabled = document.getElementById('state-authority-shadow-enabled');
  if (!enabled) return;
  try {
    const s = await api('GET', '/settings/state-authority');
    enabled.checked = Boolean(s.state_composer_shadow.enabled);
    document.getElementById('state-authority-shadow-uids').value = (s.state_composer_shadow.uids || []).join('\n');
    document.getElementById('state-authority-shadow-char-ids').value = (s.state_composer_shadow.char_ids || []).join('\n');
    document.getElementById('state-authority-dossier-injection').checked = Boolean(s.memory_dossiers.prompt_injection);
    document.getElementById('state-authority-dossier-suppression').value = s.memory_dossiers.suppression;
    document.getElementById('state-authority-hidden-gating').checked = Boolean(s.hidden_state.confidence_gating);
    document.getElementById('state-authority-hidden-min').value = s.hidden_state.min_confidence;
    document.getElementById('state-authority-semantic-min').value = s.recall.semantic_min_similarity;
    document.getElementById('state-authority-shadow-effective').textContent = `effective: ${s.state_composer_shadow.effective_state}`;
    document.getElementById('state-authority-apply-mode').textContent = `apply: ${s.apply_mode}`;
  } catch (error) { toast(error.message, 'err'); }
}

async function saveStateAuthoritySettings() {
  try {
    const result = await api('PUT', '/settings/state-authority', {
      shadow_enabled: Boolean(document.getElementById('state-authority-shadow-enabled')?.checked),
      shadow_uids: _stateAuthorityList('state-authority-shadow-uids'),
      shadow_char_ids: _stateAuthorityList('state-authority-shadow-char-ids'),
      dossier_prompt_injection: Boolean(document.getElementById('state-authority-dossier-injection')?.checked),
      dossier_suppression: document.getElementById('state-authority-dossier-suppression')?.value || 'global',
      hidden_confidence_gating: Boolean(document.getElementById('state-authority-hidden-gating')?.checked),
      hidden_min_confidence: Number(document.getElementById('state-authority-hidden-min')?.value),
      recall_semantic_min_similarity: Number(document.getElementById('state-authority-semantic-min')?.value),
    });
    toast(`已保存 · ${result.reload_status || 'reloaded'}`, 'ok');
    await loadStateAuthoritySettings();
  } catch (error) { toast(error.message, 'err'); }
}
