import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createBridge } from '../bridge/server.mjs';
import { packCommand } from '../bridge/cli.mjs';

async function fixture(t, options = {}) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'stage10-test-'));
  const b=createBridge({stateDir:dir,port:0,...options});
  await new Promise(r=>b.server.listen(0,'127.0.0.1',r));
  t.after(async()=>{b.server.closeAllConnections();await new Promise(r=>b.server.close(r));fs.rmSync(dir,{recursive:true,force:true})});
  const request=async(route,method='GET',body,headers={})=>{
    const r=await fetch(b.config.url+route,{method,headers:{Authorization:'Bearer '+b.config.token,'Content-Type':'application/json',...headers},body:body?JSON.stringify(body):undefined});
    return {status:r.status,body:await r.json()};
  };
  const connect=()=>request('/connect','POST',{sessionId:'session',context:{documentKey:'doc',pageId:'0:1',fileName:'Test'}});
  return {...b,request,connect};
}
test('bridge requires token and rejects unrelated browser origins',async t=>{
  const f=await fixture(t);
  assert.equal((await f.request('/status','GET',null,{Authorization:'Bearer wrong'})).status,401);
  assert.equal((await f.request('/status','GET',null,{Origin:'https://example.com'})).status,403);
});
test('document mismatch is rejected before queueing any command',async t=>{
  const f=await fixture(t);await f.connect();
  const r=await f.request('/jobs','POST',{documentKey:'other',command:{op:'buildSection'}});assert.equal(r.status,409);
});
test('job is leased once and result ACK can be retried without re-executing',async t=>{
  const f=await fixture(t);await f.connect();
  const {body:j}=await f.request('/jobs','POST',{documentKey:'doc',command:{op:'context'}});
  assert.equal((await f.request('/next?sessionId=session')).body.job.id,j.id);
  assert.equal((await f.request('/next?sessionId=session')).body.job,null);
  const result={sessionId:'session',ok:true,result:{fileName:'Test'}};
  assert.equal((await f.request('/jobs/'+j.id+'/result','POST',result)).status,200);
  assert.equal((await f.request('/jobs/'+j.id+'/result','POST',result)).status,200);
  assert.equal((await f.request('/jobs/'+j.id)).body.status,'succeeded');
});
test('image payload is stored as a file and removed from context-facing result',async t=>{
  const f=await fixture(t);await f.connect();
  const {body:j}=await f.request('/jobs','POST',{documentKey:'doc',command:{op:'exportNode',nodeId:'1:1'}});
  await f.request('/next?sessionId=session');
  await f.request('/jobs/'+j.id+'/result','POST',{sessionId:'session',ok:true,result:{format:'PNG',binaryBase64:Buffer.from('example').toString('base64')}});
  const r=(await f.request('/jobs/'+j.id)).body;
  assert.equal(r.result.binaryBase64,undefined);assert.equal(fs.readFileSync(r.result.exportPath,'utf8'),'example');
});
test('a different plugin session cannot silently replay an interrupted write',async t=>{
  const f=await fixture(t);await f.connect();
  const {body:j}=await f.request('/jobs','POST',{documentKey:'doc',command:{op:'patchNodes',patches:[]}});
  await f.request('/next?sessionId=session');
  await f.request('/disconnect','POST',{sessionId:'session'});
  await f.request('/connect','POST',{sessionId:'new-session',context:{documentKey:'doc'}});
  const r=(await f.request('/jobs/'+j.id)).body;
  assert.equal(r.status,'interrupted');assert.equal(r.needsCanvasRead,true);
  assert.equal((await f.request('/next?sessionId=new-session')).body.job,null);
});
test('bundle includes SVG source and hash changes when an image changes',t=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'stage10-pack-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  fs.writeFileSync(path.join(dir,'a.svg'),'<svg/>');fs.writeFileSync(path.join(dir,'a.png'),'image1');
  const source={op:'buildSection',plan:{sectionId:'a'},assets:{icon:{path:'a.svg'},photo:{path:'a.png'}}};
  const a=packCommand(source,dir);fs.writeFileSync(path.join(dir,'a.png'),'image2');const b=packCommand(source,dir);
  assert.equal(a.assets.icon.svg,'<svg/>');assert.notEqual(a.planDigest,b.planDigest);assert.equal(source.assets.photo.path,'a.png');
});
test('structural edits are accepted by bridge and leased with their payload intact',async t=>{
  const f=await fixture(t);await f.connect();
  for(const op of ['insertNodes','rearrangeNodes']) {
    const command=packCommand({op,key:'edit-'+op,parents:[],nodes:[]},'.');
    const {status,body:j}=await f.request('/jobs','POST',{documentKey:'doc',command});assert.equal(status,202);
    const next=(await f.request('/next?sessionId=session')).body.job;assert.equal(next.command.mutationDigest,command.mutationDigest);assert.equal(next.command.op,op);
    await f.request('/jobs/'+j.id+'/result','POST',{sessionId:'session',ok:true,result:{reused:false}});
  }
});
test('structural mutation digest covers order, geometry and actual asset bytes, including repacking',t=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'stage10-edit-pack-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  fs.writeFileSync(path.join(dir,'photo.png'),'first');
  const source={op:'insertNodes',key:'add-photo',index:0,nodes:[{key:'photo',props:{x:20}}],assets:{photo:{path:'photo.png'}}};
  const a=packCommand(source,dir);assert.match(a.mutationDigest,/^[a-f0-9]{64}$/);assert.equal(packCommand(a,dir).mutationDigest,a.mutationDigest);
  const shifted=structuredClone(source);shifted.nodes[0].props.x=21;assert.notEqual(packCommand(shifted,dir).mutationDigest,a.mutationDigest);
  const reordered={op:'rearrangeNodes',key:'order',parents:[{id:'p',childIds:['a','b']}]},r=packCommand(reordered,dir);reordered.parents[0].childIds.reverse();assert.notEqual(packCommand(reordered,dir).mutationDigest,r.mutationDigest);
  fs.writeFileSync(path.join(dir,'photo.png'),'second');assert.notEqual(packCommand(source,dir).mutationDigest,a.mutationDigest);
  assert.equal(source.mutationDigest,undefined);
});

const connectFile = (f, documentKey, sessionId) => f.request('/connect','POST',{
  sessionId, context:{documentKey,fileName:documentKey,pageId:'0:1'}
});
const writeFile = (f, documentKey) => f.request('/jobs','POST',{
  documentKey,command:{op:'patchNodes',patches:[]}
});

test('two files stay connected and status never chooses an ambiguous target',async t=>{
  const f=await fixture(t);
  assert.equal((await connectFile(f,'a','session-a')).status,200);
  assert.equal((await connectFile(f,'b','session-b')).status,200);
  const status=(await f.request('/status')).body;
  assert.equal(status.clients.length,2);assert.ok(status.clients.every(c=>c.connected));
  assert.equal(status.client,null);
  const selected=(await f.request('/status?documentKey=b')).body;
  assert.equal(selected.clients.length,1);assert.equal(selected.client.documentKey,'b');
});

test('different files execute independently and each session receives only its own job',async t=>{
  const f=await fixture(t);await connectFile(f,'a','sa');await connectFile(f,'b','sb');
  const a=await writeFile(f,'a'),b=await writeFile(f,'b');
  assert.equal(a.status,202);assert.equal(b.status,202);
  assert.equal((await writeFile(f,'a')).status,409);
  assert.equal((await f.request('/next?sessionId=sb')).body.job.id,b.body.id);
  assert.equal((await f.request('/next?sessionId=sa')).body.job.id,a.body.id);
  assert.equal((await f.request('/next?sessionId=sa')).body.job,null);
  assert.equal((await f.request('/jobs/'+a.body.id+'/result','POST',{sessionId:'sb',ok:true,result:{}})).status,409);
  assert.equal((await f.request('/jobs/'+a.body.id+'/result','POST',{sessionId:'sa',ok:true,result:{}})).status,200);
  assert.equal((await f.request('/jobs/'+b.body.id)).body.status,'running');
});

test('disconnecting one file preserves another file and its running write',async t=>{
  const f=await fixture(t);await connectFile(f,'a','sa');await connectFile(f,'b','sb');
  const b=(await writeFile(f,'b')).body;await f.request('/next?sessionId=sb');
  await f.request('/disconnect','POST',{sessionId:'sa'});
  assert.equal((await f.request('/next?sessionId=sa')).status,409);
  assert.equal((await f.request('/status')).body.client.documentKey,'b');
  assert.equal((await f.request('/jobs/'+b.id)).body.status,'running');
  assert.equal((await f.request('/jobs/'+b.id+'/result','POST',{sessionId:'sb',ok:true,result:{}})).status,200);
});

test('reconnecting an expired session interrupts only that file',async t=>{
  let clock=1000;const f=await fixture(t,{now:()=>clock});
  await connectFile(f,'a','sa');await connectFile(f,'b','sb');
  const a=(await writeFile(f,'a')).body,b=(await writeFile(f,'b')).body;
  await f.request('/next?sessionId=sa');await f.request('/next?sessionId=sb');
  clock+=16000;await f.request('/next?sessionId=sb');
  assert.equal((await connectFile(f,'a','sa-new')).status,200);
  assert.equal((await f.request('/jobs/'+a.id)).body.status,'interrupted');
  assert.equal((await f.request('/jobs/'+b.id)).body.status,'running');
  assert.equal((await f.request('/next?sessionId=sa')).status,409);
});

test('a session cannot claim another file or replace a live owner',async t=>{
  const f=await fixture(t);await connectFile(f,'a','sa');
  assert.equal((await connectFile(f,'b','sa')).status,409);
  assert.equal((await connectFile(f,'a','different')).status,409);
  assert.equal((await f.request('/status')).body.client.sessionId,'sa');
});

test('legacy status selects the only live file and ignores stale metadata',async t=>{
  let clock=1000;const f=await fixture(t,{now:()=>clock});
  await connectFile(f,'a','sa');clock+=16000;await connectFile(f,'b','sb');
  const status=(await f.request('/status')).body;
  assert.equal(status.clients.length,2);assert.equal(status.client.documentKey,'b');
  assert.equal((await f.request('/status?documentKey=a')).body.client,null);
});

test('concurrent UI lock acquisition admits exactly one owner',async t=>{
  const f=await fixture(t);
  const acquire=owner=>f.request('/ui-lock','POST',{action:'acquire',owner,target:'figma-file'});
  const results=await Promise.all([acquire('chat-a'),acquire('chat-b')]);
  assert.deepEqual(results.map(r=>r.status).sort(),[200,409]);
  const winner=results.find(r=>r.status===200).body.lease;
  assert.equal((await f.request('/ui-lock')).body.lease.id,winner.id);
});

test('UI lock renewal and release require the current owner and exact lease',async t=>{
  const f=await fixture(t);
  const lease=(await f.request('/ui-lock','POST',{action:'acquire',owner:'chat-a',target:'file-a'})).body.lease;
  for (const action of ['renew','release']) {
    assert.equal((await f.request('/ui-lock','POST',{action,owner:'chat-b',leaseId:lease.id})).status,409);
    assert.equal((await f.request('/ui-lock','POST',{action,owner:'chat-a',leaseId:'stale'})).status,409);
  }
  assert.equal((await f.request('/ui-lock','POST',{action:'renew',owner:'chat-a',leaseId:lease.id})).status,200);
  assert.equal((await f.request('/ui-lock','POST',{action:'release',owner:'chat-a',leaseId:lease.id})).status,200);
  assert.equal((await f.request('/ui-lock')).body.lease,null);
});

test('expired UI locks can be recovered and an old lease cannot release the new owner',async t=>{
  let clock=1000;const f=await fixture(t,{now:()=>clock});
  const old=(await f.request('/ui-lock','POST',{action:'acquire',owner:'chat-a',target:'a',ttlSeconds:30})).body.lease;
  clock+=31000;
  const next=await f.request('/ui-lock','POST',{action:'acquire',owner:'chat-b',target:'b'});
  assert.equal(next.status,200);assert.notEqual(next.body.lease.id,old.id);
  assert.equal((await f.request('/ui-lock','POST',{action:'release',owner:'chat-a',leaseId:old.id})).status,409);
  assert.equal((await f.request('/ui-lock')).body.lease.owner,'chat-b');
});

test('UI lock ownership does not interrupt connected files or their jobs',async t=>{
  const f=await fixture(t);await connectFile(f,'a','sa');await connectFile(f,'b','sb');
  await f.request('/ui-lock','POST',{action:'acquire',owner:'chat-a',target:'file-a'});
  assert.equal((await writeFile(f,'b')).status,202);
  assert.equal((await f.request('/status')).body.clients.filter(c=>c.connected).length,2);
  assert.equal((await f.request('/ui-lock','POST',{action:'acquire',owner:'chat-a',target:'different-file'})).status,409);
});

test('four connected files can lease and complete independent writes without cross-delivery',async t=>{
  const f=await fixture(t),documents=['a','b','c','d'];
  for(const doc of documents)assert.equal((await connectFile(f,doc,'session-'+doc)).status,200);
  const jobs=await Promise.all(documents.map(doc=>writeFile(f,doc)));
  assert.ok(jobs.every(j=>j.status===202));
  const deliveries=await Promise.all(documents.map(doc=>f.request('/next?sessionId=session-'+doc)));
  for(let i=0;i<documents.length;i++) {
    assert.equal(deliveries[i].body.job.id,jobs[i].body.id);
    assert.equal(deliveries[i].body.job.command.documentKey,documents[i]);
    assert.equal((await f.request('/jobs/'+jobs[i].body.id+'/result','POST',{
      sessionId:'session-'+documents[i],ok:true,result:{fileName:documents[i]}
    })).status,200);
    assert.equal((await f.request('/jobs/'+jobs[i].body.id)).body.result.fileName,documents[i]);
  }
  const status=(await f.request('/status')).body;
  assert.equal(status.clients.filter(c=>c.connected).length,4);assert.equal(status.client,null);
});

test('pairing needs authenticated issuance, and public health never exposes credentials', async t => {
  const f = await fixture(t);
  assert.equal((await f.request('/pair', 'POST', {}, {Authorization:''})).status, 401);
  assert.equal((await f.request('/pair/redeem','POST',{code:'ABCD2345'},{Authorization:'',Origin:'https://example.com'})).status,403);
  const health = await fetch(f.config.url + '/');
  assert.ok(!(await health.text()).includes(f.config.token));
  assert.equal((await f.request('/pair/redeem','POST',{code:'ABCD2345'},{Authorization:''})).status,400);
});

test('pairing codes are one-use, expire, normalize pasted hyphens, and do not disconnect other files', async t => {
  let clock=100000;
  const f=await fixture(t,{now:()=>clock});
  await connectFile(f,'a','session-a'); await connectFile(f,'b','session-b');
  const a=await f.request('/pair','POST',{}), b=await f.request('/pair','POST',{});
  assert.equal(a.status,201);assert.match(a.body.code,/^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}-[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{4}$/);
  assert.equal(a.body.expiresAt,clock+300000); assert.notEqual(a.body.code,b.body.code);
  const first=await f.request('/pair/redeem','POST',{code:' '+a.body.code.toLowerCase()+' '},{Authorization:'',Origin:'null'});
  assert.equal(first.status,200);assert.equal(first.body.token,f.config.token);
  assert.equal((await f.request('/pair/redeem','POST',{code:a.body.code},{Authorization:''})).status,400);
  assert.equal((await f.request('/status')).body.clients.filter(c=>c.connected).length,2);
  clock+=300000;
  assert.equal((await f.request('/pair/redeem','POST',{code:b.body.code},{Authorization:''})).status,400);
  const fresh=await f.request('/pair','POST',{});
  assert.equal((await f.request('/pair/redeem','POST',{code:fresh.body.code},{Authorization:''})).status,200);
  assert.ok(!fs.readFileSync(f.stateDir+'/connection.json','utf8').includes(a.body.code));
});

test('pairing redemption is rate limited and request size bounded',async t=>{
  let clock=100000;
  const f=await fixture(t,{now:()=>clock});
  const pair=(await f.request('/pair','POST',{})).body;
  assert.equal((await f.request('/pair/redeem','POST',{code:'A'.repeat(1100)},{Authorization:''})).status,400);
  for(let i=1;i<20;i++)assert.equal((await f.request('/pair/redeem','POST',{code:'wrong'},{Authorization:''})).status,400);
  assert.equal((await f.request('/pair/redeem','POST',{code:pair.code},{Authorization:''})).status,429);
  clock+=60000;
  assert.equal((await f.request('/pair/redeem','POST',{code:pair.code},{Authorization:''})).status,200);
});
