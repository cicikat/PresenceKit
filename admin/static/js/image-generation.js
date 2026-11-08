// Image generation is independent of VLM/OCR recognition connections.
let imageGenerationCatalog = {presets: {}};
let imageGenerationEditing = null;
const igText = (zh, en) => designText(zh, en);
const igEl = id => document.getElementById('ig-' + id);
const igValue = id => igEl(id).value.trim();
function igStatus(text) { igEl('status').textContent = text; }

async function loadImageGeneration() {
  if (!igEl('list')) return;
  try {
    imageGenerationCatalog = await api('GET', '/image-gen-presets');
    renderImageConstraints();
    igEl('enabled').checked = imageGenerationCatalog.enabled;
    igEl('default').replaceChildren(new Option(igText('自动选择首个可用连接', 'First available connection'), ''));
    igEl('list').replaceChildren();
    for (const [name, p] of Object.entries(imageGenerationCatalog.presets)) {
      igEl('default').add(new Option(name, name));
      const row = document.createElement('tr');
      for (const text of [name, p.model, p.ready ? igText('已配置（尚需测试）', 'Configured (test required)') : igText('未配置完整', 'Incomplete')]) {
        const td = document.createElement('td'); td.textContent = text; row.append(td);
      }
      const actions = document.createElement('td');
      for (const [label, fn] of [[igText('编辑', 'Edit'), () => editImageGeneration(name)], [igText('测试生成', 'Test generation'), () => testImageGeneration(name)], [igText('删除', 'Delete'), () => deleteImageGeneration(name)]]) {
        const button = document.createElement('button'); button.className = 'btn btn-ghost btn-sm'; button.textContent = label; button.onclick = fn; actions.append(button);
      }
      row.append(actions); igEl('list').append(row);
    }
    igEl('default').value = imageGenerationCatalog.default_preset || '';
    const chars = await api('GET', '/characters');
    const previous = igEl('character').value;
    igEl('character').replaceChildren();
    for (const c of (chars.characters || [])) {
      const id = typeof c === 'string' ? c.replace(/\.json$/, '') : (c.id || c.char_id || c.name);
      if (id) igEl('character').add(new Option(typeof c === 'string' ? id : (c.label || c.display_name || c.name || id), id));
    }
    if ([...igEl('character').options].some(o => o.value === previous)) igEl('character').value = previous;
    await loadImageGenerationCharacter();
  } catch (e) { igStatus(e.message); }
}

function editImageGeneration(name = null) {
  name = typeof name === 'string' ? name : null;
  imageGenerationEditing = name;
  const p = imageGenerationCatalog.presets[name] || {};
  igEl('provider_kind').value = p.provider_kind || 'openai_compatible';
  igEl('editor').hidden = false;
  igEl('name').disabled = Boolean(name); igEl('name').value = name || '';
  for (const field of ['base_url', 'model', 'description']) igEl(field).value = p[field] || '';
  igEl('api_key').value = '';
  igEl('key-state').textContent = p.has_api_key ? igText('已有密钥，留空保留', 'Key saved; leave blank to retain') : igText('尚无密钥', 'No saved key');
  igEl('show_to_llm').checked = p.show_to_llm !== false;
  igEl('supports_edits').checked = p.supports_edits !== false;
  igEl('extra').value = JSON.stringify(p.extra_params || {}, null, 2);
  igEl('models').replaceChildren();
}

async function saveImageGeneration() {
  try {
    const extra = JSON.parse(igValue('extra') || '{}');
    if (!extra || Array.isArray(extra) || typeof extra !== 'object') throw new Error(igText('高级参数必须是 JSON 对象', 'Extra parameters must be a JSON object'));
    const body = {provider_kind: igValue('provider_kind'), extra_params: extra};
    for (const field of ['base_url', 'model', 'description']) body[field] = igValue(field);
    if (igValue('api_key')) body.api_key = igValue('api_key');
    for (const field of ['show_to_llm', 'supports_edits']) body[field] = igEl(field).checked;
    await api('PUT', '/image-gen-presets/presets/' + encodeURIComponent(igValue('name')), body);
    igEl('api_key').value = ''; igEl('editor').hidden = true;
    await loadImageGeneration(); igStatus(igText('已保存', 'Saved'));
  } catch (e) { igStatus(e.message); }
}

async function discoverImageGeneration() {
  try {
    const result = await api('POST', '/image-gen-presets/discover', {base_url: igValue('base_url'), api_key: igValue('api_key'), preset_name: imageGenerationEditing, provider_kind: igValue('provider_kind'), search: igValue('search') || 'FLUX'});
    igEl('models').replaceChildren(...(result.models || []).map(id => new Option(id, id)));
    igStatus(result.status === 'ok' ? igText('已读取模型；目录可能混有文本模型，请选择支持 Images 的模型。', 'Models loaded; select an Images-capable model from the mixed catalogue.') : igText('无法读取列表，可手动输入模型 ID。状态：', 'Catalogue unavailable; enter a model ID manually. Status: ') + result.status);
  } catch (e) { igStatus(e.message); }
}

let imageGenerationTesting = false;
async function testImageGeneration(name) {
  if (imageGenerationTesting) return;
  if (!confirm(igText('将使用已保存的连接真实生成一张测试图，可能产生费用。继续？', 'Generate one test image using the saved connection? This may incur a charge.'))) return;
  imageGenerationTesting = true; igStatus(igText('正在生成测试图，请稍候…', 'Generating test image…'));
  try {
    const result = await api('POST', '/image-gen-presets/presets/' + encodeURIComponent(name) + '/test');
    igStatus(result.ok ? igText('生成成功', 'Generation succeeded') + ` · ${result.duration_ms} ms · ${result.bytes} B` : (result.message || result.error_category));
  } catch (e) { igStatus(e.message); }
  finally { imageGenerationTesting = false; }
}

async function deleteImageGeneration(name) {
  if (!confirm(igText('删除这个生图连接？角色白名单中的同名项将不可用。', 'Delete this connection? Character allowlists referencing it will become unavailable.'))) return;
  try { await api('DELETE', '/image-gen-presets/presets/' + encodeURIComponent(name)); await loadImageGeneration(); }
  catch (e) { igStatus(e.message); }
}

async function saveImageGenerationRouting() {
  try {
    await api('PUT', '/image-gen-presets/default-preset', {default_preset: igValue('default')});
    await api('PUT', '/image-gen-presets/enabled', {enabled: igEl('enabled').checked});
    igStatus(igText('生图开关与默认连接已保存', 'Generation switch and default connection saved'));
  } catch (e) { igStatus(e.message); }
}

async function loadImageGenerationCharacter() {
  igEl('allowlist').replaceChildren();
  if (!igValue('character')) return;
  const binding = await api('GET', '/character/' + encodeURIComponent(igValue('character')) + '/asset-bindings');
  igEl('inherit').checked = !Array.isArray(binding.image_gen);
  const names = [...new Set([...Object.keys(imageGenerationCatalog.presets), ...(binding.image_gen || [])])];
  for (const name of names) {
    const label = document.createElement('label'); label.className = 'field';
    const input = document.createElement('input'); input.type = 'checkbox'; input.value = name; input.checked = (binding.image_gen || []).includes(name);
    label.append(input, document.createTextNode(name)); igEl('allowlist').append(label);
  }
}

async function saveImageGenerationCharacter() {
  if (!igValue('character')) return;
  try {
    const names = igEl('inherit').checked ? null : [...igEl('allowlist').querySelectorAll('input:checked')].map(el => el.value);
    await api('PATCH', '/character/' + encodeURIComponent(igValue('character')) + '/asset-bindings', {image_gen: names});
    igStatus(igText('角色生图白名单已保存', 'Character generation allowlist saved'));
  } catch (e) { igStatus(e.message); }
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
    renderImageConstraints(); igStatus(igText('提示词约束已保存并选用', 'Prompt constraints saved and selected'));
  } catch (e) { igStatus(e.message); }
}
