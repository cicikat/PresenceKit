import fs from 'node:fs';
import path from 'node:path';
import { build } from 'esbuild';

const upstream = process.argv[2];
const output = process.argv[3];
if (!upstream || !output) throw Error('official_source_and_output_directories_required');
const pkg = JSON.parse(fs.readFileSync(path.join(upstream, 'package.json'), 'utf8'));
await build({ entryPoints: [path.join(upstream, 'src/api/api.ts')], outfile: path.join(output, 'official-api.mjs'),
  bundle: true, platform: 'node', format: 'esm', target: 'node24',
  plugins: [{ name: 'standalone-host-hooks', setup(builder) {
    builder.onResolve({ filter: /\/auth\/accounts\.js$/ }, () => ({ path: 'accounts', namespace: 'host-hook' }));
    builder.onResolve({ filter: /\/util\/logger\.js$/ }, () => ({ path: 'logger', namespace: 'host-hook' }));
    builder.onLoad({ filter: /.*/, namespace: 'host-hook' }, args => ({ contents: args.path === 'accounts'
      ? 'export const loadConfigBotAgent=()=>"PresenceKit/1.0";export const loadConfigRouteTag=()=>undefined;'
      : 'const noop=()=>{};export const logger={debug:noop,info:noop,warn:noop,error:noop};' }));
  } }] });
fs.writeFileSync(path.join(output, 'package.json'), JSON.stringify({ name: 'presencekit-openclaw-weixin-bridge',
  version: pkg.version, ilink_appid: pkg.ilink_appid, type: 'module' }));
console.log('Bundled official Tencent API version ' + pkg.version);
