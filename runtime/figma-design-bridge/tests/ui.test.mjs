import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

test('UI initializes in the Figma sandbox without crypto.randomUUID', async () => {
  const html = fs.readFileSync(new URL('../plugin/ui.html', import.meta.url), 'utf8');
  const code = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const elements = Object.fromEntries(['url','token','connect','disconnect','status','context','import','file','connection','log','code','pair'].map(id=>[id,{value:'',textContent:'',click(){this.clicked=true}}]));
  const messages = [];
  const context = {AbortController,crypto:{},document:{getElementById:id=>elements[id]},window:{addEventListener(){}},parent:{postMessage:m=>messages.push(m)},onmessage:null,console,setTimeout,clearTimeout};
  vm.runInNewContext(code, context);
  assert.equal(typeof elements.connect.onclick, 'function');
  elements.import.onclick();
  assert.equal(elements.file.clicked, true);
  elements.url.value = 'http://localhost:3849'; elements.token.value='test-token';
  elements.connect.onclick();
  assert.equal(messages[0].pluginMessage.type, 'hello');
  elements.token.value='';
  await elements.connection.onchange({target:{files:[{size:100,text:async()=>JSON.stringify({url:'http://127.0.0.1:3849',token:'local-test-token'})}],value:'test'}});
  assert.equal(elements.token.value,'local-test-token');
  assert.equal(elements.url.value,'http://localhost:3849');
  assert.equal(elements.status.textContent,'接続設定を読み込みました');
});

test('an idle plugin reconnects after a bridge restart without executing a job',async()=>{
  const html=fs.readFileSync(new URL('../plugin/ui.html',import.meta.url),'utf8');
  const elements=Object.fromEntries(['url','token','connect','disconnect','status','context','import','file','connection','log','code','pair'].map(id=>[id,{value:'',textContent:''}]));
  const requests=[],messages=[];
  const sandbox={AbortController,crypto:{},document:{getElementById:id=>elements[id]},window:{addEventListener(){}},
    parent:{postMessage:m=>messages.push(m)},onmessage:null,console,setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options)=>{requests.push({url,options});return requests.length===1
      ?{ok:false,status:409,json:async()=>({error:'Reconnect the plugin'})}
      :{ok:true,status:200,json:async()=>({connected:true})};}};
  vm.createContext(sandbox);vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],sandbox);
  vm.runInContext("connected=true;base='http://localhost:3849';token='test-token';context={documentKey:'expected-file'}",sandbox);
  await vm.runInContext('poll()',sandbox);
  assert.equal(requests.length,2);assert.ok(requests[0].url.includes('/next?'));
  assert.ok(requests[1].url.endsWith('/connect'));
  assert.equal(JSON.parse(requests[1].options.body).context.documentKey,'expected-file');
  assert.equal(messages.length,0);
});

test('an executing plugin does not reconnect or silently retry a write',async()=>{
  const html=fs.readFileSync(new URL('../plugin/ui.html',import.meta.url),'utf8');
  const elements=Object.fromEntries(['url','token','connect','disconnect','status','context','import','file','connection','log','code','pair'].map(id=>[id,{value:'',textContent:''}]));
  const requests=[],messages=[];
  const sandbox={AbortController,crypto:{},document:{getElementById:id=>elements[id]},window:{addEventListener(){}},
    parent:{postMessage:m=>messages.push(m)},onmessage:null,console,setTimeout:()=>1,clearTimeout(){},
    fetch:async(url)=>{requests.push(url);return{ok:false,status:409,json:async()=>({error:'Reconnect the plugin'})};}};
  vm.createContext(sandbox);vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],sandbox);
  vm.runInContext("connected=true;working=true;base='http://localhost:3849';token='test-token';context={documentKey:'expected-file'}",sandbox);
  await vm.runInContext('poll()',sandbox);
  assert.equal(requests.length,1);assert.equal(messages.length,0);
  assert.match(elements.status.textContent,/接続確認中/);
});

test('short code exchanges once, connects to the current Figma file, and keeps the token out of visible text',async()=>{
  const html=fs.readFileSync(new URL('../plugin/ui.html',import.meta.url),'utf8');
  const elements=Object.fromEntries(['url','token','connect','disconnect','status','context','import','file','connection','log','code','pair'].map(id=>[id,{value:'',textContent:''}]));
  const requests=[],messages=[];
  const sandbox={AbortController,crypto:{},document:{getElementById:id=>elements[id]},window:{addEventListener(){}},
    parent:{postMessage:m=>messages.push(m)},onmessage:null,console,setTimeout:()=>1,clearTimeout(){},
    fetch:async(url,options)=>{requests.push({url,options});return {ok:true,status:200,json:async()=>url.endsWith('/pair/redeem')
      ?{url:'http://localhost:3849',token:'private-token'}:url.endsWith('/connect')?{connected:true}:{job:null}};}};
  vm.createContext(sandbox);vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],sandbox);
  elements.code.value='abcd-2345';
  await elements.pair.onclick();
  assert.equal(requests.length,1);assert.equal(requests[0].options.headers.Authorization,undefined);
  assert.equal(JSON.parse(requests[0].options.body).code,'ABCD2345');
  assert.equal(messages[0].pluginMessage.type,'hello');assert.equal(elements.code.value,'');
  await sandbox.onmessage({data:{pluginMessage:{type:'hello',context:{documentKey:'chosen-file',pageId:'1:2',fileName:'Target'}}}});
  const connect=requests.find(r=>r.url.endsWith('/connect'));
  assert.equal(JSON.parse(connect.options.body).context.documentKey,'chosen-file');
  assert.equal(connect.options.headers.Authorization,'Bearer private-token');
  assert.equal(elements.pair.disabled,true);assert.match(elements.status.textContent,/接続中/);
  assert.ok(!Object.values(elements).some(e=>e.textContent.includes('private-token')));
});

test('an invalid pairing code unlocks the UI for a retry without asking Figma to execute anything',async()=>{
  const html=fs.readFileSync(new URL('../plugin/ui.html',import.meta.url),'utf8');
  const elements=Object.fromEntries(['url','token','connect','disconnect','status','context','import','file','connection','log','code','pair'].map(id=>[id,{value:'',textContent:''}]));
  const messages=[];
  const sandbox={AbortController,crypto:{},document:{getElementById:id=>elements[id]},window:{addEventListener(){}},
    parent:{postMessage:m=>messages.push(m)},onmessage:null,console,setTimeout:()=>1,clearTimeout(){},
    fetch:async()=>({ok:false,status:400,json:async()=>({error:'Expired code'})})};
  vm.createContext(sandbox);vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],sandbox);
  elements.code.value='ABCD-2345';await elements.pair.onclick();
  assert.equal(elements.pair.disabled,false);assert.equal(elements.code.disabled,false);
  assert.equal(elements.status.textContent,'Expired code');assert.equal(messages.length,0);
});
