// AST inventory for AR-03. No application execution or private data access.
const fs = require('node:fs');
const path = require('node:path');
const acorn = require('acorn');
const root = path.resolve(__dirname, '../admin/static');
function walk(node, visit) {
  if (!node || typeof node !== 'object') return;
  if (node.type) visit(node);
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(item => walk(item, visit));
    else if (value && typeof value === 'object') walk(value, visit);
  }
}
function files(dir, extension) {
  return fs.readdirSync(dir, {withFileTypes:true}).flatMap(entry => entry.isDirectory()
    ? files(path.join(dir, entry.name), extension)
    : entry.name.endsWith(extension) ? [path.join(dir, entry.name)] : []);
}
const scripts = files(path.join(root, 'js'), '.js').map(file => {
  const source = fs.readFileSync(file, 'utf8'), functions = [], calls = new Set(), globals = new Set(), apis = new Set();
  const ast = acorn.parse(source, {ecmaVersion:'latest', sourceType:'script'});
  walk(ast, node => {
    if (node.type === 'FunctionDeclaration') functions.push(node.id.name);
    if (node.type === 'CallExpression') {
      if (node.callee.type === 'Identifier') calls.add(node.callee.name);
      if (node.callee.name === 'api') apis.add(source.slice(node.start, node.end).split('\n')[0]);
    }
    if (node.type === 'MemberExpression' && node.object.name === 'window') globals.add(node.property.name || node.property.value);
  });
  return {file:path.relative(root,file).replaceAll('\\','/'),functions,calls:[...calls].sort(),globals:[...globals].sort(),apis:[...apis].sort()};
});
const pages = files(path.join(root, 'pages'), '.html').map(file => {
  const html=fs.readFileSync(file,'utf8');
  const handlers=[...html.matchAll(/data-action="([^"]+)"/g)].map(match=>match[1]);
  return {page:path.basename(file,'.html'), fragment:'pages/'+path.basename(file), handlers:[...new Set(handlers)],
    owners:scripts.filter(script=>script.functions.some(name=>handlers.includes(name))).map(script=>script.file),
    components:['card','btn','field','modal','badge'].filter(name=>html.includes(name))};
});
const index=fs.readFileSync(path.join(root,'index.html'),'utf8').replace(/<!--[\s\S]*?-->/g,'');
const output={scripts: [...index.matchAll(/<script[^>]+src="([^"]+)"/g)].map(m=>m[1]),
  styles:[...index.matchAll(/<link[^>]+href="([^"]+\.css[^\"]*)"/g)].map(m=>m[1]),pages,domains:scripts};
process.stdout.write(JSON.stringify(output,null,2)+'\n');
