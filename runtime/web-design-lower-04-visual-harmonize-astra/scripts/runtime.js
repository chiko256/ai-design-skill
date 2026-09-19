/* Evaluate inside functions.exec: const flow = eval(load('flowSource')).
 * Uses the existing tools/store/image globals. Does not edit Figma nodes.
 */
(() => {
  const quote = s => "'" + String(s).replace(/'/g, "'\\''") + "'";
  function decode(result) {
    if (result?.isError) throw Error(JSON.stringify(result.content));
    const parts = result?.content?.filter(c => c.type === 'text') ?? [];
    if (!parts.length) throw Error('Expected a JSON text response');
    return JSON.parse(parts[0].text);
  }
  async function command(args) {
    let r = await tools.exec_command(args);
    while (r.session_id && r.exit_code == null) r = await tools.write_stdin({session_id: r.session_id, chars: '', yield_time_ms: 10000, max_output_tokens: 1000});
    return r;
  }
  async function read(path) {
    const r = await command({cmd: `cat -- ${quote(path)}`, max_output_tokens: 20000});
    if (r.exit_code !== 0 || /truncated output/.test(r.output)) throw Error('File read failed or was truncated: ' + path);
    return r.output;
  }
  async function save(path, value) {
    const body = typeof value === 'string' ? value : JSON.stringify(value);
    const result = await tools.apply_patch(`*** Begin Patch\n*** Add File: ${path}\n${body.split('\n').map(l => '+' + l).join('\n')}\n*** End Patch`);
    if (result?.isError) throw Error('Could not save ' + path);
    return path;
  }
  async function collect({fetch, key, path}) {
    if (!key || !path) throw Error('collect requires a store key and a saved file path');
    let previous = null, result, rows = [], cursor = 0;
    do {
      result = decode(await fetch(previous ? {cursor, revision: previous.revision} : {}));
      if (!Array.isArray(result.rows) || !Number.isInteger(result.total) || typeof result.revision !== 'string') throw Error('Incomplete paged response');
      if (previous && (result.revision !== previous.revision || result.total !== previous.total || result.rootId !== previous.rootId || result.mode !== previous.mode)) throw Error('Read state changed; keep the old baseline and restart the acquisition');
      rows.push(...result.rows);
      const end = result.nextCursor === null;
      if (result.complete !== end || rows.length > result.total || (end && rows.length !== result.total) || (!end && (result.nextCursor !== rows.length || result.nextCursor <= cursor))) throw Error('Missing rows or invalid paging cursor');
      previous = result; cursor = result.nextCursor;
    } while (cursor !== null);
    const data = {...result, rows};
    await save(path, data);
    store(key, data);
    const summary = {mode: data.mode, rootId: data.rootId, total: data.total, complete: true, path};
    if (data.mode === 'overview') summary.rows = rows;
    if (data.mode === 'compare') Object.assign(summary, {checked: data.checked, changed: data.changed, added: data.added, matches: data.matches, differences: rows.filter(r => !r.fields?.includes('added')).slice(0, 20)});
    return summary;
  }
  async function show({fileKey, nodeId, path, maxDimension = 1800, display = true}) {
    if (!path?.startsWith('/')) throw Error('Use an absolute image path');
    const shot = decode(await tools.mcp__codex_apps__figma_get_screenshot({fileKey, nodeId, maxDimension}));
    if (typeof shot.image_url !== 'string' || !shot.image_url.startsWith('https://')) throw Error('Expected an HTTPS screenshot URL');
    const directory = path.slice(0, path.lastIndexOf('/'));
    const mkdir = await command({cmd: `mkdir -p -- ${quote(directory)}`, max_output_tokens: 100});
    if (mkdir.exit_code !== 0) throw Error(mkdir.output);
    const cmd = `curl --fail --silent --show-error --location --output ${quote(path)} -- ${quote(shot.image_url)}`;
    let downloaded = await command({cmd, max_output_tokens: 1000});
    if (downloaded.exit_code !== 0 && /Could not resolve|Failed to connect|Network is unreachable|Operation not permitted/i.test(downloaded.output)) {
      downloaded = await command({cmd, max_output_tokens: 1000, sandbox_permissions: 'require_escalated', justification: 'Figmaの確認画像を指定先へ保存するため、画像URLへの接続を許可しますか？'});
    }
    if (downloaded.exit_code !== 0) throw Error(downloaded.output || 'Screenshot download failed');
    if (display) image((await tools.view_image({path})).image_url);
    return {nodeId, path, width: shot.width, height: shot.height};
  }
  async function initial({fileKey, targets, directory, inspectSource, maxDimension = 1800}) {
    if (!fileKey || !directory?.startsWith('/') || !inspectSource || !Array.isArray(targets) || !targets.length) throw Error('initial requires fileKey, targets, an absolute directory and inspectSource');
    const seen = new Set();
    for (const t of targets) {
      if (!/^[A-Za-z0-9_-]+$/.test(t.key ?? '') || !t.pageId || !t.nodeId || seen.has(t.key)) throw Error('Invalid or duplicate initial target');
      seen.add(t.key);
    }
    const jobs = targets.flatMap(t => [
      ...['overview', 'snapshot'].map(mode => ({name: t.key + '/' + mode, run: () => collect({
        key: t.key + (mode === 'overview' ? 'Overview' : 'Baseline'),
        path: `${directory}/${t.key}-${mode === 'overview' ? 'overview' : 'baseline'}.json`,
        fetch: options => tools.mcp__codex_apps__figma_use_figma({fileKey, skillNames: 'figma-use', description: t.key + 'の初期' + mode + 'を取得',
          code: inspectSource + `\nawait figma.setCurrentPageAsync(await figma.getNodeByIdAsync(${JSON.stringify(t.pageId)})); const root = await figma.getNodeByIdAsync(${JSON.stringify(t.nodeId)}); if (!root) throw Error('Missing initial root'); return harmonize.${mode}(root, ${JSON.stringify({depth: 2, ...options})});`
        })
      })})),
      {name: t.key + '/image', run: () => show({fileKey, nodeId: t.nodeId, path: `${directory}/${t.key}-before.png`, maxDimension, display: false})}
    ]);
    const results = await Promise.allSettled(jobs.map(j => j.run()));
    const failures = results.flatMap((r, i) => r.status === 'rejected' ? [jobs[i].name + ': ' + String(r.reason)] : []);
    if (failures.length) throw Error('Initial acquisition incomplete; successful files are preserved. ' + failures.join('\n'));
    const values = results.map(r => r.value);
    for (let i = 0; i < jobs.length; i++) if (jobs[i].name.endsWith('/image')) image((await tools.view_image({path: values[i].path})).image_url);
    return values;
  }
  const header = '|対象|採否・役割・理由|参照元・使用素材|作成／変更ノード|最終位置・寸法|確認状態|';
  const cell = value => String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\|/g, '&#124;').replace(/\r?\n/g, '<br>');
  function table(rows) {
    return [header, '|---|---|---|---|---|---|', ...rows.map(row => {
      const writes = row.writes ?? [], byId = new Map();
      for (const p of writes.flatMap(w => w.placements ?? (w.placement ? [w.placement] : []))) byId.set(p.id, {...byId.get(p.id), ...p});
      const placements = [...byId.values()];
      const ids = [...new Set(writes.flatMap(w => [...(w.createdNodeIds ?? []), ...(w.mutatedNodeIds ?? [])]))];
      for (const p of placements) if (!p.id || !p.parent || !['x','y','w','h'].every(k => Number.isFinite(p[k]))) throw Error('Placement needs returned id, parent, x, y, w, h');
      const positions = placements.map(p => `${p.id}／親${p.parent}: (${p.x},${p.y}) ${p.w}×${p.h}`).join('\n');
      const sources = [...new Set(placements.map(p => p.source).filter(Boolean))].join('・');
      return '|' + [row.target, row.decision, [row.reference, sources].filter(Boolean).join('\n'), ids.join('・') || row.nodes || 'なし', positions || row.position || '既存維持', row.status].map(cell).join('|') + '|';
    })].join('\n');
  }
  async function updateTable({path, rows}) {
    const original = await read(path), lines = original.split('\n');
    const starts = lines.flatMap((line, i) => line.replace(/\s/g, '') === header ? [i] : []);
    if (starts.length !== 1) throw Error('Expected exactly one existing harmonize table');
    const start = starts[0]; let end = start + 1;
    while (end < lines.length && lines[end].trimStart().startsWith('|')) end++;
    const old = lines.slice(start, end), replacement = table(rows).split('\n');
    const patch = `*** Begin Patch\n*** Update File: ${path}\n@@\n${old.map(l => '-' + l).join('\n')}\n${replacement.map(l => '+' + l).join('\n')}\n*** End Patch`;
    const result = await tools.apply_patch(patch);
    if (result?.isError) throw Error('Table update failed');
    return {path, rows: rows.length};
  }
  return {decode, read, save, collect, show, initial, table, updateTable};
})()
