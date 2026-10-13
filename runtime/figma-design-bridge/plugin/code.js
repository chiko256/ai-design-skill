/* Classic Figma Plugin API only. No MCP, eval, or runtime code download. */
"use strict";

const VERSION = "0.1.5-connect.1";
const META = "stage10.bridge.v1";
const TEST_FILE_NAMES = new Set(["こもれびテーブル｜下層ページ｜プラグイン検証", "こもれびの森ようちえん｜1日の過ごし方｜プラグイン検証", "Sentinel Grid｜主な機能｜プラグイン検証", "外与ペイント｜点検から工事まで｜プラグイン検証"]);
const SAFE_PROPS = new Set([
  "name", "visible", "locked", "opacity", "rotation", "x", "y", "width", "height",
  "fills", "strokes", "strokeWeight", "strokeAlign", "dashPattern", "effects", "blendMode",
  "cornerRadius", "topLeftRadius", "topRightRadius", "bottomLeftRadius", "bottomRightRadius",
  "layoutMode", "layoutWrap", "itemSpacing", "counterAxisSpacing", "paddingTop", "paddingBottom",
  "paddingLeft", "paddingRight", "primaryAxisAlignItems", "counterAxisAlignItems",
  "primaryAxisSizingMode", "counterAxisSizingMode", "layoutSizingHorizontal", "layoutSizingVertical",
  "layoutAlign", "layoutGrow", "layoutPositioning", "minWidth", "maxWidth", "minHeight", "maxHeight",
  "clipsContent", "constraints", "isMask", "maskType", "characters", "fontName", "fontSize",
  "lineHeight", "letterSpacing", "paragraphSpacing", "paragraphIndent", "textAutoResize",
  "textAlignHorizontal", "textAlignVertical", "textCase", "textDecoration", "textTruncation", "maxLines"
]);
const FONT_PROPS = new Set(["characters", "fontName", "fontSize", "lineHeight", "letterSpacing",
  "paragraphSpacing", "paragraphIndent", "textAutoResize", "textAlignHorizontal", "textAlignVertical",
  "textCase", "textDecoration", "textTruncation", "maxLines", "width", "height"]);
const READ_PROPS = [...SAFE_PROPS, "absoluteBoundingBox", "absoluteRenderBounds", "textStyleId", "fillStyleId", "key", "remote", "componentProperties", "componentPropertyDefinitions", "componentPropertyReferences", "hasMissingFont", "boundVariables", "variantProperties"];
const TYPES = new Set(["FRAME", "TEXT", "RECTANGLE", "ELLIPSE", "LINE", "VECTOR", "COMPONENT", "INSTANCE", "SVG"]);
let busy = false;
let revision = 0;
let transientDocumentKey = null;

function assert(ok, message) { if (!ok) throw new Error(message); }
function plain(value) { return typeof value === "symbol" ? "MIXED" : JSON.parse(JSON.stringify(value)); }
function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
function sameStyleValue(a, b) {
  if (same(a, b)) return true;
  if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) <= 0.0001;
  return a && b && typeof a.value === "number" && typeof b.value === "number" &&
    a.unit === b.unit && Math.abs(a.value - b.value) <= 0.0001;
}
function meta(node) { try { return JSON.parse(node.getPluginData(META) || "{}"); } catch (_) { return {}; } }
function setMeta(node, value) { node.setPluginData(META, JSON.stringify(value)); }
function all(root) { return [root].concat("findAll" in root ? root.findAll() : []); }
function uuid() { return Date.now().toString(36) + "-" + Math.random().toString(36).slice(2); }
function docKey() {
  let key = figma.root.getPluginData(META);
  if (!key) {
    if (TEST_FILE_NAMES.has(figma.root.name)) { key = uuid(); figma.root.setPluginData(META, key); }
    else { if (!transientDocumentKey) transientDocumentKey = uuid(); key = transientDocumentKey; }
  }
  return key;
}
async function getNode(id) {
  const n = await figma.getNodeByIdAsync(id);
  assert(n, "Node not found: " + id);
  return n;
}
async function pageFor(job) {
  if (job.pageId) {
    const page = await getNode(job.pageId);
    assert(page.type === "PAGE", "pageId must refer to PAGE");
    await figma.setCurrentPageAsync(page);
  }
}
function onPage(node) {
  let n = node;
  while (n && n.type !== "PAGE") n = n.parent;
  assert(n && n.id === figma.currentPage.id, "Node is outside the active job page");
}
function read(node, fields) {
  const out = { id: node.id, type: node.type, name: node.name, parentId: node.parent && node.parent.id };
  for (const key of fields) {
    assert(READ_PROPS.includes(key), "Unsupported read field: " + key);
    if (key in node) { try { out[key] = plain(node[key]); } catch (_) { /* capability dependent */ } }
  }
  if ("children" in node) out.childIds = node.children.map(n => n.id);
  const m = meta(node);
  if (m.key) out.stage10 = m;
  return out;
}
function validateProps(props) {
  assert(props && typeof props === "object" && !Array.isArray(props), "props must be an object");
  for (const key of Object.keys(props)) assert(SAFE_PROPS.has(key), "Unsupported property: " + key);
  if ("width" in props) assert(Number.isFinite(props.width) && props.width >= 0.01, "width must be >= 0.01");
  if ("height" in props) assert(Number.isFinite(props.height) && props.height >= 0, "height must be non-negative");
}
function validateDimensions(props, type) {
  if ("height" in props) assert(type === "LINE" ? props.height === 0 : props.height >= 0.01,
    type === "LINE" ? "LINE height must be exactly 0" : "height must be >= 0.01");
}
async function loadFonts(node, props) {
  if (node.type !== "TEXT" || !Object.keys(props).some(k => FONT_PROPS.has(k))) return;
  const fonts = node.characters.length ? [...node.getRangeAllFontNames(0, node.characters.length)] : [node.fontName];
  if (props.fontName) fonts.push(props.fontName);
  const seen = new Set();
  for (const font of fonts) {
    assert(font && typeof font !== "symbol", "Cannot resolve mixed font");
    const key = JSON.stringify(font);
    if (!seen.has(key)) { await figma.loadFontAsync(font); seen.add(key); }
  }
}
async function applyProps(node, props, fontsLoaded = false) {
  validateProps(props);
  validateDimensions(props, node.type);
  if (!fontsLoaded) await loadFonts(node, props);
  const ordered = ["layoutMode", "layoutPositioning", "fontName", "fontSize", "characters", "textAutoResize"];
  for (const k of ordered) if (k in props) { assert(k in node, node.type + " cannot set " + k); node[k] = props[k]; }
  if ("width" in props || "height" in props) node.resize("width" in props ? props.width : node.width, "height" in props ? props.height : node.height);
  const last = ["layoutSizingHorizontal", "layoutSizingVertical", "primaryAxisSizingMode", "counterAxisSizingMode"];
  for (const k of Object.keys(props)) {
    if (ordered.includes(k) || last.includes(k) || k === "width" || k === "height") continue;
    assert(k in node, node.type + " cannot set " + k);
    node[k] = props[k];
  }
  for (const k of last) if (k in props) { assert(k in node, node.type + " cannot set " + k); node[k] = props[k]; }
}
function fromBase64(s) {
  const abc = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  const out = []; let bits = 0, value = 0;
  for (const c of s) {
    if (c === "=") break;
    const v = abc.indexOf(c); assert(v >= 0, "Invalid base64 asset");
    value = (value << 6) | v; bits += 6;
    if (bits >= 8) { bits -= 8; out.push((value >> bits) & 255); }
  }
  return new Uint8Array(out);
}
function validateTree(spec, assets, keys, depth) {
  assert(depth < 30, "Tree exceeds depth limit");
  assert(spec && TYPES.has(spec.type), "Unsupported node type");
  assert(typeof spec.key === "string" && spec.key.length > 0 && !keys.has(spec.key), "Missing or duplicate node key");
  keys.add(spec.key); assert(keys.size <= 1500, "One section is limited to 1500 planned nodes");
  validateProps(spec.props || {});
  validateDimensions(spec.props || {}, spec.type);
  if (spec.type === "TEXT") {
    assert(spec.props && typeof spec.props.characters === "string", "TEXT requires characters");
    assert(spec.props.fontName, "TEXT requires an explicit fontName");
  }
  if (spec.type === "SVG" || spec.image) assert(assets[spec.assetId || spec.image.assetId], "Missing asset");
  if (spec.children) {
    assert(["FRAME", "COMPONENT"].includes(spec.type), "Only FRAME/COMPONENT accept children in plans");
    spec.children.forEach(s => validateTree(s, assets, keys, depth + 1));
  }
}
async function createTree(spec, parent, assets, refs, created, sectionKey, sectionInfo) {
  let node;
  if (spec.type === "SVG") {
    const a = assets[spec.assetId]; assert(typeof a.svg === "string", "SVG asset must contain svg text");
    if (spec.svgColor) assert(/^#[a-f0-9]{6}$/i.test(spec.svgColor), "svgColor must be a six-digit hex color");
    node = figma.createNodeFromSvg(spec.svgColor ? a.svg.replace(/currentColor/g, spec.svgColor) : a.svg);
  } else if (spec.type === "INSTANCE") {
    const component = spec.componentRef ? refs[spec.componentRef] :
      spec.componentId ? await getNode(spec.componentId) : await figma.importComponentByKeyAsync(spec.componentKey);
    assert(component && component.type === "COMPONENT", "INSTANCE requires a component");
    node = component.createInstance();
  } else {
    const methods = { FRAME: "createFrame", TEXT: "createText", RECTANGLE: "createRectangle", ELLIPSE: "createEllipse",
      LINE: "createLine", VECTOR: "createVector", COMPONENT: "createComponent" };
    node = figma[methods[spec.type]]();
  }
  created.push(node); parent.appendChild(node); refs[spec.key] = node;
  setMeta(node, { key: spec.key, sectionKey, role: spec.role || null, language: spec.language || null, ...(sectionInfo || {}) });
  if (node.type === "FRAME" || node.type === "COMPONENT") node.fills = [];
  if (spec.vectorPaths) { assert(node.type === "VECTOR", "vectorPaths requires VECTOR"); node.vectorPaths = spec.vectorPaths; }
  await applyProps(node, spec.props || {});
  for (const run of spec.textRuns || []) {
    assert(node.type === "TEXT" && Number.isInteger(run.start) && Number.isInteger(run.end) && run.start >= 0 && run.end <= node.characters.length && run.end > run.start, "Invalid text run");
    for (const k of Object.keys(run.props || {})) {
      const methods = { fills: "setRangeFills", fontName: "setRangeFontName", fontSize: "setRangeFontSize", letterSpacing: "setRangeLetterSpacing" };
      assert(methods[k], "Unsupported text run property: " + k);
      if (k === "fontName") await figma.loadFontAsync(run.props[k]);
      node[methods[k]](run.start, run.end, run.props[k]);
    }
  }
  if (spec.image) {
    const a = assets[spec.image.assetId]; assert(a.base64, "Raster asset must contain base64");
    const hash = figma.createImage(fromBase64(a.base64)).hash;
    const paint = { type: "IMAGE", imageHash: hash, scaleMode: spec.image.scaleMode || "FILL" };
    if (spec.image.imageTransform) paint.imageTransform = spec.image.imageTransform;
    node.fills = [paint];
  }
  if (spec.componentProperties) { assert(node.type === "INSTANCE", "componentProperties requires INSTANCE"); node.setProperties(spec.componentProperties); }
  for (const child of spec.children || []) await createTree(child, node, assets, refs, created, sectionKey);
  // Auto Layout can grow while children are added. Set requested sizing modes again, never force a fixed section height.
  const sizing = {};
  for (const k of ["layoutSizingHorizontal", "layoutSizingVertical", "primaryAxisSizingMode", "counterAxisSizingMode"])
    if (spec.props && k in spec.props) sizing[k] = spec.props[k];
  if (Object.keys(sizing).length) await applyProps(node, sizing);
  return node;
}
function buildReceipt(root) {
  const nodes = all(root), info = meta(root);
  return { rootId: root.id, wrapperId: root.parent.type === "FRAME" ? root.parent.id : null, sectionId: info.sectionId, planDigest: info.planDigest,
    nodeIds: nodes.map(n => n.id), refs: Object.fromEntries(nodes.filter(n => {
      if (!meta(n).key) return false;
      // INSTANCE descendants inherit the source component metadata but are not explicit plan refs.
      let p = n.parent; while (p && p !== root) { if (p.type === "INSTANCE") return false; p = p.parent; }
      return true;
    }).map(n => [meta(n).key, n.id])),
    bounds: plain(root.absoluteBoundingBox), elementMap: info.elementMap || [], needsVisualReview: true };
}
async function buildSection(job) {
  const p = job.plan, assets = job.assets || {};
  assert(p && p.sectionId && p.tree && p.design && job.planDigest, "Missing section plan/design/digest");
  assert(p.tree.type === "FRAME", "Section root must be FRAME");
  assert(p.tree.props.layoutMode === "VERTICAL", "Section root must use VERTICAL Auto Layout");
  assert(p.tree.props.primaryAxisSizingMode === "AUTO", "Section height must follow content (AUTO)");
  for (const pair of [["paddingTop", "paddingTop"], ["paddingBottom", "paddingBottom"]])
    assert(Number.isFinite(p.design[pair[0]]) && p.tree.props[pair[1]] === p.design[pair[0]], "Section padding must match DESIGN values");
  if (p.design.isFV) assert(p.tree.props.minHeight >= 900, "PC FV requires minHeight >= 900");
  const keys = new Set(); validateTree(p.tree, assets, keys, 0);
  for (const e of p.elementMap || []) assert(keys.has(e.ref), "elementMap refers to a missing key: " + e.ref);
  const sectionKey = p.projectId + "/" + p.sectionId;
  assert(typeof p.projectId === "string" && p.projectId, "projectId required");
  let parent = p.parentId ? await getNode(p.parentId) : null;
  let newWrapper = false;
  if (!parent) {
    const wrappers = figma.currentPage.children.filter(n => meta(n).isWrapper && meta(n).projectId === p.projectId);
    assert(wrappers.length <= 1, "Duplicate project wrappers; inspect before continuing");
    parent = wrappers[0];
    if (!parent) {
      parent = figma.createFrame(); newWrapper = true;
      figma.currentPage.appendChild(parent); parent.name = "Stage10 / " + p.projectId;
      parent.fills = []; parent.layoutMode = "VERTICAL"; parent.itemSpacing = 0;
      parent.resize(p.tree.props.width, 100); parent.primaryAxisSizingMode = "AUTO"; parent.counterAxisSizingMode = "FIXED";
      parent.clipsContent = false;
      setMeta(parent, { isWrapper: true, projectId: p.projectId });
      const others = figma.currentPage.children.filter(n => n.id !== parent.id && "width" in n);
      parent.x = p.canvasX === undefined ? (others.length ? Math.max(...others.map(n => n.x + n.width)) + 160 : 0) : p.canvasX;
      parent.y = p.canvasY === undefined ? 0 : p.canvasY;
    }
  }
  assert(parent.type === "FRAME", "Section parent must be an Auto Layout FRAME"); onPage(parent);
  assert(parent.layoutMode === "VERTICAL" && parent.itemSpacing === 0 && parent.primaryAxisSizingMode === "AUTO",
    "Wrapper must use VERTICAL / AUTO / gap 0; inspect and adjust it explicitly");
  const existing = parent.children.filter(n => meta(n).sectionKey === sectionKey && meta(n).isSection);
  assert(existing.length <= 1, "Duplicate section keys; inspect before continuing");
  if (job.repair) {
    const r = job.repair;
    assert(p.parentId === r.wrapperId && meta(parent).isWrapper && meta(parent).projectId === p.projectId,
      "Repair must explicitly target this project's existing wrapper");
    assert(existing.length === 0 && typeof r.missingRootId === "string" &&
      !(await figma.getNodeByIdAsync(r.missingRootId)), "Repair requires the old section root to be absent");
    assert(Array.isArray(r.children) && r.children.length === (p.tree.children || []).length,
      "Repair requires every original direct child");
    const before = parent.children.slice();
    assert(same(before.map(n => n.id), r.children.map(c => c.id)), "Repair wrapper children changed");
    const expectedKeys = new Set(keys); expectedKeys.delete(p.tree.key);
    const kept = before.flatMap(n => all(n));
    assert(kept.length === expectedKeys.size, "Repair subtree is incomplete or has extra nodes");
    const seen = new Set();
    for (const n of kept) {
      const m = meta(n);
      assert(m.sectionKey === sectionKey && expectedKeys.has(m.key) && !seen.has(m.key),
        "Repair subtree metadata does not match the original section");
      seen.add(m.key);
    }
    for (let i = 0; i < before.length; i++) {
      assert(meta(before[i]).key === p.tree.children[i].key, "Repair child order changed");
      const expected = r.children[i].expected;
      for (const k of ["width", "height", "layoutMode", "primaryAxisSizingMode", "counterAxisSizingMode"])
        assert(expected && k in expected && same(plain(before[i][k]), expected[k]), "Repair child conflict: " + k);
    }
    const root = figma.createFrame(), positions = before.map(n => ({ x: n.x, y: n.y }));
    parent.appendChild(root); root.fills = [];
    try {
      await applyProps(root, p.tree.props);
      assert(same(parent.children.filter(n => n.id !== root.id).map(n => n.id), before.map(n => n.id)),
        "Repair wrapper changed during setup");
      for (const n of before) root.appendChild(n);
      setMeta(root, { key: p.tree.key, sectionKey, role: p.tree.role || null, isSection: true,
        sectionId: p.sectionId, planDigest: job.planDigest, buildComplete: true, design: p.design,
        elementMap: p.elementMap || [], reviewState: "pending", repairedFrom: r.missingRootId });
      figma.currentPage.selection = [root]; figma.viewport.scrollAndZoomIntoView([root]);
      return { repaired: true, preservedNodeIds: kept.map(n => n.id), ...buildReceipt(root) };
    } catch (error) {
      for (let i = 0; i < before.length; i++) { parent.appendChild(before[i]); before[i].x = positions[i].x; before[i].y = positions[i].y; }
      root.remove(); throw error;
    }
  }
  if (existing.length) {
    const m = meta(existing[0]);
    assert(m.planDigest === job.planDigest && m.buildComplete, "Section already exists; inspect and patch it instead of rebuilding");
    return { reused: true, ...buildReceipt(existing[0]) };
  }
  const created = [], refs = {};
  try {
    const root = await createTree(p.tree, parent, assets, refs, created, sectionKey,
      { isSection: true, sectionId: p.sectionId, planDigest: job.planDigest, buildComplete: false });
    setMeta(root, { ...meta(root), isSection: true, sectionId: p.sectionId, planDigest: job.planDigest,
      buildComplete: true, design: p.design, elementMap: p.elementMap || [], reviewState: "pending" });
    if (parent.type === "PAGE") {
      // Avoid covering pre-existing work. Explicit canvas placement is optional.
      const others = parent.children.filter(n => n.id !== root.id && "width" in n);
      root.x = p.canvasX === undefined ? (others.length ? Math.max(...others.map(n => n.x + n.width)) + 160 : 0) : p.canvasX;
      root.y = p.canvasY === undefined ? 0 : p.canvasY;
    }
    figma.currentPage.selection = [root]; figma.viewport.scrollAndZoomIntoView([root]);
    return { reused: false, ...buildReceipt(root) };
  } catch (error) {
    // Only nodes allocated by this build are removed. Existing content is never rolled back.
    for (const n of created.reverse()) if (!n.removed) n.remove();
    if (newWrapper && !parent.removed && parent.children.length === 0) parent.remove();
    throw error;
  }
}
function invalidate(node) {
  let n = node;
  while (n && n.type !== "PAGE") {
    const m = meta(n); if (m.isSection) { setMeta(n, { ...m, reviewState: "pending" }); return; }
    n = n.parent;
  }
}
async function patchNodes(job) {
  assert(Array.isArray(job.patches) && job.patches.length <= 300, "patches must be an array of at most 300");
  const entries = [];
  for (const p of job.patches) {
    const n = await getNode(p.id); onPage(n); validateProps(p.props); validateDimensions(p.props, n.type);
    assert(p.expected && typeof p.expected === "object", "Every patch needs expected current values");
    for (const k of Object.keys(p.props)) {
      assert(k in p.expected, "Missing expected value: " + k);
      assert(k in n && same(plain(n[k]), p.expected[k]), "Conflict on " + p.id + "." + k + "; re-inspect before editing");
    }
    await loadFonts(n, p.props); entries.push({ n, p });
  }
  const changed = [];
  try {
    for (const { n, p } of entries) {
      // Recheck after asynchronous font loads to avoid overwriting an intervening manual edit.
      for (const k of Object.keys(p.props)) assert(same(plain(n[k]), p.expected[k]), "Conflict during patch: " + n.id + "." + k);
      await applyProps(n, p.props); invalidate(n);
      changed.push({ id: n.id, before: p.expected, after: read(n, Object.keys(p.props)) });
    }
    return { changed, needsVisualReview: true };
  } catch (error) {
    return { partialFailure: true, message: String(error.message || error), changed,
      inspectNodeIds: entries.map(e => e.n.id), needsCanvasRead: true };
  }
}
async function inspect(job) {
  const root = job.nodeId ? await getNode(job.nodeId) : figma.currentPage;
  onPage(root);
  const depth = Math.min(Math.max(job.depth === undefined ? 1 : job.depth, 0), 8);
  const limit = Math.min(Math.max(job.limit || 100, 1), 500), offset = Math.max(job.offset || 0, 0);
  const fields = job.fields || ["width", "height", "layoutMode", "paddingTop", "paddingBottom", "fontName", "fontSize", "lineHeight"];
  const selected = [];
  function walk(n, d) {
    if (!job.types || job.types.includes(n.type)) selected.push(n);
    if (d < depth && "children" in n) n.children.forEach(c => walk(c, d + 1));
  }
  walk(root, 0);
  return { rootId: root.id, total: selected.length, nextOffset: offset + limit < selected.length ? offset + limit : null,
    nodes: await Promise.all(selected.slice(offset, offset + limit).map(async n => { const value = read(n, fields); if (n.type === "INSTANCE") { const c = await n.getMainComponentAsync(); value.mainComponent = c ? {id:c.id,name:c.name,key:c.key,remote:c.remote} : null; } return value; })), revision };
}
async function validateSection(job) {
  const root = await getNode(job.nodeId); onPage(root);
  const m = meta(root), rules = job.rules || m.design;
  assert(rules, "Validation requires rules or a section created by this plugin");
  const issues = [], nodes = all(root), byKey = Object.fromEntries(nodes.map(n => [meta(n).key, n]));
  const add = (n, rule, message) => issues.push({ nodeId: n.id, rule, message });
  if (root.layoutMode !== "VERTICAL" || root.primaryAxisSizingMode !== "AUTO") add(root, "section-layout", "Section must be vertical and content-sized");
  for (const k of ["paddingTop", "paddingBottom"]) if (root[k] !== rules[k]) add(root, k, "Expected " + rules[k] + ", actual " + root[k]);
  if (rules.isFV && root.height < 900) add(root, "fv-height", "PC FV must be at least 900px");
  for (const n of nodes) {
    const role = meta(n).role;
    if (n.type === "TEXT") {
      if (n.width <= 0 || n.height <= 0) add(n, "text-bounds", "Text has non-positive bounds");
      if (n.hasMissingFont) add(n, "missing-font", "Font is unavailable");
      if (rules.requirePercentLineHeight && (typeof n.lineHeight === "symbol" || n.lineHeight.unit !== "PERCENT")) add(n, "line-height", "Line height must use PERCENT");
      if (role === "nav" && (typeof n.fontSize === "symbol" || n.fontSize < 15)) add(n, "nav-size", "Navigation must be >=15px");
      if (typeof n.fontSize === "number" && n.fontSize <= 15 && rules.sansFamilies &&
          (typeof n.fontName === "symbol" || !rules.sansFamilies.includes(n.fontName.family))) add(n, "small-text-font", "Small text font is outside the approved sans families");
      if (role === "h1" && job.wrapperId) {
        const wrapper = await getNode(job.wrapperId);
        if (n.absoluteBoundingBox.x - wrapper.absoluteBoundingBox.x < 99.5) add(n, "h1-left", "H1 must be at least 100px from the page wrapper left edge");
      }
    }
    if (n.visible && n.parent && n.layoutPositioning !== "ABSOLUTE" && n.rotation === 0) {
      const outside = n.x < -0.5 || n.y < -0.5 || n.x + n.width > n.parent.width + 0.5 || n.y + n.height > n.parent.height + 0.5;
      if (outside && n.parent.clipsContent)
        add(n, "possible-clipping", "Bounds extend outside a clipping parent; inspect the exported image");
      else if (outside && ["HORIZONTAL", "VERTICAL"].includes(n.parent.layoutMode))
        add(n, "auto-layout-overflow", "Flow content extends outside its Auto Layout parent; inspect parent sizing and the exported image");
    }
  }
  for (const e of m.elementMap || []) {
    const n = byKey[e.ref];
    if (!n) { add(root, "element-missing", e.id + " has no node"); continue; }
    if (["text", "editable_text"].includes(e.renderMethod) && n.type !== "TEXT") add(n, "render-method", e.id + " must remain TEXT");
    if (e.renderMethod === "raster_image" && !("fills" in n && Array.isArray(n.fills) && n.fills.some(f => f.type === "IMAGE"))) add(n, "render-method", e.id + " must use the approved raster asset");
  }
  return { nodeId: root.id, nodeCount: nodes.length, structuralPassed: issues.length === 0, issues,
    bounds: plain(root.absoluteBoundingBox), needsVisualReview: true,
    notAutomaticallyChecked: ["mockup fidelity", "intentional overlaps", "text wrapping quality", "FV content centering", "all DESIGN.md role values"] };
}
async function textStyles(job) {
  assert(Array.isArray(job.styles) && job.styles.length <= 100, "styles required");
  const existing = [...await figma.getLocalTextStylesAsync()], changed = [];
  for (const s of job.styles) {
    assert(typeof s.name === "string" && s.name.startsWith("Stage10/"), "Styles must be namespaced Stage10/");
    assert(s.lineHeight && s.lineHeight.unit === "PERCENT", "Style lineHeight must use PERCENT");
    await figma.loadFontAsync(s.fontName);
    let style = existing.find(t => t.name === s.name);
    if (style) {
      for (const key of ["fontName", "fontSize", "lineHeight", "letterSpacing", "paragraphSpacing"])
        if (s[key] !== undefined) assert(sameStyleValue(plain(style[key]), s[key]), "Existing style differs: " + s.name + "; use a new versioned style name");
    } else {
      style = figma.createTextStyle(); style.name = s.name;
      for (const key of ["fontName", "fontSize", "lineHeight", "letterSpacing", "paragraphSpacing"])
        if (s[key] !== undefined) style[key] = s[key];
      existing.push(style);
    }
    // Applying a style changes multiple properties. Require a current snapshot for each target.
    for (const target of s.targets || []) {
      const n = await getNode(target.id); onPage(n); assert(n.type === "TEXT", "Style target must be TEXT");
      for (const key of ["fontName", "fontSize", "lineHeight", "letterSpacing", "paragraphSpacing", "paragraphIndent", "textCase", "textDecoration", "textStyleId"])
        assert(target.expected && same(plain(n[key]), target.expected[key]), "Style target conflict: " + n.id + "." + key);
      await loadFonts(n, { fontName: s.fontName });
      await n.setTextStyleIdAsync(style.id); invalidate(n);
      changed.push(n.id);
    }
  }
  return { changedNodeIds: changed, styles: existing.filter(s => s.name.startsWith("Stage10/")).map(s => ({ id: s.id, name: s.name })), needsVisualReview: changed.length > 0 };
}

async function inspectResources(job) {
  assert(!job.styleIds || (Array.isArray(job.styleIds) && job.styleIds.length <= 100), "At most 100 style IDs");
  const styles = job.styleIds ? await Promise.all(job.styleIds.map(id => figma.getStyleByIdAsync(id))) : await figma.getLocalTextStylesAsync();
  return { textStyles: styles.filter(Boolean).filter(s => s.type === "TEXT").map(s => { const out={id:s.id,name:s.name,key:s.key,remote:s.remote}; for(const k of ["fontName","fontSize","lineHeight","letterSpacing","paragraphSpacing","paragraphIndent","textCase","textDecoration"]) out[k]=plain(s[k]); return out; }) };
}
async function applyTextStyles(job) {
  assert(Array.isArray(job.targets) && job.targets.length <= 300, "At most 300 targets");
  const entries=[];
  for(const t of job.targets) {
    const n=await getNode(t.id);onPage(n);assert(n.type==="TEXT","Style target must be TEXT");
    assert(t.expected && same(plain(n.textStyleId),t.expected.textStyleId),"Style reference conflict: "+n.id);
    const style=t.styleId ? await figma.getStyleByIdAsync(t.styleId) : await figma.importStyleByKeyAsync(t.styleKey);
    assert(style && style.type==="TEXT","Existing Text Style required");
    await figma.loadFontAsync(style.fontName);await loadFonts(n,{fontName:style.fontName});entries.push({n,t,style});
  }
  for(const {n,t} of entries) assert(same(plain(n.textStyleId),t.expected.textStyleId),"Style reference changed during font load");
  const changed=[];
  try {for(const {n,style} of entries) {await n.setTextStyleIdAsync(style.id);invalidate(n);changed.push({id:n.id,textStyleId:n.textStyleId});}}
  catch(error){return {partialFailure:true,message:String(error.message||error),changed,needsCanvasRead:true};}
  return {changed,needsVisualReview:true};
}
async function replaceImages(job) {
  assert(Array.isArray(job.targets) && job.targets.length<=30,"At most 30 image targets");
  const entries=[];
  for(const t of job.targets){
    const n=await getNode(t.id);onPage(n);assert("fills" in n,"Image target needs fills");
    for(const k of ["fills","x","y","width","height","cornerRadius"]) assert(t.expected && k in t.expected && same(plain(n[k]),t.expected[k]),"Image conflict: "+n.id+"."+k);
    assert(job.assets && job.assets[t.assetId] && job.assets[t.assetId].base64,"Local raster asset required");
    assert(["FILL","FIT","CROP","TILE"].includes(t.scaleMode||"FILL"),"Unsupported scaleMode");
    const bytes=fromBase64(job.assets[t.assetId].base64);assert(bytes.length>0 && bytes.length<=10*1024*1024,"Image must be 1 byte–10MB");
    entries.push({n,t,bytes});
  }
  const changed=[];
  try {for(const {n,t,bytes} of entries){
    for(const k of ["fills","x","y","width","height","cornerRadius"]) assert(same(plain(n[k]),t.expected[k]),"Image changed before write: "+n.id+"."+k);
    const hash=figma.createImage(bytes).hash;const paint={type:"IMAGE",imageHash:hash,scaleMode:t.scaleMode||"FILL"};if(t.imageTransform)paint.imageTransform=t.imageTransform;
    n.fills=[paint];invalidate(n);changed.push(read(n,["fills","x","y","width","height","cornerRadius"]));
  }}catch(error){return {partialFailure:true,message:String(error.message||error),changed,needsCanvasRead:true};}
  return {changed,needsVisualReview:true};
}

// Add only absolute raster decorations to an existing section; never rebuild its content.
async function appendDecorations(job) {
  const parent = await getNode(job.parentId); onPage(parent);
  assert(parent.type === "FRAME" && meta(parent).isSection, "Decorations require an existing section FRAME");
  assert(typeof job.decorationDigest === "string" && job.decorationDigest, "Decoration digest required");
  assert(Array.isArray(job.decorations) && job.decorations.length > 0 && job.decorations.length <= 20,
    "Provide 1–20 decorations");
  assert(job.expected && Array.isArray(job.expected.childIds), "Expected section children required");
  for (const k of ["width", "height", "layoutMode"])
    assert(k in job.expected && same(plain(parent[k]), job.expected[k]), "Section conflict: " + k);
  const assets = job.assets || {}, keys = new Set();
  const allowed = new Set(["name", "x", "y", "width", "height", "layoutPositioning", "opacity", "rotation", "effects", "cornerRadius"]);
  for (const spec of job.decorations) {
    assert(spec.type === "RECTANGLE" && spec.image && !spec.children && !spec.textRuns,
      "Decorations must be individual raster RECTANGLE nodes");
    assert(spec.props && spec.props.layoutPositioning === "ABSOLUTE", "Decoration must use ABSOLUTE positioning");
    for (const k of ["x", "y", "width", "height"])
      assert(Number.isFinite(spec.props[k]), "Explicit decoration geometry required: " + k);
    for (const k of Object.keys(spec.props)) assert(allowed.has(k), "Unsupported decoration property: " + k);
    validateTree(spec, assets, keys, 0);
    assert(assets[spec.image.assetId].base64, "Raster asset required");
    fromBase64(assets[spec.image.assetId].base64);
  }
  const existing = parent.children.filter(n => keys.has(meta(n).key));
  if (existing.length) {
    assert(existing.length === keys.size && new Set(existing.map(n => meta(n).key)).size === keys.size,
      "Incomplete or duplicate decorations; inspect before editing");
    assert(same(parent.children.filter(n => !keys.has(meta(n).key)).map(n => n.id), job.expected.childIds),
      "Section children conflict");
    for (const n of existing) {
      assert(meta(n).decorationDigest === job.decorationDigest, "Decoration already exists with a different plan");
      const spec = job.decorations.find(s => s.key === meta(n).key);
      for (const k of Object.keys(spec.props))
        assert(sameStyleValue(plain(n[k]), spec.props[k]), "Decoration was edited: " + n.id + "." + k);
    }
    return { reused: true, parentId: parent.id, nodeIds: existing.map(n => n.id), needsVisualReview: true };
  }
  assert(same(parent.children.map(n => n.id), job.expected.childIds), "Section children conflict");
  const created = [], refs = {};
  try {
    for (const spec of job.decorations) {
      const n = await createTree(spec, parent, assets, refs, created, meta(parent).sectionKey);
      setMeta(n, { ...meta(n), decorationDigest: job.decorationDigest });
    }
    for (const k of ["width", "height", "layoutMode"])
      assert(same(plain(parent[k]), job.expected[k]), "Decoration unexpectedly changed section geometry");
    assert(same(parent.children.filter(n => !created.includes(n)).map(n => n.id), job.expected.childIds),
      "Section children changed during append");
    invalidate(parent);
    return { reused: false, parentId: parent.id, nodeIds: created.map(n => n.id), refs,
      nodes: created.map(n => read(n, ["x", "y", "width", "height", "fills", "layoutPositioning", "opacity", "effects"])),
      needsVisualReview: true };
  } catch (error) {
    for (const n of created.reverse()) if (!n.removed) n.remove();
    throw error;
  }
}

// Structural edits stay inside one completed, plugin-created section. Receipts
// make lost-ACK retries safe; a pending/failed edit must be inspected, not replayed.
function editableSection(node) {
  onPage(node);
  let n = node;
  while (n && n.type !== "PAGE") {
    assert(n.type !== "INSTANCE" || n === node, "Cannot structurally edit inside an instance");
    const m = meta(n);
    if (m.isSection) {
      assert(n.type === "FRAME" && m.buildComplete && m.sectionKey, "Section is not ready for structural editing");
      return n;
    }
    n = n.parent;
  }
  throw new Error("Structural edits require a plugin-created section");
}
function checkParent(parent, expected) {
  assert(parent.type === "FRAME", "Edit parent must be FRAME");
  assert(expected && Array.isArray(expected.childIds), "Expected parent childIds required");
  for (const k of ["width", "height", "layoutMode"])
    assert(k in expected && same(plain(parent[k]), expected[k]), "Parent conflict: " + parent.id + "." + k);
  assert(same(parent.children.map(n => n.id), expected.childIds), "Parent children conflict: " + parent.id);
}
function editSignature(job) {
  assert(typeof job.key === "string" && job.key.length > 0 && job.key.length <= 128, "Edit key required (1–128 characters)");
  assert(typeof job.mutationDigest === "string" && /^[a-f0-9]{64}$/.test(job.mutationDigest), "Packed mutationDigest required");
  // The CLI hashes the complete operation including assets. Keep only that
  // digest in pluginData, never multi-megabyte raster/SVG payloads.
  return job.op + ":" + job.mutationDigest;
}
async function editReplay(section, job, signature) {
  const r = (meta(section).editReceipts || []).find(r => r.key === job.key);
  if (!r) return null;
  assert(r.signature === signature, "Edit key already used with different instructions");
  assert(r.status === "complete", "Previous edit is incomplete; inspect before a new edit");
  for (const order of r.orders) {
    const p = await getNode(order.id);
    assert(editableSection(p).id === section.id && same(p.children.map(n => n.id), order.childIds), "Edited topology changed; inspect before a new edit");
  }
  for (const state of r.states) {
    const n = await getNode(state.id);
    assert(editableSection(n).id === section.id && n.parent.id === state.parentId, "Edited node moved; inspect before a new edit");
    for (const [k, v] of Object.entries(state.props))
      assert(sameStyleValue(plain(n[k]), v), "Edited node changed: " + n.id + "." + k);
  }
  return { ...r.result, reused: true, needsVisualReview: true };
}
function beginEdit(section, job, signature) {
  const m = meta(section), receipts = m.editReceipts || [];
  assert(receipts.length < 128, "Section edit receipt limit reached; inspect before migrating to a new section");
  saveEditMeta(section, { ...m, editReceipts: receipts.concat({ key: job.key, signature, status: "pending" }) });
}
function saveEditMeta(section, value) {
  const serialized = JSON.stringify(value);
  let bytes = 0;
  for (const c of serialized) { const p = c.codePointAt(0); bytes += p < 128 ? 1 : p < 2048 ? 2 : p < 65536 ? 3 : 4; }
  assert(bytes <= 90000, "Edit receipt storage limit reached; use a smaller edit or a new section");
  section.setPluginData(META, serialized);
}
function updateEdit(section, job, update) {
  const m = meta(section);
  saveEditMeta(section, { ...m, editReceipts: m.editReceipts.map(r => r.key === job.key ? { ...r, ...update } : r) });
}
function completeEdit(section, job, result, parents, states) {
  invalidate(section);
  updateEdit(section, job, { status: "complete", result, orders: parents.map(p => ({ id: p.id, childIds: p.children.map(n => n.id) })), states });
  return result;
}
async function insertNodes(job) {
  const parent = await getNode(job.parentId), section = editableSection(parent), signature = editSignature(job);
  const replay = await editReplay(section, job, signature); if (replay) return replay;
  checkParent(parent, job.expected);
  assert(Number.isInteger(job.index) && job.index >= 0 && job.index <= parent.children.length, "Insertion index is out of range");
  assert(Array.isArray(job.nodes) && job.nodes.length > 0 && job.nodes.length <= 50, "Provide 1–50 insertion trees");
  const keys = new Set(), assets = job.assets || {}, existingKeys = new Set(all(section).map(n => meta(n).key).filter(Boolean));
  for (const spec of job.nodes) validateTree(spec, assets, keys, 0);
  for (const key of keys) assert(!existingKeys.has(key), "Node key already exists in this section: " + key);
  const created = [], refs = {}, roots = [];
  beginEdit(section, job, signature);
  try {
    for (const spec of job.nodes) {
      const n = await createTree(spec, parent, assets, refs, created, meta(section).sectionKey);
      parent.insertChild(job.index + roots.length, n); roots.push(n);
    }
    assert(same(parent.children.filter(n => !roots.includes(n)).map(n => n.id), job.expected.childIds), "Parent children changed during insertion");
    const result = { reused: false, sectionNodeId: section.id, sectionId: meta(section).sectionId,
      parentId: parent.id, rootIds: roots.map(n => n.id), nodeIds: created.map(n => n.id),
      refs: Object.fromEntries(Object.entries(refs).map(([key, n]) => [key, n.id])), needsVisualReview: true };
    const specs = new Map();
    const collect = spec => { specs.set(spec.key, spec); for (const c of spec.children || []) collect(c); };
    job.nodes.forEach(collect);
    const states = created.filter(n => specs.has(meta(n).key)).map(n => ({ id: n.id, parentId: n.parent.id,
      props: Object.fromEntries(Object.keys(specs.get(meta(n).key).props || {}).map(k => [k, plain(n[k])])) }));
    return completeEdit(section, job, result, [parent, ...created.filter(n => ["FRAME", "COMPONENT"].includes(n.type))], states);
  } catch (error) {
    let rollbackError = null;
    try {
      for (const n of created.slice().reverse()) if (!n.removed) n.remove();
      // Preserve concurrent metadata updates, removing only this operation's receipt.
      const m = meta(section); setMeta(section, { ...m, editReceipts: (m.editReceipts || []).filter(r => r.key !== job.key) });
    } catch (e) { rollbackError = e; }
    if (!rollbackError) throw error;
    updateEdit(section, job, { status: "failed" }); invalidate(section);
    return { partialFailure: true, message: String(error.message || error), rollbackError: String(rollbackError.message || rollbackError),
      inspectNodeIds: [section.id, parent.id, ...created.filter(n => !n.removed).map(n => n.id)], needsCanvasRead: true };
  }
}
async function rearrangeNodes(job) {
  assert(Array.isArray(job.parents) && job.parents.length > 0 && job.parents.length <= 50, "Provide 1–50 affected parents");
  const parents = [], byId = new Map(), currentOwners = new Map(), desiredOwners = new Map();
  let section = null;
  for (const p of job.parents) {
    assert(!byId.has(p.id), "Duplicate parent");
    const node = await getNode(p.id), owner = editableSection(node);
    if (!section) section = owner;
    assert(owner.id === section.id, "Rearrangement must stay within one section");
    assert(node.type === "FRAME", "Edit parent must be FRAME");
    byId.set(p.id, node); parents.push(node);
  }
  const signature = editSignature(job), replay = await editReplay(section, job, signature); if (replay) return replay;
  for (const p of job.parents) {
    const parent = byId.get(p.id); checkParent(parent, p.expected);
    assert(Array.isArray(p.childIds), "Desired childIds required");
    for (const n of parent.children) {
      assert(!currentOwners.has(n.id), "Duplicate current child"); currentOwners.set(n.id, parent.id);
    }
    for (const id of p.childIds) {
      assert(typeof id === "string" && !desiredOwners.has(id), "Duplicate desired child"); desiredOwners.set(id, parent.id);
    }
  }
  assert(currentOwners.size <= 500 && currentOwners.size === desiredOwners.size && [...currentOwners.keys()].every(id => desiredOwners.has(id)),
    "Desired children must preserve exactly the existing node IDs");
  const nodes = new Map();
  for (const id of currentOwners.keys()) {
    const n = await getNode(id); assert(n.id !== section.id && editableSection(n).id === section.id, "Cannot move a section boundary"); nodes.set(id, n);
  }
  function depth(id) {
    let n = byId.get(id) || nodes.get(id), d = 0; const seen = new Set();
    while (n && n.id !== section.id) {
      assert(!seen.has(n.id), "Rearrangement would create a parent cycle"); seen.add(n.id); d++;
      n = desiredOwners.has(n.id) ? byId.get(desiredOwners.get(n.id)) : n.parent;
    }
    assert(n && n.id === section.id, "Rearrangement leaves the section"); return d;
  }
  for (const id of desiredOwners.keys()) depth(id);
  const patches = new Map();
  assert(job.nodes === undefined || Array.isArray(job.nodes), "nodes must be an array");
  for (const p of job.nodes || []) {
    assert(nodes.has(p.id) && !patches.has(p.id), "Patch must identify a unique affected child");
    const n = nodes.get(p.id); validateProps(p.props); validateDimensions(p.props, n.type);
    assert(p.expected && p.expected.parentId === n.parent.id, "Node parent conflict: " + n.id);
    for (const k of Object.keys(p.props))
      assert(k in n && k in p.expected && same(plain(n[k]), p.expected[k]), "Node property conflict: " + n.id + "." + k);
    await loadFonts(n, p.props); patches.set(p.id, p);
  }
  for (const [id, parentId] of desiredOwners) {
    if (currentOwners.get(id) === parentId) continue;
    const n = nodes.get(id), p = patches.get(id), target = byId.get(parentId);
    const targetMode = patches.get(parentId)?.props.layoutMode || target.layoutMode;
    if (targetMode === "NONE" || (p ? p.props.layoutPositioning || n.layoutPositioning : n.layoutPositioning) === "ABSOLUTE")
      assert(p && Number.isFinite(p.props.x) && Number.isFinite(p.props.y), "Free-positioned reparenting requires explicit x/y and expected current values");
  }
  // Font loads yield to the editor; recheck the entire transaction before writes.
  for (const p of job.parents) checkParent(byId.get(p.id), p.expected);
  for (const [id, p] of patches) {
    const n = nodes.get(id); assert(n.parent.id === p.expected.parentId, "Node parent changed before rearrangement");
    for (const k of Object.keys(p.props)) assert(same(plain(n[k]), p.expected[k]), "Node property changed before rearrangement");
  }
  beginEdit(section, job, signature);
  try {
    // Move outer parents first, avoiding temporary cycles in valid inversions.
    const ordered = parents.slice().sort((a, b) => depth(a.id) - depth(b.id));
    for (const parent of ordered) {
      const p = job.parents.find(p => p.id === parent.id);
      for (let i = 0; i < p.childIds.length; i++) parent.insertChild(i, nodes.get(p.childIds[i]));
    }
    // Parent layout changes precede child geometry. All fonts were loaded and
    // values rechecked above; do not yield to a second asynchronous font load.
    for (const [id, p] of [...patches].sort((a, b) => depth(a[0]) - depth(b[0])))
      await applyProps(nodes.get(id), p.props, true);
    for (const p of job.parents) assert(same(byId.get(p.id).children.map(n => n.id), p.childIds), "Unexpected final child order");
    const states = [...nodes.values()].map(n => ({ id: n.id, parentId: n.parent.id,
      props: Object.fromEntries(Object.keys(patches.get(n.id)?.props || {}).map(k => [k, plain(n[k])])) }));
    const result = { reused: false, sectionNodeId: section.id, sectionId: meta(section).sectionId,
      preservedNodeIds: [...nodes.keys()], parents: parents.map(p => ({ id: p.id, childIds: p.children.map(n => n.id) })), needsVisualReview: true };
    return completeEdit(section, job, result, parents, states);
  } catch (error) {
    updateEdit(section, job, { status: "failed" }); invalidate(section);
    return { partialFailure: true, message: String(error.message || error),
      inspectNodeIds: [section.id, ...parents.map(n => n.id), ...nodes.keys()], needsCanvasRead: true };
  }
}

function ownedComponentContext(node) {
  assert(TEST_FILE_NAMES.has(figma.root.name), "Component setup is restricted to named test files");
  onPage(node); assert(meta(node).key, "Component setup requires plugin-created nodes");
}
async function componentTextProperties(job) {
  const component = await getNode(job.componentId); ownedComponentContext(component);
  assert(component.type === "COMPONENT", "Main COMPONENT required");
  assert(typeof job.key === "string" && job.key.length > 0 && job.key.length <= 120, "Stable setup key required");
  assert(Array.isArray(job.bindings) && job.bindings.length > 0 && job.bindings.length <= 20, "Provide 1–20 text bindings");
  const signature = JSON.stringify(job.bindings.map(b => ({nodeId:b.nodeId, name:b.name, expected:b.expected})));
  const previous = meta(component).textPropertySetup;
  if (previous) {
    assert(previous.key === job.key && previous.signature === signature, "Component setup key/digest conflict");
    for (const b of previous.bindings) {
      const n=await getNode(b.nodeId);
      assert(n.componentPropertyReferences?.characters === b.propertyId && component.componentPropertyDefinitions[b.propertyId]?.type === "TEXT", "Existing text binding was edited");
    }
    return {componentId:component.id, bindings:previous.bindings, reused:true};
  }
  assert(job.expected && same(plain(component.componentPropertyDefinitions),job.expected.componentPropertyDefinitions), "Component definitions conflict");
  const descendants=all(component), names=new Set(), ids=new Set(), entries=[];
  for (const b of job.bindings) {
    assert(typeof b.name === "string" && /^[A-Za-z][A-Za-z0-9 _-]{0,79}$/.test(b.name) && !names.has(b.name), "Invalid or duplicate text property name"); names.add(b.name);
    assert(!ids.has(b.nodeId), "Duplicate text binding node"); ids.add(b.nodeId);
    const n=await getNode(b.nodeId); assert(n.type === "TEXT" && descendants.includes(n), "Text binding must be inside this component");
    for(const k of ["characters","componentPropertyReferences"]) assert(b.expected && k in b.expected && same(plain(n[k]),b.expected[k]),"Text binding conflict: "+n.id+"."+k);
    assert(!n.componentPropertyReferences?.characters,"Text already has a characters binding");
    assert(!Object.keys(component.componentPropertyDefinitions).some(k=>k.split("#")[0]===b.name),"Text property name already exists");
    entries.push({n,b});
  }
  const changed=[];
  try {
    for(const {n,b} of entries) {
      const propertyId=component.addComponentProperty(b.name,"TEXT",n.characters);
      changed.push({nodeId:n.id,propertyId,before:b.expected.componentPropertyReferences});
      n.componentPropertyReferences={...n.componentPropertyReferences,characters:propertyId};
    }
    const bindings=changed.map(({nodeId,propertyId})=>({nodeId,propertyId}));
    setMeta(component,{...meta(component),textPropertySetup:{key:job.key,signature,bindings}});
    return {componentId:component.id,bindings,reused:false,needsVisualReview:true};
  } catch(error) {
    let rollbackComplete=true;
    for(const c of [...changed].reverse()) try { const n=await getNode(c.nodeId);n.componentPropertyReferences=c.before;component.deleteComponentProperty(c.propertyId); } catch(_) {rollbackComplete=false;}
    return {partialFailure:true,message:String(error.message||error),rollbackComplete,changed,needsCanvasRead:true};
  }
}
async function componentVariants(job) {
  const parent=await getNode(job.parentId);ownedComponentContext(parent);
  assert(parent.type==="FRAME", "Variants require a plugin-created FRAME parent");
  assert(typeof job.key==="string" && job.key.length>0 && job.key.length<=120,"Stable variants key required");
  assert(typeof job.name==="string" && /^Lower\/H2-(A|B|C|Custom\/[A-Za-z0-9_-]+)$/.test(job.name),"Only named Lower H2 sets are supported");
  assert(Array.isArray(job.variants) && job.variants.length===2,"Exactly Light and Dark variants required");
  const signature=JSON.stringify({name:job.name,variants:job.variants});
  const existing=parent.children.filter(n=>meta(n).componentSetKey===job.key);
  assert(existing.length<=1,"Duplicate component set key");
  if(existing.length) {
    const set=existing[0];assert(set.type==="COMPONENT_SET" && meta(set).componentSetSignature===signature,"Variants setup digest conflict");
    assert(same(set.children.map(n=>n.id),job.variants.map(v=>v.id)) && set.name===job.name,"Component set was edited");
    for(let i=0;i<job.variants.length;i++)assert(set.children[i].name===job.variants[i].name,"Variant name was edited");
    return {componentSetId:set.id,variantIds:set.children.map(n=>n.id),reused:true};
  }
  assert(job.expected && same(parent.children.map(n=>n.id),job.expected.childIds),"Variant parent children conflict");
  const names=new Set(),components=[];
  for(const v of job.variants) {
    assert(["Mode=Light","Mode=Dark"].includes(v.name) && !names.has(v.name),"Distinct Light/Dark names required");names.add(v.name);
    const n=await getNode(v.id);ownedComponentContext(n);assert(n.type==="COMPONENT" && n.parent===parent,"Variant COMPONENT must be a direct child");
    for(const k of ["name","width","height","componentPropertyDefinitions"])assert(v.expected && k in v.expected && same(plain(n[k]),v.expected[k]),"Variant conflict: "+n.id+"."+k);
    components.push(n);
  }
  let set=null;
  try {
    for(let i=0;i<components.length;i++)components[i].name=job.variants[i].name;
    set=figma.combineAsVariants(components,parent);set.name=job.name;
    setMeta(set,{key:job.key,componentSetKey:job.key,componentSetSignature:signature});
    return {componentSetId:set.id,variantIds:components.map(n=>n.id),reused:false,needsVisualReview:true};
  } catch(error) {
    return {partialFailure:true,message:String(error.message||error),componentSetId:set?.id||null,inspectNodeIds:[parent.id,...components.map(n=>n.id)],needsCanvasRead:true};
  }
}

async function execute(job) {
  assert(job && typeof job.op === "string", "Missing command op");
  if (job.documentKey) assert(job.documentKey === docKey(), "Connected Figma document does not match the job");
  await pageFor(job);
  switch (job.op) {
    case "context": return { version: VERSION, documentKey: docKey(), fileName: figma.root.name,
      pageId: figma.currentPage.id, pages: figma.root.children.map(p => ({ id: p.id, name: p.name })),
      selection: figma.currentPage.selection.map(n => ({ id: n.id, name: n.name, type: n.type })) };
    case "fonts": {
      const fonts = (await figma.listAvailableFontsAsync()).map(f => f.fontName).filter(f => !job.query || f.family.toLowerCase().includes(job.query.toLowerCase()));
      const offset = job.offset || 0, limit = Math.min(job.limit || 50, 200);
      return { total: fonts.length, fonts: fonts.slice(offset, offset + limit), nextOffset: offset + limit < fonts.length ? offset + limit : null };
    }
    case "inspect": return inspect(job);
    case "inspectResources": return inspectResources(job);
    case "componentTextProperties": return componentTextProperties(job);
    case "componentVariants": return componentVariants(job);
    case "applyTextStyles": return applyTextStyles(job);
    case "replaceImages": return replaceImages(job);
    case "appendDecorations": return appendDecorations(job);
    case "insertNodes": return insertNodes(job);
    case "rearrangeNodes": return rearrangeNodes(job);
    case "buildSection": return buildSection(job);
    case "patchNodes": return patchNodes(job);
    case "validateSection": return validateSection(job);
    case "textStyles": return textStyles(job);
    case "exportNode": {
      const n = await getNode(job.nodeId); onPage(n);
      const format = job.format === "SVG" ? "SVG" : "PNG";
      const scale = job.scale === undefined ? 1 : job.scale;
      assert(Number.isFinite(scale) && scale > 0 && scale <= 2, "Export scale must be 0–2");
      const options = format === "PNG" ? { format, constraint: { type: "SCALE", value: scale } } : { format };
      const bytes = await n.exportAsync(options);
      assert(bytes.length < 24 * 1024 * 1024, "Export exceeds 24MB; export a smaller section");
      return { nodeId: n.id, format, bounds: plain(n.absoluteBoundingBox), revision, binary: Array.from(bytes) };
    }
    default: throw new Error("Unknown operation: " + job.op);
  }
}

// Export the engine only for the local contract tests; Figma has no module loader here.
if (typeof module !== "undefined") module.exports = { execute, validateProps, validateTree, fromBase64, read };
if (typeof figma !== "undefined" && typeof __html__ !== "undefined") {
  figma.showUI(__html__, { width: 380, height: 560, themeColors: true });
  const watchedPages = new Set();
  const watchPage = () => {
    if (watchedPages.has(figma.currentPage.id)) return;
    watchedPages.add(figma.currentPage.id);
    figma.currentPage.on("nodechange", () => { revision += 1; });
  };
  watchPage();
  figma.on("currentpagechange", () => { revision += 1; watchPage(); });
  figma.ui.onmessage = async message => {
    if (message.type === "hello") {
      figma.ui.postMessage({ type: "hello", context: await execute({ op: "context" }) }); return;
    }
    if (message.type !== "execute") return;
    if (busy) { figma.ui.postMessage({ type: "result", id: message.id, ok: false, error: "Plugin is busy" }); return; }
    busy = true;
    try {
      const result = await execute(message.job);
      figma.ui.postMessage({ type: "result", id: message.id, ok: true, result });
    } catch (error) {
      figma.ui.postMessage({ type: "result", id: message.id, ok: false,
        error: String(error.message || error), needsCanvasRead: true });
    } finally { busy = false; }
  };
}
