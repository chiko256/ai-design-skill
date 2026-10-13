import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { DEFAULT_STATE, VERSION } from './server.mjs';

const serverFile = fileURLToPath(new URL('./server.mjs', import.meta.url));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
export function readConfig(stateDir, port) {
  const file = path.join(stateDir, 'connection.json');
  if (!fs.existsSync(file)) return null;
  const c = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!new RegExp('^http://(127\\.0\\.0\\.1|localhost):' + port + '$').test(c.url) || typeof c.token !== 'string' || !c.token)
    throw new Error('接続設定が一致しません。同じBridgeのフォルダーを使ってください。');
  return c;
}
async function request(c, route, method = 'GET') {
  const r = await fetch(c.url + route, { method, headers: { Authorization: 'Bearer ' + c.token }, signal: AbortSignal.timeout(2500) });
  if (!r.ok) throw new Error('Bridgeへの接続を確認できませんでした (HTTP ' + r.status + ')。別のBridgeを停止せず、Codexに確認を依頼してください。');
  return r.json();
}
async function probe(stateDir, port) {
  const c = readConfig(stateDir, port);
  if (!c) return null;
  try { return { config: c, status: await request(c, '/status') }; }
  catch (e) { if (e.cause?.code === 'ECONNREFUSED') return null; throw e; }
}
async function portBusy(port) {
  return new Promise(resolve => {
    const socket = net.createConnection({ host: '127.0.0.1', port });
    const finish = busy => { socket.destroy(); resolve(busy); };
    socket.once('connect', () => finish(true));
    socket.once('error', () => finish(false));
    socket.setTimeout(1500, () => finish(true));
  });
}

// One launcher for both OSes; no shell interpolation, npm install, or GUI automation.
export async function startBridge({ stateDir = DEFAULT_STATE, port = 3849, timeoutMs = 12000 } = {}) {
  if (Number(process.versions.node.split('.')[0]) < 18) throw new Error('Node.js 18以上が必要です。');
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid port');
  stateDir = path.resolve(stateDir);
  fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
  const lock = path.join(stateDir, 'startup.lock'), deadline = Date.now() + timeoutMs;
  let ready = await probe(stateDir, port), childPid;
  while (!ready && Date.now() < deadline) {
    let lockFd;
    try { lockFd = fs.openSync(lock, 'wx', 0o600); }
    catch (e) {
      if (e.code !== 'EEXIST') throw e;
      // A crashed launcher leaves no live owner; reclaim only after a full startup timeout.
      try { if (Date.now() - fs.statSync(lock).mtimeMs > 30000) { fs.rmSync(lock, { force: true }); continue; } }
      catch (statError) { if (statError.code === 'ENOENT') continue; throw statError; }
      await sleep(150); ready = await probe(stateDir, port); continue;
    }
    try {
      ready = await probe(stateDir, port);
      if (ready) break;
      if (await portBusy(port)) throw new Error('ポート' + port + 'は別のBridgeまたはアプリが使用中です。同じBridgeのフォルダーを使うようCodexに依頼してください。');
      const log = fs.openSync(path.join(stateDir, 'server.log'), 'a', 0o600);
      let child;
      try { child = spawn(process.execPath, [serverFile, '--state', stateDir, '--port', String(port)], {
        detached: true, windowsHide: true, stdio: ['ignore', log, log]
      }); } finally { fs.closeSync(log); }
      let startupError;
      child.on('error', e => { startupError = e; });
      childPid = child.pid;
      child.unref();
      while (!ready && Date.now() < deadline) {
        if (startupError) throw startupError;
        await sleep(150); ready = await probe(stateDir, port);
      }
    } finally { fs.closeSync(lockFd); fs.rmSync(lock, { force: true }); }
  }
  if (!ready) throw new Error('Bridgeの起動を確認できませんでした。Codexに.runtime/server.logの確認を依頼してください。');
  if (ready.status.version !== VERSION)
    throw new Error('起動中のBridgeは旧版です。実行中の作業を確認してから更新してください。自動では停止しません。');
  const pairing = await request(ready.config, '/pair', 'POST');
  return { version: VERSION, ...pairing, ...(childPid ? { childPid } : {}) };
}

async function main() {
  const args = process.argv.slice(2);
  const option = flag => { const at = args.indexOf(flag); if (at < 0) return undefined; if (!args[at + 1]) throw new Error(flag + ' requires a value'); return args[at + 1]; };
  const result = await startBridge({ stateDir: option('--state'), port: option('--port') === undefined ? 3849 : Number(option('--port')) });
  if (args.includes('--json')) { console.log(JSON.stringify(result)); return; }
  console.log('\nFigma Design Bridgeが起動しています。\n\n  接続コード: ' + result.code + '\n\nFigmaの「Figma Design Bridge」にコードを入力し、「接続する」を押してください。\nコードは5分間有効・1回限りです。Codexが接続する場合は操作を任せてください。\nこのウィンドウを閉じてもBridgeは動き続けます。\n');
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main().catch(e => { console.error(e.message); process.exitCode = 1; });
