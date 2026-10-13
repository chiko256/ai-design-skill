import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { DEFAULT_STATE } from './server.mjs';

export function packCommand(source, baseDir) {
  const command = structuredClone(source);
  const assets = command.assets || {};
  for (const [key, asset] of Object.entries(assets)) {
    if (!asset.path) continue;
    const file = path.resolve(baseDir, asset.path);
    const bytes = fs.readFileSync(file);
    if (bytes.length > 10 * 1024 * 1024) throw new Error('Asset exceeds 10MB: ' + file);
    assets[key] = path.extname(file).toLowerCase() === '.svg' ? { svg: bytes.toString('utf8') } : { base64: bytes.toString('base64') };
  }
  command.assets = assets;
  if (command.op === 'buildSection') {
    command.planDigest = crypto.createHash('sha256').update(JSON.stringify({ plan: command.plan, assets })).digest('hex');
  }
  if (command.op === 'appendDecorations') {
    command.decorationDigest = crypto.createHash('sha256').update(JSON.stringify({ parentId: command.parentId, decorations: command.decorations, assets })).digest('hex');
  }
  if (['insertNodes', 'rearrangeNodes'].includes(command.op)) {
    delete command.mutationDigest;
    command.mutationDigest = crypto.createHash('sha256').update(JSON.stringify(command)).digest('hex');
  }
  return command;
}

export function preflight(projectDir, sectionId) {
  const p = path.resolve(projectDir), analysisPath = path.join(p, '_figma/analysis.json');
  const analysis = JSON.parse(fs.readFileSync(analysisPath, 'utf8'));
  const section = analysis.sections.find(s => s.id === sectionId);
  if (!section) throw new Error('Unknown section: ' + sectionId);
  const record = section.record_path || section.materials?.record_path;
  if (!record) throw new Error('Section has no materials record_path');
  const materialsPath = path.resolve(p, record);
  const materials = JSON.parse(fs.readFileSync(materialsPath, 'utf8'));
  const designPath = path.join(p, 'DESIGN.md'), textsPath = path.join(p, 'texts.md');
  const missing = [];
  for (const file of [designPath, textsPath, section.source_path || materials.source_path])
    if (!file || !fs.existsSync(path.resolve(p, file))) missing.push(file || '(source_path missing)');
  const elements = materials.elements.map(e => {
    const merged = (section.elements || []).find(x => x.id === e.id);
    const asset = e.asset_path ? path.resolve(p, e.asset_path) : null;
    if (asset && !fs.existsSync(asset)) missing.push(asset);
    return { id: e.id, kind: e.kind, renderMethod: e.render_method || merged?.render_method || null,
      assetPath: asset, placement: e.scope?.placement };
  });
  const unresolved = (materials.unresolved_issues || []).filter(i => i.status !== 'resolved');
  return { projectDir: p, sectionId, analysisPath, materialsPath, designPath, textsPath,
    sourcePath: section.source_path || materials.source_path, figma: analysis.figma,
    sourceStatus: materials.status, missing, unresolved, elements,
    readyForPlanning: missing.length === 0 && unresolved.length === 0 && ['passed', 'completed_with_issues'].includes(materials.status),
    note: 'Read-only preflight. DESIGN.md values and mockup must be reviewed before writing. Existing stage10 passed state is not proof for this plugin run.' };
}

async function main() {
  let args = process.argv.slice(2), state = DEFAULT_STATE;
  const stateAt = args.indexOf('--state');
  if (stateAt >= 0) { state = path.resolve(args[stateAt + 1]); args.splice(stateAt, 2); }
  const [action, value, extra] = args;
  const emit = obj => console.log(JSON.stringify(obj, null, 2));
  if (action === 'preflight') { emit(preflight(value, extra || 'fv')); return; }
  if (action === 'pack') {
    const file = path.resolve(value), dest = path.resolve(extra || value.replace(/\.json$/, '') + '.packed.json');
    const packed = packCommand(JSON.parse(fs.readFileSync(file, 'utf8')), path.dirname(file));
    fs.writeFileSync(dest, JSON.stringify(packed)); emit({ path: dest, bytes: fs.statSync(dest).size, planDigest: packed.planDigest }); return;
  }
  if (!['status', 'submit', 'result', 'cancel', 'ui-lock', 'pair'].includes(action)) {
    console.log('Commands:\n  pair\n  preflight PROJECT_DIR SECTION_ID\n  pack JOB.json OUTPUT.json\n  status [--document DOCUMENT_KEY]\n  submit JOB.json [--wait SECONDS] [--document DOCUMENT_KEY]\n  result JOB_ID\n  cancel JOB_ID\n  ui-lock status\n  ui-lock acquire --owner CHAT_ID --target FIGMA_URL\n  ui-lock renew --owner CHAT_ID --lease LEASE_ID\n  ui-lock release --owner CHAT_ID --lease LEASE_ID\nOptional: --state DIR'); return;
  }
  const config = JSON.parse(fs.readFileSync(path.join(state, 'connection.json'), 'utf8'));
  async function request(route, method = 'GET', data) {
    const r = await fetch(config.url + route, { method, headers: { Authorization: 'Bearer ' + config.token, 'Content-Type': 'application/json' },
      body: data === undefined ? undefined : JSON.stringify(data), signal: AbortSignal.timeout(15000) });
    const b = await r.json(); if (!r.ok) throw new Error(b.error); return b;
  }
  if (action === 'pair') return emit(await request('/pair', 'POST', {}));
  if (action === 'ui-lock') {
    if (!value || value === 'status') return emit(await request('/ui-lock'));
    const option = flag => { const at = args.indexOf(flag); return at >= 0 ? args[at + 1] : undefined; };
    if (!['acquire','renew','release'].includes(value)) throw new Error('Unknown ui-lock action');
    return emit(await request('/ui-lock', 'POST', {
      action: value, owner: option('--owner'), target: option('--target'), leaseId: option('--lease'),
      ttlSeconds: option('--ttl') === undefined ? undefined : Number(option('--ttl'))
    }));
  }
  if (action === 'status') {
    const at = args.indexOf('--document');
    if (at >= 0 && !args[at + 1]) throw new Error('--document requires a documentKey');
    return emit(await request('/status' + (at >= 0 ? '?documentKey=' + encodeURIComponent(args[at + 1]) : '')));
  }
  if (action === 'result') return emit(await request('/jobs/' + value));
  if (action === 'cancel') return emit(await request('/jobs/' + value + '/cancel', 'POST', {}));
  const file = path.resolve(value), source = JSON.parse(fs.readFileSync(file, 'utf8'));
  const command = packCommand(source, path.dirname(file));
  const documentAt = args.indexOf('--document');
  const documentKey = documentAt >= 0 ? args[documentAt + 1] : source.documentKey;
  if (!documentKey) throw new Error('Run status, verify the connected file, then pass --document DOCUMENT_KEY');
  const j = await request('/jobs', 'POST', { command, documentKey });
  const waitAt = args.indexOf('--wait'), seconds = waitAt >= 0 ? Math.min(45, Number(args[waitAt + 1])) : 0;
  const end = Date.now() + seconds * 1000;
  let current = j;
  while (['queued', 'running'].includes(current.status) && Date.now() < end) {
    await new Promise(r => setTimeout(r, 500)); current = await request('/jobs/' + j.id);
  }
  emit(current);
  if (current.status === 'failed') process.exitCode = 1;
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main().catch(e => { console.error(e.message); process.exitCode = 1; });
