import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

export function locateBridge(clientRoot = fileURLToPath(new URL('../', import.meta.url))) {
  const root = fs.realpathSync(path.join(clientRoot,'runtime','figma-design-bridge'));
  const release = JSON.parse(fs.readFileSync(path.join(root,'RELEASE.json'),'utf8'));
  if(release.name!=='figma-design-bridge'||!/^[a-f0-9]{40}$/.test(release.sourceCommit)||release.hashNormalization!=='utf8-lf'||!release.files||typeof release.files!=='object')throw Error('Invalid Bridge release');
  for(const [name,hash] of Object.entries(release.files)) {
    if(!name||name.includes('\\')||name.includes(':')||name.startsWith('/')||name.split('/').includes('..'))throw Error('Invalid Bridge release path');
    const file=fs.realpathSync(path.join(root,name));
    if(!file.startsWith(root+path.sep)||!fs.statSync(file).isFile())throw Error('Bridge file escaped its root');
    if(crypto.createHash('sha256').update(fs.readFileSync(file,'utf8').replace(/\r\n/g,'\n')).digest('hex')!==hash)throw Error('Bridge was edited; preserve local changes before updating: '+name);
  }
  for(const required of ['AGENT_USAGE.md','CODEX_SETUP.md','PROTOCOL.md','package.json','plugin/manifest.json','bridge/start.mjs','bridge/cli.mjs'])
    if(!Object.hasOwn(release.files,required))throw Error('Bridge release is incomplete: '+required);
  return {pluginRoot:root,version:release.version,sourceCommit:release.sourceCommit,
    usage:path.join(root,'AGENT_USAGE.md'),setup:path.join(root,'CODEX_SETUP.md'),manifest:path.join(root,'.runtime','plugin','manifest.json'),sourceManifest:path.join(root,'plugin','manifest.json')};
}
export function preparePlugin(clientRoot) {
  const located=locateBridge(clientRoot),target=path.dirname(located.manifest);
  const original=JSON.parse(fs.readFileSync(located.sourceManifest,'utf8'));
  if(fs.existsSync(located.manifest)) {
    const local=JSON.parse(fs.readFileSync(located.manifest,'utf8'));
    if(typeof local.id!=='string'||!local.id)throw Error('Invalid local Figma plugin id');
    original.id=local.id;
  }
  const content=new Map([['manifest.json',Buffer.from(JSON.stringify(original,null,2)+'\n')]]);
  for(const name of ['code.js','ui.html'])content.set(name,fs.readFileSync(path.join(located.pluginRoot,'plugin',name)));
  const changed=[...content].some(([name,bytes])=>!fs.existsSync(path.join(target,name))||!fs.readFileSync(path.join(target,name)).equals(bytes));
  const jobs=path.join(located.pluginRoot,'.runtime','jobs');
  if(changed&&fs.existsSync(jobs)&&fs.readdirSync(jobs).filter(n=>n.endsWith('.json')).some(n=>['running','queued'].includes(JSON.parse(fs.readFileSync(path.join(jobs,n),'utf8')).status)))
    throw Error('Finish active Bridge jobs before preparing the plugin');
  if(changed) {
    fs.mkdirSync(target,{recursive:true});
    for(const [name,bytes]of content)fs.writeFileSync(path.join(target,name),bytes);
  }
  return {...located,prepared:true,changed};
}
if(process.argv[1]&&fs.realpathSync(process.argv[1])===fileURLToPath(import.meta.url)) {
  try {
    const [action='locate',...args]=process.argv.slice(2),located=locateBridge();
    if(action==='prepare')console.log(JSON.stringify(preparePlugin(),null,2));
    else if(action==='locate')console.log(JSON.stringify(located,null,2));
    else if(['start','pair','status'].includes(action)) {
      const script=action==='start'?'start.mjs':'cli.mjs';
      const result=spawnSync(process.execPath,[path.join(located.pluginRoot,'bridge',script),...(action==='start'?[]:[action]),...args],{cwd:located.pluginRoot,stdio:'inherit',windowsHide:true});
      if(result.error)throw result.error;process.exitCode=result.status??1;
    } else throw Error('Usage: node scripts/figma-bridge.mjs locate|prepare|start|pair|status');
  }catch(e){console.error(e.message);process.exitCode=1;}
}
