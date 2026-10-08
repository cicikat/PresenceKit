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
test('chat can accompany movement; stop remains available when receipts are full', async () => {
  const f = fixture(); await f.connect(); f.body.submit(f.command('follow', 'follow'));
  assert.equal(f.body.submit(f.command('chat', 'say', { text: 'Fixture response' })).status, 'succeeded');
  assert.equal(f.bot.goal, 'follow');
  for (let n = f.body.receipts.size; n < 256; n++) f.body.receipts.set('fixture-' + n, { status: 'succeeded' });
  assert.equal(f.body.submit(f.command('stop-at-limit', 'stop')).status, 'succeeded');
  assert.equal(f.bot.goal, null); assert.equal(f.body.receipts.size, 256);
});
test('owner UUID gates incoming messages; local stop precedes model', async () => {
  const f = fixture(); await f.connect(); f.body.submit(f.command('follow', 'follow'));
  f.bot.emit('chat', 'stranger', 'stop'); assert.equal(f.body.events.length, 0);
  f.bot.emit('chat', 'owner', '停下'); assert.equal(f.bot.goal, null); assert.equal(f.body.events.length, 1);
  assert.throws(() => f.body.submit({...f.command('late-follow', 'follow'), owner_stop_revision:0}), /owner_stopped_since_snapshot/);
  assert.equal(f.body.snapshot().owner_stop_revision, 1);
});
test('rejects arbitrary fields, server commands, missing owner and low health', async () => {
  const f = fixture(); await f.connect();
  assert.throws(() => f.body.submit({ ...f.command('x', 'stop'), shell: 'bad' }), /invalid_fields/);
  assert.throws(() => f.body.submit(f.command('say', 'say', { text: '/op player' })), /invalid_chat/);
  assert.equal(f.body.submit(f.command('defend-empty', 'defend')).error, 'weapon_unavailable');
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

test('collection requires explicit bounded params and visible ore, never hidden mining', async () => {
  const f = fixture(); await f.connect();
  assert.throws(() => f.body.submit(f.command('oversized', 'collect_iron', {count: 9, radius: 4})), /invalid_collection/);
  assert.equal(f.body.submit(f.command('empty', 'collect_iron', {count: 1, radius: 4})).error, 'no_visible_iron');
});

test('collection verifies inventory and return; stop invalidates pending digging', async () => {
  const f = fixture(); await f.connect();
  const vec = {x: 1, y: 64, z: 1, clone() {return this;}, offset() {return {neighbor:true};}, distanceTo() {return 1;}};
  const pick = {name:'stone_pickaxe',maxDurability:131,durabilityUsed:0,count:1};
  let mined = false, gained = 0;
  Object.assign(f.bot, {game:{dimension:'overworld'}, entity:{position:vec}, heldItem:pick,
    findBlocks:()=>[vec], canSeeBlock:()=>true, canDigBlock:()=>true,
    blockAt:p=>({name:p.neighbor?'air':mined?'air':'iron_ore',position:vec}),
    equip:async()=>{}, dig:async()=>{mined=true;}, stopDigging:()=>{},
    inventory:{items:()=>[pick,{name:'raw_iron',count:gained}],emptySlotCount:()=>4}});
  f.body.submit(f.command('mine', 'collect_iron', {count:1,radius:4}));
  await new Promise(r=>setImmediate(r));
  f.body.tick(); await new Promise(r=>setImmediate(r));
  assert.equal(f.body.current.collect.stage,'pickup');
  gained=1; f.body.tick();
  assert.equal(f.body.current.collect.stage,'pickup'); // Inventory alone cannot prove this task collected it.
  const dropped = {id:101,position:vec,getDroppedItem:()=>({name:'raw_iron'})};
  f.bot.emit('itemDrop',dropped); f.bot.emit('playerCollect',f.bot.entity,dropped);
  f.body.tick(); f.body.tick(); f.body.tick();
  assert.equal(f.body.receipts.get('mine').status,'succeeded');
  assert.equal(f.body.receipts.get('mine').collected,1);
  mined=false; let release; f.bot.dig=()=>new Promise(r=>{release=r;});
  f.body.submit(f.command('pending', 'collect_iron', {count:1,radius:4}));
  await new Promise(r=>setImmediate(r)); f.body.tick(); await new Promise(r=>setImmediate(r));
  f.body.submit(f.command('cancel', 'stop')); release(); await new Promise(r=>setImmediate(r));
  assert.equal(f.body.current,null); assert.equal(f.bot.goal,null);
  assert.equal(f.body.receipts.get('pending').status,'outcome_unknown');
});

test('approach finishes near owner; protection follows and cannot chase past its local radius', async () => {
  const f = fixture(); await f.connect();
  f.body.submit(f.command('near', 'approach')); f.body.tick();
  assert.equal(f.body.receipts.get('near').status,'succeeded');
  f.bot.inventory.items = () => [{name:'stone_sword',maxDurability:131,durabilityUsed:0}];
  f.bot.equip = async()=>{};
  f.bot.pvp.attack = target => {f.bot.pvp.target=target;};
  f.bot.pvp.stop = () => {f.bot.pvp.target=null;};
  f.body.submit(f.command('protect', 'protect')); await new Promise(r=>setImmediate(r)); f.body.tick();
  assert.equal(f.bot.goal,'follow');
  const target={id:42,name:'zombie',position:{distanceTo:()=>2}};
  f.bot.entities={42:target}; f.body.tick(); assert.equal(f.bot.pvp.target,target);
  target.position.distanceTo=()=>8; f.body.tick(); assert.equal(f.bot.pvp.target,null); assert.equal(f.bot.goal,'follow');
  f.body.submit(f.command('stop', 'stop')); assert.equal(f.bot.goal,null);
});
