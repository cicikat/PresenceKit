// Session-local iron collection. Every asynchronous continuation checks ownership.
const ORES = new Set(['iron_ore', 'deepslate_iron_ore']);
const PICKS = new Set(['stone_pickaxe', 'iron_pickaxe', 'diamond_pickaxe', 'netherite_pickaxe']);
export function visibleIron(bot, radius = 8) {
  if (!bot?.findBlocks || !bot.entity) return [];
  return bot.findBlocks({matching: b => ORES.has(b.name), maxDistance: radius, count: 8})
    .map(p => bot.blockAt(p)).filter(b => b && bot.canSeeBlock(b));
}
export function ironCount(bot) {
  return bot.inventory.items().filter(i => i.name === 'raw_iron').reduce((n, i) => n + i.count, 0);
}
export function startCollect(body, c) {
  const bot = body.bot;
  const ores = visibleIron(bot, c.params.radius);
  if (!ores.length) throw new Error('no_visible_iron');
  const pick = bot.inventory.items().find(i => PICKS.has(i.name) && Number.isFinite(i.maxDurability)
    && Number.isFinite(i.durabilityUsed) && i.maxDurability - i.durabilityUsed >= c.params.count + 8);
  if (!pick) throw new Error('pickaxe_unavailable');
  c.collect = {stage: 'equip', pick, origin: bot.entity.position.clone(), dimension: bot.game.dimension,
    targets: ores.slice(0, c.params.count).map(b => b.position.clone()), index: 0,
    baseline: ironCount(bot), collected: 0, changed: false, pending: false, deadline: body.now() + 15000,
    existingItems: new Set(Object.keys(bot.entities)), eligibleItems: new Set(), pickupSeen: false};
  stepCollect(body, c);
}
export function stepCollect(body, c) {
  const bot = body.bot, s = c.collect;
  const live = () => body.bot === bot && body.current === c;
  if (!s || !live()) return;
  const fail = code => {
    if (!live()) return;
    body.halt(code);
    Object.assign(body.receipts.get(c.command_id), {status: s.changed ? 'outcome_unknown' : 'failed', error: code});
  };
  if (bot.game.dimension !== s.dimension || bot.entity.position.distanceTo(s.origin) > c.params.radius + 2
      || body.snapshot().game.threats.length || bot.inventory.emptySlotCount() < 1) return fail('collection_unsafe');
  if (body.now() > s.deadline) return fail('collection_stage_timeout');
  if (s.pending) return;
  const asyncStep = (fn, done) => {
    s.pending = true;
    Promise.resolve().then(() => { if (!live()) return; return fn(); }).then(() => {
      s.pending = false; if (live()) { done(); s.deadline = body.now() + 15000; }
    }).catch(() => { s.pending = false; fail('collection_execution_failed'); });
  };
  if (s.stage === 'equip') return asyncStep(() => bot.equip(s.pick, 'hand'), () => { s.stage = 'approach'; });
  if (s.stage === 'approach') {
    if (s.index >= s.targets.length || s.collected >= c.params.count) {
      if (!s.collected) return fail('collection_not_observed');
      s.stage = 'return'; bot.pathfinder.setGoal(bot.pkGoals.near(body.owner().position, 2)); return;
    }
    const block = bot.blockAt(s.targets[s.index]);
    if (!block || !ORES.has(block.name) || !bot.canSeeBlock(block)) return fail('ore_unavailable');
    const p = block.position, here = bot.entity.position;
    // No removal of support/ceiling, liquids, falling blocks, or underground expansion.
    if (p.y < Math.floor(here.y) || p.y > Math.floor(here.y) + 1) return fail('unsafe_ore_position');
    const neighbors = [[0,1,0],[0,-1,0],[1,0,0],[-1,0,0],[0,0,1],[0,0,-1]]
      .map(([x,y,z]) => bot.blockAt(p.offset(x,y,z)));
    if (neighbors.some(b => !b || /lava|water|sand|gravel/.test(b.name))) return fail('unsafe_ore_neighbors');
    if (bot.canDigBlock(block)) {
      bot.pathfinder.setGoal(null);
      if (!PICKS.has(bot.heldItem?.name) || bot.heldItem.maxDurability - bot.heldItem.durabilityUsed < 8) return fail('pickaxe_unavailable');
      s.stage = 'dig'; s.changed = true;
      return asyncStep(() => bot.dig(block), () => {
        if (ORES.has(bot.blockAt(p)?.name)) return fail('dig_not_confirmed');
        s.stage = 'pickup'; bot.pathfinder.setGoal(bot.pkGoals.near(p, 0));
      });
    }
    if (!s.goalSet) { bot.pathfinder.setGoal(bot.pkGoals.near(p, 2)); s.goalSet = true; }
  }
  if (s.stage === 'pickup') {
    const gained = ironCount(bot) - s.baseline;
    if (s.pickupSeen && gained > s.collected) {
      s.collected = gained; s.index++; s.goalSet = false; s.pickupSeen = false; s.eligibleItems.clear(); s.stage = 'approach'; s.deadline = body.now() + 15000;
    }
  }
  if (s.stage === 'return' && bot.entity.position.distanceTo(body.owner().position) <= 2.5) {
    body.halt('collection_returned');
    Object.assign(body.receipts.get(c.command_id), {status: s.collected >= c.params.count ? 'succeeded' : 'failed',
      error: s.collected >= c.params.count ? null : 'insufficient_visible_iron', collected: s.collected});
  }
}
