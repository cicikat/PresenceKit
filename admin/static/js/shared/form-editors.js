// AR-03 single owner; classic function exports preserve existing HTML/domain consumers.
const ANNIVERSARY_EDITOR_FIELDS = Object.freeze([
  ['key', 'key', 'text', 'Anniversary key'],
  ['month', 'month', 'number', 'Anniversary month'],
  ['day', 'day', 'number', 'Anniversary day'],
  ['year_start', 'year-start', 'number', 'Anniversary starting year'],
  ['prompt_zero', 'prompt-zero', 'text', 'Anniversary first-year prompt'],
  ['prompt_years', 'prompt-years', 'text', 'Anniversary later-years prompt'],
]);

function renderAnniversaryRow(value = {}, {removeAction = 'removeAnniversaryRow'} = {}) {
  const fields = ANNIVERSARY_EDITOR_FIELDS.map(([name, dataName, type, ariaLabel]) => {
    const rawValue = value[name] ?? '';
    const escapedValue = escapeHtml(String(rawValue));
    const placeholder = name === 'prompt_zero'
      ? 'prompt (first year)'
      : name === 'prompt_years' ? 'prompt (later years)' : name;
    const constraints = name === 'month'
      ? ' min="1" max="12"'
      : name === 'day' ? ' min="1" max="31"' : '';
    return `<input type="${type}" data-anniversary-field="${dataName}" data-${dataName} aria-label="${ariaLabel}" placeholder="${placeholder}"${constraints} value="${escapedValue}">`;
  }).join('');
  return `<div class="form-row" data-anniversary-row>${fields}<button type="button" class="btn btn-ghost btn-sm" data-action="${removeAction}">Remove</button></div>`;
}

function renderAnniversaryEditor(root, values = [], options = {}) {
  if (!root) return;
  root.innerHTML = (values.length ? values : [{}]).map(value => renderAnniversaryRow(value, options)).join('');
  bindPageActions(root);
}

function addAnniversaryEditorRow(root, options = {}) {
  if (!root) return;
  root.insertAdjacentHTML('beforeend', renderAnniversaryRow({}, options));
  bindPageActions(root);
}

function removeAnniversaryEditorRow(button) {
  const row = button?.closest('[data-anniversary-row]');
  if (!row) return false;
  row.remove();
  return true;
}

function readAnniversaryEditor(root, {onValidationError} = {}) {
  if (!root) return [];
  const result = [];
  for (const row of root.querySelectorAll('[data-anniversary-row]')) {
    const read = (name) => row.querySelector(`[data-anniversary-field="${name}"]`);
    const raw = Object.fromEntries(ANNIVERSARY_EDITOR_FIELDS.map(([name, dataName]) => [name, read(dataName)?.value || '']));
    const key = raw.key.trim();
    const month = Number(raw.month);
    const day = Number(raw.day);
    const hasValue = Object.values(raw).some(value => String(value).trim() !== '');
    if (!hasValue) continue;
    if (!key || !Number.isInteger(month) || !Number.isInteger(day)) {
      onValidationError?.();
      return null;
    }
    result.push({
      key,
      month,
      day,
      ...(raw.year_start.trim() ? {year_start: Number(raw.year_start)} : {}),
      ...(raw.prompt_zero.trim() ? {prompt_zero: raw.prompt_zero.trim()} : {}),
      ...(raw.prompt_years.trim() ? {prompt_years: raw.prompt_years.trim()} : {}),
    });
  }
  return result;
}
// Small structured editor for settings whose supported keys vary by provider.
function renderKeyValueEditor(id, values = {}, options = {}) {
  const root = document.getElementById(id);
  if (!root) return;
  root._kvOptions = options;
  const entries = Object.entries(values || {}).filter(([key]) => !options.exclude?.includes(key));
  const rows = entries.length ? entries : [['', '']];
  const labels = options.labels || {};
  const heading = options.labels
    ? `<div class="form-row kv-header"><span>${escapeHtml(labels.key || 'Name')}</span><span>${escapeHtml(labels.value || 'Value')}</span><span>${escapeHtml(labels.type || 'Type')}</span><span></span></div>`
    : '';
  root.innerHTML = heading + rows.map(([key, value]) => _renderKeyValueRow(key, value, options)).join('');
  root.querySelectorAll('[data-kv-row]').forEach((row, index) => {
    const value = entries[index]?.[1];
    const type = typeof value === 'number' ? 'number' : typeof value === 'boolean' ? 'boolean' : 'string';
    row.querySelector('[data-kv-type]').value = type;
    _syncKeyValueRowType(row);
  });
  root.querySelectorAll('[data-kv-type]').forEach(select => {
    select.addEventListener('change', () => _syncKeyValueRowType(select.closest('[data-kv-row]')));
  });
}

function _syncKeyValueRowType(row) {
  if (!row) return;
  const type = row.querySelector('[data-kv-type]')?.value;
  const text = row.querySelector('[data-kv-value]');
  const boolean = row.querySelector('[data-kv-boolean]');
  if (!text || !boolean) return;
  if (type === 'boolean') {
    if (/^(true|false)$/i.test(text.value.trim())) boolean.value = text.value.trim().toLowerCase();
    text.hidden = true;
    boolean.hidden = false;
  } else {
    if (text.hidden) text.value = boolean.value;
    text.hidden = false;
    boolean.hidden = true;
  }
}

function _renderKeyValueRow(key = '', value = '', options = {}) {
  const labels = options.labels || {};
  return `<div class="form-row" data-kv-row>
    <input type="text" data-kv-key aria-label="${escapeHtml(labels.key || 'Name')}" placeholder="${escapeHtml(options.keyPlaceholder || 'key')}" value="${escapeHtml(String(key))}">
    <input type="text" data-kv-value aria-label="${escapeHtml(labels.value || 'Value')}" placeholder="${escapeHtml(options.valuePlaceholder || 'value')}" value="${escapeHtml(value == null ? '' : String(value))}">
    <select data-kv-boolean aria-label="${escapeHtml(labels.value || 'Value')} boolean"><option value="true">true</option><option value="false">false</option></select>
    <select data-kv-type aria-label="${escapeHtml(labels.type || 'Type')}"><option value="string">text</option><option value="number">number</option><option value="boolean">true/false</option></select>
    <button type="button" class="btn btn-ghost btn-sm" data-action="removeKeyValueRow">Remove</button>
  </div>`;
}

function addKeyValueRow(id, options = undefined) {
  const root = document.getElementById(id);
  if (!root) return;
  const rowOptions = options || root._kvOptions || {};
  root.insertAdjacentHTML('beforeend', _renderKeyValueRow('', '', rowOptions));
  _syncKeyValueRowType(root.querySelector('[data-kv-row]:last-of-type'));
  root.querySelector('[data-kv-row]:last-of-type [data-kv-type]')?.addEventListener('change', event => _syncKeyValueRowType(event.target.closest('[data-kv-row]')));
  bindPageActions(root);
}

function removeKeyValueRow(button) {
  const row = button.closest('[data-kv-row]');
  const root = row?.parentElement;
  row?.remove();
  if (root && !root.querySelector('[data-kv-row]') && !root._kvOptions?.allowEmpty) addKeyValueRow(root.id);
}

function readKeyValueEditor(id) {
  const result = {};
  document.querySelectorAll(`#${CSS.escape(id)} [data-kv-row]`).forEach(row => {
    const key = row.querySelector('[data-kv-key]').value.trim();
    const raw = row.querySelector('[data-kv-value]').value;
    const type = row.querySelector('[data-kv-type]').value;
    if (!key) return;
    if (type === 'number') {
      const value = Number(raw);
      if (!Number.isFinite(value)) throw new Error(`${key} must be a number`);
      result[key] = value;
    } else if (type === 'boolean') {
      result[key] = row.querySelector('[data-kv-boolean]').value === 'true';
    } else result[key] = raw;
  });
  return result;
}
