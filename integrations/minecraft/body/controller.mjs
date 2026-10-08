import { randomUUID, createHash } from 'node:crypto';
import { visibleIron, startCollect, stepCollect } from './collect.mjs';

const ACTIONS = new Set(['stop', 'follow', 'return', 'pickup', 'defend', 'say', 'collect_iron', 'approach', 'accompany', 'protect']);
const HOSTILES = new Set(['zombie', 'husk', 'drowned', 'skeleton', 'stray', 'spider', 'cave_spider', 'silverfish', 'endermite']);
const ID = /^[a-zA-Z0-9_-]{1,64}$/;
const UUID = /^[0-9a-f-]{32,36}$/i;
export class Fault extends Error {
  constructor(code, status = 409) { super(code); this.status = status; }
}
export function strict(value, allowed) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(k => !allowed.includes(k))) throw new Fault('invalid_fields', 422);
}
export class Body {
  constructor(factory, { now = () => Date.now(), leaseMs = 15000 } = {}) {
    this.factory = factory; this.now = now; this.leaseMs = leaseMs;
    this.bot = null; this.binding = null; this.status = 'disconnected';
    this.epoch = randomUUID(); this.receipts = new Map(); this.events = [];
    this.sequence = 0; this.current = null; this.error = null;
  }
  async connect(input) {
    strict(input, ['session_id', 'host', 'port', 'version', 'username', 'auth', 'owner_uuid']);
    if (input.auth === 'offline' && !/^[A-Za-z0-9_]{1,16}$/.test(input.username || '')) throw new Fault('invalid_connection', 422);
    if (!ID.test(input.session_id || '') || typeof input.host !== 'string' || !input.host || input.host.length > 253 || !Number.isInteger(input.port) || input.port < 1 || input.port > 65535 || typeof input.version !== 'string' || !/^[0-9.]{3,20}$/.test(input.version) || typeof input.username !== 'string' || !input.username || input.username.length > 128 || !['offline', 'microsoft'].includes(input.auth) || !UUID.test(input.owner_uuid || '')) throw new Fault('invalid_connection', 422);
    if (this.binding) throw new Fault('body_already_bound');
    this.epoch = randomUUID(); this.receipts.clear(); this.events = [];
    this.binding = { session: input.session_id, owner: input.owner_uuid.replaceAll('-', '').toLowerCase(), lease: this.now() + this.leaseMs };
    this.status = 'connecting'; this.error = null;
    try {
      const bot = await this.factory(input);
      this.bot = bot;
      const live = () => this.bot === bot && this.binding;
      bot.on('spawn', () => { if (live()) this.status = 'connected'; });
      bot.on('end', () => { if (live()) { this.halt('connection_lost'); this.bot = null; this.status = 'disconnected'; this.binding = null; } });
      bot.on('error', () => { if (live()) this.error = 'connection_error'; });
      bot.on('kicked', () => { if (live()) this.error = 'server_kicked'; });
      bot.on('death', () => { if (live()) this.halt('bot_died'); });
      bot.on('pk_path_failed', () => { if (live()) this.halt('path_unavailable'); });
      bot.on('itemDrop', entity => {
        const s = this.current?.collect;
        if (!live() || !s || !s.changed || s.existingItems.has(String(entity.id)) || s.eligibleItems.size >= 16) return;
        if (entity.getDroppedItem?.()?.name === 'raw_iron' && entity.position.distanceTo(s.targets[s.index]) <= 2) s.eligibleItems.add(entity.id);
      });
      bot.on('playerCollect', (collector, collected) => {
        if (live() && collector === bot.entity && this.current?.collect?.eligibleItems.has(collected.id)) this.current.collect.pickupSeen = true;
        if (!live() || collector !== bot.entity || this.current?.action !== 'pickup' || this.current.params.entity_id !== collected.id) return;
        const id = this.current.command_id; this.halt('collected');
        Object.assign(this.receipts.get(id), { status: 'succeeded', error: null });
      });
      bot.on('chat', (name, message) => {
        if (!live() || name === bot.username || typeof message !== 'string') return;
        const uuid = (bot.players?.[name]?.uuid || '').replaceAll('-', '').toLowerCase();
        if (uuid !== this.binding.owner) return;
        // Emergency stop is local and never waits for the backend or a model.
        if (['!pk stop', '停下', '停止'].includes(message.trim())) this.halt('owner_stop');
        if (this.events.length >= 100) this.events.shift();
        this.events.push({ seq: ++this.sequence, text: message.slice(0, 500), occurred_at: this.now() });
      });
      return this.snapshot();
    } catch (e) { this.binding = null; this.bot = null; this.status = 'disconnected'; throw e instanceof Fault ? e : new Fault('connection_failed', 503); }
  }
  check(input) {
    if (!this.binding || input.session_id !== this.binding.session || input.connection_epoch !== this.epoch) throw new Fault('stale_binding');
    if (this.now() > this.binding.lease) { this.disconnect('lease_expired'); throw new Fault('lease_expired'); }
  }
  heartbeat(input) {
    strict(input, ['session_id', 'connection_epoch']); this.check(input);
    this.binding.lease = this.now() + this.leaseMs; return this.snapshot();
  }
  owner() {
    return Object.values(this.bot?.players || {}).find(p => (p.uuid || '').replaceAll('-', '').toLowerCase() === this.binding?.owner)?.entity || null;
  }
  snapshot() {
    const bot = this.bot; const owner = this.owner();
    const position = p => p ? { x: Math.round(p.x * 10) / 10, y: Math.round(p.y * 10) / 10, z: Math.round(p.z * 10) / 10 } : null;
    return {
      protocol_version: 1, connection_epoch: this.epoch, status: this.status,
      session_id: this.binding?.session || null, observed_at: this.now(), error: this.error,
      game: bot && this.status === 'connected' ? {
        dimension: bot.game?.dimension || 'unknown', position: position(bot.entity?.position),
        health: bot.health ?? null, food: bot.food ?? null, owner_visible: Boolean(owner),
        owner_distance: owner && bot.entity?.position ? Math.round(owner.position.distanceTo(bot.entity.position) * 10) / 10 : null,
        inventory: (bot.inventory?.items() || []).slice(0, 36).map(i => ({ name: i.name, count: i.count })),
        threats: Object.values(bot.entities || {}).filter(e => HOSTILES.has(e.name) && bot.entity?.position && e.position.distanceTo(bot.entity.position) < 12).slice(0, 8).map(e => ({ id: e.id, name: e.name })),
        dropped_items: Object.values(bot.entities || {}).filter(e => ['item', 'Item'].includes(e.name) && bot.entity?.position && e.position.distanceTo(bot.entity.position) <= 8).slice(0, 8).map(e => ({ entity_id: e.id })),
        visible_iron: visibleIron(bot).map(b => ({x: b.position.x, y: b.position.y, z: b.position.z})),
        terrain: 'local_loaded_only'
      } : null,
      current: this.current ? { command_id: this.current.command_id, action: this.current.action,
        stage: this.current.collect?.stage, collected: this.current.collect?.collected } : null,
      receipts: [...this.receipts.values()].slice(-30).map(({ digest, ...r }) => r),
      event_seq: this.sequence
    };
  }
  halt(reason = 'canceled') {
    const bot = this.bot;
    if (bot) { bot.stopDigging?.(); bot.pvp?.stop(); bot.pathfinder?.setGoal(null); bot.clearControlStates?.(); }
    if (this.current) this.finish(this.current.command_id, this.current.collect?.changed ? 'outcome_unknown' : 'canceled', reason);
  }
  finish(id, status, error = null) {
    const r = this.receipts.get(id); if (!r || r.status !== 'running') return;
    Object.assign(r, { status, error, finished_at: this.now() });
    if (this.current?.command_id === id) this.current = null;
  }
  disconnect(reason = 'disconnected') {
    this.halt(reason); const bot = this.bot; this.bot = null; this.binding = null;
    this.status = 'disconnected'; bot?.quit?.(); return this.snapshot();
  }
  submit(input) {
    strict(input, ['session_id', 'connection_epoch', 'command_id', 'action', 'expires_at', 'params']);
    this.check(input);
    if (!ID.test(input.command_id || '') || !ACTIONS.has(input.action) || !Number.isFinite(input.expires_at) || input.expires_at <= this.now() || input.expires_at > this.now() + 120000) throw new Fault('invalid_command', 422);
    const p = input.params || {}; strict(p, input.action === 'say' ? ['text'] : input.action === 'pickup' ? ['entity_id'] : input.action === 'collect_iron' ? ['count', 'radius'] : []);
    if (input.action === 'collect_iron' && (!Number.isInteger(p.count) || p.count < 1 || p.count > 8 || !Number.isInteger(p.radius) || p.radius < 1 || p.radius > 8)) throw new Fault('invalid_collection', 422);
    if (input.action === 'say' && (typeof p.text !== 'string' || !p.text.trim() || p.text.length > 240 || /[\r\n\u0000-\u001f]/.test(p.text) || p.text.trimStart().startsWith('/'))) throw new Fault('invalid_chat', 422);
    if (input.action === 'pickup' && !Number.isInteger(p.entity_id)) throw new Fault('invalid_pickup', 422);
    const digest = createHash('sha256').update(JSON.stringify({ action: input.action, params: p })).digest('hex');
    const old = this.receipts.get(input.command_id);
    if (old) { if (old.digest !== digest) throw new Fault('command_conflict'); return { ...old, digest: undefined }; }
    if (input.action !== 'stop' && this.status !== 'connected') throw new Fault('not_spawned');
    if (!['stop', 'say'].includes(input.action) && this.current) throw new Fault('body_busy');
    if (this.receipts.size >= 256) {
      if (input.action === 'stop') { this.halt('owner_stop'); return { command_id: input.command_id, action: 'stop', status: 'succeeded', accepted_at: this.now(), error: null }; }
      throw new Fault('receipt_limit');
    }
    const receipt = { command_id: input.command_id, action: input.action, status: 'running', accepted_at: this.now(), error: null, digest };
    this.receipts.set(input.command_id, receipt);
    if (input.action === 'stop') { this.halt('owner_stop'); this.finish(input.command_id, 'succeeded'); }
    else if (input.action === 'say') {
      this.bot.chat(p.text); this.finish(input.command_id, 'succeeded');
    } else {
      this.current = { ...input, params: p, started_at: this.now() };
      try { this.begin(this.current); } catch (e) { this.halt(e instanceof Fault ? e.message : 'execution_failed'); receipt.status = 'failed'; receipt.error = e instanceof Fault ? e.message : 'execution_failed'; }
    }
    return { ...receipt, digest: undefined };
  }
  begin(command) {
    const bot = this.bot; const owner = this.owner();
    if (!owner || !bot.entity || owner.position.distanceTo(bot.entity.position) > 32) throw new Fault('owner_not_nearby');
    if (bot.health <= 8) throw new Fault('low_health');
    if (command.action === 'follow') bot.pathfinder.setGoal(bot.pkGoals.follow(owner, 3), true);
    if (command.action === 'accompany') bot.pathfinder.setGoal(bot.pkGoals.follow(owner, 2.5), true);
    if (command.action === 'approach') bot.pathfinder.setGoal(bot.pkGoals.near(owner.position, 1.5));
    if (command.action === 'return') bot.pathfinder.setGoal(bot.pkGoals.near(owner.position, 2));
    if (['defend', 'protect'].includes(command.action)) {
      const weapon = bot.inventory.items().find(i => /_(sword|axe)$/.test(i.name)
        && Number.isFinite(i.maxDurability) && i.maxDurability - i.durabilityUsed >= 8);
      if (!weapon) throw new Fault('weapon_unavailable');
      command.defendReady = false;
      Promise.resolve().then(() => {
        if (this.current === command && this.bot === bot) return bot.equip(weapon, 'hand');
      }).then(() => {
        if (this.current === command && this.bot === bot) command.defendReady = true;
      }).catch(() => {
        if (this.current === command && this.bot === bot) {
          this.halt('equip_failed'); Object.assign(this.receipts.get(command.command_id), {status: 'failed', error: 'equip_failed'});
        }
      });
    }
    if (command.action === 'collect_iron') {
      try { startCollect(this, command); } catch (e) { throw new Fault(['no_visible_iron', 'pickaxe_unavailable'].includes(e.message) ? e.message : 'collection_unavailable'); }
    }
    if (command.action === 'pickup') {
      const entity = bot.entities[command.params.entity_id];
      if (!entity || !['item', 'Item'].includes(entity.name) || entity.position.distanceTo(bot.entity.position) > 8 || bot.inventory.emptySlotCount() <= 0) throw new Fault('pickup_unavailable');
      // Explicit entity selection is required; ownership cannot be inferred from a dropped item.
      bot.pathfinder.setGoal(bot.pkGoals.near(entity.position, 0));
    }
  }
  tick() {
    if (this.binding && this.now() > this.binding.lease) return this.disconnect('lease_expired');
    const c = this.current; if (!c || !this.bot) return;
    const bot = this.bot; const owner = this.owner();
    if (this.now() >= c.expires_at || !owner || !bot.entity || owner.position.distanceTo(bot.entity.position) > 32 || bot.health <= 8) return this.halt(this.now() >= c.expires_at ? 'command_expired' : 'unsafe_or_owner_lost');
    if (c.action === 'return' && owner.position.distanceTo(bot.entity.position) <= 2.5) { this.halt('arrived'); const r = this.receipts.get(c.command_id); r.status = 'succeeded'; r.error = null; }
    if (c.action === 'approach' && owner.position.distanceTo(bot.entity.position) <= 2) { this.halt('arrived'); const r = this.receipts.get(c.command_id); r.status = 'succeeded'; r.error = null; }
    if (c.action === 'pickup' && !bot.entities[c.params.entity_id]) { this.halt('item_disappeared'); const r = this.receipts.get(c.command_id); r.status = 'outcome_unknown'; }
    if (c.action === 'collect_iron') stepCollect(this, c);
    if (['defend', 'protect'].includes(c.action)) {
      if (!c.defendReady) return;
      const target = Object.values(bot.entities).find(e => HOSTILES.has(e.name) && e.position.distanceTo(owner.position) <= 6 && e.position.distanceTo(bot.entity.position) <= 6);
      if (target && bot.pvp.target !== target) bot.pvp.attack(target);
      if (!target && bot.pvp.target) { bot.pvp.stop(); bot.pathfinder.setGoal(null); }
      if (!target && c.action === 'protect' && !c.followingOwner) { bot.pathfinder.setGoal(bot.pkGoals.follow(owner, 2.5), true); c.followingOwner = true; }
      if (target) c.followingOwner = false;
    }
  }
}
