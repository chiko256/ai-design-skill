import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const DEFAULT_STATE = path.join(ROOT, '.runtime');
export const VERSION = '0.1.5-connect.1';
const LIMIT = 48 * 1024 * 1024;
const OPERATIONS = new Set(['context', 'fonts', 'inspect', 'buildSection', 'patchNodes', 'validateSection', 'textStyles', 'exportNode', 'inspectResources', 'applyTextStyles', 'replaceImages', 'appendDecorations', 'componentTextProperties', 'componentVariants', 'insertNodes', 'rearrangeNodes']);
const READ_ONLY = new Set(['context', 'fonts', 'inspect', 'validateSection', 'exportNode', 'inspectResources']);
const atomic = (file, data) => { fs.writeFileSync(file + '.tmp', JSON.stringify(data, null, 2), { mode: 0o600 }); fs.renameSync(file + '.tmp', file); };

export function createBridge({ stateDir = DEFAULT_STATE, port = 3849, now = Date.now } = {}) {
  fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
  fs.mkdirSync(path.join(stateDir, 'jobs'), { recursive: true });
  fs.mkdirSync(path.join(stateDir, 'exports'), { recursive: true });
  const configFile = path.join(stateDir, 'connection.json');
  const old = fs.existsSync(configFile) ? JSON.parse(fs.readFileSync(configFile, 'utf8')) : {};
  const config = { url: `http://127.0.0.1:${port}`, token: old.token || crypto.randomBytes(24).toString('hex') };
  atomic(configFile, config);
  const clients = new Map();
  // Codes stay in memory, expire after five minutes, and can be used only once.
  // Only an authenticated local caller may issue them. Never log the bearer token.
  const pairings = new Map();
  const codeHash = code => crypto.createHash('sha256').update(code).digest('hex');
  const prunePairings = () => { for (const [key, value] of pairings) if (value <= now()) pairings.delete(key); };
  let attempts = 0, attemptWindow = now();
  const live = client => !!client && now() - client.lastSeen < 15000;
  let uiLease = null;
  const currentLease = () => {
    if (uiLease && uiLease.expiresAt <= now()) uiLease = null;
    return uiLease;
  };
  const jobFile = id => path.join(stateDir, 'jobs', id + '.json');
  const jobs = new Map();
  for (const name of fs.readdirSync(path.join(stateDir, 'jobs')).filter(n => n.endsWith('.json'))) {
    const j = JSON.parse(fs.readFileSync(path.join(stateDir, 'jobs', name), 'utf8'));
    if (j.status === 'running' || j.status === 'queued') {
      j.status = 'interrupted'; j.error = 'Bridge restarted; inspect canvas before submitting a new job.'; atomic(jobFile(j.id), j);
    }
    jobs.set(j.id, j);
  }
  const save = j => { jobs.set(j.id, j); atomic(jobFile(j.id), j); };
  const summary = j => ({ id: j.id, op: j.command.op, status: j.status, createdAt: j.createdAt,
    finishedAt: j.finishedAt, result: j.result, error: j.error, documentKey: j.documentKey, needsCanvasRead: j.needsCanvasRead });
  function authenticate(req) {
    const provided = Buffer.from((req.headers.authorization || '').replace(/^Bearer /, ''));
    const wanted = Buffer.from(config.token);
    return provided.length === wanted.length && crypto.timingSafeEqual(provided, wanted);
  }
  function reply(res, status, value) {
    res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(value));
  }
  async function body(req, limit = LIMIT) {
    const parts = []; let size = 0;
    for await (const part of req) { size += part.length; if (size > limit) throw new Error('Request body too large'); parts.push(part); }
    return JSON.parse(Buffer.concat(parts).toString('utf8') || '{}');
  }
  const server = http.createServer(async (req, res) => {
    const host = req.headers.host || '';
    if (!/^(127\.0\.0\.1|localhost):\d+$/.test(host)) return reply(res, 403, { error: 'Loopback Host required' });
    const origin = req.headers.origin;
    const originAllowed = !origin || origin === 'null' || /^https:\/\/([a-z0-9-]+\.)?figma\.com$/.test(origin) || /^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(origin);
    if (!originAllowed) return reply(res, 403, { error: 'Origin is not allowed' });
    if (origin) res.setHeader('Access-Control-Allow-Origin', origin);
    res.setHeader('Vary', 'Origin');
    res.setHeader('Access-Control-Allow-Headers', 'Authorization, Content-Type');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Private-Network', 'true');
    if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }
    const url = new URL(req.url, config.url);
    if (url.pathname === '/' && req.method === 'GET') {
      // This public health page never contains a token or pairing code.
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'" });
      res.end('<meta charset="utf-8"><title>Figma Design Bridge</title><body style="font:16px system-ui;padding:48px"><h1>Figma Design Bridge</h1><p>ローカル接続は起動しています。Figmaのプラグインで接続してください。</p></body>'); return;
    }
    try {
      if (url.pathname === '/pair/redeem' && req.method === 'POST') {
        if (now() - attemptWindow >= 60000) { attempts = 0; attemptWindow = now(); }
        if (++attempts > 20) return reply(res, 429, { error: '試行が多すぎます。1分後にもう一度お試しください。' });
        const b = await body(req, 1024);
        const code = typeof b.code === 'string' ? b.code.replace(/[\s-]/g, '').toUpperCase() : '';
        prunePairings();
        const hash = codeHash(code);
        if (!/^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}$/.test(code) || !pairings.has(hash))
          return reply(res, 400, { error: 'コードが違うか、有効期限が切れています。Codexで新しいコードを発行してください。' });
        pairings.delete(hash);
        return reply(res, 200, { url: `http://localhost:${server.address().port}`, token: config.token });
      }
      if (!authenticate(req)) return reply(res, 401, { error: 'Pairing token required' });
      if (url.pathname === '/pair' && req.method === 'POST') {
        prunePairings();
        if (pairings.size >= 32) return reply(res, 429, { error: 'Too many pending pairing codes; wait five minutes' });
        const alphabet = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';
        let code;
        do { code = Array.from({ length: 8 }, () => alphabet[crypto.randomInt(alphabet.length)]).join(''); } while (pairings.has(codeHash(code)));
        const expiresAt = now() + 5 * 60000;
        pairings.set(codeHash(code), expiresAt);
        return reply(res, 201, { code: code.slice(0, 4) + '-' + code.slice(4), expiresAt });
      }
      if (url.pathname === '/ui-lock' && req.method === 'GET')
        return reply(res, 200, { lease: currentLease() });
      if (url.pathname === '/ui-lock' && req.method === 'POST') {
        const b = await body(req), existing = currentLease();
        if (typeof b.owner !== 'string' || !b.owner || b.owner.length > 256)
          return reply(res, 400, { error: 'UI lock owner is required' });
        if (b.action === 'release') {
          if (!existing || existing.owner !== b.owner || existing.id !== b.leaseId)
            return reply(res, 409, { error: 'UI lock lease does not match' });
          uiLease = null; return reply(res, 200, { released: true });
        }
        if (!['acquire','renew'].includes(b.action)) return reply(res, 400, { error: 'Unknown UI lock action' });
        const ttl = b.ttlSeconds ?? 120;
        if (!Number.isInteger(ttl) || ttl < 30 || ttl > 300)
          return reply(res, 400, { error: 'UI lock TTL must be 30 to 300 seconds' });
        if (b.action === 'renew') {
          if (!existing || existing.owner !== b.owner || existing.id !== b.leaseId)
            return reply(res, 409, { error: 'UI lock lease does not match' });
          uiLease.expiresAt = now() + ttl * 1000;
          return reply(res, 200, { lease: uiLease });
        }
        if (typeof b.target !== 'string' || !b.target || b.target.length > 2048)
          return reply(res, 400, { error: 'UI lock target is required' });
        if (existing) {
          if (existing.owner !== b.owner || existing.target !== b.target)
            return reply(res, 409, { error: 'Figma UI is in use', lease: existing });
          return reply(res, 200, { lease: existing });
        }
        uiLease = { id: crypto.randomUUID(), owner: b.owner, target: b.target, expiresAt: now() + ttl * 1000 };
        return reply(res, 200, { lease: uiLease });
      }
      if (req.method === 'GET' && url.pathname === '/status') {
        const documentKey = url.searchParams.get('documentKey');
        const selected = [...clients.values()].filter(c => documentKey === null || c.documentKey === documentKey)
          .map(c => ({ ...c, connected: live(c) }));
        const liveClients = selected.filter(c => c.connected);
        return reply(res, 200, {
          version: VERSION, clients: selected,
          // Legacy callers get a target only when one live connection is unambiguous.
          client: liveClients.length === 1 ? liveClients[0] : null,
          jobs: [...jobs.values()].filter(j => documentKey === null || j.documentKey === documentKey)
            .slice(-20).map(j => ({ id: j.id, op: j.command.op, status: j.status, documentKey: j.documentKey }))
        });
      }
      if (req.method === 'POST' && url.pathname === '/connect') {
        const b = await body(req);
        if (typeof b.sessionId !== 'string' || !b.sessionId || typeof b.context?.documentKey !== 'string' || !b.context.documentKey)
          return reply(res, 400, { error: 'Missing session or document' });
        const documentKey = b.context.documentKey;
        if ([...clients.values()].some(c => c.sessionId === b.sessionId && c.documentKey !== documentKey) ||
            [...jobs.values()].some(j => j.status === 'running' && j.sessionId === b.sessionId && j.documentKey !== documentKey))
          return reply(res, 409, { error: 'Session belongs to another document; use a new plugin session' });
        const client = clients.get(documentKey);
        if (live(client) && client.sessionId !== b.sessionId)
          return reply(res, 409, { error: 'Another plugin is connected to this file' });
        for (const j of jobs.values()) {
          if (j.documentKey === documentKey && j.status === 'running' && j.sessionId !== b.sessionId) {
            j.status = 'interrupted'; j.needsCanvasRead = true; j.error = 'Plugin session changed during execution'; save(j);
          }
        }
        clients.set(documentKey, { ...b.context, sessionId: b.sessionId, lastSeen: now() });
        return reply(res, 200, { connected: true });
      }
      if (req.method === 'POST' && url.pathname === '/disconnect') {
        const b = await body(req);
        for (const [key, client] of clients) if (client.sessionId === b.sessionId) clients.delete(key);
        return reply(res, 200, { disconnected: true });
      }
      if (req.method === 'POST' && url.pathname === '/jobs') {
        const b = await body(req), command = b.command;
        if (!command || !OPERATIONS.has(command.op)) return reply(res, 400, { error: 'Unknown operation' });
        const client = clients.get(b.documentKey);
        if (!live(client)) return reply(res, 409, { error: 'Connect the Figma plugin for the explicit documentKey first' });
        if (!READ_ONLY.has(command.op) && [...jobs.values()].some(j => j.documentKey === b.documentKey && ['queued', 'running'].includes(j.status)))
          return reply(res, 409, { error: 'Finish the active job before submitting another write' });
        const id = crypto.randomUUID();
        const j = { id, documentKey: b.documentKey, command: { ...command, documentKey: b.documentKey }, status: 'queued', createdAt: new Date().toISOString() };
        save(j); return reply(res, 202, summary(j));
      }
      if (req.method === 'GET' && url.pathname === '/next') {
        const client = [...clients.values()].find(c => c.sessionId === url.searchParams.get('sessionId'));
        if (!client) return reply(res, 409, { error: 'Reconnect the plugin' });
        client.lastSeen = now();
        if ([...jobs.values()].some(j => j.status === 'running' && j.documentKey === client.documentKey)) return reply(res, 200, { job: null });
        const j = [...jobs.values()].find(j => j.status === 'queued' && j.documentKey === client.documentKey);
        if (!j) return reply(res, 200, { job: null });
        j.status = 'running'; j.sessionId = client.sessionId; j.startedAt = new Date().toISOString(); save(j);
        return reply(res, 200, { job: { id: j.id, command: j.command } });
      }
      const match = url.pathname.match(/^\/jobs\/([a-f0-9-]{36})(\/result|\/cancel)?$/);
      if (match) {
        const j = jobs.get(match[1]); if (!j) return reply(res, 404, { error: 'Unknown job' });
        if (req.method === 'GET' && !match[2]) return reply(res, 200, summary(j));
        if (req.method === 'POST' && match[2] === '/cancel') {
          if (j.status !== 'queued') return reply(res, 409, { error: 'Only queued jobs can be cancelled; running writes must be inspected' });
          j.status = 'cancelled'; save(j); return reply(res, 200, summary(j));
        }
        if (req.method === 'POST' && match[2] === '/result') {
          const b = await body(req);
          if (b.sessionId !== j.sessionId) return reply(res, 409, { error: 'Result session does not match' });
          if (['succeeded', 'failed'].includes(j.status)) return reply(res, 200, { saved: true }); // Lost ACK: do not re-execute.
          if (j.status !== 'running') return reply(res, 409, { error: 'Job is not running; inspect interrupted work' });
          if (b.result?.binaryBase64) {
            const ext = b.result.format === 'SVG' ? 'svg' : 'png';
            const output = path.join(stateDir, 'exports', j.id + '.' + ext);
            const bytes = Buffer.from(b.result.binaryBase64, 'base64');
            if (bytes.length > 24 * 1024 * 1024) throw new Error('Export too large');
            fs.writeFileSync(output, bytes); delete b.result.binaryBase64;
            b.result.exportPath = output; b.result.exportSha256 = crypto.createHash('sha256').update(bytes).digest('hex');
          }
          j.status = b.ok && !b.result?.partialFailure ? 'succeeded' : 'failed';
          j.result = b.result; j.error = b.error || b.result?.message;
          j.needsCanvasRead = !!b.needsCanvasRead || !!b.result?.partialFailure;
          j.finishedAt = new Date().toISOString(); save(j);
          return reply(res, 200, { saved: true });
        }
      }
      return reply(res, 404, { error: 'Unknown endpoint' });
    } catch (error) { return reply(res, 400, { error: error.message }); }
  });
  server.on('listening', () => {
    config.url = `http://127.0.0.1:${server.address().port}`; atomic(configFile, config);
  });
  return { server, config, stateDir };
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const i = process.argv.indexOf('--state');
  const stateDir = i >= 0 ? path.resolve(process.argv[i + 1]) : DEFAULT_STATE;
  const portAt = process.argv.indexOf('--port');
  const port = portAt >= 0 ? Number(process.argv[portAt + 1]) : 3849;
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid port');
  const { server } = createBridge({ stateDir, port });
  server.listen(port, '127.0.0.1', () => console.log('Figma Design Bridge: http://127.0.0.1:' + port + '\n接続設定: ' + path.join(stateDir, 'connection.json')));
  server.on('error', e => { console.error(e.message); process.exitCode = 1; });
}
