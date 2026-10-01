// 本地模型运行页（工单 D2；工单 H 增加 sherpa-onnx 引擎）。只接本地 STT；远程连接在 tts-config 页。
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
const LOCAL_RUNTIME_ENGINE_COST = Object.freeze({
  faster_whisper: ['local_runtime.engine.faster_whisper', 'faster-whisper（默认）— 通用多语种 Whisper，稳定；CPU 上较慢'],
  sherpa_onnx: ['local_runtime.engine.sherpa_onnx', 'sherpa-onnx — 中英双语 transducer，更轻更快；需要另装 sherpa-onnx 并下载约 200 MB 模型文件'],
});
const LOCAL_RUNTIME_DECODING_COST = Object.freeze({
  modified_beam_search: ['local_runtime.sherpa.decoding.modified_beam_search', 'modified_beam_search（推荐）— 束搜索：支持专有名词热词，更不容易出现单字重复'],
  greedy_search: ['local_runtime.sherpa.decoding.greedy_search', 'greedy_search — 更快，但不支持热词，也更容易出现单字重复'],
});
let _lrPollTimer = null;

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

function _lrParagraphs(body, lines) {
  body.replaceChildren(...lines.map(text => {
    const p = document.createElement('p');
    p.textContent = text;
    return p;
  }));
}

function _lrMb(bytes) { return (Number(bytes || 0) / 1048576).toFixed(1); }

function _lrRefreshNotes() {
  _lrNote('local-runtime-size-note', LOCAL_RUNTIME_SIZE_COST, document.getElementById('local-runtime-model-size')?.value);
  _lrNote('local-runtime-device-note', LOCAL_RUNTIME_DEVICE_COST, document.getElementById('local-runtime-device')?.value);
  _lrNote('local-runtime-compute-note', LOCAL_RUNTIME_COMPUTE_COST, document.getElementById('local-runtime-compute-type')?.value);
  _lrNote('local-runtime-engine-note', LOCAL_RUNTIME_ENGINE_COST, document.getElementById('local-runtime-engine')?.value);
  _lrNote('local-runtime-sherpa-decoding-note', LOCAL_RUNTIME_DECODING_COST, document.getElementById('local-runtime-sherpa-decoding')?.value);
}

// 只显示当前引擎的参数组；另一组的值仍留在输入框里，保存时一并带上，来回切换不会丢。
function _lrApplyEngine() {
  const engine = document.getElementById('local-runtime-engine')?.value;
  const whisper = document.getElementById('local-runtime-whisper-group');
  const sherpa = document.getElementById('local-runtime-sherpa-group');
  if (whisper) whisper.hidden = engine === 'sherpa_onnx';
  if (sherpa) sherpa.hidden = engine !== 'sherpa_onnx';
  _lrRefreshNotes();
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
    if (effective.backend === 'sherpa_onnx' && effective.hotwords) {
      lines.push(t('local_runtime.effective.hotwords', '热词偏置：{state}', {state: effective.hotwords}));
    }
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
  _lrParagraphs(body, lines);
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
  const sherpa = hardware.sherpa_onnx;
  if (sherpa) {
    lines.push(sherpa.installed
      ? t('local_runtime.hardware.sherpa_installed', 'sherpa-onnx 已安装（{version}）', {version: sherpa.version})
      : t('local_runtime.hardware.sherpa_missing', '没有安装 sherpa-onnx：请在运行环境执行 pip install sherpa-onnx（支持 Python 3.10–3.12）。'));
  }
  _lrParagraphs(body, lines);
}

function _lrRenderSherpaAssets(sherpa) {
  const body = document.getElementById('local-runtime-sherpa-assets-body');
  const button = document.getElementById('local-runtime-sherpa-download');
  if (!body || !sherpa) return;
  const selected = document.getElementById('local-runtime-sherpa-model')?.value;
  const model = (sherpa.models || {})[selected] || Object.values(sherpa.models || {})[0];
  const download = sherpa.download || {};
  const lines = [];
  lines.push(sherpa.installed
    ? t('local_runtime.hardware.sherpa_installed', 'sherpa-onnx 已安装（{version}）', {version: sherpa.version})
    : t('local_runtime.hardware.sherpa_missing', '没有安装 sherpa-onnx：请在运行环境执行 pip install sherpa-onnx（支持 Python 3.10–3.12）。'));
  if (model) {
    if (model.present) {
      lines.push(t('local_runtime.sherpa.assets.present', '模型文件已就位并通过校验：{model}（{mb} MB）', {model: model.model, mb: _lrMb(model.bytes)}));
    } else if (model.corrupt && model.corrupt.length) {
      lines.push(t('local_runtime.sherpa.assets.corrupt', '模型文件校验不通过：{files}。请重新下载。', {files: model.corrupt.join('、')}));
    } else {
      lines.push(t('local_runtime.sherpa.assets.missing', '模型文件还没下载或不完整：缺少 {files}（共约 {mb} MB）', {
        files: (model.missing || []).join('、'), mb: _lrMb(model.expected_bytes)}));
    }
  }
  if (download.state === 'running') {
    lines.push(t('local_runtime.sherpa.assets.downloading', '正在下载 {file}：{done} / {total} MB', {
      file: download.file || '…', done: _lrMb(download.bytes_done), total: _lrMb(download.bytes_total)}));
  } else if (download.state === 'error' && download.error) {
    lines.push(t('local_runtime.sherpa.assets.error', '下载失败：{error}', {error: download.error}));
  }
  _lrParagraphs(body, lines);
  if (button) button.disabled = download.state === 'running';
}

function _lrRenderRemoteNote(view) {
  const note = document.getElementById('local-runtime-remote-note');
  if (!note) return;
  note.hidden = !view.remote_stt_overrides_local;
  note.textContent = view.remote_stt_overrides_local
    ? t('local_runtime.remote_overrides', '配置里已有远程语音识别（stt_presets）：语音转写会优先走远程连接，这里选的本地引擎暂时不会被用到。要改用本地引擎，请先在「语音合成与声音」页处理远程语音识别。')
    : '';
}

function _lrStopPolling() {
  if (_lrPollTimer) { clearInterval(_lrPollTimer); _lrPollTimer = null; }
}

// 下载约 200 MB，不能挂在一次请求上：后台线程下载，这里轮询 GET 看进度。
function _lrPollDownload() {
  if (_lrPollTimer) return;
  _lrPollTimer = setInterval(async () => {
    try {
      const view = await api('GET', '/settings/local-runtime');
      _lrRenderSherpaAssets(view.sherpa);
      const state = view.sherpa?.download?.state;
      if (state !== 'running') {
        _lrStopPolling();
        if (state === 'done') toast(t('local_runtime.sherpa.assets.done', '模型下载并校验完成'), 'ok');
        else if (state === 'error') toast(t('local_runtime.sherpa.assets.error', '下载失败：{error}', {error: view.sherpa.download.error}), 'err');
      }
    } catch (error) {
      _lrStopPolling();
    }
  }, 1500);
}

async function loadLocalModelRuntime() {
  const [view, hardware] = await Promise.all([
    api('GET', '/settings/local-runtime'),
    api('GET', '/settings/local-runtime/hardware').catch(error => ({
      faster_whisper: false, devices: ['cpu'], cuda_devices: 0, missing_runtime: String(error), hint: ''})),
  ]);
  _lrFill(document.getElementById('local-runtime-engine'), view.options.engines, LOCAL_RUNTIME_ENGINE_COST);
  _lrFill(document.getElementById('local-runtime-model-size'), view.options.model_sizes, LOCAL_RUNTIME_SIZE_COST);
  _lrFill(document.getElementById('local-runtime-device'), view.options.devices, LOCAL_RUNTIME_DEVICE_COST);
  _lrFill(document.getElementById('local-runtime-compute-type'), view.options.compute_types, LOCAL_RUNTIME_COMPUTE_COST);
  const sherpaModelCosts = Object.fromEntries(Object.entries(view.options.sherpa_models)
    .map(([id, label]) => [id, ['local_runtime.sherpa.model.' + id, label]]));
  _lrFill(document.getElementById('local-runtime-sherpa-model'), Object.keys(view.options.sherpa_models), sherpaModelCosts);
  _lrFill(document.getElementById('local-runtime-sherpa-decoding'), view.options.sherpa_decoding_methods, LOCAL_RUNTIME_DECODING_COST);
  const cfg = view.stt.configured;
  const sherpaCfg = cfg.sherpa_onnx || {};
  document.getElementById('local-runtime-engine').value = cfg.engine;
  document.getElementById('local-runtime-model-size').value = cfg.model_size;
  document.getElementById('local-runtime-device').value = cfg.device;
  document.getElementById('local-runtime-compute-type').value = cfg.compute_type;
  document.getElementById('local-runtime-beam-size').value = cfg.beam_size;
  document.getElementById('local-runtime-timeout').value = cfg.timeout_seconds;
  document.getElementById('local-runtime-sherpa-model').value = sherpaCfg.model;
  document.getElementById('local-runtime-sherpa-decoding').value = sherpaCfg.decoding_method;
  document.getElementById('local-runtime-sherpa-threads').value = sherpaCfg.num_threads;
  document.getElementById('local-runtime-sherpa-paths').value = sherpaCfg.max_active_paths;
  document.getElementById('local-runtime-sherpa-hotwords').value = sherpaCfg.hotwords_score;
  document.getElementById('local-runtime-sherpa-endpoint').value = sherpaCfg.endpoint_silence_seconds;
  document.getElementById('local-runtime-sherpa-repeat').value = sherpaCfg.repeat_collapse_min_run;
  document.getElementById('local-runtime-sherpa-base').value = sherpaCfg.download_base;
  for (const id of ['local-runtime-engine', 'local-runtime-model-size', 'local-runtime-device',
                    'local-runtime-compute-type', 'local-runtime-sherpa-decoding']) {
    const select = document.getElementById(id);
    if (select.dataset.noteBound !== 'true') {
      select.addEventListener('change', id === 'local-runtime-engine' ? _lrApplyEngine : _lrRefreshNotes);
      select.dataset.noteBound = 'true';
    }
  }
  const sherpaModel = document.getElementById('local-runtime-sherpa-model');
  if (sherpaModel.dataset.noteBound !== 'true') {
    sherpaModel.addEventListener('change', () => _lrRenderSherpaAssets(window._lrSherpa));
    sherpaModel.dataset.noteBound = 'true';
  }
  window._lrSherpa = view.sherpa;
  _lrApplyEngine();
  _lrRenderRemoteNote(view);
  _lrRenderEffective(view.stt);
  _lrRenderHardware({...hardware, sherpa_onnx: view.sherpa});
  _lrRenderSherpaAssets(view.sherpa);
  if (view.sherpa?.download?.state === 'running') _lrPollDownload();
}

async function downloadSherpaModel() {
  const result = document.getElementById('local-runtime-result');
  const model = document.getElementById('local-runtime-sherpa-model').value;
  const downloadBase = document.getElementById('local-runtime-sherpa-base').value.trim();
  try {
    const response = await fetch(BASE + '/settings/local-runtime/stt/sherpa/download', {
      method: 'POST', headers: authHeaders(), body: JSON.stringify({model, download_base: downloadBase || undefined})});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const reason = typeof payload.detail === 'string' ? payload.detail : response.statusText;
      result.textContent = t('local_runtime.sherpa.assets.error', '下载失败：{error}', {error: reason});
      result.dataset.state = 'err';
      return;
    }
    result.textContent = t('local_runtime.sherpa.assets.started', '已开始下载，可以留在本页看进度。');
    result.dataset.state = 'busy';
    _lrRenderSherpaAssets({...window._lrSherpa, download: payload.download});
    _lrPollDownload();
  } catch (error) {
    result.textContent = t('common.save_failed', '保存失败: {error}', {error: String(error)});
    result.dataset.state = 'err';
  }
}

function _lrNumber(id, parser) {
  const value = parser(document.getElementById(id).value);
  return Number.isNaN(value) ? undefined : value;
}

async function saveLocalSttRuntime() {
  const button = document.getElementById('local-runtime-save');
  const result = document.getElementById('local-runtime-result');
  // 两个引擎的参数组都带上：后端只严格校验当前引擎那一组，另一组原样保留。
  const body = {
    engine: document.getElementById('local-runtime-engine').value,
    model_size: document.getElementById('local-runtime-model-size').value,
    device: document.getElementById('local-runtime-device').value,
    compute_type: document.getElementById('local-runtime-compute-type').value,
    beam_size: parseInt(document.getElementById('local-runtime-beam-size').value, 10),
    timeout_seconds: parseFloat(document.getElementById('local-runtime-timeout').value),
    sherpa_onnx: {
      model: document.getElementById('local-runtime-sherpa-model').value,
      decoding_method: document.getElementById('local-runtime-sherpa-decoding').value,
      num_threads: _lrNumber('local-runtime-sherpa-threads', v => parseInt(v, 10)),
      max_active_paths: _lrNumber('local-runtime-sherpa-paths', v => parseInt(v, 10)),
      hotwords_score: _lrNumber('local-runtime-sherpa-hotwords', parseFloat),
      endpoint_silence_seconds: _lrNumber('local-runtime-sherpa-endpoint', parseFloat),
      repeat_collapse_min_run: _lrNumber('local-runtime-sherpa-repeat', v => parseInt(v, 10)),
      download_base: document.getElementById('local-runtime-sherpa-base').value.trim(),
    },
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
    _lrRenderRemoteNote(payload);
    // Show what was actually stored (e.g. the trailing slash on the mirror address is stripped).
    document.getElementById('local-runtime-sherpa-base').value = payload.stt.configured.sherpa_onnx.download_base;
    window._lrSherpa = payload.sherpa;
    _lrRenderSherpaAssets(payload.sherpa);
  } catch (error) {
    result.textContent = t('common.save_failed', '保存失败: {error}', {error: String(error)});
    result.dataset.state = 'err';
  } finally {
    if (button) button.disabled = false;
  }
}

window.loadLocalModelRuntime = loadLocalModelRuntime;
window.saveLocalSttRuntime = saveLocalSttRuntime;
window.downloadSherpaModel = downloadSherpaModel;
