let _dreamCurrentWorld = '';
let _dreamLoreEntries  = [];
let _dreamLoreEditIdx  = -1;
let _dreamAuthoringMode = 'sandbox';
let _dreamScenarios = [];
let _dreamScenarioEditingId = null;
let _dreamCurrentPreset = '';

async function loadDreamSettings() {
  document.getElementById('dream-authoring-mode-select').value = _dreamAuthoringMode;
  onDreamAuthoringModeChange();
  await Promise.all([loadDreamWorlds(), loadStandaloneDreamPresets(), loadDreamScenarioInjectionMode()]);
}

async function loadDreamScenarioInjectionMode() {
  const toggle = document.getElementById('dream-full-script-toggle');
  const status = document.getElementById('dream-scenario-injection-status');
  if (!toggle || !status) return;
  try {
    const [settings, state] = await Promise.all([api('GET', '/dream/settings'), api('GET', '/dream/state')]);
    const nextMode = settings.scenario_injection_mode === 'full_script' ? 'full_script' : 'strict_stage';
    toggle.checked = nextMode === 'full_script';
    const activeMode = state.dream_mode === 'scenario' ? (state.scenario_injection_mode || 'strict_stage') : null;
    status.textContent = activeMode
      ? t('dream.scenario_injection.active_frozen', '当前梦已冻结为 {mode}；设置只影响下一场梦。', {mode: activeMode})
      : t('dream.scenario_injection.next', '下一场 scenario 梦：{mode}。', {mode: nextMode});
  } catch (e) {
    status.textContent = t('dream.scenario_injection.load_failed', '剧本注入模式读取失败：{error}', {error: e.message});
  }
}

async function saveDreamScenarioInjectionMode(checked) {
  const status = document.getElementById('dream-scenario-injection-status');
  try {
    await api('PATCH', '/dream/settings', {scenario_injection_mode: checked ? 'full_script' : 'strict_stage'});
    if (status) status.textContent = t('dream.scenario_injection.saved', '已保存；只影响下一场 scenario 梦。');
  } catch (e) {
    if (status) status.textContent = t('dream.scenario_injection.save_failed', '保存失败：{error}', {error: e.message});
    document.getElementById('dream-full-script-toggle').checked = !checked;
    toast(e.message, 'err');
  }
}

// ══════════════════════════════════════════════════════════
//  三模式切换（sandbox / scenario / mirror）——只切本页创作栏
// ══════════════════════════════════════════════════════════
function onDreamAuthoringModeChange() {
  const mode = document.getElementById('dream-authoring-mode-select').value;
  _dreamAuthoringMode = mode;
  document.getElementById('dream-authoring-sandbox').style.display = mode === 'sandbox' ? '' : 'none';
  document.getElementById('dream-authoring-scenario').style.display = mode === 'scenario' ? 'block' : 'none';
  document.getElementById('dream-authoring-mirror').style.display = mode === 'mirror' ? 'block' : 'none';
  if (mode === 'scenario') loadDreamScenarios();
}

async function loadDreamWorlds() {
  try {
    const d = await api('GET', '/dream/worlds');
    const sel = document.getElementById('dream-world-select');
    const prev = sel.value;
    sel.innerHTML = '<option value="">-- 选择世界 --</option>';
    (d.worlds || []).forEach(w => {
      const opt = document.createElement('option');
      opt.value = w;
      opt.textContent = w;
      sel.appendChild(opt);
    });
    if (prev && d.worlds.includes(prev)) {
      sel.value = prev;
    }
    onDreamWorldChange();
  } catch(e) {
    toast('加载世界列表失败: ' + e.message, 'err');
  }
}

async function newDreamWorld() {
  const world = prompt('新建世界名称（用作文件夹名，不能以 _ 开头）：');
  if (!world) return;
  const label = prompt('世界显示名（可留空，默认与世界名相同）：', world.trim()) || '';
  try {
    const d = await api('POST', '/dream/worlds', { world: world.trim(), label: label.trim() });
    toast(`世界 ${d.world} 已创建`, 'ok');
    await loadDreamWorlds();
    document.getElementById('dream-world-select').value = d.world;
    onDreamWorldChange();
  } catch(e) { toast('新建失败：' + e.message, 'err'); }
}

async function renameDreamWorld() {
  const world = _dreamCurrentWorld;
  if (!world) { toast('请先选择世界', 'warn'); return; }
  const new_name = prompt(`重命名「${world}」为：`, world);
  if (!new_name || new_name === world) return;
  try {
    const d = await api('PUT', `/dream/worlds/${encodeURIComponent(world)}/rename`, { new_name });
    toast(`已重命名为 ${d.world}`, 'ok');
    await loadDreamWorlds();
    document.getElementById('dream-world-select').value = d.world;
    onDreamWorldChange();
  } catch(e) { toast('重命名失败：' + e.message, 'err'); }
}

async function deleteDreamWorld() {
  const world = _dreamCurrentWorld;
  if (!world) { toast('请先选择世界', 'warn'); return; }
  if (!confirm(t('dynamic.dream.delete_world_confirm', 'Delete world “{name}”? Its lorebook will be deleted. Independent presets are kept.', { name: world }))) return;
  try {
    await api('DELETE', `/dream/worlds/${encodeURIComponent(world)}`);
    toast(`世界 ${world} 已删除`, 'ok');
    _dreamCurrentWorld = '';
    await loadDreamWorlds();
  } catch(e) { toast('删除失败：' + e.message, 'err'); }
}

function onDreamWorldChange() {
  const world = document.getElementById('dream-world-select').value;
  _dreamCurrentWorld = world;
  if (!world) {
    document.getElementById('dream-lore-card').style.display = 'none';
    return;
  }
  document.getElementById('dream-lore-card').style.display = '';
  loadDreamLore();
}

async function loadDreamLore() {
  if (!_dreamCurrentWorld) return;
  const el = document.getElementById('dream-lore-list');
  el.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const d = await api('GET', `/dream/worlds/${encodeURIComponent(_dreamCurrentWorld)}/lorebook`);
    _dreamLoreEntries = d.entries || [];
    renderDreamLore();
  } catch(e) {
    el.innerHTML = `<div class="empty">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

function renderDreamLore() {
  const el = document.getElementById('dream-lore-list');
  if (!_dreamLoreEntries.length) {
    el.innerHTML = '<div class="empty">暂无条目</div>';
    return;
  }
  const rows = _dreamLoreEntries.map((e, i) => {
    const kw = (e.keywords || []).join(', ');
    const preview = (e.content || '').slice(0, 80).replace(/\n/g, ' ');
    return `<tr>
      <td style="font-size:12px;color:var(--accent)">${escapeHtml(kw)}</td>
      <td style="font-size:12px;color:var(--muted)">${escapeHtml(preview)}…</td>
      <td style="text-align:center;white-space:nowrap">
        <button class="btn btn-ghost btn-sm" onclick="openDreamLoreModal(${i})">编辑</button>
        <button class="btn btn-danger btn-sm" style="margin-left:4px" onclick="deleteDreamLoreEntry(${i})">删除</button>
      </td>
    </tr>`;
  }).join('');
  el.innerHTML = `<div class="tbl-wrap"><table>
    <thead><tr><th>Keywords</th><th>内容预览</th><th style="width:140px">操作</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
}

async function loadStandaloneDreamPresets() {
  const select = document.getElementById('dream-preset-select');
  const text = document.getElementById('dream-preset-text');
  try {
    const [assets, settings] = await Promise.all([
      api('GET', '/dream/presets'),
      api('GET', '/dream/settings'),
    ]);
    const selected = new Set(settings.jailbreak_presets || []);
    const previous = _dreamCurrentPreset || select.value;
    select.innerHTML = '';
    (assets.presets || []).forEach(preset => {
      const option = document.createElement('option');
      option.value = preset.id;
      option.textContent = preset.label === preset.id ? preset.id : `${preset.label} (${preset.id})`;
      option.selected = selected.has(preset.id);
      select.appendChild(option);
    });
    _dreamCurrentPreset = previous && [...select.options].some(option => option.value === previous)
      ? previous
      : [...select.selectedOptions][0]?.value || select.options[0]?.value || '';
    if (_dreamCurrentPreset) {
      await loadStandaloneDreamPresetContent();
    } else {
      text.value = '';
    }
  } catch(e) {
    toast(t('dynamic.dream.presets_load_failed', 'Failed to load presets: {error}', { error: e.message }), 'err');
  }
}

async function onStandaloneDreamPresetChange() {
  _dreamCurrentPreset = document.getElementById('dream-preset-select').value;
  await loadStandaloneDreamPresetContent();
}

async function loadStandaloneDreamPresetContent() {
  if (!_dreamCurrentPreset) return;
  try {
    const d = await api('GET', `/dream/presets/${encodeURIComponent(_dreamCurrentPreset)}`);
    document.getElementById('dream-preset-text').value = d.content || '';
  } catch(e) {
    toast(t('dynamic.dream.presets_load_failed', 'Failed to load presets: {error}', { error: e.message }), 'err');
  }
}

async function saveStandaloneDreamPreset() {
  if (!_dreamCurrentPreset) {
    toast(t('dynamic.dream.select_preset', 'Select a preset first'), 'warn');
    return;
  }
  const content = document.getElementById('dream-preset-text').value;
  try {
    await api('PUT', `/dream/presets/${encodeURIComponent(_dreamCurrentPreset)}`, { content });
    toast(t('dynamic.dream.preset_saved', 'Preset saved'), 'ok');
  } catch(e) {
    toast(t('common.operation_failed', 'Operation failed: {error}', { error: e.message }), 'err');
  }
}

async function createStandaloneDreamPreset() {
  const id = prompt(t('dynamic.dream.new_preset_id', 'New preset ID (letters, numbers, _ and - only):'));
  if (!id) return;
  try {
    await api('POST', '/dream/presets', { id: id.trim(), content: '' });
    _dreamCurrentPreset = id.trim();
    await loadStandaloneDreamPresets();
    const option = [...document.getElementById('dream-preset-select').options]
      .find(item => item.value === _dreamCurrentPreset);
    if (option) option.selected = true;
    toast(t('dynamic.dream.preset_created', 'Preset created. Save the selection to use it in the next dream.'), 'ok');
  } catch(e) {
    toast(t('common.create_failed', 'Create failed: {error}', { error: e.message }), 'err');
  }
}

async function saveStandaloneDreamPresetSelection() {
  const presets = [...document.getElementById('dream-preset-select').selectedOptions]
    .map(option => option.value);
  if (!presets.length) {
    toast(t('dynamic.dream.select_one_preset', 'Select at least one preset'), 'warn');
    return;
  }
  try {
    await api('PATCH', '/dream/settings', { jailbreak_presets: presets });
    toast(t('dynamic.dream.preset_selection_saved', 'Preset selection saved for the next dream'), 'ok');
  } catch(e) {
    toast(t('common.operation_failed', 'Operation failed: {error}', { error: e.message }), 'err');
  }
}

async function deleteStandaloneDreamPreset() {
  if (!_dreamCurrentPreset) {
    toast(t('dynamic.dream.select_preset', 'Select a preset first'), 'warn');
    return;
  }
  if (!confirm(t('dynamic.dream.delete_preset_confirm', 'Delete preset “{id}”? This cannot be undone.', { id: _dreamCurrentPreset }))) return;
  try {
    await api('DELETE', `/dream/presets/${encodeURIComponent(_dreamCurrentPreset)}`);
    _dreamCurrentPreset = '';
    await loadStandaloneDreamPresets();
    toast(t('dynamic.dream.preset_deleted', 'Preset deleted'), 'ok');
  } catch(e) {
    toast(t('common.delete_failed', 'Delete failed: {error}', { error: e.message }), 'err');
  }
}

function openDreamLoreModal(idx = -1) {
  _dreamLoreEditIdx = idx;
  const modal = document.getElementById('dream-lore-modal');
  const title = document.getElementById('dream-lore-modal-title');
  if (idx >= 0 && idx < _dreamLoreEntries.length) {
    const e = _dreamLoreEntries[idx];
    title.textContent = '编辑世界书条目';
    document.getElementById('dl-keywords').value = (e.keywords || []).join(', ');
    document.getElementById('dl-content').value = e.content || '';
    document.getElementById('dl-insertion-order').value = e.insertion_order ?? 0;
    document.getElementById('dl-regex').checked = !!e.regex;
  } else {
    title.textContent = '新增世界书条目';
    document.getElementById('dl-keywords').value = '';
    document.getElementById('dl-content').value = '';
    document.getElementById('dl-insertion-order').value = _dreamLoreEntries.length;
    document.getElementById('dl-regex').checked = false;
  }
  AdminModal.open(modal, closeDreamLoreModal);
}

function closeDreamLoreModal() {
  AdminModal.close(document.getElementById('dream-lore-modal'));
}

async function saveDreamLoreEntry() {
  const world = _dreamCurrentWorld;
  if (!world) return;
  const kw = document.getElementById('dl-keywords').value.split(',').map(s => s.trim()).filter(Boolean);
  const content = document.getElementById('dl-content').value.trim();
  const insertion_order = parseInt(document.getElementById('dl-insertion-order').value) || 0;
  const regex = document.getElementById('dl-regex').checked;
  if (!kw.length) { toast('Keywords 不能为空', 'err'); return; }
  if (!content) { toast('Content 不能为空', 'err'); return; }
  try {
    if (_dreamLoreEditIdx >= 0) {
      await api('PUT', `/dream/worlds/${encodeURIComponent(world)}/lorebook/${_dreamLoreEditIdx}`,
        { keywords: kw, content, insertion_order, regex });
      toast('条目已更新', 'ok');
    } else {
      await api('POST', `/dream/worlds/${encodeURIComponent(world)}/lorebook`,
        { keywords: kw, content, insertion_order, regex });
      toast('条目已新增', 'ok');
    }
    closeDreamLoreModal();
    loadDreamLore();
  } catch(e) {
    toast('保存失败: ' + e.message, 'err');
  }
}

async function deleteDreamLoreEntry(idx) {
  const world = _dreamCurrentWorld;
  if (!world) return;
  if (!confirm(`确认删除第 ${idx} 条条目？`)) return;
  try {
    await api('DELETE', `/dream/worlds/${encodeURIComponent(world)}/lorebook/${idx}`);
    toast('条目已删除', 'ok');
    loadDreamLore();
  } catch(e) {
    toast('删除失败: ' + e.message, 'err');
  }
}

// ══════════════════════════════════════════════════════════
//  scenario 剧本创作栏（Brief 96 §3）
// ══════════════════════════════════════════════════════════
async function loadDreamScenarios() {
  const el = document.getElementById('dream-scenario-list');
  el.innerHTML = '<div class="loading">加载中…</div>';
  try {
    const d = await api('GET', '/dream/scenarios');
    _dreamScenarios = d.scenarios || [];
    renderDreamScenarios();
  } catch(e) {
    el.innerHTML = `<div class="empty">${escapeHtml(t('dream.scenario.list_failed', '加载失败：{error}', {error: e.message}))}</div>`;
  }
}

function renderDreamScenarios() {
  const el = document.getElementById('dream-scenario-list');
  if (!_dreamScenarios.length) {
    el.innerHTML = `<div class="empty">${escapeHtml(t('dream.scenario.empty', '暂无剧本'))}</div>`;
    return;
  }
  const rows = _dreamScenarios.map(s => `<tr>
    <td style="font-size:12px;color:var(--accent)">${escapeHtml(s.id)}</td>
    <td style="font-size:12px">${escapeHtml(s.title)}</td>
    <td><span class="badge">${escapeHtml(t(`dream.scenario.source_${s.source || 'user'}`, s.source === 'legacy' ? '旧路径只读' : '用户数据'))}</span>
      ${s.progressable === false ? `<span class="badge badge-warn" style="margin-left:4px">${escapeHtml(t('dream.scenario.unprogressable', '不可推进'))}${Array.isArray(s.unprogressable_stage_ids) && s.unprogressable_stage_ids.length ? ` · ${escapeHtml(s.unprogressable_stage_ids.join(', '))}` : ''}</span>` : ''}</td>
    <td style="text-align:center;white-space:nowrap">
      <button class="btn btn-ghost btn-sm" data-action="openDreamScenarioEditor" data-action-args='${escapeHtml(JSON.stringify([s.id]))}'>${escapeHtml(t('common.edit', '编辑'))}</button>
      <button class="btn btn-danger btn-sm" style="margin-left:4px" data-action="deleteDreamScenario" data-action-args='${escapeHtml(JSON.stringify([s.id]))}' ${s.source === 'legacy' ? 'disabled' : ''}>${escapeHtml(t('common.delete', '删除'))}</button>
    </td>
  </tr>`).join('');
  el.innerHTML = `<div class="tbl-wrap"><table>
    <thead><tr><th>ID</th><th>${escapeHtml(t('dream.scenario.title_label', '标题'))}</th><th>${escapeHtml(t('dream.scenario.source', '来源'))}</th><th style="width:140px">${escapeHtml(t('common.actions', '操作'))}</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>`;
  bindPageActions(el);
}

function _emptyDreamScenarioStage(index = 1) {
  return {
    id: `stage_${index}`,
    name: t('dream.scenario.default_stage_name', '阶段 {index}', {index}),
    dramatic_task: '',
    entry_pressure: '',
    exit_signs: [],
    not_yet_allowed: [],
  };
}

function _scenarioLines(value) {
  return Array.isArray(value) ? value.join('\n') : '';
}

function _emptyDreamScenarioPrivateTruth(index = 1) {
  return {id: `truth_${index}`, truth: '', disclosure: {}};
}

function _scenarioDisclosurePolicyOptions(selected) {
  const policies = [
    ['hidden', t('dream.scenario.policy_hidden', '隐藏：角色知道但不披露')],
    ['hint_only', t('dream.scenario.policy_hint_only', '仅暗示：只用允许线索')],
    ['reveal_allowed', t('dream.scenario.policy_reveal_allowed', '允许揭露：剧情自然到达时可说')],
    ['reveal_required', t('dream.scenario.policy_reveal_required', '必须揭露：本阶段让真相落地')],
  ];
  return policies.map(([value, label]) => `<option value="${value}" ${value === selected ? 'selected' : ''}>${escapeHtml(label)}</option>`).join('');
}

function _renderDreamScenarioPrivateTruths(privateTruths, stages) {
  const root = document.getElementById('ds-private-truths');
  if (!root) return;
  const values = Array.isArray(privateTruths) ? privateTruths : [];
  if (!values.length) {
    root.innerHTML = `<div class="empty">${escapeHtml(t('dream.scenario.private_truths_empty', '暂无私密真相；普通线性剧本可以留空。'))}</div>`;
    return;
  }
  root.innerHTML = values.map((item, index) => {
    const disclosure = item.disclosure && typeof item.disclosure === 'object' ? item.disclosure : {};
    const rows = (stages || []).map(stage => {
      const rule = disclosure[stage.id] || {};
      const policy = rule.policy || 'hidden';
      return `<div class="dream-scenario-disclosure-row" data-truth-disclosure>
        <label class="field"><span>${escapeHtml(t('dream.scenario.disclosure_stage', '阶段'))}</span><input type="text" value="${escapeHtml(stage.name || stage.id || '')}" disabled></label>
        <label class="field"><span>${escapeHtml(t('dream.scenario.disclosure_policy', '披露策略'))}</span><select data-truth-policy>${_scenarioDisclosurePolicyOptions(policy)}</select></label>
        <label class="field"><span>${escapeHtml(t('dream.scenario.allowed_hints', '允许线索（每行一条，仅“仅暗示”使用）'))}</span><textarea data-truth-hints>${escapeHtml(_scenarioLines(rule.allowed_hints))}</textarea></label>
      </div>`;
    }).join('');
    return `<section class="dream-scenario-private-truth" data-scenario-private-truth>
      <div class="card-header"><h4>${escapeHtml(t('dream.scenario.private_truth_number', '私密真相 {index}', {index: index + 1}))}</h4><button type="button" class="btn btn-danger btn-sm" data-action="removeDreamScenarioPrivateTruth" data-action-args='[${index}]'>${escapeHtml(t('dream.scenario.remove_private_truth', '移除私密真相'))}</button></div>
      <div class="form-row"><label class="field"><span>${escapeHtml(t('dream.scenario.private_truth_id', '真相 ID'))}</span><input type="text" data-truth-id value="${escapeHtml(item.id || '')}"></label><label class="field"><span>${escapeHtml(t('dream.scenario.private_truth_knower', '知情者'))}</span><input type="text" value="${escapeHtml(t('dream.scenario.private_truth_actor', '当前梦境角色'))}" disabled></label></div>
      <label class="field"><span>${escapeHtml(t('dream.scenario.private_truth_text', '角色始终知道的幕后真相'))}</span><textarea data-truth-text>${escapeHtml(item.truth || '')}</textarea></label>
      <div class="dream-scenario-disclosure-grid">${rows}</div>
    </section>`;
  }).join('');
  bindPageActions(root);
}

function _renderDreamScenarioStages(stages) {
  const root = document.getElementById('ds-stages');
  const values = stages?.length ? stages : [_emptyDreamScenarioStage(1)];
  root.innerHTML = values.map((stage, index) => {
    const drift = stage.drift_pressure || {};
    return `<section class="dream-scenario-stage" data-scenario-stage>
      <div class="card-header"><h4>${escapeHtml(t('dream.scenario.stage_number', '阶段 {index}', {index: index + 1}))}</h4><button type="button" class="btn btn-danger btn-sm" data-action="removeDreamScenarioStage" data-action-args='[${index}]'>${escapeHtml(t('dream.scenario.remove_stage', '移除阶段'))}</button></div>
      <div class="form-row"><label class="field"><span>${escapeHtml(t('dream.scenario.stage_id', '阶段 ID'))}</span><input type="text" data-stage-id value="${escapeHtml(stage.id || '')}"></label><label class="field"><span>${escapeHtml(t('dream.scenario.stage_name', '阶段名称'))}</span><input type="text" data-stage-name value="${escapeHtml(stage.name || '')}"></label></div>
      <label class="field"><span>${escapeHtml(t('dream.scenario.dramatic_task', '戏剧任务'))}</span><textarea data-stage-dramatic-task>${escapeHtml(stage.dramatic_task || '')}</textarea></label>
      <label class="field"><span>${escapeHtml(t('dream.scenario.entry_pressure', '入场压力 / 开场驱动'))}</span><textarea data-stage-entry-pressure>${escapeHtml(stage.entry_pressure || '')}</textarea></label>
      <div class="form-row"><label class="field"><span>${escapeHtml(t('dream.scenario.exit_signs', '阶段完成信号（每行一条）'))}</span><textarea data-stage-exit-signs>${escapeHtml(_scenarioLines(stage.exit_signs))}</textarea></label><label class="field"><span>${escapeHtml(t('dream.scenario.not_yet_allowed', '当前阶段禁止事项（每行一条）'))}</span><textarea data-stage-not-yet-allowed>${escapeHtml(_scenarioLines(stage.not_yet_allowed))}</textarea></label></div>
      <details><summary>${escapeHtml(t('dream.scenario.drift_title', '可选：停滞后加压'))}</summary><div class="form-row"><label class="field"><span>${escapeHtml(t('dream.scenario.drift_after_turns', '连续多少轮后触发'))}</span><input type="number" min="1" data-stage-drift-turns value="${escapeHtml(drift.after_turns ?? '')}"></label><label class="field"><span>${escapeHtml(t('dream.scenario.drift_instruction', '加压指令'))}</span><textarea data-stage-drift-instruction>${escapeHtml(drift.instruction || '')}</textarea></label></div></details>
    </section>`;
  }).join('');
  bindPageActions(root);
}

function _scenarioListValue(element) {
  return element.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
}

function _readDreamScenarioDocument() {
  const id = document.getElementById('ds-id').value.trim();
  const title = document.getElementById('ds-title').value.trim();
  const stages = [...document.querySelectorAll('[data-scenario-stage]')].map(stage => {
    const result = {
      id: stage.querySelector('[data-stage-id]').value.trim(),
      name: stage.querySelector('[data-stage-name]').value.trim(),
      dramatic_task: stage.querySelector('[data-stage-dramatic-task]').value.trim(),
      entry_pressure: stage.querySelector('[data-stage-entry-pressure]').value.trim(),
      exit_signs: _scenarioListValue(stage.querySelector('[data-stage-exit-signs]')),
      not_yet_allowed: _scenarioListValue(stage.querySelector('[data-stage-not-yet-allowed]')),
    };
    const turns = stage.querySelector('[data-stage-drift-turns]').value.trim();
    const instruction = stage.querySelector('[data-stage-drift-instruction]').value.trim();
    if (turns || instruction) result.drift_pressure = {after_turns: Number(turns), instruction};
    return result;
  });
  const private_truths = [...document.querySelectorAll('[data-scenario-private-truth]')].map(item => {
    const disclosure = {};
    [...item.querySelectorAll('[data-truth-disclosure]')].forEach((row, index) => {
      const stageId = stages[index]?.id;
      if (!stageId) return;
      const policy = row.querySelector('[data-truth-policy]').value;
      const allowed_hints = _scenarioListValue(row.querySelector('[data-truth-hints]'));
      disclosure[stageId] = {policy};
      if (allowed_hints.length) disclosure[stageId].allowed_hints = allowed_hints;
    });
    return {
      id: item.querySelector('[data-truth-id]').value.trim(),
      truth: item.querySelector('[data-truth-text]').value.trim(),
      disclosure,
    };
  });
  const result = {id, title, stages};
  if (private_truths.length) result.private_truths = private_truths;
  return result;
}

function addDreamScenarioStage() {
  const scenario = _readDreamScenarioDocument();
  scenario.stages.push(_emptyDreamScenarioStage(scenario.stages.length + 1));
  _renderDreamScenarioStages(scenario.stages);
  _renderDreamScenarioPrivateTruths(scenario.private_truths || [], scenario.stages);
}

function removeDreamScenarioStage(index) {
  const scenario = _readDreamScenarioDocument();
  if (scenario.stages.length <= 1) return toast(t('dream.scenario.one_stage_required', '剧本至少需要一个阶段'), 'warn');
  scenario.stages.splice(Number(index), 1);
  _renderDreamScenarioStages(scenario.stages);
  _renderDreamScenarioPrivateTruths(scenario.private_truths || [], scenario.stages);
}

function addDreamScenarioPrivateTruth() {
  const scenario = _readDreamScenarioDocument();
  const privateTruths = scenario.private_truths || [];
  privateTruths.push(_emptyDreamScenarioPrivateTruth(privateTruths.length + 1));
  _renderDreamScenarioPrivateTruths(privateTruths, scenario.stages);
}

function removeDreamScenarioPrivateTruth(index) {
  const scenario = _readDreamScenarioDocument();
  const privateTruths = scenario.private_truths || [];
  privateTruths.splice(Number(index), 1);
  _renderDreamScenarioPrivateTruths(privateTruths, scenario.stages);
}

async function openDreamScenarioEditor(id) {
  _dreamScenarioEditingId = id;
  document.getElementById('dream-scenario-editor-card').style.display = 'block';
  const fileInput = document.getElementById('ds-json-file');
  fileInput.value = '';
  onDreamScenarioFileChange(fileInput);
  const idInput = document.getElementById('ds-id');
  if (id) {
    document.getElementById('dream-scenario-editor-title').textContent = t('dream.scenario.edit_title', '编辑剧本 · {id}', {id});
    idInput.value = id;
    idInput.disabled = true;
    document.getElementById('ds-title').value = '';
    const loadingStages = [_emptyDreamScenarioStage(1)];
    _renderDreamScenarioStages(loadingStages);
    _renderDreamScenarioPrivateTruths([], loadingStages);
    try {
      const d = await api('GET', `/dream/scenarios/${encodeURIComponent(id)}`);
      const scenario = d.document || {};
      document.getElementById('ds-title').value = scenario.title || '';
      _renderDreamScenarioStages(scenario.stages || []);
      _renderDreamScenarioPrivateTruths(scenario.private_truths || [], scenario.stages || []);
    } catch(e) {
      toast(t('dream.scenario.load_failed', '读取失败: {error}', {error: e.message}), 'err');
    }
  } else {
    document.getElementById('dream-scenario-editor-title').textContent = t('dream.scenario.new_title', '新建剧本');
    idInput.value = '';
    idInput.disabled = false;
    document.getElementById('ds-title').value = '';
    const stages = [_emptyDreamScenarioStage(1)];
    _renderDreamScenarioStages(stages);
    _renderDreamScenarioPrivateTruths([], stages);
  }
}

function _dreamScenarioFileFormat(file) {
  const name = String(file?.name || '').toLowerCase();
  if (name.endsWith('.yaml') || name.endsWith('.yml')) return 'yaml';
  if (name.endsWith('.json')) return 'json';
  return '';
}

function onDreamScenarioFileChange(input) {
  const formatEl = document.getElementById('ds-import-format');
  if (!formatEl) return;
  const format = _dreamScenarioFileFormat(input?.files?.[0]);
  formatEl.textContent = format
    ? t('dream.scenario.import_format', '检测到格式：{format}', {format: format.toUpperCase()})
    : t('dream.scenario.import_format_none', '未选择文件');
}

function _applyDreamScenarioDocument(scenario) {
  const documentValue = scenario || {};
  if (!_dreamScenarioEditingId) document.getElementById('ds-id').value = String(documentValue.id || '').trim();
  document.getElementById('ds-title').value = String(documentValue.title || '').trim();
  const stages = Array.isArray(documentValue.stages) ? documentValue.stages : [];
  _renderDreamScenarioStages(stages);
  _renderDreamScenarioPrivateTruths(Array.isArray(documentValue.private_truths) ? documentValue.private_truths : [], stages);
}

function closeDreamScenarioEditor() {
  document.getElementById('dream-scenario-editor-card').style.display = 'none';
  _dreamScenarioEditingId = null;
}

async function saveDreamScenario() {
  const documentValue = _readDreamScenarioDocument();
  const id = documentValue.id;
  if (!id) { toast(t('dream.scenario.id_required', 'ID 不能为空'), 'err'); return; }
  if (!documentValue.title) { toast(t('dream.scenario.title_required', '标题不能为空'), 'err'); return; }
  try {
    if (_dreamScenarioEditingId) {
      await api('PUT', `/dream/scenarios/${encodeURIComponent(_dreamScenarioEditingId)}`, { document: documentValue });
      toast(t('dream.scenario.saved', '剧本已保存'), 'ok');
    } else {
      await api('POST', '/dream/scenarios', { id, document: documentValue });
      toast(t('dream.scenario.created', '剧本已新建'), 'ok');
    }
    closeDreamScenarioEditor();
    loadDreamScenarios();
  } catch(e) { toast(t('dream.scenario.save_failed', '保存失败: {error}', {error: e.message}), 'err'); }
}

async function importDreamScenarioJson() {
  const file = document.getElementById('ds-json-file').files?.[0];
  if (!file) return toast(t('dream.scenario.choose_file', '请先选择 YAML、YML 或 JSON 文件'), 'warn');
  const format = _dreamScenarioFileFormat(file);
  if (!format) return toast(t('dream.scenario.unsupported_file', '仅支持 .yaml、.yml 和 .json 文件'), 'warn');
  try {
    const text = await file.text();
    let scenario;
    if (format === 'yaml') {
      const payload = {yaml: text};
      if (_dreamScenarioEditingId) payload.id = _dreamScenarioEditingId;
      const result = await api('POST', '/dream/scenarios/validate', payload);
      scenario = result.document;
    } else {
      scenario = JSON.parse(text);
      if (!scenario || typeof scenario !== 'object' || Array.isArray(scenario)) {
        throw new Error(t('dream.scenario.json_object_required', '顶层必须是 JSON object'));
      }
      const importedId = String(scenario.id || '').trim();
      if (_dreamScenarioEditingId && importedId !== _dreamScenarioEditingId) {
        throw new Error(t('dream.scenario.import_id_mismatch', '导入文件的 id 必须与当前剧本一致'));
      }
    }
    _applyDreamScenarioDocument(scenario);
    toast(t('dream.scenario.imported', '剧本已导入草稿，请检查后保存'), 'ok');
  } catch (error) {
    toast(t('dream.scenario.import_failed', '导入失败：{error}', {error: error.message}), 'err');
  }
}

async function _validateDreamScenarioDraft(documentValue) {
  return api('POST', '/dream/scenarios/validate', {
    id: documentValue.id,
    document: documentValue,
  });
}

function _downloadDreamScenario(filename, content, type) {
  const blob = new Blob([content], {type});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function exportDreamScenarioYaml() {
  const scenario = _readDreamScenarioDocument();
  if (!scenario.id) return toast(t('dream.scenario.id_required', 'ID 不能为空'), 'warn');
  try {
    const result = await _validateDreamScenarioDraft(scenario);
    _downloadDreamScenario(`${result.id}.yaml`, result.yaml, 'application/yaml;charset=utf-8');
  } catch (error) {
    toast(t('dream.scenario.export_failed', '导出失败：{error}', {error: error.message}), 'err');
  }
}

async function exportDreamScenarioJson() {
  const scenario = _readDreamScenarioDocument();
  if (!scenario.id) return toast(t('dream.scenario.id_required', 'ID 不能为空'), 'warn');
  try {
    const result = await _validateDreamScenarioDraft(scenario);
    _downloadDreamScenario(`${result.id}.json`, `${JSON.stringify(result.document, null, 2)}\n`, 'application/json;charset=utf-8');
  } catch (error) {
    toast(t('dream.scenario.export_failed', '导出失败：{error}', {error: error.message}), 'err');
  }
}

async function deleteDreamScenario(id) {
  if (!confirm(t('dream.scenario.delete_confirm', '确认删除剧本“{id}”？此操作不可撤销。', {id}))) return;
  try {
    await api('DELETE', `/dream/scenarios/${encodeURIComponent(id)}`);
    toast(t('dream.scenario.deleted', '剧本已删除'), 'ok');
    if (_dreamScenarioEditingId === id) closeDreamScenarioEditor();
    loadDreamScenarios();
  } catch(e) { toast(t('dream.scenario.delete_failed', '删除失败: {error}', {error: e.message}), 'err'); }
}

// ══════════════════════════════════════════════════════════
//  印象溯源（observe-provenance）
// ══════════════════════════════════════════════════════════
