import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { Body } from '../controller.mjs';
import { blueprint } from '../build.mjs';

class Point {
  constructor(x, y, z) {Object.assign(this, {x,y,z});}
  offset(x,y,z) {return new Point(this.x+x,this.y+y,this.z+z);}
  distanceTo(p) {return Math.hypot(this.x-p.x,this.y-p.y,this.z-p.z);}
}
const settle = () => new Promise(r => setImmediate(r));
async function fixture() {
  const bot = new EventEmitter(), blocks = new Map(), owner = new Point(0,64,0);
  const key = p => `${p.x},${p.y},${p.z}`;
  let materialCount = 100, placements = 0;
  Object.assign(bot, {entity:{position:owner},health:20,food:20,game:{dimension:'overworld'},entities:{},
    players:{owner:{uuid:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',entity:{position:owner}}},
    inventory:{items:()=>[{name:'oak_planks',count:materialCount}]},
    blockAt:p=>({name:blocks.get(key(p)) || (p.y===63?'stone':'air'),boundingBox:blocks.has(key(p)) || p.y===63?'block':'empty',position:p}),
    pkGoals:{near:p=>p},pathfinder:{setGoal:p=>{if(p) bot.entity.position=p;}},
    equip:async i=>{bot.heldItem=i;}, placeBlock:async (ref,face)=>{const p=ref.position.offset(face.x,face.y,face.z);blocks.set(key(p),'oak_planks');materialCount--;placements++;},
    pvp:{stop(){}},clearControlStates(){},quit(){}});
  const body = new Body(async()=>bot,{now:()=>1000});
  await body.connect({session_id:'build_fixture',host:'localhost',port:25565,version:'1.21.1',username:'BodyFixture',auth:'offline',owner_uuid:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'});bot.emit('spawn');
  const command = (id,action='build_house',params={material:'oak_planks',x:4,y:64,z:0})=>({session_id:'build_fixture',connection_epoch:body.epoch,command_id:id,action,params,expires_at:121000});
  return {bot,body,blocks,key,command,placements:()=>placements,setMaterials:n=>{materialCount=n;}};
}
test('hut confirms 80 placements, keeps doorway/interior air and never digs',async()=>{
  const f=await fixture(); assert.equal(blueprint(new Point(4,64,0)).length,80);
  assert.equal(f.body.submit(f.command('hut')).status,'running');
  for(let n=0;n<90;n++){f.body.tick();await settle();}
  const r=f.body.receipts.get('hut');assert.equal(r.status,'succeeded');assert.equal(r.placed,80);
  assert.equal(f.placements(),80);assert.equal(f.bot.blockAt(new Point(6,65,0)).name,'air');
  assert.equal(f.bot.blockAt(new Point(6,66,2)).name,'air');assert.equal(f.bot.pkBuildOrigin,null);
});
test('missing materials, occupied terrain and invalid schema cannot start placing',async()=>{
  const f=await fixture();f.setMaterials(79);assert.equal(f.body.submit(f.command('short')).error,'build_materials_insufficient');
  f.setMaterials(100);f.blocks.set('4,64,0','chest');assert.equal(f.body.submit(f.command('occupied')).error,'build_site_not_clear');
  assert.throws(()=>f.body.submit(f.command('bad','build_house',{material:'tnt',x:4,y:64,z:0})),/invalid_build/);
  assert.throws(()=>f.body.submit(f.command('missing','build_house',{material:'oak_planks'})),/invalid_build/);
  assert.equal(f.placements(),0);
});
test('stop during sent placement leaves unknown partial outcome and cannot continue',async()=>{
  const f=await fixture();let release; const original=f.bot.placeBlock;
  f.bot.placeBlock=async(...args)=>{await new Promise(r=>{release=r;});await original(...args);};
  f.body.submit(f.command('hut'));f.body.tick();await settle();assert.ok(release);
  f.body.submit(f.command('stop','stop',{}));release();await settle();
  for(let n=0;n<4;n++){f.body.tick();await settle();}
  assert.equal(f.body.receipts.get('hut').status,'outcome_unknown');assert.equal(f.placements(),1);
  assert.equal(f.body.current,null);assert.equal(f.bot.pkBuildOrigin,null);
  assert.equal(f.body.submit(f.command('hut')).status,'outcome_unknown');
});
test('another entity entering the footprint halts a partial build',async()=>{
  const f=await fixture();f.body.submit(f.command('hut'));f.body.tick();await settle();
  f.bot.entities.visitor={name:'player',position:new Point(6,65,2)};f.body.tick();
  assert.equal(f.body.receipts.get('hut').status,'outcome_unknown');assert.equal(f.placements(),1);
});
