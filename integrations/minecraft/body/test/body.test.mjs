import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { Body } from '../controller.mjs';
import { createServer } from '../server.mjs';

function fixture() {
  let clock = 1000; const bot = new EventEmitter(); const pos = { x: 0, y: 64, z: 0, distanceTo: () => 1 };
  Object.assign(bot, { username: 'BodyFixture', players: { owner: { uuid: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', entity: { position: pos } } }, entity: { position: pos }, health: 20, food: 20, entities: {}, inventory: { items: () => [], emptySlotCount: () => 4 }, pkGoals: { follow: () => 'follow', near: () => 'near' }, pathfinder: { setGoal: g => { bot.goal = g; } }, pvp: { stop: () => {}, attack: () => {} }, clearControlStates: () => {}, quit: () => { bot.quitCalled = true; }, chat: t => { bot.sent = t; } });
  const body = new Body(async () => bot, { now: () => clock });
  return { bot, body, advance: ms => { clock += ms; }, connect: async () => { await body.connect({ session_id: 'fixture_session', host: 'localhost', port: 25565, version: '1.21.1', username: 'BodyFixture', auth: 'offline', owner_uuid: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' }); bot.emit('spawn'); }, command: (id, action, params = {}) => ({ session_id: 'fixture_session', connection_epoch: body.epoch, command_id: id, action, params, expires_at: clock + 10000 }) };
}
test('stop preempts follow; duplicated command cannot restart motion', async () => {
  const f = fixture(); await f.connect(); const c = f.command('one', 'follow'); f.body.submit(c);
  f.body.submit(f.command('stop', 'stop')); assert.equal(f.bot.goal, null);
  assert.equal(f.body.submit(c).status, 'canceled'); assert.equal(f.bot.goal, null);
  assert.throws(() => f.body.submit({ ...c, action: 'return' }), /command_conflict/);
});
test('expired lease disconnects; stale epoch and command fail closed', async () => {
  const f = fixture(); await f.connect(); f.body.submit(f.command('follow', 'follow')); f.advance(16000); f.body.tick();
  assert.equal(f.body.status, 'disconnected'); assert.equal(f.bot.goal, null); assert.ok(f.bot.quitCalled);
  assert.throws(() => f.body.submit(f.command('late', 'return')), /stale_binding/);
});
test('owner UUID gates incoming messages; local stop precedes model', async () => {
  const f = fixture(); await f.connect(); f.body.submit(f.command('follow', 'follow'));
  f.bot.emit('chat', 'stranger', 'stop'); assert.equal(f.body.events.length, 0);
  f.bot.emit('chat', 'owner', '停下'); assert.equal(f.bot.goal, null); assert.equal(f.body.events.length, 1);
});
test('rejects arbitrary fields, server commands, missing owner and low health', async () => {
  const f = fixture(); await f.connect();
  assert.throws(() => f.body.submit({ ...f.command('x', 'stop'), shell: 'bad' }), /invalid_fields/);
  assert.throws(() => f.body.submit(f.command('say', 'say', { text: '/op player' })), /invalid_chat/);
  f.bot.health = 4; assert.equal(f.body.submit(f.command('f', 'follow')).error, 'low_health');
});
test('HTTP rejects unauthenticated mutation and caps request size', async () => {
  const f = fixture(); const server = createServer(f.body, 'x'.repeat(32)); await new Promise(r => server.listen(0, '127.0.0.1', r));
  const url = `http://127.0.0.1:${server.address().port}`;
  try {
    assert.equal((await fetch(url + '/v1/connect', { method: 'POST', body: '{}' })).status, 401);
    assert.equal((await fetch(url + '/v1/connect', { method: 'POST', headers: { authorization: 'Bearer ' + 'x'.repeat(32) }, body: JSON.stringify({ x: 'x'.repeat(9000) }) })).status, 413);
    assert.equal((await fetch(url + '/health')).status, 200);
  } finally { server.closeAllConnections(); await new Promise(r => server.close(r)); }
});
