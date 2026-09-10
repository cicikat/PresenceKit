let _ttsProviderParamsByProvider = {};
let _ttsLoadedProvider = 'gsv';

async function _loadTtsCharacters() {
  const select = document.getElementById('tts-char-select');
  if (!select || select.dataset.loaded === 'true') return;
  try {
    const data = await api('GET', '/characters');
    for (const char of (data.characters || [])) {
      const option = document.createElement('option');
      option.value = char.id || '';
      option.textContent = char.label || char.id || '';
      select.appendChild(option);
    }
    select.dataset.loaded = 'true';
  } catch (_error) { /* keep global-only mode */ }
}

function _renderTtsResourceSelect(id, rows, current) {
  const select = document.getElementById(id);
  if (!select) return;
  const options = ['<option value="">未选择</option>'];
  for (const row of (rows || [])) {
    options.push(`<option value="${escapeHtml(row.logical_id)}">${escapeHtml(row.name || row.logical_id)} · ${escapeHtml(row.source || '')}</option>`);
  }
  if (current && !(rows || []).some(row => row.logical_id === current || row.name === current)) {
    const legacyLabel = /[\\/]|^[A-Za-z]:/.test(String(current))
      ? t('status.tts.legacy_resource_redacted', '旧版外部资源（路径已隐藏）')
      : `${escapeHtml(current)} · ${escapeHtml(t('status.tts.unverified_resource', '外部/不可验证'))}`;
    options.push(`<option value="${escapeHtml(current)}">${legacyLabel}</option>`);
  }
  select.innerHTML = options.join('');
  select.value = current || '';
}

function _renderTtsProvider(provider) {
  const params = _ttsProviderParamsByProvider[provider] || {};
  renderKeyValueEditor('tts-provider-params', params, { exclude: ['api_key', 'api_url', 'ref_audio', 'gpt_model_path', 'sovits_model_path', 'prompt_text', 'speed'] });
  document.getElementById('tts-api-url').value = params.api_url || '';
  document.getElementById('tts-ref-audio').value = params.ref_audio || '';
  document.getElementById('tts-gpt-model-path').value = params.gpt_model_path || '';
  document.getElementById('tts-sovits-model-path').value = params.sovits_model_path || '';
  document.getElementById('tts-prompt-text').value = params.prompt_text || '';
  const keyEl = document.getElementById('tts-provider-api-key');
  if (keyEl) keyEl.value = '';
  const speed = parseFloat(params.speed) || 1.0;
  document.getElementById('tts-speed').value = speed;
  document.getElementById('tts-speed-val').textContent = speed.toFixed(2);
}

function onTtsProviderChange() {
  const next = document.getElementById('tts-provider').value;
  if (next === _ttsLoadedProvider) return;
  if (!confirm(t('status.tts.switch_discard_confirm', '切换 Provider 会放弃当前未保存的参数编辑，继续吗？'))) {
    document.getElementById('tts-provider').value = _ttsLoadedProvider;
    return;
  }
  _ttsLoadedProvider = next;
  _renderTtsProvider(next);
}

async function loadTtsConfig() {
  try {
    await _loadTtsCharacters();
    const charId = document.getElementById('tts-char-select')?.value || '';
    const d = await api('GET', `/tts-config${charId ? `?char_id=${encodeURIComponent(charId)}` : ''}`);
    document.getElementById('tts-enabled').checked = !!d.enabled;
    document.getElementById('tts-emotion-enabled').checked = !!d.emotion_enabled;
    document.getElementById('tts-desktop-enabled').checked = !!d.desktop_enabled;
    document.getElementById('tts-provider').value = d.provider || 'gsv';
    _ttsProviderParamsByProvider = d.provider_params_by_provider || {};
    _ttsProviderParamsByProvider[d.provider || 'gsv'] = d.provider_params || {};
    _ttsLoadedProvider = d.provider || 'gsv';
    _renderTtsProvider(_ttsLoadedProvider);
    const s = d.provider_status || {};
    const providerStatus = s.ready
      ? t('status.tts.ready', 'ready')
      : (s.reason || t('status.tts.not_ready', 'not ready'));
    const providerStatusEl = document.getElementById('tts-provider-status');
    if (providerStatusEl) {
      if ('value' in providerStatusEl) providerStatusEl.value = providerStatus;
      else providerStatusEl.textContent = providerStatus;
      providerStatusEl.className = `badge ${s.ready ? 'badge-success' : 'badge-danger'}`;
    }
    const binding = d.character_binding;
    const bindingEl = document.getElementById('tts-character-binding');
    if (bindingEl) bindingEl.value = binding ? `${binding.name || binding.char_id} · ${binding.tts_preset || t('status.tts.global', '全局默认')} · ${binding.preset_exists ? 'ok' : 'fallback'}` : t('status.tts.global', '全局默认');
    const options = d.resource_options || {};
    const params = d.provider_params || {};
    _renderTtsResourceSelect('tts-ref-audio', options.reference_audio, params.ref_audio || d.ref_audio || '');
    _renderTtsResourceSelect('tts-gpt-model-path', options.gpt_model, params.gpt_model_path || '');
    _renderTtsResourceSelect('tts-sovits-model-path', options.sovits_model, params.sovits_model_path || '');
    if (document.getElementById('tts-emotion-tiers')) {
      renderKeyValueEditor('tts-emotion-tiers', d.emotions || {}, {labels: {
        key: t('tts_config.emotion_key', '情绪'),
        value: t('tts_config.emotion_value', '参数'),
        type: t('common.type', '类型'),
      }});
    }
  } catch (e) { toast(t('status.tts.load_error', '读取 TTS 配置失败: {error}', {error: e.message}), 'err'); }
}
async function saveTtsConfig() {
  if (document.getElementById('tts-char-select')?.value) {
    toast(t('status.tts.role_binding_managed', 'Role TTS bindings are managed by named presets on the character page.'), 'err');
    return;
  }
  let providerParams;
  try { providerParams = readKeyValueEditor('tts-provider-params'); }
  catch (e) { toast(e.message, 'err'); return; }
  const provider = document.getElementById('tts-provider').value;
  if (provider === 'openai_compatible') { toast(t('status.tts.provider_unavailable', '该 TTS Provider 尚未实装，不能保存。'), 'err'); return; }
  providerParams.api_url = document.getElementById('tts-api-url').value.trim();
  providerParams.ref_audio = document.getElementById('tts-ref-audio').value.trim();
  providerParams.gpt_model_path = document.getElementById('tts-gpt-model-path').value.trim();
  providerParams.sovits_model_path = document.getElementById('tts-sovits-model-path').value.trim();
  providerParams.prompt_text = document.getElementById('tts-prompt-text').value.trim();
  providerParams.speed = parseFloat(document.getElementById('tts-speed').value);
  const providerApiKey = document.getElementById('tts-provider-api-key')?.value.trim();
  if (providerApiKey) providerParams.api_key = providerApiKey;
  let emotions = {};
  if (document.getElementById('tts-emotion-tiers')) {
    try { emotions = readKeyValueEditor('tts-emotion-tiers'); }
    catch (e) { toast(e.message, 'err'); return; }
  }
  const body = {
    enabled: document.getElementById('tts-enabled').checked,
    emotion_enabled: document.getElementById('tts-emotion-enabled').checked,
    desktop_enabled: document.getElementById('tts-desktop-enabled').checked,
    api_url: document.getElementById('tts-api-url').value.trim(),
    ref_audio: document.getElementById('tts-ref-audio').value.trim(),
    prompt_text: document.getElementById('tts-prompt-text').value.trim(),
    speed: parseFloat(document.getElementById('tts-speed').value),
    emotions,
    provider,
    provider_params: providerParams,
  };
  try {
    await api('PUT', '/tts-config', body);
    toast(t('status.tts.saved', 'TTS 配置已保存'), 'ok');
  } catch (e) { toast(t('common.save_failed', '保存失败: {error}', {error: e.message}), 'err'); }
}
function addTtsProviderParam() { addKeyValueRow('tts-provider-params'); }

async function loadStickerConfig() {
  try {
    const d = await api('GET', '/sticker-config');
    document.getElementById('sticker-enabled').checked = !!d.enabled;
    document.getElementById('sticker-trigger-prob').value = d.trigger_prob ?? 0.06;
  } catch (e) { toast(t('status.sticker.load_error', '读取表情包配置失败: {error}', {error: e.message}), 'err'); }
}
async function saveStickerConfig() {
  const triggerProb = Number(document.getElementById('sticker-trigger-prob').value);
  if (!Number.isFinite(triggerProb) || triggerProb < 0 || triggerProb > 1) {
    toast(t('status.sticker.invalid_prob', '触发概率必须在 0 到 1 之间'), 'err');
    return;
  }
  try {
    await api('PUT', '/sticker-config', {
      enabled: document.getElementById('sticker-enabled').checked,
      trigger_prob: triggerProb,
    });
    toast(t('status.sticker.saved', '表情包配置已保存'), 'ok');
  } catch (e) { toast(t('common.save_failed', '保存失败: {error}', {error: e.message}), 'err'); }
}
async function testTtsConfig() {
  try {
    const d = await api('POST', '/tts-config/test', { text: t('status.tts.test_text', '这是一段 TTS 配置试听。'), emotion: 'neutral', char_id: document.getElementById('tts-char-select')?.value || null });
    const audio = new Audio(`data:${d.mime};base64,${d.audio_b64}`);
    await audio.play();
    toast(t('status.tts.test_success', '试听成功 ({provider})', { provider: d.provider }), 'ok');
  } catch (e) { toast(t('status.tts.test_failed', '试听失败: {error}', { error: e.message }), 'err'); }
}
async function loadTtsCallLog() {
  try {
    const d = await api('GET', '/observability/api-calls?caller=tts&limit=10');
    document.getElementById('tts-call-log').textContent = (d.entries || []).map(x =>
      `${new Date(x.ts * 1000).toLocaleString()} | ${x.provider} | ${x.ok ? 'ok' : 'failed'} | ${x.duration_ms}ms | ${x.output_hint || ''}`
    ).join('\n') || t('status.tts.records_empty', 'No TTS synthesis records yet.');
  } catch (e) { toast(t('status.tts.call_log_load_error', '读取合成记录失败: {error}', { error: e.message }), 'err'); }
}

async function _ensurePronounUidOptions() {
  const sel = document.getElementById('pn-uid-select');
  if (sel.options.length) return;
  try {
    if (!_allUsers || !_allUsers.length) {
      const d = await api('GET', '/users/');
      _allUsers = d.users || [];
    }
    sel.innerHTML = _allUsers.map(u => `<option value="${u}">${u}</option>`).join('');
  } catch (e) { /* best-effort */ }
}
async function loadUserPronoun() {
  await _ensurePronounUidOptions();
  const uid = document.getElementById('pn-uid-select').value;
  if (!uid) return;
  try {
    const d = await api('GET', `/users/${encodeURIComponent(uid)}/pronoun`);
    document.getElementById('pn-value').value = d.pronoun || '她';
  } catch (e) { toast(t('status.pronoun.load_error', '读取称谓失败: {error}', {error: e.message}), 'err'); }
}
async function saveUserPronoun() {
  const uid = document.getElementById('pn-uid-select').value;
  if (!uid) { toast(t('status.pronoun.select_user', '请先选择用户'), 'err'); return; }
  const pronoun = document.getElementById('pn-value').value;
  try {
    await api('PATCH', `/users/${encodeURIComponent(uid)}/pronoun`, { pronoun });
    toast(t('status.pronoun.saved', '称谓已保存'), 'ok');
  } catch (e) { toast(t('common.save_failed', '保存失败: {error}', {error: e.message}), 'err'); }
}

// ══════════════════════════════════════════════════════════
//  QQ 设置
// ══════════════════════════════════════════════════════════
let _avatarBase64 = '';

async function loadQqPage() {
  await loadGroups();
}

function onAvatarFileChange(evt) {
  const file = evt.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    const dataUrl = e.target.result;
    // 去掉 "data:image/xxx;base64," 前缀
    _avatarBase64 = dataUrl.split(',')[1] || '';
    const img = document.getElementById('qq-avatar-preview');
    const ph  = document.getElementById('qq-avatar-placeholder');
    img.src = dataUrl;
    img.style.display = '';
    ph.style.display  = 'none';
  };
  reader.readAsDataURL(file);
}

async function uploadAvatar() {
  if (!_avatarBase64) { toast('请先选择图片', 'err'); return; }
  try {
    await api('PUT', '/qq-avatar', { base64: _avatarBase64 });
    toast('头像已更新', 'ok');
  } catch(e) { toast('上传失败：' + (e.message || e), 'err'); }
}

async function saveNickname() {
  const nickname = document.getElementById('qq-nickname').value.trim();
  if (!nickname) { toast('昵称不能为空', 'err'); return; }
  try {
    await api('PUT', '/qq-nickname', { nickname });
    toast('昵称已保存', 'ok');
  } catch(e) { toast('保存失败：' + (e.message || e), 'err'); }
}

async function loadGroups() {
  try {
    const data = await api('GET', '/qq-groups');
    const sel = document.getElementById('qq-group-select');
    sel.innerHTML = '<option value="">-- 选择群 --</option>';
    for (const g of (data.groups || [])) {
      const opt = document.createElement('option');
      opt.value = g.group_id;
      opt.textContent = `${g.group_name} (${g.group_id})`;
      sel.appendChild(opt);
    }
    if (!data.groups?.length) sel.innerHTML = '<option value="">暂无群（NapCat 是否已连接？）</option>';
  } catch(e) { toast('加载群列表失败：' + (e.message || e), 'err'); }
}

function onGroupChange() { /* 可扩展：切群时预填当前名片 */ }

async function saveGroupCard() {
  const group_id = document.getElementById('qq-group-select').value;
  const card     = document.getElementById('qq-group-card').value.trim();
  if (!group_id) { toast('请先选择群', 'err'); return; }
  try {
    await api('PUT', '/qq-group-card', { group_id, card });
    toast('群名片已保存', 'ok');
  } catch(e) { toast('保存失败：' + (e.message || e), 'err'); }
}

// ══════════════════════════════════════════════════════════
//  角色卡编辑器
// ══════════════════════════════════════════════════════════
// _charList: [{id, label, filename, hidden}] from GET /characters
let _charList = [];
let _charEditing = '';  // 当前正在编辑的文件名 (with extension, used by file-management endpoints)
let _charData = {};     // 当前载入的原始 JSON（保留未知字段）

function _charFilenameById(id) {
  const c = _charList.find(c => c.id === id);
  return c ? c.filename : id;
}

function _charLabelById(id) {
  const c = _charList.find(c => c.id === id);
  return c ? (c.label || c.id) : id;
}

async function loadCharacterPage() {
  await loadCharacterList();
}

async function loadCharacterList() {
  try {
    const d = await api('GET', '/characters');
    // Exclude hidden assets from the selector; editors can still open them via direct URL
    _charList = (d.characters || []).filter(c => !c.hidden);
    const activeId = d.active_id || '';
    const sel = document.getElementById('char-select');
    sel.innerHTML = '<option value="">-- 选择角色卡 --</option>' +
      _charList.map(c =>
        `<option value="${escapeHtml(c.id)}"${c.id === activeId ? ' selected' : ''}>${escapeHtml(c.label || c.id)}</option>`
      ).join('');
    // 显示活跃标记
    const badge = document.getElementById('char-active-badge');
    if (activeId) {
      badge.textContent = '当前：' + _charLabelById(activeId);
      badge.style.display = '';
    } else {
      badge.style.display = 'none';
    }
    // 自动加载当前活跃角色
    if (activeId && _charList.some(c => c.id === activeId)) {
      sel.value = activeId;
      await loadCharacterDetail(_charFilenameById(activeId));
    }
  } catch(e) { toast('加载角色卡列表失败：' + e.message, 'err'); }
}

async function onCharSelectChange() {
  const id = document.getElementById('char-select').value;
  if (!id) {
    document.getElementById('char-edit-form').style.display = 'none';
    document.getElementById('char-empty').style.display = '';
    return;
  }
  await loadCharacterDetail(_charFilenameById(id));
}

async function loadCharacterDetail(filename) {
  try {
    const d = await api('GET', `/characters/${encodeURIComponent(filename)}`);
    _charEditing = filename;
    _charData = d;
    document.getElementById('char-edit-title').textContent = `编辑：${filename}`;
    document.getElementById('char-empty').style.display = 'none';

    if (d.type === 'text') {
      document.getElementById('char-text-content').value = d.content || '';
      document.getElementById('char-text-form').style.display = '';
      document.getElementById('char-edit-form').style.display = 'none';
    } else {
      document.getElementById('char-name').value           = d.name || '';
      document.getElementById('char-gender').value          = d.gender || 'neutral';
      document.getElementById('char-scenario').value        = d.scenario || '';
      document.getElementById('char-system-prompt').value   = d.system_prompt || '';
      document.getElementById('char-description').value     = d.description || '';
      document.getElementById('char-personality').value     = d.personality || '';
      document.getElementById('char-mes-example').value     = d.mes_example || '';
      document.getElementById('char-birthday-month').value  = d.birthday?.month ?? '';
      document.getElementById('char-birthday-day').value    = d.birthday?.day   ?? '';
      document.getElementById('char-birthday-prompt').value = d.birthday?.prompt ?? '';
      _renderCharacterAnniversaries(d.anniversaries || []);
      document.getElementById('char-first-mes').value       = d.first_mes || '';
      const dreamBehavior = d.presence_ext?.dream_behavior || {};
      document.getElementById('char-dream-identity-anchor').value = dreamBehavior.identity_anchor || '';
      document.getElementById('char-dream-sandbox-directive').value = dreamBehavior.sandbox_directive || '';
      document.getElementById('char-dream-scenario-directive').value = dreamBehavior.scenario_directive || '';
      document.getElementById('char-dream-scenario-identity').value = dreamBehavior.scenario_identity || '';
      document.getElementById('char-edit-form').style.display = '';
      document.getElementById('char-text-form').style.display = 'none';
    }

  } catch(e) { toast('加载角色卡失败：' + e.message, 'err'); }
}

function _characterCapabilityId() {
  return document.getElementById('char-select')?.value || '';
}

async function saveCharacter() {
  if (!_charEditing) { toast('请先选择角色卡', 'warn'); return; }
  try {
    if (_charData && _charData.type === 'text') {
      // txt/md：直接把文本框内容整体写回
      const content = document.getElementById('char-text-content').value;
      const r = await fetch(`${BASE}/characters/${encodeURIComponent(_charEditing)}`, {
        method: 'PUT',
        headers: { 'Authorization': `Bearer ${TOKEN}`, 'Content-Type': 'application/octet-stream' },
        body: new TextEncoder().encode(content),
      });
      if (!r.ok) { const t = await r.json(); throw new Error(t.detail || r.statusText); }
      toast(`角色卡 ${_charEditing} 已保存`, 'ok');
      _charData = { ..._charData, content };
    } else {
      // json：合并编辑字段到原始数据，保留其他字段
      const bdMonth  = document.getElementById('char-birthday-month').value.trim();
      const bdDay    = document.getElementById('char-birthday-day').value.trim();
      const bdPrompt = document.getElementById('char-birthday-prompt').value.trim();
      const birthdayVal = (bdMonth || bdDay || bdPrompt)
        ? { month: bdMonth ? parseInt(bdMonth) : null, day: bdDay ? parseInt(bdDay) : null, prompt: bdPrompt }
        : null;
      const anniversariesVal = _readCharacterAnniversaries();
      if (!anniversariesVal) return;
      const dreamBehavior = {
        identity_anchor: document.getElementById('char-dream-identity-anchor').value.trim(),
        sandbox_directive: document.getElementById('char-dream-sandbox-directive').value.trim(),
        scenario_directive: document.getElementById('char-dream-scenario-directive').value.trim(),
        scenario_identity: document.getElementById('char-dream-scenario-identity').value.trim(),
      };
      Object.keys(dreamBehavior).forEach(key => { if (!dreamBehavior[key]) delete dreamBehavior[key]; });
      const presenceExt = { ...(_charData.presence_ext || {}) };
      if (Object.keys(dreamBehavior).length) presenceExt.dream_behavior = dreamBehavior;
      else delete presenceExt.dream_behavior;
      const body = {
        ..._charData,
        presence_ext:  presenceExt,
        name:          document.getElementById('char-name').value,
        gender:        document.getElementById('char-gender').value,
        scenario:      document.getElementById('char-scenario').value,
        system_prompt: document.getElementById('char-system-prompt').value,
        description:   document.getElementById('char-description').value,
        personality:   document.getElementById('char-personality').value,
        mes_example:   document.getElementById('char-mes-example').value,
        birthday:      birthdayVal,
        anniversaries: anniversariesVal,
        first_mes:     document.getElementById('char-first-mes').value,
      };
      await api('PUT', `/characters/${encodeURIComponent(_charEditing)}`, body);
      toast(`角色卡 ${_charEditing} 已保存`, 'ok');
      _charData = body;
    }
  } catch(e) { toast('保存失败：' + e.message, 'err'); }
}

function _renderCharacterAnniversaries(values) {
  renderAnniversaryEditor(document.getElementById('char-anniversaries'), values, {removeAction: 'removeCharacterAnniversary'});
}
function addCharacterAnniversary() {
  addAnniversaryEditorRow(document.getElementById('char-anniversaries'), {removeAction: 'removeCharacterAnniversary'});
}
function removeCharacterAnniversary(button) { removeAnniversaryEditorRow(button); }
function _readCharacterAnniversaries() {
  return readAnniversaryEditor(document.getElementById('char-anniversaries'), {
    onValidationError: () => toast('Each anniversary needs key, month, and day.', 'err'),
  });
}

async function setActiveCharacter() {
  const id = document.getElementById('char-select').value;
  if (!id) { toast('请先选择角色卡', 'warn'); return; }
  try {
    // Submit id (not filename) to the backend
    const d = await api('PUT', '/characters/active', { id });
    toast(d.message, 'ok');
    const badge = document.getElementById('char-active-badge');
    badge.textContent = '当前：' + (d.label || _charLabelById(id));
    badge.style.display = '';
  } catch(e) { toast('切换失败：' + e.message, 'err'); }
}

async function newCharacter() {
  const id = prompt('新建角色卡 id（用作文件名，建议字母数字/下划线/短横线）：');
  if (!id) return;
  try {
    const d = await api('POST', '/characters/new', { id: id.trim() });
    toast(d.message, 'ok');
    await loadCharacterList();
    const sel = document.getElementById('char-select');
    sel.value = d.id;
    await loadCharacterDetail(d.filename);
  } catch(e) { toast('新建失败：' + e.message, 'err'); }
}

async function uploadCharacterFile(evt) {
  const file = evt.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await fetch(`${BASE}/characters/upload`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${TOKEN}` },
      body: fd,
    });
    if (!r.ok) { const t = await r.text(); throw new Error(t); }
    const d = await r.json();
    toast(d.message, 'ok');
    await loadCharacterList();
    // 自动选中并打开刚上传的文件（通过 filename 查找对应 id）
    const uploaded = _charList.find(c => c.filename === file.name);
    if (uploaded) {
      const sel = document.getElementById('char-select');
      sel.value = uploaded.id;
      await loadCharacterDetail(uploaded.filename);
    }
  } catch(e) { toast('上传失败：' + e.message, 'err'); }
  evt.target.value = '';
}


async function exportCharacter() {
  if (!_charEditing) { toast('请先选择角色卡', 'warn'); return; }
  try {
    const resp = await fetch(`/characters/${encodeURIComponent(_charEditing)}/export`, {
      headers: { 'Authorization': `Bearer ${localStorage.getItem('qq_admin_key')||''}` }
    });
    if (!resp.ok) throw new Error(await resp.text());
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = _charEditing; a.click();
    URL.revokeObjectURL(url);
  } catch(e) { toast('导出失败：' + e.message, 'err'); }
}

async function renameCharacter() {
  if (!_charEditing) { toast('请先选择角色卡', 'warn'); return; }
  const new_name = prompt(`重命名「${_charEditing}」为：`, _charEditing);
  if (!new_name || new_name === _charEditing) return;
  try {
    const d = await api('POST', `/characters/${encodeURIComponent(_charEditing)}/rename`, { new_name });
    toast(d.message, 'ok');
    _charEditing = new_name;
    await loadCharacterList();
    // 自动选中重命名后的文件
    const renamed = _charList.find(c => c.filename === new_name);
    if (renamed) {
      document.getElementById('char-select').value = renamed.id;
      await loadCharacterDetail(renamed.filename);
    }
  } catch(e) { toast('重命名失败：' + e.message, 'err'); }
}


// ══════════════════════════════════════════════════════════
// ══════════════════════════════════════════════════════════
