// 通话内主动性放宽（工单 F）：默认关闭，挂在「自主活动安排」页。
const VIDEO_CALL_PRESENCE_FIELDS = Object.freeze({
  'vcp-max-talks': 'max_talks_per_call',
  'vcp-min-gap': 'min_gap_seconds',
  'vcp-silence': 'silence_seconds',
  'vcp-signal-interval': 'camera_signal_interval_seconds',
});

async function loadVideoCallPresence() {
  const enabled = document.getElementById('vcp-enabled');
  if (!enabled) return;
  try {
    const data = await api('GET', '/video-call/presence');
    enabled.checked = !!data.settings.enabled;
    for (const [id, key] of Object.entries(VIDEO_CALL_PRESENCE_FIELDS)) {
      document.getElementById(id).value = data.settings[key];
    }
    const blocks = Object.entries(data.gate_blocks || {}).map(([gate, count]) => `${gate}: ${count}`).join('，');
    document.getElementById('vcp-stats').textContent = t(
      'video_call_presence.stats', '本进程通话内主动发言 {talks} 条；被拦下的闸：{blocks}', {
        talks: data.talks_total, blocks: blocks || t('video_call_presence.no_blocks', '无')});
  } catch (error) { toast(t('common.load_failed', '读取失败：{error}', {error: error.message}), 'err'); }
}

async function saveVideoCallPresence() {
  const body = {enabled: document.getElementById('vcp-enabled').checked};
  for (const [id, key] of Object.entries(VIDEO_CALL_PRESENCE_FIELDS)) {
    body[key] = parseInt(document.getElementById(id).value, 10);
  }
  try {
    await api('PUT', '/video-call/presence', body);
    toast(t('video_call_presence.saved', '通话内主动性设置已保存'), 'ok');
    await loadVideoCallPresence();
  } catch (error) {
    toast(t('common.save_failed', '保存失败: {error}', {error: error.message}), 'err');
  }
}

window.loadVideoCallPresence = loadVideoCallPresence;
window.saveVideoCallPresence = saveVideoCallPresence;
