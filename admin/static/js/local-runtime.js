// 本地模型运行页（工单 D2）。只接本地 STT；远程连接在 tts-config 页。
const LOCAL_RUNTIME_SIZE_COST = Object.freeze({
  tiny: ['local_runtime.size.tiny', 'tiny — 约 75 MB 内存，最快，准确度最低'],
  base: ['local_runtime.size.base', 'base — 约 150 MB，较快；在较长语音段上可能明显变慢'],
  small: ['local_runtime.size.small', 'small（默认）— 约 500 MB，CPU 可用，速度与准确度较均衡'],
  medium: ['local_runtime.size.medium', 'medium — 约 1.5 GB，更准，CPU 上明显变慢'],
  'large-v3': ['local_runtime.size.large', 'large-v3 — 约 3 GB，最准，建议有独立显卡时使用'],
});
const LOCAL_RUNTIME_DEVICE_COST = Object.freeze({
  auto: ['local_runtime.device.auto', 'auto（推荐）— 能用显卡就用，跑不通自动回落 CPU，并在「实际生效状态」里写明原因'],
  cpu: ['local_runtime.device.cpu', 'cpu — 任何机器都能用，较慢'],
  cuda: ['local_runtime.device.cuda', 'cuda — 只用显卡；跑不通时保存会失败，不会偷偷回落 CPU'],
});
const LOCAL_RUNTIME_COMPUTE_COST = Object.freeze({
  int8: ['local_runtime.compute.int8', 'int8 — CPU 上最快、占内存少，精度略降（推荐）'],
  int8_float16: ['local_runtime.compute.int8_float16', 'int8_float16 — 仅显卡，省显存'],
  float16: ['local_runtime.compute.float16', 'float16 — 仅显卡，精度高，占显存多'],
  float32: ['local_runtime.compute.float32', 'float32 — 最准、最慢、最占内存'],
});

function _lrText(entry) { return t(entry[0], entry[1]); }

function _lrFill(select, values, costs) {
  const previous = select.value;
  select.replaceChildren(...values.map(value => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = costs[value] ? _lrText(costs[value]) : value;
    return option;
  }));
  if (previous && values.includes(previous)) select.value = previous;
}

function _lrNote(id, costs, value) {
  const node = document.getElementById(id);
  if (node) node.textContent = costs[value] ? _lrText(costs[value]) : '';
}

function _lrRefreshNotes() {
  _lrNote('local-runtime-size-note', LOCAL_RUNTIME_SIZE_COST, document.getElementById('local-runtime-model-size')?.value);
  _lrNote('local-runtime-device-note', LOCAL_RUNTIME_DEVICE_COST, document.getElementById('local-runtime-device')?.value);
  _lrNote('local-runtime-compute-note', LOCAL_RUNTIME_COMPUTE_COST, document.getElementById('local-runtime-compute-type')?.value);
}

function _lrRenderEffective(stt) {
  const body = document.getElementById('local-runtime-effective-body');
  const badge = document.getElementById('local-runtime-stt-status');
  if (!body) return;
  const lines = [];
  const effective = stt.effective;
  let state = 'idle';
  if (!effective) {
    lines.push(t('local_runtime.effective.not_loaded', '尚未加载：第一次转写时才会加载模型。'));
  } else {
    lines.push(t('local_runtime.effective.loaded', '当前加载：{model} · {device} · {compute}', {
      model: effective.model_size, device: effective.device, compute: effective.compute_type}));
    state = 'ok';
    if (effective.fallback) {
      state = 'warn';
      lines.push(t('local_runtime.effective.fallback', '已回落到 CPU，原因：{reason}', {reason: effective.fallback.reason}));
    }
    if (!stt.matches_config) {
      lines.push(t('local_runtime.effective.pending', '已保存的配置与当前加载的不一致，下一次转写时会按配置重新加载。'));
    }
  }
  if (stt.blocking_reason) {
    state = 'err';
    lines.push(t('local_runtime.effective.blocked', '按当前配置无法运行：{reason}', {reason: stt.blocking_reason}));
  }
  body.replaceChildren(...lines.map(text => {
    const p = document.createElement('p');
    p.textContent = text;
    return p;
  }));
  if (badge) {
    badge.textContent = {ok: t('local_runtime.badge.ok', '已生效'), warn: t('local_runtime.badge.warn', '已回落'),
      err: t('local_runtime.badge.err', '不可用'), idle: t('local_runtime.badge.idle', '未加载')}[state];
    badge.dataset.state = state;
  }
}

function _lrRenderHardware(hardware) {
  const body = document.getElementById('local-runtime-hardware-body');
  if (!body) return;
  const lines = [];
  if (!hardware.faster_whisper) {
    lines.push(t('local_runtime.hardware.no_lib', '没有安装 faster-whisper：请在运行环境执行 pip install faster-whisper。'));
  } else {
    lines.push(t('local_runtime.hardware.devices', '可用设备：{devices}；检测到 {count} 块 CUDA 显卡', {
      devices: hardware.devices.join(' / '), count: hardware.cuda_devices}));
    if (hardware.missing_runtime) lines.push(hardware.missing_runtime);
    if (hardware.hint) lines.push(hardware.hint);
  }
  body.replaceChildren(...lines.map(text => {
    const p = document.createElement('p');
    p.textContent = text;
    return p;
  }));
}

async function loadLocalModelRuntime() {
  const [view, hardware] = await Promise.all([
    api('GET', '/settings/local-runtime'),
    api('GET', '/settings/local-runtime/hardware').catch(error => ({
      faster_whisper: false, devices: ['cpu'], cuda_devices: 0, missing_runtime: String(error), hint: ''})),
  ]);
  _lrFill(document.getElementById('local-runtime-model-size'), view.options.model_sizes, LOCAL_RUNTIME_SIZE_COST);
  _lrFill(document.getElementById('local-runtime-device'), view.options.devices, LOCAL_RUNTIME_DEVICE_COST);
  _lrFill(document.getElementById('local-runtime-compute-type'), view.options.compute_types, LOCAL_RUNTIME_COMPUTE_COST);
  const cfg = view.stt.configured;
  document.getElementById('local-runtime-model-size').value = cfg.model_size;
  document.getElementById('local-runtime-device').value = cfg.device;
  document.getElementById('local-runtime-compute-type').value = cfg.compute_type;
  document.getElementById('local-runtime-beam-size').value = cfg.beam_size;
  document.getElementById('local-runtime-timeout').value = cfg.timeout_seconds;
  for (const id of ['local-runtime-model-size', 'local-runtime-device', 'local-runtime-compute-type']) {
    const select = document.getElementById(id);
    if (select.dataset.noteBound !== 'true') {
      select.addEventListener('change', _lrRefreshNotes);
      select.dataset.noteBound = 'true';
    }
  }
  _lrRefreshNotes();
  _lrRenderEffective(view.stt);
  _lrRenderHardware(hardware);
}

async function saveLocalSttRuntime() {
  const button = document.getElementById('local-runtime-save');
  const result = document.getElementById('local-runtime-result');
  const body = {
    model_size: document.getElementById('local-runtime-model-size').value,
    device: document.getElementById('local-runtime-device').value,
    compute_type: document.getElementById('local-runtime-compute-type').value,
    beam_size: parseInt(document.getElementById('local-runtime-beam-size').value, 10),
    timeout_seconds: parseFloat(document.getElementById('local-runtime-timeout').value),
  };
  if (button) button.disabled = true;
  result.textContent = t('local_runtime.switching', '正在切换，加载模型可能需要一会儿…');
  result.dataset.state = 'busy';
  try {
    const response = await fetch(BASE + '/settings/local-runtime/stt', {
      method: 'PUT', headers: authHeaders(), body: JSON.stringify(body)});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = payload.detail;
      const reason = typeof detail === 'string' ? detail : (detail?.error || response.statusText);
      const kept = detail?.kept;
      result.textContent = t('local_runtime.failed', '切换失败，未保存：{reason}', {reason})
        + (kept ? ' ' + t('local_runtime.kept', '仍在使用：{model} · {device} · {compute}', {
          model: kept.model_size, device: kept.device, compute: kept.compute_type}) : '');
      result.dataset.state = 'err';
      toast(t('local_runtime.failed_toast', '本地语音识别切换失败'), 'err');
      return;
    }
    result.textContent = t('local_runtime.saved', '已保存并切换成功。');
    result.dataset.state = 'ok';
    toast(t('local_runtime.saved', '已保存并切换成功。'), 'ok');
    _lrRenderEffective(payload.stt);
  } catch (error) {
    result.textContent = t('common.save_failed', '保存失败: {error}', {error: String(error)});
    result.dataset.state = 'err';
  } finally {
    if (button) button.disabled = false;
  }
}

window.loadLocalModelRuntime = loadLocalModelRuntime;
window.saveLocalSttRuntime = saveLocalSttRuntime;
