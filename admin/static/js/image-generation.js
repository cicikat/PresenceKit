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
      for (const [label, fn] of [[igText('编辑', 'Edit'), () => editImageGeneration(name)], [igText('测试生成', 'Test generation'), button => testImageGeneration(name, button)], [igText('删除', 'Delete'), () => deleteImageGeneration(name)]]) {
        const button = document.createElement('button'); button.className = 'btn btn-ghost btn-sm'; button.textContent = label; button.onclick = () => fn(button); actions.append(button);
      }
      const state = document.createElement('span'); state.id = 'ig-test-' + name; state.setAttribute('role','status'); actions.append(state);
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
  igEl('test_model').value = p.test_model || '';
  igEl('test_extra').value = JSON.stringify(p.test_extra_params || {}, null, 2);
  igEl('models').replaceChildren();
}

async function saveImageGeneration() {
  try {
    const extra = JSON.parse(igValue('extra') || '{}');
    if (!extra || Array.isArray(extra) || typeof extra !== 'object') throw new Error(igText('高级参数必须是 JSON 对象', 'Extra parameters must be a JSON object'));
    const testExtra = JSON.parse(igValue('test_extra') || '{}');
    if (!testExtra || Array.isArray(testExtra) || typeof testExtra !== 'object') throw new Error('测试参数必须是JSON对象');
    const body = {provider_kind: igValue('provider_kind'), extra_params: extra, test_model: igValue('test_model'), test_extra_params: testExtra};
    for (const field of ['base_url', 'model', 'description']) body[field] = igValue(field);
    if (igValue('api_key')) body.api_key = igValue('api_key');
    for (const field of ['show_to_llm', 'supports_edits']) body[field] = igEl(field).checked;
    await api('PUT', '/image-gen-presets/presets/' + encodeURIComponent(igValue('name')), body);
    igEl('api_key').value = ''; igEl('editor').hidden = true;
    await loadImageGeneration(); igStatus(igText('已保存', 'Saved'));
  } catch (e) { igStatus(e.message); }
}

async function discoverImageGeneration(button) {
  const status = igEl('model-status');
  if (button) button.disabled = true;
  status.textContent = igText('正在读取模型目录…', 'Loading model catalogue…');
  const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 25000);
  try {
    const result = await api('POST', '/image-gen-presets/discover', {base_url: igValue('base_url'), api_key: igValue('api_key'), preset_name: imageGenerationEditing, provider_kind: igValue('provider_kind'), search: igValue('search') || 'FLUX'}, {signal:controller.signal});
    const models = result.models || [];
    const options = () => models.map(id => new Option(result.model_names?.[id] ? result.model_names[id] + ' · ' + id : id, id));
    igEl('models').replaceChildren(...options());
    igEl('model-results').replaceChildren(new Option(igText('选择模型（也可直接手填）', 'Choose a model (or type an ID)'), ''), ...options());
    igEl('model-results').hidden = !models.length;
    status.textContent = result.status === 'ok' ? igText('已读取 ', 'Loaded ') + models.length + igText(' 个模型，请在下方选择。', ' models; choose below.') : igText('目录不可用，可手填模型。状态：', 'Catalogue unavailable; type a model ID. Status: ') + result.status;
    igStatus(status.textContent);
  } catch (e) {
    status.textContent = e.name === 'AbortError' ? igText('读取超时，请重试。', 'Catalogue request timed out; retry.') : e.message;
    igStatus(status.textContent);
  } finally { clearTimeout(timeout); if (button) button.disabled = false; }
}

let imageGenerationTesting = false;
async function testImageGeneration(name, button) {
  const state = document.getElementById('ig-test-' + name);
  if (imageGenerationTesting) { if(state) state.textContent = igText('正在测试其他连接，请稍候。', 'Another connection is being tested.'); return; }
  imageGenerationTesting = true;
  const originalLabel = button?.textContent;
  if(button) {button.disabled=true; button.textContent=igText('测试中…','Testing…');}
  const message = igText('正在用测试模型生成一张图，请稍候…', 'Generating one image with the test model…');
  if(state) state.textContent=message; igStatus(message);
  const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 140000);
  try {
    const result = await api('POST', '/image-gen-presets/presets/' + encodeURIComponent(name) + '/test', {}, {signal:controller.signal});
    const text = result.ok ? igText('连接测试成功', 'Connection test passed') + ` · ${result.tested_model || ''} · ${result.duration_ms} ms` + (result.same_model === false ? igText('（高级模型未执行）',' (configured premium model not executed)') : '') : (result.message || result.error_category);
    if(state) state.textContent=text; igStatus(text);
  } catch (e) {
    const text=e.name==='AbortError' ? igText('请求超时，结果未确认；请勿连续重复测试。','Request timed out; result unknown. Avoid repeated submissions.') : e.message;
    if(state) state.textContent=text; igStatus(text);
  } finally {clearTimeout(timeout);imageGenerationTesting=false;if(button){button.disabled=false;button.textContent=originalLabel;}}
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
