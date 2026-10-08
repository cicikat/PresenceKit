import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';
import QRCode from 'qrcode';

const OFFICIAL_BASE = 'https://ilinkai.weixin.qq.com';
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

export function trustedBase(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password && !url.port
      && !url.search && !url.hash && url.pathname === '/'
      && (url.hostname === 'weixin.qq.com' || url.hostname.endsWith('.weixin.qq.com'));
  } catch { return false; }
}

export function normalize(message, credentials) {
  if (!message || message.message_type !== 1 || message.message_state !== 2
      || message.group_id || message.from_user_id !== credentials.owner
      || message.to_user_id !== credentials.account || !message.message_id
      || !Array.isArray(message.item_list) || message.item_list.length !== 1
      || message.item_list[0].type !== 1) return null;
  const text = message.item_list[0].text_item?.text;
  const timestamp = message.create_time_ms / 1000;
  const age = Date.now() / 1000 - timestamp;
  if (typeof text !== 'string' || !text.trim() || text.length > 16000
      || !Number.isFinite(timestamp) || age < -30 || age > 300) return null;
  return { message_id: String(message.message_id), sender_id: message.from_user_id,
    recipient_id: message.to_user_id, conversation_id: message.from_user_id,
    timestamp, text, kind: 'text', is_group: false };
}

export function createBridge({ api, stateDir, localToken }) {
  if (!localToken || localToken.length < 32) throw new Error('local_token_required');
  fs.mkdirSync(stateDir, { recursive: true });
  const credentialFile = path.join(stateDir, 'credentials.json');
  let credentials = null;
  try { credentials = JSON.parse(fs.readFileSync(credentialFile, 'utf8')); } catch {}
  if (credentials && (!trustedBase(credentials.base) || !credentials.token
      || !credentials.owner || !credentials.account)) throw new Error('invalid_credentials');
  let closed = false, connected = false, error = '', cursor = '', login = null;
  let monitorGeneration = 0;
  const events = [], contexts = new Map();
  const counts = { received: 0, dropped: 0, sent: 0, unknown: 0 };

  function persist() {
    fs.writeFileSync(credentialFile + '.tmp', JSON.stringify(credentials), { mode: 0o600 });
    fs.renameSync(credentialFile + '.tmp', credentialFile);
  }
  async function monitor(generation) {
    while (!closed && credentials && generation === monitorGeneration) {
      try {
        const batch = await api.getUpdates({ baseUrl: credentials.base,
          token: credentials.token, get_updates_buf: cursor, timeoutMs: 35000 });
        if (closed || generation !== monitorGeneration) return;
        if (batch.ret === -14 || batch.errcode === -14) {
          connected = false; error = 'login_expired'; return;
        }
        if ((batch.ret != null && batch.ret !== 0) || (batch.errcode != null && batch.errcode !== 0)
            || (!Array.isArray(batch.msgs) && typeof batch.get_updates_buf !== 'string')) {
          throw new Error('poll_rejected');
        }
        connected = true; error = '';
        if (typeof batch.get_updates_buf === 'string' && batch.get_updates_buf.length < 65536) {
          cursor = batch.get_updates_buf;
        }
        for (const item of (Array.isArray(batch.msgs) ? batch.msgs : []).slice(0, 100)) {
          const event = normalize(item, credentials);
          if (!event) { counts.dropped++; continue; }
          if (typeof item.context_token === 'string' && item.context_token.length <= 8192) {
            contexts.set(event.sender_id, { token: item.context_token, time: Date.now() });
          }
          if (events.length < 64) { events.push(event); counts.received++; }
          else counts.dropped++;
        }
        await delay(250);
      } catch (failure) {
        if (generation !== monitorGeneration) return;
        const httpCode = /^getUpdates (\d{3}):/.exec(String(failure?.message || ''))?.[1];
        const category = api.classifyFetchError?.(failure)?.type || 'unknown';
        connected = false; error = httpCode ? 'poll_http_' + httpCode : 'poll_failed_' + category;
        await delay(3000);
      }
    }
  }
  if (credentials) void monitor(++monitorGeneration);

  async function pollLogin(active) {
    while (!closed && login === active && Date.now() - active.started < 300000) {
      try {
        const verify = active.verify ? '&verify_code=' + encodeURIComponent(active.verify) : '';
        active.verify = '';
        const raw = await api.apiGetFetch({ baseUrl: active.base,
          endpoint: 'ilink/bot/get_qrcode_status?qrcode=' + encodeURIComponent(active.qrcode) + verify,
          timeoutMs: 35000, label: 'qr_status' });
        if (login !== active) return;
        const result = JSON.parse(raw);
        active.status = result.status;
        if (result.status === 'scaned_but_redirect' && result.redirect_host) {
          const base = 'https://' + result.redirect_host + '/';
          if (!trustedBase(base)) throw new Error('untrusted_redirect');
          active.base = base;
        }
        if (result.status === 'confirmed') {
          const base = result.baseurl || OFFICIAL_BASE;
          if (!trustedBase(base) || !result.bot_token || !result.ilink_bot_id || !result.ilink_user_id) {
            throw new Error('invalid_login_confirmation');
          }
          credentials = { token: result.bot_token, account: result.ilink_bot_id,
            owner: result.ilink_user_id, base };
          persist(); connected = false; cursor = ''; contexts.clear(); events.length = 0;
          error = ''; void monitor(++monitorGeneration); return;
        }
        if (['expired', 'verify_code_blocked', 'binded_redirect'].includes(result.status)) return;
        await delay(1000);
      } catch { active.error = 'login_poll_failed'; await delay(2000); }
    }
    if (login === active && active.status !== 'confirmed') active.status = 'expired';
  }

  function json(response, code, data) {
    response.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    response.end(JSON.stringify(data));
  }
  async function body(request) {
    let text = '';
    for await (const chunk of request) {
      text += chunk;
      if (Buffer.byteLength(text) > 100000) throw new Error('body_too_large');
    }
    return JSON.parse(text || '{}');
  }
  const server = http.createServer(async (request, response) => {
    const route = new URL(request.url, 'http://localhost').pathname;
    if (route === '/' && request.method === 'GET') {
      response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
        'Content-Security-Policy': "default-src 'self'; img-src 'self' data:; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'" });
      return response.end(PAGE);
    }
    const candidate = Buffer.from(request.headers.authorization || '');
    const expected = Buffer.from('Bearer ' + localToken);
    if (candidate.length !== expected.length
        || !crypto.timingSafeEqual(candidate, expected)) {
      return json(response, 401, { error: 'unauthorized' });
    }
    try {
      if (route === '/status' && request.method === 'GET') {
        return json(response, 200, { connected, logged_in: Boolean(credentials), error,
          login_status: login?.status || '', login_error: login?.error || '', queue_depth: events.length,
          counters: counts, account_id: credentials?.account || '', owner_sender_id: credentials?.owner || '' });
      }
      if (route === '/login/reset' && request.method === 'POST') {
        monitorGeneration++; login = null; credentials = null; connected = false;
        cursor = ''; error = ''; events.length = 0; contexts.clear(); persist();
        return json(response, 200, { status: 'cleared' });
      }
      if (route === '/login/start' && request.method === 'POST') {
        const raw = await api.apiPostFetch({ baseUrl: OFFICIAL_BASE,
          endpoint: 'ilink/bot/get_bot_qrcode?bot_type=3',
          body: JSON.stringify({ local_token_list: credentials ? [credentials.token] : [] }),
          timeoutMs: 15000, label: 'qr_start' });
        const result = JSON.parse(raw);
        if (typeof result.qrcode !== 'string' || typeof result.qrcode_img_content !== 'string') {
          throw new Error('invalid_qr_response');
        }
        login = { qrcode: result.qrcode, base: OFFICIAL_BASE,
          status: 'wait', started: Date.now(), verify: '', error: '' };
        const png = await QRCode.toBuffer(result.qrcode_img_content, { width: 360, margin: 2 });
        fs.writeFileSync(path.join(stateDir, 'qrcode.png'), png, { mode: 0o600 });
        void pollLogin(login);
        return json(response, 200, { status: 'wait', image: 'data:image/png;base64,' + png.toString('base64') });
      }
      if (route === '/login/verify' && request.method === 'POST') {
        const input = await body(request);
        if (!login || login.status !== 'need_verifycode' || !/^\d{4,8}$/.test(input.code || '')) {
          return json(response, 400, { error: 'invalid_verification' });
        }
        login.verify = input.code; return json(response, 200, { status: 'submitted' });
      }
      if (route === '/events' && request.method === 'GET') {
        return json(response, 200, { connected, events: connected ? events.splice(0, 64) : [] });
      }
      if (route === '/send' && request.method === 'POST') {
        const input = await body(request);
        const context = contexts.get(input.address);
        if (!connected || !credentials || input.address !== credentials.owner
            || typeof input.text !== 'string' || !input.text.trim() || input.text.length > 16000
            || !context?.token || Date.now() - context.time > 86400000) {
          return json(response, 200, { status: 'rejected' });
        }
        let status = 'unknown';
        try {
          const result = await api.sendMessage({ baseUrl: credentials.base, token: credentials.token,
            timeoutMs: 15000, body: { msg: { from_user_id: '', to_user_id: input.address,
              client_id: crypto.randomUUID(), message_type: 2, message_state: 2,
              context_token: context.token, item_list: [{ type: 1, text_item: { text: input.text } }] } } });
          status = result.ret === 0 && (result.errcode == null || result.errcode === 0) ? 'accepted' : 'unknown';
        } catch { status = 'unknown'; }
        counts[status === 'accepted' ? 'sent' : 'unknown']++;
        return json(response, 200, { status });
      }
      return json(response, 404, { error: 'not_found' });
    } catch { return json(response, 502, { error: 'bridge_request_failed' }); }
  });
  return { server, stop() { closed = true; monitorGeneration++; connected = false; server.close(); } };
}

const PAGE = `<!doctype html><html lang="zh"><meta charset="utf-8"><title>腾讯微信连接</title>
<style>body{font:16px sans-serif;max-width:680px;margin:40px auto;padding:20px}input,button{padding:10px;margin:5px}img{max-width:360px}pre{white-space:pre-wrap}</style>
<h1>腾讯微信连接</h1><p>用你准备用来和 bot 聊天的日常微信扫码，不是备用 bot 微信。腾讯会分配独立的 bot 身份。填入本机桥接密钥获取二维码。</p>
<input id="key" type="password" placeholder="本机桥接密钥" autocomplete="off"><button id="login">获取 / 刷新二维码</button>
<button id="switch">切换扫码账号</button>
<p><img id="qr" alt="登录二维码"></p><input id="code" placeholder="手机上的验证码"><button id="verify">提交验证码</button><pre id="state"></pre>
<script>
const key=document.querySelector('#key'),state=document.querySelector('#state');
key.value=sessionStorage.getItem('bridge-key')||'';
if(location.hash){key.value=decodeURIComponent(location.hash.slice(1));history.replaceState(null,'',location.pathname)}
async function call(route,body){sessionStorage.setItem('bridge-key',key.value);const r=await fetch(route,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+key.value,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok)throw Error(data.error);return data}
document.querySelector('#login').onclick=async()=>{try{const r=await call('/login/start',{});document.querySelector('#qr').src=r.image}catch(e){state.textContent=e.message}};
document.querySelector('#switch').onclick=async()=>{try{await call('/login/reset',{});const r=await call('/login/start',{});document.querySelector('#qr').src=r.image}catch(e){state.textContent=e.message}};
document.querySelector('#verify').onclick=async()=>{try{await call('/login/verify',{code:document.querySelector('#code').value});document.querySelector('#code').value=''}catch(e){state.textContent=e.message}};
setInterval(async()=>{if(!key.value)return;try{state.textContent=JSON.stringify(await call('/status'),null,2)}catch(e){state.textContent=e.message}},2000);
</script></html>`;

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const api = await import('./official-api.mjs');
  const bridge = createBridge({ api, stateDir: process.env.STATE_DIR || '/state',
    localToken: process.env.BRIDGE_TOKEN });
  bridge.server.listen(1238, '0.0.0.0', () => console.log('Tencent Weixin bridge listening'));
  process.on('SIGTERM', () => { bridge.stop(); process.exit(0); });
}
