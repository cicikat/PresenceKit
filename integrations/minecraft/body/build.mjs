// A finite starter hut, never arbitrary model-provided blocks or terrain removal.
export const MATERIALS = new Set(['oak_planks', 'spruce_planks', 'birch_planks', 'cobblestone', 'stone_bricks']);
const AIR = new Set(['air', 'cave_air', 'void_air']);
const solid = b => b && b.boundingBox === 'block' && !/sand|gravel|magma|cactus|ice|leaves/.test(b.name);
export function blueprint(origin) {
  const blocks = [];
  for (let y = 0; y < 4; y++) for (let x = 0; x < 5; x++) for (let z = 0; z < 5; z++) {
    if (y === 0 || y === 3 || ((x === 0 || x === 4 || z === 0 || z === 4) && !(x === 2 && z === 0))) blocks.push(origin.offset(x, y, z));
  }
  return blocks;
}
export function validBuildParams(p) {
  return p && typeof p === 'object' && !Array.isArray(p)
    && Object.keys(p).every(k => ['material', 'x', 'y', 'z'].includes(k)) && MATERIALS.has(p.material)
    && ['x', 'y', 'z'].every(k => Number.isInteger(p[k]) && Math.abs(p[k]) <= 30000000)
    && p.y >= -60 && p.y <= 316;
}
const inside = (p, o) => p && p.x >= o.x - 0.4 && p.x < o.x + 5.4 && p.z >= o.z - 0.4 && p.z < o.z + 5.4 && p.y >= o.y - 2 && p.y < o.y + 4;
function occupied(bot, origin) {
  return [bot.entity, ...Object.values(bot.entities || {}), ...Object.values(bot.players || {}).map(p => p.entity)]
    .some(e => e && !['item', 'Item', 'experience_orb'].includes(e.name) && inside(e.position, origin));
}
export function startBuild(body, c) {
  const bot = body.bot, p = c.params;
  const origin = bot.entity.position.offset(p.x - bot.entity.position.x, p.y - bot.entity.position.y, p.z - bot.entity.position.z);
  if (origin.distanceTo(body.owner().position) > 12) throw new Error('build_site_too_far');
  if (occupied(bot, origin)) throw new Error('build_site_occupied');
  const targets = blueprint(origin);
  if (bot.inventory.items().filter(i => i.name === p.material).reduce((n, i) => n + i.count, 0) < targets.length) throw new Error('build_materials_insufficient');
  // Include interior air as well as the footprint. Never overwrite plants or furniture.
  for (let x = 0; x < 5; x++) for (let z = 0; z < 5; z++) {
    if (!solid(bot.blockAt(origin.offset(x, -1, z)))) throw new Error('build_foundation_unavailable');
    for (let y = 0; y < 4; y++) if (!AIR.has(bot.blockAt(origin.offset(x, y, z))?.name)) throw new Error('build_site_not_clear');
  }
  c.build = {origin, targets, index: 0, stage: 'approach', pending: false, changed: false,
    dimension: bot.game.dimension, deadline: body.now() + 15000, goal: null};
  bot.pkBuildOrigin = origin;
  Object.assign(body.receipts.get(c.command_id), {placed: 0, total: targets.length, material: p.material});
}
export function stepBuild(body, c) {
  const bot = body.bot, s = c.build;
  const live = () => body.current === c && body.bot === bot;
  if (!s || !live()) return;
  const fail = code => {
    if (!live()) return;
    body.halt(code);
    Object.assign(body.receipts.get(c.command_id), {status: s.changed ? 'outcome_unknown' : 'failed', error: code});
  };
  if (bot.game.dimension !== s.dimension || body.snapshot().game.threats.length || occupied(bot, s.origin)) return fail('build_unsafe');
  if (body.now() > s.deadline) return fail('build_stage_timeout');
  if (s.pending) return;
  if (s.index === s.targets.length) {
    if (!s.targets.every(p => bot.blockAt(p)?.name === c.params.material)) return fail('build_verification_failed');
    body.halt('build_completed'); Object.assign(body.receipts.get(c.command_id), {status: 'succeeded', error: null}); return;
  }
  const target = s.targets[s.index];
  if (!AIR.has(bot.blockAt(target)?.name)) return fail('build_target_changed');
  // Stand outside the footprint on a checked, level surface; no scaffolding or digging.
  if (!s.goal) {
    const stands = [];
    for (let i = -1; i <= 5; i++) for (const [x, z] of [[-1, i], [5, i], [i, -1], [i, 5]]) {
      const p = s.origin.offset(x, 0, z);
      if (solid(bot.blockAt(p.offset(0, -1, 0))) && AIR.has(bot.blockAt(p)?.name) && AIR.has(bot.blockAt(p.offset(0, 1, 0))?.name)) stands.push(p);
    }
    stands.sort((a, b) => a.distanceTo(target) - b.distanceTo(target));
    if (!stands.length) return fail('build_stand_unavailable');
    s.goal = stands[0]; bot.pathfinder.setGoal(bot.pkGoals.near(s.goal.offset(0.5, 0, 0.5), 0.4));
  }
  if (bot.entity.position.distanceTo(s.goal.offset(0.5, 0, 0.5)) > 0.7) return;
  bot.pathfinder.setGoal(null);
  const item = bot.inventory.items().find(i => i.name === c.params.material && i.count > 0);
  if (!item) return fail('build_materials_insufficient');
  const neighbors = [[0,-1,0],[-1,0,0],[0,0,-1],[1,0,0],[0,0,1],[0,1,0]];
  const ref = neighbors.map(([x,y,z]) => bot.blockAt(target.offset(x,y,z))).find(solid);
  if (!ref || target.offset(0.5,0.5,0.5).distanceTo(bot.entity.position.offset(0,1.62,0)) > 4.5) return fail('build_reference_unavailable');
  const face = target.offset(-ref.position.x, -ref.position.y, -ref.position.z);
  s.pending = true;
  Promise.resolve().then(async () => {
    if (!live()) return;
    await bot.equip(item, 'hand');
    if (!live()) return;
    if (occupied(bot, s.origin) || !AIR.has(bot.blockAt(target)?.name) || bot.heldItem?.name !== c.params.material) throw new Error('build_target_changed');
    // A sent placement cannot be recalled; stop leaves a truthful partial/unknown receipt.
    s.changed = true;
    await bot.placeBlock(ref, face);
    if (!live()) return;
    if (bot.blockAt(target)?.name !== c.params.material) throw new Error('build_not_confirmed');
    s.index++; s.goal = null; s.deadline = body.now() + 15000;
    body.receipts.get(c.command_id).placed = s.index;
  }).catch(() => fail('build_execution_failed')).finally(() => {s.pending = false;});
}
