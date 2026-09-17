async function loadFeatureFlags() {
  const el = document.getElementById('feature-flags-grid');
  if (!el) return;
  try {
    const data = await api('GET', '/settings/feature-flags');
    _featureFlags = data.flags || {};
    el.innerHTML = Object.entries(_featureFlags).map(([name, item]) =>
      `<label class="checkbox-row" style="gap:9px;padding:9px 10px;border:1px solid var(--border);border-radius:6px"><input type="checkbox" data-feature-flag="${escapeHtml(name)}" ${item.enabled ? 'checked' : ''}><span>${escapeHtml(t('flag.' + name, item.label))}<small style="display:block;color:var(--muted)">${escapeHtml(name)} · effective: ${escapeHtml(item.effective_state || 'unknown')} · ${escapeHtml(item.apply_mode || 'unknown')}${item.restart_required ? ' (restart required)' : ''}</small>${item.description ? `<small style="display:block;color:var(--muted)">${escapeHtml(item.description)}</small>` : ''}</span></label>`
    ).join('');
  } catch (error) {
    el.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
  }
}

async function loadEventShadowRecallSettings() {
  const enabled = document.getElementById('event-shadow-enabled');
  if (!enabled) return;
  try {
    const settings = await api('GET', '/settings/event-shadow-recall');
    enabled.checked = Boolean(settings.enabled);
    document.getElementById('event-shadow-uids').value = (settings.uids || []).join('\n');
    document.getElementById('event-shadow-char-ids').value = (settings.char_ids || []).join('\n');
    document.getElementById('event-shadow-effective').textContent = `effective: ${settings.effective_state || 'unknown'}`;
    document.getElementById('event-shadow-apply-mode').textContent = `apply: ${settings.apply_mode || 'unknown'}`;
  } catch (error) { toast(error.message, 'err'); }
}

async function saveEventShadowRecallSettings() {
  try {
    const result = await api('PUT', '/settings/event-shadow-recall', {
      enabled: Boolean(document.getElementById('event-shadow-enabled')?.checked),
      uids: _shadowListValue('event-shadow-uids'),
      char_ids: _shadowListValue('event-shadow-char-ids'),
    });
    toast(`Saved · ${result.reload_status || result.apply_mode || 'unknown'}`, 'ok');
    await loadEventShadowRecallSettings();
    await loadFeatureFlags();
  } catch (error) { toast(error.message, 'err'); }
}

async function loadEventContextObserverSettings() {
  const enabled = document.getElementById('event-context-observer-enabled');
  if (!enabled) return;
  try {
    const settings = await api('GET', '/settings/event-context-observer');
    enabled.checked = settings.desired === 'observe';
    document.getElementById('event-context-observer-effective').textContent = `effective: ${settings.effective_state || 'unknown'}`;
    document.getElementById('event-context-observer-state').textContent = `state: ${settings.run_state || 'unknown'}`;
  } catch (error) { toast(error.message, 'err'); }
}

async function saveEventContextObserverSettings() {
  try {
    const result = await api('PUT', '/settings/event-context-observer', {
      mode: document.getElementById('event-context-observer-enabled')?.checked ? 'observe' : 'disabled',
    });
    toast(`Saved · ${result.reload_status || 'reloaded'}`, 'ok');
    await loadEventContextObserverSettings();
  } catch (error) { toast(error.message, 'err'); }
}

function _retirementDraftLabel(draft) {
  if (!draft || typeof draft !== 'object') return 'pending_approval · not a gate';
  const reasons = Array.isArray(draft.open_reasons) ? draft.open_reasons.join(', ') : 'thresholds_pending_approval';
  const numeric = draft.numeric_ready ? 'numeric ready' : 'numeric open';
  return `${draft.status || 'pending_approval'} · ${numeric} · used_as_gate=${Boolean(draft.used_as_gate)} · ${reasons}`;
}

function _memoryEventScopeQuery() {
  const uid = (document.getElementById('event-query-uid')?.value || '').trim();
  const charId = (document.getElementById('event-query-char-id')?.value || '').trim();
  if (!uid || !charId) throw new Error('UID and character ID are required');
  return `uid=${encodeURIComponent(uid)}&char_id=${encodeURIComponent(charId)}`;
}

function _renderMemoryEventMetrics(targetId, data, rows) {
  const target = document.getElementById(targetId);
  if (!target) return;
  if (!data.has_run) {
    target.className = 'empty';
    target.textContent = `未运行 · effective: ${data.effective_state || 'unknown'}`;
    return;
  }
  target.className = '';
  target.innerHTML = rows.map(([label, value]) =>
    `<div class="config-item-meta"><span>${escapeHtml(label)}</span><strong>${escapeHtml(String(value ?? 0))}</strong></div>`
  ).join('');
}

async function loadMemoryEventShadowObservability() {
  const target = document.getElementById('event-shadow-observability');
  try {
    const data = await api('GET', `/observability/memory-event-shadow-recall?${_memoryEventScopeQuery()}`);
    const summary = data.summary || {};
    _renderMemoryEventMetrics('event-shadow-observability', data, [
      ['effective', data.effective_state], ['schema', data.schema_health],
      ['calls', summary.calls], ['completed', summary.completed], ['timeouts', summary.timeouts],
      ['busy / cancelled', `${summary.busy || 0} / ${summary.cancelled || 0}`],
      ['rejected', summary.rejected],
      ['mapped events / turns', `${summary.mapped_events || 0} / ${summary.mapped_turns || 0}`],
      ['unmapped old / new', `${summary.unmapped_old || 0} / ${summary.unmapped_new || 0}`],
      ['average coverage', summary.average_coverage == null ? '-' : summary.average_coverage],
      ['latest date', data.latest_date || '-'],
      ['retirement draft', _retirementDraftLabel(data.retirement_draft)],
    ]);
  } catch (error) { if (target) { target.className = 'empty'; target.textContent = error.message; } }
}

async function loadMemoryEventProposerObservability() {
  const target = document.getElementById('event-proposer-observability');
  try {
    const data = await api('GET', `/observability/memory-event-edge-proposals?${_memoryEventScopeQuery()}`);
    _renderMemoryEventMetrics('event-proposer-observability', data, [
      ['effective', data.effective_state], ['schema', data.schema_health],
      ['route', data.route_effective ? `${data.route?.effective_preset || '-'} / ${data.route?.model || '-'}` : 'unresolved'],
      ['runs', data.runs], ['candidates', data.candidate_count], ['inserted candidates', data.inserted_count],
      ['duplicates', data.duplicate_count], ['failed runs', data.failed_count],
      ['discovered / eligible scopes', `${data.discovery?.candidate_directories || 0} / ${data.discovery?.eligible_scopes || 0}`],
      ['daily calls / limit', `${data.daily?.calls || 0} / ${data.daily?.call_limit || 0}`],
      ['daily tokens / limit', `${data.daily?.tokens || 0} / ${data.daily?.token_limit || 0}`],
      ['source-policy input / filtered', `${data.source_policy?.input_count || 0} / ${data.source_policy?.filtered_count || 0}`],
      ['timeouts', data.discovery?.timed_out_scopes || 0],
      ['latest run', data.latest_run_at ? new Date(data.latest_run_at * 1000).toLocaleString() : '-'],
    ]);
  } catch (error) { if (target) { target.className = 'empty'; target.textContent = error.message; } }
}

function renderMemoryEventProposerRoute(data) {
  const target = document.getElementById('mr-event-proposer-route-status');
  if (!target) return;
  const route = data.routing_effective?.[data.active_routing]?.event_edge_proposer || {};
  target.textContent = `event_edge_proposer: ${route.effective_preset || 'unresolved'} / ${route.model || 'no model'}`;
}

const _loadModelRoutingWithMemoryEventStatus = loadModelRouting;
loadModelRouting = async function loadModelRoutingWithMemoryEventStatus() {
  await _loadModelRoutingWithMemoryEventStatus();
  renderMemoryEventProposerRoute(_mrData);
};
