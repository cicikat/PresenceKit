import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { once } from 'node:events';
import { createBridge, normalize, trustedBase } from './bridge.mjs';

const credentials = { owner: 'fixture-owner', account: 'fixture-account',
  token: 'private-fixture-bot-token', base: 'https://ilinkai.weixin.qq.com' };
const message = { from_user_id: credentials.owner, to_user_id: credentials.account,
  message_id: '18446744073709551615', create_time_ms: Date.now(), message_type: 1,
  message_state: 2, context_token: 'private-fixture-context',
  item_list: [{ type: 1, text_item: { text: 'hello' } }] };

test('normalization rejects other identities, media, history and untrusted API redirects', () => {
  assert.equal(normalize(message, credentials).message_id, message.message_id);
  for (const changes of [{ from_user_id: 'stranger' }, { group_id: 'group' },
    { message_type: 2 }, { message_state: 1 }, { create_time_ms: 0 },
    { item_list: [{ type: 2 }] }, { to_user_id: 'other-account' }]) {
    assert.equal(normalize({ ...message, ...changes }, credentials), null);
  }
  assert.ok(trustedBase(credentials.base));
  for (const value of ['http://ilinkai.weixin.qq.com', 'https://weixin.qq.com.evil.invalid',
    'https://user:secret@ilinkai.weixin.qq.com', 'https://ilinkai.weixin.qq.com/?token=secret']) {
    assert.equal(trustedBase(value), false);
  }
});

test('local bearer authentication, restored credentials and reply context are isolated', async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'weixin-fixture-'));
  fs.writeFileSync(path.join(stateDir, 'credentials.json'), JSON.stringify(credentials));
  const sent = [];
  let first = true, result = { ret: 0 };
  const api = {
    getUpdates: async () => {
      if (!first) return new Promise(() => {});
      first = false; return { ret: 0, msgs: [message, { ...message, from_user_id: 'stranger' }] };
    },
    sendMessage: async params => { sent.push(params); return result; },
  };
  const localToken = 'fixture-local-token-with-at-least-32-characters';
  const bridge = createBridge({ api, stateDir, localToken });
  bridge.server.listen(0, '127.0.0.1'); await once(bridge.server, 'listening');
  const base = 'http://127.0.0.1:' + bridge.server.address().port;
  const call = (route, body) => fetch(base + route, { method: body ? 'POST' : 'GET',
    headers: { Authorization: 'Bearer ' + localToken, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined });
  try {
    assert.equal((await fetch(base + '/status')).status, 401);
    const status = await (await call('/status')).json();
    assert.equal(status.connected, true);
    assert.equal(JSON.stringify(status).includes(credentials.token), false);
    const batch = await (await call('/events')).json();
    assert.equal(batch.events.length, 1);
    assert.equal(JSON.stringify(batch).includes(message.context_token), false);
    assert.equal((await (await call('/send', { address: 'stranger', text: 'reply' })).json()).status, 'rejected');
    assert.equal((await (await call('/send', { address: credentials.owner, text: 'reply' })).json()).status, 'accepted');
    assert.equal(sent[0].body.msg.context_token, message.context_token);
    assert.equal(sent[0].body.msg.item_list[0].text_item.text, 'reply');
    result = {};
    assert.equal((await (await call('/send', { address: credentials.owner, text: 'reply' })).json()).status, 'unknown');
    assert.equal(sent.length, 2);
    assert.equal((await (await call('/login/reset', {})).json()).status, 'cleared');
    const cleared = await (await call('/status')).json();
    assert.equal(cleared.logged_in, false);
    assert.equal(cleared.connected, false);
    assert.equal(JSON.parse(fs.readFileSync(path.join(stateDir, 'credentials.json'))), null);
    assert.equal((await (await call('/send', { address: credentials.owner, text: 'reply' })).json()).status, 'rejected');
  } finally { bridge.stop(); }
});

test('QR confirmation stores credentials without returning bot token', async () => {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'weixin-login-fixture-'));
  const bridge = createBridge({ stateDir, localToken: 'fixture-token-with-at-least-32-characters', api: {
    apiPostFetch: async () => JSON.stringify({ qrcode: 'qr-value', qrcode_img_content: 'https://example.invalid/qr' }),
    apiGetFetch: async () => JSON.stringify({ status: 'confirmed', bot_token: credentials.token,
      ilink_bot_id: credentials.account, ilink_user_id: credentials.owner, baseurl: credentials.base }),
    getUpdates: async () => new Promise(() => {}),
  } });
  bridge.server.listen(0, '127.0.0.1'); await once(bridge.server, 'listening');
  try {
    const response = await fetch('http://127.0.0.1:' + bridge.server.address().port + '/login/start', {
      method: 'POST', headers: { Authorization: 'Bearer fixture-token-with-at-least-32-characters' } });
    const payload = await response.json();
    assert.equal(response.status, 200);
    assert.ok(payload.image.startsWith('data:image/png;base64,'));
    assert.equal(JSON.stringify(payload).includes(credentials.token), false);
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.equal(JSON.parse(fs.readFileSync(path.join(stateDir, 'credentials.json'))).token, credentials.token);
    assert.ok(fs.existsSync(path.join(stateDir, 'qrcode.png')));
  } finally { bridge.stop(); }
});
