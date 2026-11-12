// Image generation is independent of VLM/OCR recognition connections.
let imageGenerationCatalog = {presets: {}};
let imageGenerationEditing = null;
let imageGenerationTrigger = null;
let imageGenerationDiscovery = null;
let imageGenerationTesting = null;
const igText = (zh, en) => designText(zh, en);
const igEl = id => document.getElementById('ig-' + id);
const igValue = id => igEl(id).value.trim();
function igStatus(text, id = 'status') { igEl(id).textContent = text; }

function renderImageGenerationPresets() {
  const presets = Object.entries(imageGenerationCatalog.presets);
  if (!presets.length) {
    igEl('list').innerHTML = '<div class="empty">' + igText('暂无生图连接', 'No generation presets') + '</div>';
    return;
  }
  const rows = presets.map(([name, preset]) => {
    const escapedName = escapeHtml(name);
    const badge = (text, accent = false) => '<span class="badge' + (accent ? ' badge-accent' : '') + '">' + escapeHtml(text) + '</span>';
    const defaultBadge = name === imageGenerationCatalog.default_preset ? badge(igText('全局默认', 'Global default'), true) : '';
    const accessBadge = preset.show_to_llm !== false ? badge(igText('已公开', 'Exposed')) : badge(igText('未公开', 'Hidden'));
    const readyBadge = preset.ready ? '' : badge(igText('配置不完整', 'Incomplete'));
    const testDisabled = !preset.ready || imageGenerationTesting !== null ? 'disabled' : '';
    const testLabel = imageGenerationTesting === name ? igText('测试中…', 'Testing…') : igText('测试', 'Test');
    return `
      <tr>
        <td><strong>${escapedName}</strong> ${defaultBadge}<div>${accessBadge} ${readyBadge}</div></td>
        <td>${badge(preset.provider_kind || 'openai_compatible', true)}</td>
        <td><code>${escapeHtml(preset.model || '')}</code></td>
        <td class="admin-inline-087">${escapeHtml(preset.base_url || '')}</td>
        <td style="white-space:nowrap">
          <button class="btn btn-ghost btn-sm" data-ig-test data-preset-name="${escapedName}" onclick="testImageGeneration(this.dataset.presetName)" ${testDisabled}>${testLabel}</button>
          <button class="btn btn-ghost btn-sm" data-preset-name="${escapedName}" onclick="editImageGeneration(this.dataset.presetName, this)">${igText('编辑', 'Edit')}</button>
          <button class="btn btn-ghost btn-sm" data-preset-name="${escapedName}" onclick="deleteImageGeneration(this.dataset.presetName)">${igText('删除', 'Delete')}</button>
        </td>
      </tr>
      <tr id="ig-test-row-${escapedName}" hidden><td colspan="5" id="ig-test-${escapedName}" class="admin-inline-087" role="status"></td></tr>
    `;
  }).join('');
  igEl('list').innerHTML = `<div class="tbl-wrap"><table>
    <thead><tr><th>${igText('名称', 'Name')}</th><th>Provider</th><th>Model</th><th>Base URL</th><th>${igText('操作', 'Actions')}</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

function renderImageGenerationRouting() {
  igEl('enabled').checked = imageGenerationCatalog.enabled;
  igEl('default').replaceChildren(new Option(igText('自动选择首个可用连接', 'First available connection'), ''));
  for (const name of Object.keys(imageGenerationCatalog.presets)) {
    igEl('default').add(new Option(name, name));
  }
  igEl('default').value = imageGenerationCatalog.default_preset || '';
}

async function loadImageGeneration() {
  if (!igEl('list')) return;
  try {
    imageGenerationCatalog = await api('GET', '/image-gen-presets');
    renderImageGenerationRouting();
    renderImageGenerationPresets();
    renderImageConstraints();
    const chars = await api('GET', '/characters');
    const previous = igValue('character') || chars.active_id;
    igEl('character').replaceChildren();
    for (const c of (chars.characters || [])) {
      const id = typeof c === 'string' ? c.replace(/\.json$/, '') : (c.id || c.char_id || c.name);
      if (id) igEl('character').add(new Option(typeof c === 'string' ? id : (c.label || c.display_name || c.name || id), id));
    }
    if ([...igEl('character').options].some(o => o.value === previous)) igEl('character').value = previous;
    await loadImageGenerationCharacter();
  } catch (e) { igStatus(e.message); }
}

function editImageGeneration(name, trigger = name) {
  imageGenerationEditing = typeof name === 'string' ? name : null;
  imageGenerationTrigger = trigger;
  const p = imageGenerationCatalog.presets[imageGenerationEditing] || {};
  igEl('editor-title').textContent = imageGenerationEditing
    ? igText('编辑 Preset · ', 'Edit Preset · ') + imageGenerationEditing
    : igText('新建 Preset', 'New Preset');
  igEl('name').disabled = Boolean(imageGenerationEditing);
  igEl('name').value = imageGenerationEditing || '';
  igEl('provider_kind').value = p.provider_kind || 'openai_compatible';
  for (const field of ['base_url', 'model', 'description']) igEl(field).value = p[field] || '';
  igEl('api_key').value = '';
  igEl('key-state').textContent = p.has_api_key ? igText('已有密钥，留空保留', 'Key saved; leave blank to retain') : igText('尚无密钥', 'No saved key');
  igEl('show_to_llm').checked = p.show_to_llm !== false;
  igEl('supports_edits').checked = p.supports_edits !== false;
  igEl('extra').value = JSON.stringify(p.extra_params || {}, null, 2);
  igEl('test_model').value = p.test_model || '';
  igEl('test_extra').value = JSON.stringify(p.test_extra_params || {}, null, 2);
  igEl('search').value = 'FLUX';
  igEl('advanced').open = false;
  igStatus('', 'editor-error');
  resetImageGenerationDiscovery();
  const editor = igEl('editor');
  editor.classList.add('open');
  editor.querySelector('.modal').scrollTop = 0;
  igEl(imageGenerationEditing ? 'base_url' : 'name').focus({preventScroll: true});
}

function closeImageGeneration() {
  resetImageGenerationDiscovery();
  igEl('api_key').value = '';
  igEl('editor').classList.remove('open');
  imageGenerationEditing = null;
  imageGenerationTrigger?.focus?.();
  imageGenerationTrigger = null;
}

function resetImageGenerationDiscovery() {
  imageGenerationDiscovery?.abort();
  imageGenerationDiscovery = null;
  igEl('discover-button').disabled = false;
  igEl('search-field').hidden = igValue('provider_kind') !== 'runware';
  igEl('models').replaceChildren();
  igEl('model-results').replaceChildren();
  igEl('model-results').hidden = true;
  igStatus('', 'model-status');
}

async function saveImageGeneration() {
  const save = igEl('save');
  const cancel = igEl('cancel');
  save.disabled = cancel.disabled = true;
  igStatus('', 'editor-error');
  try {
    const extra = JSON.parse(igValue('extra') || '{}');
    if (!extra || Array.isArray(extra) || typeof extra !== 'object') throw new Error(igText('高级参数必须是 JSON 对象', 'Extra parameters must be a JSON object'));
    const testExtra = JSON.parse(igValue('test_extra') || '{}');
    if (!testExtra || Array.isArray(testExtra) || typeof testExtra !== 'object') throw new Error(igText('测试参数必须是 JSON 对象', 'Test parameters must be a JSON object'));
    const body = {provider_kind: igValue('provider_kind'), extra_params: extra, test_model: igValue('test_model'), test_extra_params: testExtra};
    for (const field of ['base_url', 'model', 'description']) body[field] = igValue(field);
    if (igValue('api_key')) body.api_key = igValue('api_key');
    for (const field of ['show_to_llm', 'supports_edits']) body[field] = igEl(field).checked;
    await api('PUT', `/image-gen-presets/presets/${encodeURIComponent(igValue('name'))}`, body);
    closeImageGeneration();
    igStatus(igText('生图连接已保存', 'Generation preset saved'));
    await loadImageGeneration();
  } catch (e) {
    igStatus(e.message, 'editor-error');
  } finally {
    save.disabled = cancel.disabled = false;
  }
}

async function discoverImageGeneration(button) {
  resetImageGenerationDiscovery();
  button.disabled = true;
  igStatus(igText('正在读取模型目录…', 'Loading model catalogue…'), 'model-status');
  const controller = new AbortController();
  imageGenerationDiscovery = controller;
  const timeout = setTimeout(() => controller.abort(), 25000);
  try {
    const result = await api('POST', '/image-gen-presets/discover', {
      base_url: igValue('base_url'), api_key: igValue('api_key'), preset_name: imageGenerationEditing,
      provider_kind: igValue('provider_kind'), search: igValue('search') || 'FLUX',
    }, {signal: controller.signal});
    if (imageGenerationDiscovery !== controller) return;
    const models = result.models || [];
    const options = () => models.map(id => new Option(result.model_names?.[id] ? result.model_names[id] + ' · ' + id : id, id));
    igEl('models').replaceChildren(...options());
    igEl('model-results').replaceChildren(new Option(igText('选择模型（也可直接手填）', 'Choose a model (or type an ID)'), ''), ...options());
    igEl('model-results').hidden = !models.length;
    const text = result.status === 'ok'
      ? igText(`已读取 ${models.length} 个模型。`, `Loaded ${models.length} models.`)
      : igText('目录不可用，可手填模型。状态：', 'Catalogue unavailable; type a model ID. Status: ') + result.status;
    igStatus(text, 'model-status');
  } catch (e) {
    if (imageGenerationDiscovery === controller) {
      igStatus(e.name === 'AbortError' ? igText('读取超时，请重试。', 'Catalogue request timed out; retry.') : e.message, 'model-status');
    }
  } finally {
    clearTimeout(timeout);
    if (imageGenerationDiscovery === controller) {
      imageGenerationDiscovery = null;
      button.disabled = false;
    }
  }
}

function showImageTestResult(name, message) {
  const row = document.getElementById('ig-test-row-' + name);
  if (row) row.hidden = false;
  const state = document.getElementById('ig-test-' + name);
  if (state) state.textContent = message;
}

async function testImageGeneration(name) {
  if (imageGenerationTesting !== null) return;
  imageGenerationTesting = name;
  const buttons = () => [...igEl('list').querySelectorAll('[data-ig-test]')];
  for (const button of buttons()) {
    button.disabled = true;
    if (button.dataset.presetName === name) button.textContent = igText('测试中…', 'Testing…');
  }
  showImageTestResult(name, igText('正在生成一张测试图…', 'Generating one test image…'));
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 140000);
  try {
    const result = await api('POST', '/image-gen-presets/presets/' + encodeURIComponent(name) + '/test', {}, {signal: controller.signal});
    const model = result.tested_model ? ' · ' + result.tested_model : '';
    const text = result.ok
      ? igText('连接测试成功', 'Connection test passed') + model + ` · ${result.duration_ms} ms` +
        (result.same_model === false ? igText('（仅验证测试模型）', ' (test model only)') : '')
      : (result.message || result.error_category) + model;
    showImageTestResult(name, text);
  } catch (e) {
    showImageTestResult(name, e.name === 'AbortError'
      ? igText('请求超时，结果未确认；请勿连续重复测试。', 'Request timed out; result unknown. Avoid repeated submissions.')
      : e.message);
  } finally {
    clearTimeout(timeout);
    imageGenerationTesting = null;
    for (const button of buttons()) {
      button.disabled = !imageGenerationCatalog.presets[button.dataset.presetName]?.ready;
      button.textContent = igText('测试', 'Test');
    }
  }
}

function deleteImageGeneration(name) {
  const warning = name === imageGenerationCatalog.default_preset
    ? igText('删除前须先更换默认生图连接。', 'Choose a different default connection before deleting this preset.')
    : igText('角色白名单中的同名项将不可用。', 'Character allowlists referencing this preset will lose access to it.');
  _openAtConfirm(igText(`删除 Preset「${name}」`, `Delete Preset “${name}”`),
    warning, async () => {
      try {
        await api('DELETE', '/image-gen-presets/presets/' + encodeURIComponent(name));
        igStatus(igText('生图连接已删除', 'Generation preset deleted'));
        await loadImageGeneration();
      } catch (e) { igStatus(e.message); }
    });
}

async function saveImageGenerationRouting(button) {
  const selected = igValue('default');
  const enabled = igEl('enabled').checked;
  button.disabled = true;
  try {
    await api('PUT', '/image-gen-presets/default-preset', {default_preset: selected});
    await api('PUT', '/image-gen-presets/enabled', {enabled});
    igStatus(igText('生图开关与默认连接已保存', 'Generation switch and default connection saved'), 'routing-status');
  } catch (e) {
    igStatus(e.message, 'routing-status');
  } finally {
    // Re-read both fields even if only the first write succeeded.
    try {
      const catalog = await api('GET', '/image-gen-presets');
      imageGenerationCatalog.enabled = catalog.enabled;
      imageGenerationCatalog.default_preset = catalog.default_preset;
      renderImageGenerationRouting();
      renderImageGenerationPresets();
    } catch (e) {
      igStatus(e.message, 'routing-status');
    } finally {
      button.disabled = false;
    }
  }
}

let imageGenerationCharacterRequest = 0;
async function loadImageGenerationCharacter() {
  const request = ++imageGenerationCharacterRequest;
  const character = igValue('character');
  const save = igEl('save-character');
  save.disabled = true;
  igEl('allowlist').replaceChildren();
  igStatus('', 'character-status');
  refreshImageGenerationReference(character);
  if (!character) return;
  try {
    const binding = await api('GET', '/character/' + encodeURIComponent(character) + '/asset-bindings');
    if (request !== imageGenerationCharacterRequest) return;
    const allowed = binding.image_gen || [];
    igEl('inherit').checked = !Array.isArray(binding.image_gen);
    const names = [...new Set([...Object.keys(imageGenerationCatalog.presets), ...allowed])];
    for (const name of names) {
      const label = document.createElement('label');
      label.className = 'admin-inline-019';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.value = name;
      input.checked = allowed.includes(name);
      label.append(input, document.createTextNode(name));
      igEl('allowlist').append(label);
    }
    save.disabled = false;
  } catch (e) {
    if (request === imageGenerationCharacterRequest) igStatus(e.message, 'character-status');
  }
}

async function saveImageGenerationCharacter(button) {
  const character = igValue('character');
  if (!character) return;
  const request = imageGenerationCharacterRequest;
  button.disabled = true;
  try {
    const names = igEl('inherit').checked ? null : [...igEl('allowlist').querySelectorAll('input:checked')].map(el => el.value);
    await api('PATCH', '/character/' + encodeURIComponent(character) + '/asset-bindings', {image_gen: names});
    if (request === imageGenerationCharacterRequest) {
      igStatus(igText('角色生图白名单已保存', 'Character generation allowlist saved'), 'character-status');
    }
  } catch (e) {
    if (request === imageGenerationCharacterRequest) igStatus(e.message, 'character-status');
  } finally {
    if (request === imageGenerationCharacterRequest) button.disabled = false;
  }
}

function renderImageConstraints() {
  const settings = imageGenerationCatalog.prompt_constraints || {presets: {}, selected: '', custom: ''};
  igEl('constraint').replaceChildren(new Option(igText('不附加约束', 'No constraints'), ''), new Option(igText('直接手写', 'Custom text'), '__custom__'));
  for (const name of Object.keys(settings.presets || {})) igEl('constraint').add(new Option(name, name));
  igEl('constraint').value = settings.selected || '';
  editImageConstraint();
}
function editImageConstraint() {
  const settings = imageGenerationCatalog.prompt_constraints || {};
  const selected = igValue('constraint');
  igEl('constraint-text').value = selected === '__custom__' ? (settings.custom || '') : ((settings.presets || {})[selected] || '');
  igEl('constraint-name').value = '';
}
async function saveImageConstraints() {
  try {
    const current = imageGenerationCatalog.prompt_constraints || {presets: {}, selected: '', custom: ''};
    const body = {selected: igValue('constraint'), custom: current.custom || '', presets: {...current.presets}};
    const name = igValue('constraint-name');
    const text = igValue('constraint-text');
    if (name) { body.presets[name] = text; body.selected = name; }
    else if (body.selected === '__custom__') body.custom = text;
    else if (body.selected) body.presets[body.selected] = text;
    else if (text) { body.custom = text; body.selected = '__custom__'; }
    imageGenerationCatalog.prompt_constraints = await api('PUT', '/image-gen-presets/prompt-constraints', body);
    renderImageConstraints();
    igStatus(igText('提示词约束已保存并选用', 'Prompt constraints saved and selected'), 'constraints-status');
  } catch (e) { igStatus(e.message, 'constraints-status'); }
}


let imageReferenceRequest = 0;
let imageReferencePreviewUrl = '';
function imageReferencePath(character) {
  return '/image-gen-presets/character-reference/' + encodeURIComponent(character);
}
async function refreshImageGenerationReference(character) {
  const request = ++imageReferenceRequest;
  const preview = igEl('reference-preview');
  const remove = igEl('reference-remove');
  preview.hidden = true;
  preview.removeAttribute('src');
  if (imageReferencePreviewUrl) URL.revokeObjectURL(imageReferencePreviewUrl);
  imageReferencePreviewUrl = '';
  igEl('reference-file').value = '';
  remove.disabled = true;
  igStatus('', 'reference-status');
  if (!character) return;
  const current = () => request === imageReferenceRequest && preview.isConnected;
  try {
    const path = imageReferencePath(character);
    const state = await api('GET', path);
    if (!current()) return;
    remove.disabled = !state.configured;
    igStatus(state.configured ? '已固定参考图' : '尚未固定参考图', 'reference-status');
    if (!state.configured) return;
    const response = await fetch(BASE + path + '/preview', {headers: authHeaders()});
    if (!response.ok) throw new Error('参考图预览加载失败');
    const blob = await response.blob();
    if (!current()) return;
    imageReferencePreviewUrl = URL.createObjectURL(blob);
    preview.src = imageReferencePreviewUrl;
    preview.hidden = false;
  } catch (e) {
    if (current()) igStatus(e.message, 'reference-status');
  }
}
async function uploadImageGenerationReference(button) {
  const character = igValue('character');
  const file = igEl('reference-file').files[0];
  if (!character || !file) {
    igStatus('请先选择角色和图片。', 'reference-status');
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    igStatus('参考图不能超过 10 MB。', 'reference-status');
    return;
  }
  const data = new FormData();
  data.append('file', file);
  const headers = authHeaders();
  delete headers['Content-Type'];
  button.disabled = true;
  try {
    const r = await fetch(BASE + imageReferencePath(character), {method: 'PUT', headers, body: data});
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || '参考图导入失败');
    if (igEl('character')?.value === character) await refreshImageGenerationReference(character);
  } catch (e) {
    if (igEl('character')?.value === character) igStatus(e.message, 'reference-status');
  } finally {
    button.disabled = false;
  }
}
async function removeImageGenerationReference(button) {
  const character = igValue('character');
  if (!character) return;
  button.disabled = true;
  try {
    await api('DELETE', imageReferencePath(character));
    if (igEl('character')?.value === character) await refreshImageGenerationReference(character);
  } catch (e) {
    if (igEl('character')?.value === character) {
      igStatus(e.message, 'reference-status');
      button.disabled = false;
    }
  }
}
