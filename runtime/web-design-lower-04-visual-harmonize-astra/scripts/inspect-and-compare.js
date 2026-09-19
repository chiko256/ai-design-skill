/* Read-only Figma helper. Append this source to use_figma code, then call
 * harmonize.overview(root), .snapshot(root), or .compare(root, baseline).
 * Results are paginated: collect rows until nextCursor === null. Keep snapshot
 * rows in a local file/store; show only counts and differences to the model.
 * Use the same revision token across pages; restart if it changes.
 */
const harmonize = (() => {
  const groups = {
    identity: ['type', 'name', 'visible', 'locked'],
    geometry: ['x', 'y', 'width', 'height', 'rotation', 'relativeTransform'],
    layout: ['layoutMode', 'layoutPositioning', 'layoutAlign', 'layoutGrow', 'layoutSizingHorizontal', 'layoutSizingVertical', 'primaryAxisSizingMode', 'counterAxisSizingMode', 'primaryAxisAlignItems', 'counterAxisAlignItems', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'itemSpacing', 'counterAxisSpacing', 'layoutWrap', 'constraints', 'clipsContent'],
    paint: ['fills', 'strokes', 'strokeWeight', 'strokeAlign', 'strokeCap', 'strokeJoin', 'dashPattern', 'opacity', 'blendMode', 'effects', 'boundVariables', 'fillStyleId', 'strokeStyleId', 'effectStyleId'],
    text: ['characters', 'fontName', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing', 'paragraphSpacing', 'paragraphIndent', 'textAlignHorizontal', 'textAlignVertical', 'textAutoResize', 'textCase', 'textDecoration', 'textStyleId'],
    shape: ['cornerRadius', 'topLeftRadius', 'topRightRadius', 'bottomLeftRadius', 'bottomRightRadius', 'vectorPaths', 'vectorNetwork', 'arcData', 'isMask', 'maskType'],
    component: ['componentProperties', 'variantProperties'],
  };
  const keys = Object.keys(groups);
  const canonical = value => {
    if (typeof value === 'symbol') return '<mixed>';
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])]));
    return value;
  };
  function fingerprint(value) {
    const s = JSON.stringify(canonical(value));
    let a = 2166136261, b = 2246822507;
    for (let i = 0; i < s.length; i++) { a = Math.imul(a ^ s.charCodeAt(i), 16777619); b = Math.imul(b ^ s.charCodeAt(i), 3266489909); }
    return (a >>> 0).toString(16).padStart(8, '0') + (b >>> 0).toString(16).padStart(8, '0');
  }
  function nodes(root) {
    const out = [], pending = [root];
    while (pending.length) { const n = pending.pop(); out.push(n); if ('children' in n) for (let i = n.children.length - 1; i >= 0; i--) pending.push(n.children[i]); }
    return out;
  }
  function effectiveVisible(n) {
    for (let a = n; a; a = a.parent) if ('visible' in a && !a.visible) return false;
    return true;
  }
  function bytes(value) {
    const s = JSON.stringify(value); let size = 0;
    for (let i = 0; i < s.length; i++) { const c = s.charCodeAt(i); if (c < 128) size++; else if (c < 2048) size += 2; else if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length && s.charCodeAt(i + 1) >= 0xdc00 && s.charCodeAt(i + 1) <= 0xdfff) { size += 4; i++; } else size += 3; }
    return size;
  }
  function page(rows, options = {}, metadata = {}) {
    const cursor = options.cursor ?? 0, limit = options.maxBytes ?? 12000;
    if (!Number.isInteger(cursor) || cursor < 0 || cursor > rows.length || !Number.isInteger(limit) || limit < 1000 || limit > 16000) throw Error('Invalid cursor or maxBytes (1000..16000)');
    const revision = fingerprint(rows);
    if (options.revision && options.revision !== revision) throw Error('Read state changed between pages; do not mix snapshots');
    const result = { version: 1, ...metadata, revision, total: rows.length, rows: [], nextCursor: cursor, complete: false };
    if (bytes(result) > limit) throw Error('Metadata exceeds output budget');
    while (result.nextCursor < rows.length) {
      const i = result.nextCursor;
      result.rows.push(rows[i]); result.nextCursor = i + 1;
      if (bytes(result) > limit) {
        result.rows.pop(); result.nextCursor = i;
        if (!result.rows.length) throw Error('One record exceeds output budget; request fewer fields or a smaller range');
        break;
      }
    }
    if (result.nextCursor === rows.length) { result.nextCursor = null; result.complete = true; }
    return result;
  }
  function record(n) {
    const values = keys.map(key => {
      const value = {};
      for (const field of groups[key]) if (field in n) value[field] = n[field];
      if (key === 'identity') value.effectiveVisible = effectiveVisible(n);
      if (key === 'text' && n.type === 'TEXT') value.runs = n.getStyledTextSegments(['fontName', 'fontSize', 'fills', 'lineHeight', 'letterSpacing', 'textCase', 'textDecoration']);
      return fingerprint(value);
    });
    return [n.id, n.parent?.id ?? null, 'children' in n ? n.children.map(c => c.id) : [], ...values];
  }
  function overview(root, options = {}) {
    const depth = options.depth ?? 2;
    if (!Number.isInteger(depth) || depth < 0) throw Error('Invalid depth');
    const rows = [], pending = [[root, 0]];
    while (pending.length) {
      const [n, d] = pending.pop();
      rows.push({ id: n.id, parent: n.parent?.id ?? null, type: n.type, name: n.name, visible: effectiveVisible(n), x: n.x, y: n.y, width: n.width, height: n.height });
      if (d < depth && 'children' in n) for (let i = n.children.length - 1; i >= 0; i--) pending.push([n.children[i], d + 1]);
    }
    return page(rows, options, { mode: 'overview', rootId: root.id });
  }
  function snapshot(root, options = {}) {
    return page(nodes(root).map(record), options, { mode: 'snapshot', rootId: root.id, columns: ['id', 'parent', 'children', ...keys] });
  }
  function compare(root, baseline, options = {}) {
    if (baseline.version !== 1 || baseline.mode !== 'snapshot' || baseline.rootId !== root.id || baseline.complete !== true || baseline.nextCursor !== null || baseline.rows?.length !== baseline.total || JSON.stringify(baseline.columns) !== JSON.stringify(['id', 'parent', 'children', ...keys])) throw Error('Baseline is incomplete or incompatible');
    if (fingerprint(baseline.rows) !== baseline.revision) throw Error('Baseline content does not match its revision');
    const original = new Map(baseline.rows.map(r => [r[0], r]));
    if (original.size !== baseline.rows.length) throw Error('Duplicate baseline node IDs');
    const currentRows = nodes(root).map(record), current = new Map(currentRows.map(r => [r[0], r])), rows = [];
    for (const [id, before] of original) {
      const after = current.get(id), fields = [];
      if (!after) { rows.push({ id, fields: ['missing'] }); continue; }
      if (before[1] !== after[1]) fields.push('parent');
      if (JSON.stringify(before[2]) !== JSON.stringify(after[2].filter(child => original.has(child)))) fields.push('originalChildOrder');
      keys.forEach((key, i) => { if (before[i + 3] !== after[i + 3]) fields.push(key); });
      if (fields.length) rows.push({ id, fields });
    }
    const added = currentRows.filter(r => !original.has(r[0])).map(r => ({ id: r[0], fields: ['added'] }));
    return page([...rows, ...added], options, { mode: 'compare', rootId: root.id, checked: original.size, changed: rows.length, added: added.length, matches: rows.length === 0 });
  }
  return { overview, snapshot, compare, page };
})();
