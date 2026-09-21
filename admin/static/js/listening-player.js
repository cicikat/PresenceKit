const LISTENING_HOST_STORAGE = 'admin_listening_host_id';
const LISTENING_UID_STORAGE = 'admin_listening_uid';

function _listeningUid() {
  return (document.getElementById('listening-uid')?.value || '').trim();
}

function _listeningHostId() {
  const input = document.getElementById('listening-host-id');
  const stored = sessionStorage.getItem(LISTENING_HOST_STORAGE);
  const value = (input?.value || stored || `admin-html-audio-${Date.now().toString(36)}`).trim();
  if (input && !input.value) input.value = value;
  sessionStorage.setItem(LISTENING_HOST_STORAGE, value);
  return value;
}

function _listeningQuery() {
  const uid = _listeningUid();
  return uid ? `?uid=${encodeURIComponent(uid)}` : '';
}

function _listeningAudio() {
  return document.getElementById('listening-audio');
}

window._listeningPlayer = window._listeningPlayer || {
  bound: false,
  generation: 0,
  sessionId: '',
  revision: 0,
  sequence: 0,
  trackId: null,
  occurrenceOpen: false,
  lastPosition: 0,
  lastMediaTime: 0,
  accumulatedDelta: 0,
  lastProgressSent: 0,
  ttsWasPlaying: false,
  pendingCommandId: '',
  pendingAction: '',
};

function _lp() {
  return window._listeningPlayer;
}

function _listeningStatus(text) {
  const el = document.getElementById('listening-status');
  if (el) el.textContent = text || '';
}

function _pauseTtsIfNeeded() {
  const audio = _listeningAudio();
  const others = [...document.querySelectorAll('audio, video')].filter(el => el !== audio && !el.paused);
  _lp().ttsWasPlaying = others.length > 0;
  others.forEach(el => { try { el.pause(); } catch (_error) { /* best effort */ } });
}

async function _sendListeningEvent(kind, extra) {
  const state = _lp();
  if (!state.bound) return;
  state.sequence += 1;
  const event = {
    event_id: `${kind}-${state.sequence}-${Date.now().toString(36)}`,
    kind,
    generation: state.generation,
    session_id: state.sessionId,
    sequence: state.sequence,
    track_id: extra?.track_id || state.trackId,
    position_s: extra?.position_s ?? (_listeningAudio()?.currentTime || 0),
    occurred_at: Date.now() / 1000,
    ...extra,
  };
  if (state.pendingCommandId && kind !== 'progress') {
    event.causation_command_id = state.pendingCommandId;
    state.pendingCommandId = '';
    state.pendingAction = '';
  }
  try {
    const result = await api('POST', '/player/event', { uid: _listeningUid(), event });
    const log = document.getElementById('listening-events');
    if (log) {
      const line = `${kind} seq=${state.sequence} ok=${result.ok} ${result.reason || ''}`.trim();
      log.textContent = `${line}\n${log.textContent}`.slice(0, 4000);
    }
    return result;
  } catch (error) {
    _listeningStatus(error.message);
  }
}

function _resetPlayClock() {
  const audio = _listeningAudio();
  _lp().lastPosition = audio?.currentTime || 0;
  _lp().lastMediaTime = audio?.currentTime || 0;
  _lp().accumulatedDelta = 0;
  _lp().lastProgressSent = 0;
}

function _accumulateIfPlaying() {
  const audio = _listeningAudio();
  const state = _lp();
  if (!audio || audio.paused || audio.seeking) return 0;
  const now = audio.currentTime || 0;
  const delta = now - state.lastMediaTime;
  state.lastMediaTime = now;
  if (delta > 0 && delta < 2) state.accumulatedDelta += delta;
  return state.accumulatedDelta;
}

async function _flushProgress(force) {
  const state = _lp();
  const played = _accumulateIfPlaying();
  if (!force && played - state.lastProgressSent < 1) return;
  const delta = played - state.lastProgressSent;
  if (delta <= 0) return;
  state.lastProgressSent = played;
  await _sendListeningEvent('progress', { played_delta_s: delta, position_s: _listeningAudio()?.currentTime || 0 });
}

function _bindAudioEvents() {
  const audio = _listeningAudio();
  if (!audio || audio.dataset.listeningBound === 'true') return;
  audio.dataset.listeningBound = 'true';
  audio.addEventListener('playing', async () => {
    _pauseTtsIfNeeded();
    _resetPlayClock();
    if (_lp().occurrenceOpen) {
      await _sendListeningEvent('resumed');
    } else {
      await _sendListeningEvent('started', { track_id: _lp().trackId });
      _lp().occurrenceOpen = true;
    }
  });
  audio.addEventListener('pause', async () => {
    await _flushProgress(true);
    if (audio.ended) return;
    await _sendListeningEvent('paused');
  });
  audio.addEventListener('ended', async () => {
    await _flushProgress(true);
    await _sendListeningEvent('finished');
    _lp().occurrenceOpen = false;
  });
  audio.addEventListener('error', async () => {
    await _sendListeningEvent('error');
    _lp().occurrenceOpen = false;
    _listeningStatus(t('listening_player.decode_error', '当前文件无法解码或被浏览器阻止播放。'));
  });
  audio.addEventListener('seeking', () => {
    _lp().lastMediaTime = audio.currentTime || 0;
  });
  audio.addEventListener('timeupdate', () => { _flushProgress(false); });
}

async function _command(action, args, extra) {
  const state = _lp();
  const body = {
    uid: _listeningUid(),
    command_id: `${action}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    action,
    generation: state.generation,
    session_id: state.sessionId,
    expected_revision: state.revision,
    args: args || {},
    ...extra,
  };
  const result = await api('POST', '/player/command', body);
  if (result.session) {
    state.revision = result.session.revision;
    state.generation = result.session.generation;
    state.sessionId = result.session.session_id;
    if (result.session.track_id) state.trackId = result.session.track_id;
  }
  if (!result.accepted) throw new Error(result.reason || 'rejected');
  if (result.outcome === 'accepted') {
    state.pendingCommandId = result.command_id || body.command_id;
    state.pendingAction = action;
  }
  return result;
}

async function refreshListeningPlayer() {
  _bindAudioEvents();
  const uidInput = document.getElementById('listening-uid');
  if (uidInput && !uidInput.value) {
    uidInput.value = sessionStorage.getItem(LISTENING_UID_STORAGE) || '';
  }
  uidInput?.addEventListener('change', () => {
    sessionStorage.setItem(LISTENING_UID_STORAGE, _listeningUid());
  });
  try {
    const state = await api('GET', `/player/state${_listeningQuery()}`);
    const control = document.getElementById('listening-control-state');
    if (control) {
      control.textContent = state.music_control_enabled
        ? t('listening_player.control_on', 'music_control 已开启；命令会被接受，实际出声仍看本页 audio。')
        : t('listening_player.control_off', 'music_control 默认关闭。可绑定宿主并导入歌曲，但播放命令会被拒绝。');
    }
    const sessionEl = document.getElementById('listening-session');
    if (sessionEl) sessionEl.textContent = JSON.stringify(state.session || {}, null, 2);
    if (state.session) {
      _lp().generation = state.session.generation || 0;
      _lp().sessionId = state.session.session_id || '';
      _lp().revision = state.session.revision || 0;
      _lp().bound = Boolean(state.session.host_online);
    }
  } catch (error) {
    _listeningStatus(error.message);
  }
  await loadListeningLibrary();
}

async function bindListeningHost() {
  const result = await api('POST', '/player/host/bind', { uid: _listeningUid(), host_id: _listeningHostId() });
  _lp().bound = true;
  _lp().generation = result.session.generation;
  _lp().sessionId = result.session.session_id;
  _lp().revision = result.session.revision;
  _lp().sequence = 0;
  _lp().occurrenceOpen = false;
  _lp().pendingCommandId = '';
  _lp().pendingAction = '';
  _listeningStatus(t('listening_player.bound', '已绑定本页宿主。'));
  await refreshListeningPlayer();
}

async function disconnectListeningHost() {
  await api('POST', `/player/host/disconnect${_listeningQuery()}`);
  _lp().bound = false;
  _lp().occurrenceOpen = false;
  _lp().pendingCommandId = '';
  _lp().pendingAction = '';
  _listeningStatus(t('listening_player.disconnected', '后端已离线。本页音频可继续出声，但不再计入听歌时长。'));
  await refreshListeningPlayer();
}

async function loadListeningLibrary() {
  const list = document.getElementById('listening-library');
  if (!list) return;
  try {
    const data = await api('GET', `/player/library${_listeningQuery()}`);
    if (!(data.tracks || []).length) {
      list.innerHTML = `<li>${escapeHtml(t('listening_player.no_tracks', '还没有受控音频。'))}</li>`;
      return;
    }
    list.innerHTML = data.tracks.map(track => (
      `<li><button type="button" class="btn btn-ghost btn-sm" data-action="selectListeningTrack" data-action-args='${JSON.stringify([track.track_id])}'>${escapeHtml(track.title || track.track_id)}</button> <small>${escapeHtml(track.audio_access || '')}</small></li>`
    )).join('');
    bindPageActions(list);
  } catch (error) {
    list.textContent = error.message;
  }
}

async function uploadListeningTrack() {
  const file = document.getElementById('listening-file')?.files?.[0];
  if (!file) {
    _listeningStatus(t('listening_player.need_file', '请先选择本地音频。'));
    return;
  }
  const body = new FormData();
  body.append('file', file);
  body.append('uid', _listeningUid());
  body.append('title', document.getElementById('listening-title')?.value || file.name);
  const response = await fetch(BASE + '/player/tracks', {
    method: 'POST',
    headers: { Authorization: `Bearer ${TOKEN}` },
    body,
  });
  if (!response.ok) throw new Error(await response.text());
  const data = await response.json();
  _listeningStatus(t('listening_player.uploaded', '已导入受控音频。'));
  await loadListeningLibrary();
  if (data.track?.track_id) await selectListeningTrack(data.track.track_id);
}

async function selectListeningTrack(trackId) {
  const audio = _listeningAudio();
  _lp().trackId = trackId;
  _lp().occurrenceOpen = false;
  audio.src = `${BASE}/player/audio/${encodeURIComponent(trackId)}${_listeningQuery()}`;
  audio.load();
  const now = document.getElementById('listening-now');
  if (now) now.textContent = trackId;
  if (_lp().bound) {
    try { await _command('play', { track_id: trackId }); }
    catch (error) { _listeningStatus(error.message); }
  }
}

async function listeningPlay() {
  const audio = _listeningAudio();
  if (!_lp().trackId) {
    _listeningStatus(t('listening_player.need_track', '请先选择一首受控音频。'));
    return;
  }
  try {
    if (_lp().bound) await _command('play', { track_id: _lp().trackId });
    _pauseTtsIfNeeded();
    await audio.play();
  } catch (error) {
    _listeningStatus(error.message || t('listening_player.autoplay_blocked', '浏览器阻止自动播放，请再点一次播放。'));
  }
}

async function listeningPause() {
  _listeningAudio()?.pause();
  if (_lp().bound) {
    try { await _command('pause'); } catch (error) { _listeningStatus(error.message); }
  }
}

async function listeningResume() {
  try {
    if (_lp().bound) await _command('resume');
    await _listeningAudio()?.play();
  } catch (error) {
    _listeningStatus(error.message);
  }
}

async function listeningStop() {
  const audio = _listeningAudio();
  if (audio) {
    audio.pause();
    audio.currentTime = 0;
  }
  if (_lp().bound) {
    await _flushProgress(true);
    await _sendListeningEvent('stopped');
    _lp().occurrenceOpen = false;
    try { await _command('stop'); } catch (error) { _listeningStatus(error.message); }
  }
}

async function listeningNext() {
  if (!_lp().bound) return;
  try {
    const result = await _command('next');
    if (result.session?.track_id) await selectListeningTrack(result.session.track_id);
    await _listeningAudio()?.play();
  } catch (error) {
    _listeningStatus(error.message);
  }
}

window.refreshListeningPlayer = refreshListeningPlayer;
window.bindListeningHost = bindListeningHost;
window.disconnectListeningHost = disconnectListeningHost;
window.uploadListeningTrack = uploadListeningTrack;
window.selectListeningTrack = selectListeningTrack;
window.listeningPlay = listeningPlay;
window.listeningPause = listeningPause;
window.listeningResume = listeningResume;
window.listeningStop = listeningStop;
window.listeningNext = listeningNext;
