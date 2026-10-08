import http from 'node:http';
import { readFileSync } from 'node:fs';
import { timingSafeEqual } from 'node:crypto';
import { Body, Fault, strict } from './controller.mjs';

export function createServer(body, token) {
  if (typeof token !== 'string' || token.length < 32) throw new Error('bridge_token_required');
  return http.createServer(async (req, res) => {
    const send = (status, value) => { res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' }); res.end(JSON.stringify(value)); };
    if (req.url === '/health' && req.method === 'GET') return send(200, { healthy: true, protocol_version: 1 });
    const auth = Buffer.from(req.headers.authorization || ''); const expected = Buffer.from(`Bearer ${token}`);
    if (auth.length !== expected.length || !timingSafeEqual(auth, expected)) return send(401, { error: 'unauthorized' });
    try {
      if (req.method === 'GET' && req.url === '/v1/state') return send(200, body.snapshot());
      if (req.method === 'GET' && /^\/v1\/events\?after=\d{1,12}$/.test(req.url)) {
        const after = Number(req.url.split('=')[1]);
        return send(200, { connection_epoch: body.epoch, events: body.events.filter(e => e.seq > after).slice(0, 20) });
      }
      if (req.method !== 'POST') throw new Fault('not_found', 404);
      let raw = ''; for await (const chunk of req) { raw += chunk; if (Buffer.byteLength(raw) > 8192) throw new Fault('body_too_large', 413); }
      let input; try { input = JSON.parse(raw); } catch { throw new Fault('invalid_json', 422); }
      if (req.url === '/v1/connect') return send(200, await body.connect(input));
      if (req.url === '/v1/heartbeat') return send(200, body.heartbeat(input));
      if (req.url === '/v1/commands') return send(200, body.submit(input));
      if (req.url === '/v1/disconnect') { strict(input, ['session_id', 'connection_epoch']); body.check(input); return send(200, body.disconnect()); }
      throw new Fault('not_found', 404);
    } catch (e) { send(e instanceof Fault ? e.status : 500, { error: e instanceof Fault ? e.message : 'internal_error' }); }
  });
}

async function mineflayerFactory(input) {
  const { default: mineflayer } = await import('mineflayer');
  const { default: pf } = await import('mineflayer-pathfinder');
  const { default: pvp } = await import('mineflayer-pvp');
  const bot = mineflayer.createBot({ host: input.host, port: input.port, version: input.version, username: input.username, auth: input.auth, profilesFolder: '/auth', hideErrors: true });
  bot.loadPlugin(pf.pathfinder); bot.loadPlugin(pvp.plugin);
  bot.pkGoals = { follow: (e, r) => new pf.goals.GoalFollow(e, r), near: (p, r) => new pf.goals.GoalNear(p.x, p.y, p.z, r) };
  bot.on('spawn', () => {
    const moves = new pf.Movements(bot); moves.canDig = false; moves.allow1by1towers = false;
    moves.allowParkour = false; moves.allowFreeMotion = false; moves.maxDropDown = 1;
    moves.scafoldingBlocks = []; bot.pathfinder.setMovements(moves);
    bot.pathfinder.searchRadius = 32; bot.pathfinder.thinkTimeout = 1000; bot.pathfinder.tickTimeout = 10;
  });
  bot.on('path_update', result => { if (result.status === 'noPath' || result.status === 'timeout') bot.emit('pk_path_failed'); });
  return bot;
}

if (process.argv[1]?.endsWith('server.mjs')) {
  const token = readFileSync(process.env.BRIDGE_TOKEN_FILE || '/run/secrets/bridge_token', 'utf8').trim();
  const body = new Body(mineflayerFactory);
  const server = createServer(body, token);
  const tick = setInterval(() => body.tick(), 250);
  server.requestTimeout = 5000; server.headersTimeout = 5000;
  server.listen(3210, '0.0.0.0');
  const close = () => { clearInterval(tick); body.disconnect('shutdown'); server.close(() => process.exit(0)); setTimeout(() => process.exit(0), 3000).unref(); };
  process.on('SIGTERM', close); process.on('SIGINT', close);
}
