import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import http from 'node:http';
import { startBridge } from '../bridge/start.mjs';
import { createBridge } from '../bridge/server.mjs';

async function freePort(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p;}
function state(t){const d=fs.mkdtempSync(path.join(os.tmpdir(),'bridge path with spaces '));t.after(()=>fs.rmSync(d,{recursive:true,force:true}));return d;}

test('launcher starts a detached local server in a spaced path, reuses it, and issues distinct codes',async t=>{
  const stateDir=state(t),port=await freePort();
  const first=await startBridge({stateDir,port});
  t.after(()=>{try{process.kill(first.childPid)}catch{}});
  assert.ok(first.childPid);assert.match(first.code,/^[A-Z2-9]{4}-[A-Z2-9]{4}$/);
  const config=JSON.parse(fs.readFileSync(path.join(stateDir,'connection.json'),'utf8'));
  const second=await startBridge({stateDir,port});
  assert.equal(second.childPid,undefined);assert.notEqual(first.code,second.code);
  assert.ok(!JSON.stringify(first).includes(config.token));
  const redeem=await fetch(config.url+'/pair/redeem',{method:'POST',body:JSON.stringify({code:first.code})});
  assert.equal(redeem.status,200);assert.equal((await redeem.json()).token,config.token);
});

test('launcher refuses a foreign listener before creating or overwriting connection credentials',async t=>{
  const stateDir=state(t),s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));
  t.after(()=>new Promise(r=>s.close(r)));
  await assert.rejects(startBridge({stateDir,port:s.address().port}),/使用中/);
  assert.equal(fs.existsSync(path.join(stateDir,'connection.json')),false);
});

test('launcher leaves an old running bridge untouched and reports the update requirement',async t=>{
  const stateDir=state(t),token='test-secret',s=http.createServer((req,res)=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({version:'old-version'}));});
  await new Promise(r=>s.listen(0,'127.0.0.1',r));const port=s.address().port;
  t.after(()=>{s.closeAllConnections();return new Promise(r=>s.close(r));});
  fs.writeFileSync(path.join(stateDir,'connection.json'),JSON.stringify({url:'http://127.0.0.1:'+port,token}));
  await assert.rejects(startBridge({stateDir,port}),/旧版/);
  assert.equal(JSON.parse(fs.readFileSync(path.join(stateDir,'connection.json'))).token,token);
  assert.equal(s.listening,true);
});

test('two launchers share an existing server and do not replace its connections',async t=>{
  const stateDir=state(t),bridge=createBridge({stateDir,port:0});await new Promise(r=>bridge.server.listen(0,'127.0.0.1',r));
  t.after(()=>{bridge.server.closeAllConnections();return new Promise(r=>bridge.server.close(r));});
  const port=bridge.server.address().port;
  await fetch(bridge.config.url+'/connect',{method:'POST',headers:{Authorization:'Bearer '+bridge.config.token},body:JSON.stringify({sessionId:'s',context:{documentKey:'keep-file'}})});
  const results=await Promise.all([startBridge({stateDir,port}),startBridge({stateDir,port})]);
  assert.ok(results.every(r=>!r.childPid));assert.notEqual(results[0].code,results[1].code);
  const response=await fetch(bridge.config.url+'/status',{headers:{Authorization:'Bearer '+bridge.config.token}});
  assert.equal((await response.json()).clients[0].documentKey,'keep-file');
});

test('two simultaneous cold starts launch exactly one process and preserve the shared token',async t=>{
  const stateDir=state(t),port=await freePort();
  const results=await Promise.all([startBridge({stateDir,port}),startBridge({stateDir,port})]);
  const children=results.filter(r=>r.childPid);
  t.after(()=>{for(const r of children)try{process.kill(r.childPid)}catch{}});
  assert.equal(children.length,1);assert.notEqual(results[0].code,results[1].code);
  const c=JSON.parse(fs.readFileSync(path.join(stateDir,'connection.json'),'utf8'));
  for(const r of results){const response=await fetch(c.url+'/pair/redeem',{method:'POST',body:JSON.stringify({code:r.code})});assert.equal(response.status,200);assert.equal((await response.json()).token,c.token);}
});
