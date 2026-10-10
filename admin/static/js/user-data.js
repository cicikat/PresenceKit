const USER_DATA_GROUP_ORDER = ['stickers', 'voice', 'models'];
const USER_DATA_CATEGORY_ORDER = [
  'sticker', 'sticker_pack',
  'reference_audio', 'gpt_model', 'sovits_model',
  'live2d', 'model3d',
];

function _userDataCategoryOrder(category) {
  const index = USER_DATA_CATEGORY_ORDER.indexOf(category.id);
  return index < 0 ? USER_DATA_CATEGORY_ORDER.length : index;
}

function _userDataSourceKey(source) {
  return ['user', 'legacy', 'bundled'].includes(source) ? source : 'unknown';
}

function _userDataBaseName(value) {
  const name = String(value || '').replace(/\\/g, '/').split('/').pop() || '';
  return name.replace(/\.[^.]+$/, '');
}

function _userDataFormatSize(value) {
  const size = Number(value || 0);
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`;
  return `${(size / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

async function loadUserDataPage() {
  const el = document.getElementById('user-data-content');
  if (!el) return;
  const ui = (key, fallback, params) => t(`user_data.${key}`, fallback, params);
  const categoryLabel = category => ui(`category.${category}`, category);
  const groupLabel = group => ui(`group.${group}`, group);
  const fieldLabel = field => ui(`field.${field}`, field);

  let pageData = {categories: [], assets: [], bindings: {}, errors: []};

  const findCategory = category => pageData.categories.find(item => item.id === category);
  const assetsFor = category => pageData.assets.filter(asset => asset.category === category);

  const renderScope = (asset, meta) => {
    const values = [];
    if (meta?.scope === 'character' && asset.char_id) {
      values.push(`${ui('scope.character', 'Character')}: ${asset.char_id}`);
    }
    const scope = asset.scope || {};
    if (scope.pack) values.push(`${ui('scope.pack', 'Pack')}: ${scope.pack}`);
    if (scope.emotion) values.push(`${ui('scope.emotion', 'Emotion')}: ${scope.emotion}`);
    if (!values.length) values.push(ui('scope.global', 'Global'));
    return values.map(escapeHtml).join(' · ');
  };

  const matchesDirectBinding = (asset, binding) => {
    if (!binding) return false;
    if (asset.category === 'sticker_pack') {
      return Boolean(asset.scope?.pack && binding.sticker_pack === asset.scope.pack);
    }
    if (asset.category === 'live2d') {
      return _userDataBaseName(binding.live2d_model) === asset.logical_id;
    }
    if (asset.category === 'model3d') {
      return _userDataBaseName(binding.model_3d) === asset.logical_id;
    }
    return false;
  };

  const hasCurrentRoleBinding = asset => matchesDirectBinding(asset, pageData.bindings);

  const renderStatusBadges = (asset, meta) => {
    const badges = [];
    const source = _userDataSourceKey(asset.source);
    if (source === 'user') badges.push(`<span class="badge badge-success">${ui('status.uploaded', 'Uploaded')}</span>`);
    else badges.push(`<span class="badge badge-warn">${ui(`source.${source}`, source)}</span>`);
    if (asset.valid) badges.push(`<span class="badge badge-accent">${ui('status.backend_readable', 'Backend readable')}</span>`);
    else badges.push(`<span class="badge badge-danger">${ui('status.invalid', 'Invalid')}</span>`);
    if (hasCurrentRoleBinding(asset)) badges.push(`<span class="badge badge-accent">${ui('status.current_role_bound', 'Current role bound')}</span>`);
    if (asset.bindings?.length) {
      badges.push(`<span class="badge badge-warn">${ui('status.binding_impact', '{count} binding(s)', {count: asset.bindings.length})}</span>`);
    }
    if (asset.desktop_available && asset.valid) {
      badges.push(`<span class="badge badge-success">${ui('status.desktop_usable', 'Desktop usable')}</span>`);
    } else if (meta?.desktop_available === false || asset.availability === 'partial') {
      badges.push(`<span class="badge badge-warn">${ui('status.backend_only', 'Backend only / partial')}</span>`);
    }
    if (source !== 'user') badges.push(`<span class="badge badge-warn">${ui('status.read_only', 'Read-only')}</span>`);
    return badges.join(' ');
  };

  const renderBindingDetail = asset => {
    const direct = hasCurrentRoleBinding(asset) ? ui('status.current_role_bound', 'Current role bound') : '';
    const configured = (asset.bindings || []).map(binding => {
      const kind = binding.type === 'tts_preset' ? ui('binding.tts_preset', 'TTS preset') : ui('binding.tts_global', 'Global TTS');
      return `${kind}: ${binding.id} / ${binding.field}`;
    });
    const values = [direct, ...configured].filter(Boolean);
    return values.length ? values.map(escapeHtml).join(' · ') : ui('status.no_binding', 'No binding detected');
  };

  const renderAsset = (asset, meta) => {
    const readonly = asset.source !== 'user';
    const scope = {...(asset.scope || {})};
    if (meta?.scope === 'character' && asset.char_id) scope.char_id = asset.char_id;
    const args = JSON.stringify([asset.category, asset.logical_id, scope]);
    const action = readonly
      ? `<span class="user-data-readonly-note">${ui('status.read_only', 'Read-only')}</span>`
      : `<button type="button" class="btn btn-danger btn-sm" data-action="deleteUserDataAsset" data-action-args='${escapeHtml(args)}'>${ui('delete', 'Delete')}</button>`;
    return `<article class="user-data-asset" data-source="${escapeHtml(asset.source || 'unknown')}">
      <div class="user-data-asset-head">
        <div class="user-data-asset-title"><strong>${escapeHtml(asset.logical_id)}</strong><span class="user-data-file">${escapeHtml(asset.name || '')}</span></div>
        <div class="user-data-asset-actions">${action}</div>
      </div>
      <div class="user-data-badges">${renderStatusBadges(asset, meta)}</div>
      <dl class="user-data-asset-meta">
        <div><dt>${ui('scope.label', 'Scope')}</dt><dd>${renderScope(asset, meta)}</dd></div>
        <div><dt>${ui('source', 'Source')}</dt><dd>${escapeHtml(ui(`source.${_userDataSourceKey(asset.source)}`, asset.source || 'unknown'))}</dd></div>
        <div><dt>${ui('status', 'Status')}</dt><dd>${renderBindingDetail(asset)}</dd></div>
        <div><dt>${ui('size', 'Size')}</dt><dd>${_userDataFormatSize(asset.size)}</dd></div>
        <div><dt>${ui('updated_at', 'Updated')}</dt><dd>${asset.updated_at ? escapeHtml(new Date(Number(asset.updated_at) * 1000).toLocaleString()) : '—'}</dd></div>
      </dl>
    </article>`;
  };

  const renderAssetList = (rows, meta) => rows.length
    ? `<div class="user-data-asset-list">${rows.map(asset => renderAsset(asset, meta)).join('')}</div>`
    : `<div class="empty user-data-empty">${ui('empty.category', 'No assets in this category.')}</div>`;

  const renderStickerGroups = (rows, meta) => {
    if (!rows.length) return `<div class="empty user-data-empty">${ui('empty.category', 'No assets in this category.')}</div>`;
    const groups = new Map();
    rows.forEach(asset => {
      const scope = asset.scope || {};
      const key = meta.id === 'sticker_pack'
        ? `${scope.pack || ui('scope.unknown_pack', 'Unspecified pack')} / ${scope.emotion || ui('scope.unknown_emotion', 'Unspecified emotion')}`
        : (scope.emotion || ui('scope.unknown_emotion', 'Unspecified emotion'));
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(asset);
    });
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([key, groupRows]) =>
      `<section class="user-data-subgroup"><div class="user-data-subgroup-head"><strong>${escapeHtml(key)}</strong><span class="badge">${groupRows.length}</span></div>${renderAssetList(groupRows, meta)}</section>`
    ).join('');
  };

  const renderCategory = (meta, rows) => {
    const sources = [...new Set(rows.map(asset => asset.source).filter(Boolean))]
      .map(source => ui(`source.${_userDataSourceKey(source)}`, source)).join(' · ') || ui('empty.source', 'No source yet');
    const scope = ui(`scope.${meta.scope}`, meta.scope || '—');
    const body = meta.id === 'sticker' || meta.id === 'sticker_pack'
      ? renderStickerGroups(rows, meta)
      : renderAssetList(rows, meta);
    const note = meta.desktop_available === false ? ui('category_note.backend_only', 'Uploads are stored by the backend; desktop consumption is not connected yet.') : '';
    return `<article class="card user-data-category" data-category="${escapeHtml(meta.id)}">
      <div class="card-header"><div><h3>${escapeHtml(ui(`category.${meta.id}`, meta.label || meta.id))}</h3><p class="user-data-category-summary">${ui('category_summary', '{count} assets · {sources} · {scope}', {count: rows.length, sources, scope})}</p></div><span class="badge badge-accent">${rows.length}</span></div>
      ${note ? `<p class="admin-source-note">${note}</p>` : ''}
      ${body}
    </article>`;
  };

  const renderGroup = (group, categories, assets) => {
    const groupCategories = categories.filter(category => category.group === group).sort((a, b) => _userDataCategoryOrder(a) - _userDataCategoryOrder(b));
    if (!groupCategories.length) return '';
    const count = groupCategories.reduce((total, category) => total + assetsFor(category.id).length, 0);
    return `<section class="user-data-group" data-asset-group="${escapeHtml(group)}">
      <div class="user-data-group-head"><div><h2>${escapeHtml(groupLabel(group))}</h2><p>${ui(`group.${group}.hint`, '')}</p></div><span class="badge badge-accent">${ui('group_count', '{count} assets', {count})}</span></div>
      <div class="user-data-category-grid">${groupCategories.map(category => renderCategory(category, assets.filter(asset => asset.category === category.id))).join('')}</div>
    </section>`;
  };

  const renderUploadField = (meta, field, charId) => {
    const id = `user-data-upload-${field}`;
    if (field === 'file') {
      return `<label class="field"><span>${fieldLabel('file')}</span><input type="file" id="${id}" accept="${escapeHtml(meta.accept || '')}" required><small class="user-data-field-hint">${escapeHtml((meta.extensions || []).join(', ') || ui('file.any', 'Supported files'))} · ${ui('max_size', 'max {size}', {size: _userDataFormatSize(meta.max_bytes)})}</small></label>`;
    }
    if (field === 'char_id') {
      return `<label class="field"><span>${fieldLabel('char_id')}</span><input type="text" id="${id}" value="${escapeHtml(charId || '')}" placeholder="char_id" required></label>`;
    }
    if (field === 'emotion' || field === 'pack') {
      const category = field === 'pack' ? 'sticker_pack' : meta.id;
      const values = [...new Set(assetsFor(category).map(asset => asset.scope?.[field]).filter(Boolean))].sort();
      const listId = `${id}-options`;
      return `<label class="field"><span>${fieldLabel(field)}</span><input type="text" id="${id}" list="${listId}" placeholder="${field}" required><datalist id="${listId}">${values.map(value => `<option value="${escapeHtml(value)}"></option>`).join('')}</datalist><small class="user-data-field-hint">${ui('safe_id_hint', 'Use a safe ID; existing values can be selected.')}</small></label>`;
    }
    return `<label class="field"><span>${fieldLabel(field)}</span><input type="text" id="${id}" placeholder="${field}" required></label>`;
  };

  const renderUploadFields = () => {
    const root = document.getElementById('user-data-upload-fields');
    const categorySelect = document.getElementById('user-data-upload-category');
    if (!root || !categorySelect) return;
    const meta = findCategory(categorySelect.value) || pageData.categories[0];
    if (!meta) {
      root.replaceChildren();
      return;
    }
    root.innerHTML = `<div class="form-row user-data-upload-grid">${meta.upload_fields.map(field => renderUploadField(meta, field, document.getElementById('user-data-char')?.value || '')).join('')}</div><p class="admin-source-note user-data-upload-note">${meta.desktop_available === false ? ui('upload.backend_only', 'This category is backend-only / partial until the desktop consumer contract exists.') : ui('upload.canonical_note', 'New uploads are written to canonical user data; legacy and bundled sources stay read-only.')}</p>`;
  };

  const render = async () => {
    el.innerHTML = `<div class="loading">${ui('loading', 'Loading...')}</div>`;
    try {
      const charId = document.getElementById('user-data-char')?.value.trim() || '';
      const qs = new URLSearchParams();
      if (charId) qs.set('char_id', charId);
      const data = await api('GET', `/user-data/assets${qs.toString() ? `?${qs.toString()}` : ''}`);
      let bindings = {};
      if (charId) {
        try { bindings = await api('GET', `/character/${encodeURIComponent(charId)}/asset-bindings`); } catch (_error) { /* Optional role binding detail. */ }
      }
      pageData = {categories: data.categories || [], assets: data.assets || [], bindings, errors: data.errors || []};
      el.innerHTML = `<div class="card user-data-toolbar"><div class="admin-toolbar"><label class="field"><span>${ui('character', 'Character')}</span><input type="text" id="user-data-char" value="${escapeHtml(charId)}" placeholder="char_id"></label><button type="button" class="btn btn-ghost btn-sm" data-action="reloadUserData">${ui('refresh', 'Refresh')}</button></div><p class="admin-source-note">${ui('toolbar_note', 'The inventory is grouped by logical category. Physical roots are intentionally not shown.')}</p></div>`;
      el.insertAdjacentHTML('beforeend', `<div class="user-data-groups">${USER_DATA_GROUP_ORDER.map(group => renderGroup(group, pageData.categories, pageData.assets)).join('')}</div>`);
      for (const error of pageData.errors) {
        const host = el.querySelector(`[data-category="${error.category}"]`);
        if (host) {
          const notice = document.createElement('p');
          notice.className = 'admin-error-panel'; notice.setAttribute('role', 'alert');
          notice.textContent = error.message;
          host.querySelector('.user-data-empty')?.remove();
          host.append(notice);
        }
      }
      el.insertAdjacentHTML('beforeend', `<div class="card user-data-upload"><div class="card-header"><div><h2>${ui('upload.title', 'Upload authored asset')}</h2><p class="user-data-category-summary">${ui('upload.subtitle', 'Only fields required by the selected category are shown.')}</p></div></div><div class="form-row"><label class="field"><span>${ui('upload.category', 'Asset category')}</span><select id="user-data-upload-category">${pageData.categories.map(category => `<option value="${escapeHtml(category.id)}">${escapeHtml(categoryLabel(category.id))}</option>`).join('')}</select></label></div><div id="user-data-upload-fields"></div><div class="admin-action-group"><button type="button" class="btn btn-primary" data-action="uploadUserDataAsset">${ui('upload.action', 'Upload')}</button></div><div id="user-data-status" class="admin-status-text" aria-live="polite"></div></div>`);
      el.insertAdjacentHTML('beforeend', `<details class="card user-data-advanced"><summary>${ui('advanced.title', 'Advanced inventory table')}</summary><p class="admin-source-note">${ui('advanced.note', 'Diagnostic view only; the grouped cards above are the primary interface.')}</p><div class="tbl-wrap"><table class="tbl"><thead><tr><th>${ui('logical_id', 'Logical ID')}</th><th>${ui('category', 'Category')}</th><th>${ui('scope.label', 'Scope')}</th><th>${ui('source', 'Source')}</th><th>${ui('status', 'Status')}</th><th>${ui('size', 'Size')}</th><th>${ui('updated_at', 'Updated')}</th></tr></thead><tbody>${pageData.assets.length ? pageData.assets.map(asset => `<tr><td>${escapeHtml(asset.logical_id)}</td><td>${escapeHtml(categoryLabel(asset.category))}</td><td>${renderScope(asset, findCategory(asset.category))}</td><td>${escapeHtml(ui(`source.${_userDataSourceKey(asset.source)}`, asset.source || 'unknown'))}</td><td>${renderStatusBadges(asset, findCategory(asset.category))}</td><td>${_userDataFormatSize(asset.size)}</td><td>${asset.updated_at ? escapeHtml(new Date(Number(asset.updated_at) * 1000).toLocaleString()) : '—'}</td></tr>`).join('') : `<tr><td colspan="7" class="empty">${ui('empty.all', 'No assets found.')}</td></tr>`}</tbody></table></div></details>`);
      renderUploadFields();
      document.getElementById('user-data-upload-category')?.addEventListener('change', renderUploadFields);
      bindPageActions(el);
    } catch (e) {
      el.innerHTML = `<div class="admin-error-panel">${ui('load_failed', 'Load failed: {error}', {error: e.message})}</div>`;
    }
  };

  window.reloadUserData = render;
  window.uploadUserDataAsset = async () => {
    const category = document.getElementById('user-data-upload-category')?.value;
    const meta = findCategory(category);
    if (!meta) return toast(ui('upload.no_category', 'Choose an asset category first.'), 'error');
    const file = document.getElementById('user-data-upload-file')?.files?.[0];
    if (!file) return toast(ui('choose_file', 'Choose a file first.'), 'error');
    const values = {};
    for (const field of meta.upload_fields) {
      if (field === 'file') continue;
      values[field] = document.getElementById(`user-data-upload-${field}`)?.value.trim() || '';
      if (!values[field]) return toast(ui('field_required', '{field} is required.', {field: fieldLabel(field)}), 'error');
    }
    const form = new FormData();
    form.append('category', category);
    form.append('logical_id', values.logical_id);
    form.append('file', file, file.name);
    for (const field of ['char_id', 'emotion', 'pack']) if (values[field]) form.append(field, values[field]);
    try {
      const resp = await fetch(BASE + '/user-data/assets', {method: 'POST', headers: {Authorization: `Bearer ${TOKEN}`}, body: form});
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try { detail = (await resp.json()).detail || detail; } catch (_error) { detail = await resp.text(); }
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      }
      toast(ui('upload.success', 'Uploaded {logicalId}.', {logicalId: values.logical_id}));
      await render();
    } catch (e) {
      toast(ui('upload.failed', 'Upload failed: {error}', {error: e.message}), 'error');
    }
  };

  window.deleteUserDataAsset = async (category, logicalId, scope = {}) => {
    const query = new URLSearchParams(scope).toString();
    try {
      const impact = await api('GET', `/user-data/assets/${encodeURIComponent(category)}/${encodeURIComponent(logicalId)}/impact${query ? `?${query}` : ''}`);
      const bindingText = (impact.bindings || []).map(binding => `${binding.id}/${binding.field}`).join(', ') || ui('status.no_binding', 'No binding detected');
      if (!impact.can_delete) return toast(ui('delete_blocked', 'This asset is read-only or still bound: {bindings}', {bindings: bindingText}), 'error');
      if (!confirm(ui('delete_confirm', 'Delete {category} {logicalId} ({scope})?\nBinding impact: {bindings}', {category: categoryLabel(category), logicalId, scope: Object.values(scope).filter(Boolean).join(' · ') || ui('scope.global', 'Global'), bindings: bindingText}))) return;
      await api('DELETE', `/user-data/assets/${encodeURIComponent(category)}/${encodeURIComponent(logicalId)}`, scope);
      toast(ui('delete.success', 'Deleted {logicalId}.', {logicalId}));
      await render();
    } catch (e) {
      toast(ui('delete.failed', 'Delete failed: {error}', {error: e.message}), 'error');
    }
  };

  await render();
}
