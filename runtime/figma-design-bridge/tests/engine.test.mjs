import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { packCommand } from '../bridge/cli.mjs';

const code = fs.readFileSync(new URL('../plugin/code.js', import.meta.url), 'utf8');
function fixture() {
  let next = 0;
  const registry = new Map(), fonts = [];
  class Node {
    constructor(type) {
      this.id = '1:' + ++next; this.type = type; this.name = type; this.parent = null; this.children = [];
      Object.assign(this, { x: 0, y: 0, width: 100, height: 100, opacity: 1, rotation: 0, visible: true, fills: [], effects: [], cornerRadius: 0,
        layoutMode: 'NONE', layoutPositioning: 'AUTO', primaryAxisSizingMode: 'FIXED', counterAxisSizingMode: 'FIXED',
        layoutSizingHorizontal: 'FIXED', layoutSizingVertical: 'FIXED', paddingTop: 0, paddingBottom: 0, paddingLeft: 0,
        paddingRight: 0, itemSpacing: 0, minHeight: null, clipsContent: false, strokes: [], strokeWeight: 1, dashPattern: [] });
      if (type === 'LINE') this.height = 0;
      if (type === 'TEXT') Object.assign(this, { fontName: { family: 'Inter', style: 'Regular' }, fontSize: 12,
        characters: '', lineHeight: { unit: 'AUTO' }, textAutoResize: 'NONE', textStyleId: '', hasMissingFont: false });
      this.data = {}; registry.set(this.id, this);
    }
    get absoluteBoundingBox() { return { x: this.x, y: this.y, width: this.width, height: this.height }; }
    getPluginData(k) { return this.data[k] || ''; }
    setPluginData(k,v) { this.data[k] = v; }
    appendChild(n) { if (n.parent) n.parent.children = n.parent.children.filter(c => c !== n); this.children.push(n); n.parent = this; }
    insertChild(index,n) {
      for(let p=this;p;p=p.parent) if(p===n)throw new Error('Mock API parent cycle');
      if(!Number.isInteger(index)||index<0||index>this.children.length)throw new Error('Mock API invalid index');
      if(n.parent)n.parent.children=n.parent.children.filter(c=>c!==n);
      this.children.splice(index,0,n);n.parent=this;
    }
    resize(w,h) { if(this.type==='LINE' && h!==0)throw new Error('LINE height must be zero');this.width=w;this.height=h;this.layoutSizingHorizontal='FIXED';this.layoutSizingVertical='FIXED'; }
    findAll() { return this.children.flatMap(n => [n, ...n.findAll()]); }
    remove() { for (const c of [...this.children]) c.remove(); if (this.parent) this.parent.children=this.parent.children.filter(n=>n!==this);this.removed=true;registry.delete(this.id); }
    getRangeAllFontNames() { return [this.fontName]; }
  }
  const page = new Node('PAGE'); page.selection=[];
  const root = new Node('DOCUMENT');root.appendChild(page);
  const figma = { root, currentPage: page, getNodeByIdAsync: async id=>registry.get(id)||null,
    setCurrentPageAsync:async p=>{figma.currentPage=p}, loadFontAsync:async f=>{if(f.family==='Missing')throw new Error('Missing font');fonts.push(f)},
    viewport:{scrollAndZoomIntoView(){}}, createImage:()=>({hash:'image-hash'}) };
  for (const type of ['Frame','Text','Rectangle','Ellipse','Line','Vector','Component']) figma['create'+type]=()=>new Node(type.toUpperCase());
  const sandbox = { module: { exports: {} }, figma, Uint8Array, console };
  vm.createContext(sandbox);vm.runInContext(code,sandbox);
  return { ...sandbox.module.exports, figma, page, Node, registry, fonts };
}
function build() { return { op:'buildSection',planDigest:'digest',plan:{projectId:'test',sectionId:'a',design:{paddingTop:24,paddingBottom:24},
  tree:{key:'root',type:'FRAME',props:{layoutMode:'VERTICAL',primaryAxisSizingMode:'AUTO',paddingTop:24,paddingBottom:24,width:1440},children:[
    {key:'heading',type:'TEXT',props:{fontName:{family:'Inter',style:'Regular'},fontSize:32,characters:'Title',textAutoResize:'HEIGHT',width:500}}
  ]},elementMap:[{id:'title',ref:'heading',renderMethod:'editable_text'}]}}; }

test('repeated build does not overwrite a manual text edit or create duplicates',async()=>{
  const f=fixture(),j=build(),a=await f.execute(j);
  const title=await f.figma.getNodeByIdAsync(a.refs.heading);title.characters='User edit';
  const b=await f.execute(j);assert.equal(b.reused,true);assert.equal(f.page.children.length,1);assert.equal(title.characters,'User edit');
  j.planDigest='new';await assert.rejects(f.execute(j),/already exists/);
});
test('font failure removes newly created section and preserves pre-existing nodes',async()=>{
  const f=fixture(),old=new f.Node('FRAME');f.page.appendChild(old);
  const j=build();j.plan.tree.children[0].props.fontName.family='Missing';
  await assert.rejects(f.execute(j),/Missing font/);assert.deepEqual(f.page.children,[old]);
});
test('repair restores only a missing section container and keeps existing child IDs and edits',async()=>{
  const f=fixture(),j=build(),a=await f.execute(j),old=f.registry.get(a.rootId),wrapper=old.parent,text=f.registry.get(a.refs.heading);
  text.characters='User edit'; wrapper.appendChild(text); old.remove();
  j.plan.parentId=wrapper.id;
  j.repair={wrapperId:wrapper.id,missingRootId:a.rootId,children:[{id:text.id,expected:{width:text.width,height:text.height,layoutMode:text.layoutMode,primaryAxisSizingMode:text.primaryAxisSizingMode,counterAxisSizingMode:text.counterAxisSizingMode}}]};
  const restored=await f.execute(j);
  assert.equal(restored.repaired,true);assert.notEqual(restored.rootId,a.rootId);
  assert.equal(restored.refs.heading,text.id);assert.equal(text.characters,'User edit');assert.equal(wrapper.children.length,1);
  assert.deepEqual(Array.from(restored.preservedNodeIds),[text.id]);
  await assert.rejects(f.execute(j),/absent/);
});
test('repair rejects changed children before creating or moving any node',async()=>{
  const f=fixture(),j=build(),a=await f.execute(j),old=f.registry.get(a.rootId),wrapper=old.parent,text=f.registry.get(a.refs.heading);
  wrapper.appendChild(text);old.remove();j.plan.parentId=wrapper.id;
  j.repair={wrapperId:wrapper.id,missingRootId:a.rootId,children:[{id:text.id,expected:{width:999,height:text.height,layoutMode:text.layoutMode,primaryAxisSizingMode:text.primaryAxisSizingMode,counterAxisSizingMode:text.counterAxisSizingMode}}]};
  const count=f.registry.size;
  await assert.rejects(f.execute(j),/child conflict/);assert.equal(f.registry.size,count);assert.equal(text.parent,wrapper);
});
test('patch preflight rejects stale expected values before any patch is written',async()=>{
  const f=fixture(),a=await f.execute(build());
  const root=f.registry.get(a.rootId),text=f.registry.get(a.refs.heading);
  await assert.rejects(f.execute({op:'patchNodes',patches:[{id:root.id,expected:{paddingTop:24},props:{paddingTop:40}},
    {id:text.id,expected:{characters:'stale'},props:{characters:'new'}}]}),/Conflict/);
  assert.equal(root.paddingTop,24);assert.equal(text.characters,'Title');
});
test('bounded inspection returns pagination without dumping the full canvas',async()=>{
  const f=fixture(),a=await f.execute(build());
  const result=await f.execute({op:'inspect',nodeId:a.rootId,depth:2,limit:1,fields:['width']});
  assert.equal(result.nodes.length,1);assert.equal(result.nextOffset,1);assert.equal(result.total,2);
});
test('DESIGN padding is enforced before creating nodes',async()=>{
  const f=fixture(),j=build();j.plan.tree.props.paddingTop=50;
  await assert.rejects(f.execute(j),/padding/);assert.equal(f.page.children.length,0);
});
test('native dotted lines retain zero height while invalid rectangle and line heights fail before build',async()=>{
  const f=fixture(),j=build();
  j.plan.tree.children.push({key:'underline',type:'LINE',props:{width:120,height:0,strokes:[{type:'SOLID',color:{r:0,g:0.5,b:0}}],dashPattern:[2,4]}});
  const a=await f.execute(j),line=f.registry.get(a.refs.underline);
  assert.equal(line.height,0);assert.equal(line.width,120);assert.equal(line.dashPattern.length,2);
  await f.execute({op:'patchNodes',patches:[{id:line.id,expected:{width:120},props:{width:140}}]});
  assert.equal(line.height,0);assert.equal(line.width,140);
  for(const [type,height] of [['RECTANGLE',0],['LINE',1]]) {
    const fresh=fixture(),bad=build();bad.plan.tree.children.push({key:'invalid',type,props:{width:100,height}});
    await assert.rejects(fresh.execute(bad),/height/);assert.equal(fresh.page.children.length,0);
  }
});
test('guarded patch changes only requested properties and loads current fonts',async()=>{
  const f=fixture(),a=await f.execute(build()),text=f.registry.get(a.refs.heading);
  const r=await f.execute({op:'patchNodes',patches:[{id:text.id,expected:{characters:'Title'},props:{characters:'Updated'}}]});
  assert.equal(r.changed.length,1);assert.equal(text.fontSize,32);assert.equal(text.characters,'Updated');assert.ok(f.fonts.length>0);
});
test('text styles handle frozen API arrays and reuse a style with Figma float rounding',async()=>{
  const f=fixture(),a=await f.execute(build()),text=f.registry.get(a.refs.heading),styles=[];
  Object.assign(text,{letterSpacing:{unit:'PERCENT',value:0},paragraphSpacing:0,paragraphIndent:0,textCase:'ORIGINAL',textDecoration:'NONE'});
  text.getRangeAllFontNames=()=>Object.freeze([text.fontName]);
  f.figma.getLocalTextStylesAsync=async()=>Object.freeze(styles.slice());
  f.figma.createTextStyle=()=>{
    const style={id:'style:'+styles.length};let lineHeight;
    Object.defineProperty(style,'lineHeight',{get:()=>lineHeight,set:v=>{lineHeight={unit:v.unit,value:new Float32Array([v.value])[0]}}});
    styles.push(style);return style;
  };
  text.setTextStyleIdAsync=async id=>{
    const style=styles.find(s=>s.id===id);text.textStyleId=id;
    for(const key of ['fontName','fontSize','lineHeight','letterSpacing','paragraphSpacing'])text[key]=style[key];
  };
  const expected=()=>Object.fromEntries(['fontName','fontSize','lineHeight','letterSpacing','paragraphSpacing','paragraphIndent','textCase','textDecoration','textStyleId'].map(k=>[k,text[k]]));
  const style={name:'Stage10/Test/Body',fontName:text.fontName,fontSize:32,lineHeight:{unit:'PERCENT',value:213.3333333333},letterSpacing:text.letterSpacing,paragraphSpacing:0};
  const command=()=>({op:'textStyles',styles:[{...style,targets:[{id:text.id,expected:expected()}]}]});
  await f.execute(command());assert.equal(styles.length,1);assert.equal(text.characters,'Title');assert.equal(text.textStyleId,'style:0');
  await f.execute(command());assert.equal(styles.length,1);
  style.fontSize=33;await assert.rejects(f.execute(command()),/Existing style differs/);assert.equal(text.fontSize,32);
});
test('structural validation never claims visual completion',async()=>{
  const f=fixture(),a=await f.execute(build());
  const r=await f.execute({op:'validateSection',nodeId:a.rootId});
  assert.equal(r.structuralPassed,true);assert.equal(r.needsVisualReview,true);
});
test('validation catches flow content overflowing an unclipped Auto Layout parent',async()=>{
  const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),text=f.registry.get(a.refs.heading);
  root.clipsContent=false;root.height=100;text.height=485;
  const r=await f.execute({op:'validateSection',nodeId:a.rootId});
  assert.equal(r.structuralPassed,false);
  assert.ok(r.issues.some(i=>i.nodeId===text.id&&i.rule==='auto-layout-overflow'));
  text.layoutPositioning='ABSOLUTE';
  const intentional=await f.execute({op:'validateSection',nodeId:a.rootId});
  assert.equal(intentional.structuralPassed,true);
});
test('commands cannot set unapproved properties',()=>{
  const f=fixture();assert.throws(()=>f.validateProps({__proto__:null,arbitraryProperty:1}),/Unsupported/);
});
test('base64 decoder preserves raster bytes',()=>{
  const f=fixture(),bytes=Buffer.from([0,1,25,127,128,200,255]);assert.deepEqual(Buffer.from(f.fromBase64(bytes.toString('base64'))),bytes);
});
const localPlanFixture = new URL('../examples/tokyo-spice/build-fv.packed.json',import.meta.url);
test('the prepared Tokyo Spice plan passes tree validation and maps every source element', { skip: !fs.existsSync(localPlanFixture) }, ()=>{
  const f=fixture(),job=JSON.parse(fs.readFileSync(localPlanFixture,'utf8'));
  const keys=new Set();f.validateTree(job.plan.tree,job.assets,keys,0);
  assert.equal(keys.size,37);assert.equal(new Set(job.plan.elementMap.map(e=>e.id)).size,13);
  assert.equal(Object.keys(job.assets).length,5);
  for(const e of job.plan.elementMap)assert.ok(keys.has(e.ref));
});

test('existing style application keeps a real Style ID and rejects stale references', async()=>{
  const f=fixture(), a=await f.execute(build()), t=f.registry.get(a.refs.heading);
  const style={id:'S:existing,',type:'TEXT',name:'Lower/Typography/Body',fontName:{family:'Inter',style:'Regular'},fontSize:15};
  f.figma.getStyleByIdAsync=async id=>id===style.id?style:null;
  t.setTextStyleIdAsync=async id=>{t.textStyleId=id;t.fontName=style.fontName;t.fontSize=style.fontSize};
  await f.execute({op:'applyTextStyles',targets:[{id:t.id,styleId:style.id,expected:{textStyleId:''}}]});
  assert.equal(t.textStyleId,style.id);assert.equal(t.characters,'Title');
  await assert.rejects(f.execute({op:'applyTextStyles',targets:[{id:t.id,styleId:style.id,expected:{textStyleId:''}}]}),/conflict/);
});
test('local raster replacement preserves frame identity and rejects stale geometry before writes',async()=>{
  const f=fixture(),a=await f.execute(build()), n=f.registry.get(a.rootId);n.cornerRadius=24;
  const expected=()=>Object.fromEntries(['fills','x','y','width','height','cornerRadius'].map(k=>[k,JSON.parse(JSON.stringify(n[k]))]));
  const before=expected();const payload={op:'replaceImages',assets:{photo:{base64:Buffer.from([1,2,3]).toString('base64')}},targets:[{id:n.id,assetId:'photo',scaleMode:'FIT',expected:before}]};
  await f.execute(payload);assert.equal(n.id,a.rootId);assert.equal(n.cornerRadius,24);assert.equal(n.width,before.width);assert.equal(n.fills[0].scaleMode,'FIT');
  await assert.rejects(f.execute(payload),/Image conflict/);assert.equal(n.fills[0].imageHash,'image-hash');
});
test('image replacement validates every target before creating any raster or modifying fills',async()=>{
  const f=fixture(),a=await f.execute(build()),n=f.registry.get(a.rootId);n.cornerRadius=0;
  let created=0;f.figma.createImage=()=>{created++;return {hash:'new'}};
  const expected=Object.fromEntries(['fills','x','y','width','height','cornerRadius'].map(k=>[k,JSON.parse(JSON.stringify(n[k]))]));
  await assert.rejects(f.execute({op:'replaceImages',assets:{photo:{base64:'AQID'}},targets:[{id:n.id,assetId:'photo',expected},{id:n.id,assetId:'missing',expected}]}),/raster asset/);
  assert.equal(created,0);assert.equal(n.fills.length,0);
});

function decorations(parent) { return { op:'appendDecorations',parentId:parent.id,decorationDigest:'decor-v1',
  expected:{childIds:parent.children.map(n=>n.id),width:parent.width,height:parent.height,layoutMode:parent.layoutMode},
  assets:{leaf:{base64:'AA=='}},decorations:[{type:'RECTANGLE',key:'VisualHarmonize/leaf',
    props:{name:'VisualHarmonize/leaf',layoutPositioning:'ABSOLUTE',x:24,y:50,width:180,height:90,opacity:1,effects:[]},image:{assetId:'leaf',scaleMode:'FIT'}}]}; }
test('decorations append without replacing existing IDs; repeated delivery reuses the same node',async()=>{
  const f=fixture(),a=await f.execute(build()),parent=f.registry.get(a.rootId),old=parent.children.slice(),j=decorations(parent);
  const before={width:parent.width,height:parent.height},r=await f.execute(j),again=await f.execute(j);
  assert.equal(r.reused,false);assert.equal(again.reused,true);assert.deepEqual(Array.from(r.nodeIds),Array.from(again.nodeIds));
  assert.equal(parent.children.length,old.length+1);assert.equal(parent.children[0],old[0]);
  assert.equal(parent.width,before.width);assert.equal(parent.height,before.height);
  const added=f.registry.get(r.nodeIds[0]);assert.equal(added.layoutPositioning,'ABSOLUTE');assert.equal(added.x,24);
  added.x=25;await assert.rejects(f.execute(j),/was edited/);assert.equal(added.x,25);
});
test('decorations reject changed parents and relative positioning before allocating nodes',async()=>{
  for(const kind of ['children','size','position','duplicate']){
    const f=fixture(),a=await f.execute(build()),parent=f.registry.get(a.rootId),j=decorations(parent);
    if(kind==='children')j.expected.childIds=[];
    if(kind==='size')j.expected.height=999;
    if(kind==='position')j.decorations[0].props.layoutPositioning='AUTO';
    if(kind==='duplicate')j.decorations.push(structuredClone(j.decorations[0]));
    const count=f.registry.size;
    await assert.rejects(f.execute(j),/conflict|ABSOLUTE|duplicate/);assert.equal(f.registry.size,count);
  }
});
test('failed decoration append removes only newly allocated nodes',async()=>{
  const f=fixture(),a=await f.execute(build()),parent=f.registry.get(a.rootId),j=decorations(parent),old=parent.children.slice();
  j.decorations.push({...structuredClone(j.decorations[0]),key:'VisualHarmonize/second'});
  let imageCount=0;f.figma.createImage=()=>{if(++imageCount===2)throw new Error('image failure');return{hash:'first'}};
  await assert.rejects(f.execute(j),/image failure/);assert.deepEqual(parent.children,old);assert.equal(old[0].characters,'Title');
});

function componentSetupFixture(){
  const f=fixture();f.figma.root.name='こもれびの森ようちえん｜1日の過ごし方｜プラグイン検証';
  const parent=new f.Node('FRAME');parent.setPluginData('stage10.bridge.v1',JSON.stringify({key:'kit'}));f.page.appendChild(parent);
  const components=['light','dark'].map((name,i)=>{
    const c=new f.Node('COMPONENT');c.name=name;c.componentPropertyDefinitions={};c.setPluginData('stage10.bridge.v1',JSON.stringify({key:name}));parent.appendChild(c);
    const t=new f.Node('TEXT');t.characters='Heading';t.componentPropertyReferences={};c.appendChild(t);
    c.addComponentProperty=(name,type,value)=>{const id=name+'#'+i;c.componentPropertyDefinitions[id]={type,defaultValue:value};return id;};
    c.deleteComponentProperty=id=>{delete c.componentPropertyDefinitions[id];};return {c,t};
  });
  f.figma.combineAsVariants=(nodes,p)=>{const set=new f.Node('COMPONENT_SET');p.appendChild(set);for(const n of nodes)set.appendChild(n);return set;};
  const textJob=({c,t})=>({op:'componentTextProperties',key:c.name+'-props',componentId:c.id,expected:{componentPropertyDefinitions:{}},bindings:[{nodeId:t.id,name:'Title',expected:{characters:'Heading',componentPropertyReferences:{}}}]});
  const variantJob=()=>({op:'componentVariants',key:'h2-a',name:'Lower/H2-A',parentId:parent.id,expected:{childIds:parent.children.map(n=>n.id)},variants:components.map(({c},i)=>({id:c.id,name:i?'Mode=Dark':'Mode=Light',expected:{name:c.name,width:c.width,height:c.height,componentPropertyDefinitions:structuredClone(c.componentPropertyDefinitions)}}))});
  return {...f,parent,components,textJob,variantJob};
}
test('component property setup preflights every target and rejects source-file writes',async()=>{
  const f=componentSetupFixture(),a=f.components[0],j=f.textJob(a);
  f.figma.root.name='Protected source TOP';await assert.rejects(f.execute(j),/restricted/);assert.equal(Object.keys(a.c.componentPropertyDefinitions).length,0);
  f.figma.root.name='こもれびの森ようちえん｜1日の過ごし方｜プラグイン検証';j.bindings.push({nodeId:f.components[1].t.id,name:'Other',expected:{characters:'Heading',componentPropertyReferences:{}}});
  await assert.rejects(f.execute(j),/inside this component/);assert.equal(Object.keys(a.c.componentPropertyDefinitions).length,0);assert.deepEqual(a.t.componentPropertyReferences,{});
});
test('text-property setup keeps text IDs/content and replay returns the existing binding',async()=>{
  const f=componentSetupFixture(),a=f.components[0],j=f.textJob(a),r=await f.execute(j),again=await f.execute(j);
  assert.equal(r.reused,false);assert.equal(again.reused,true);assert.equal(a.t.characters,'Heading');assert.equal(a.t.id,j.bindings[0].nodeId);assert.equal(Object.keys(a.c.componentPropertyDefinitions).length,1);
  assert.equal(a.t.componentPropertyReferences.characters,r.bindings[0].propertyId);
  a.t.componentPropertyReferences={};await assert.rejects(f.execute(j),/was edited/);
});
test('failed text property setup rolls back only its newly created property and references',async()=>{
  const f=componentSetupFixture(),a=f.components[0],j=f.textJob(a);let refs={};
  Object.defineProperty(a.t,'componentPropertyReferences',{get:()=>refs,set:v=>{if(v.characters)throw new Error('binding failure');refs=v;}});
  const r=await f.execute(j);assert.equal(r.partialFailure,true);assert.equal(r.rollbackComplete,true);assert.equal(Object.keys(a.c.componentPropertyDefinitions).length,0);assert.deepEqual(refs,{});
});
test('Light/Dark combination preserves component IDs and repeated delivery adds no set',async()=>{
  const f=componentSetupFixture();for(const a of f.components)await f.execute(f.textJob(a));const j=f.variantJob(),ids=f.components.map(a=>a.c.id),r=await f.execute(j),again=await f.execute(j);
  assert.equal(r.reused,false);assert.equal(again.reused,true);assert.deepEqual(Array.from(r.variantIds),ids);assert.equal(f.parent.children.length,1);assert.equal(f.parent.children[0].type,'COMPONENT_SET');
  f.components[1].c.name='Edited';await assert.rejects(f.execute(j),/was edited/);
});
test('Light/Dark combination rejects stale values before renaming or moving components',async()=>{
  const f=componentSetupFixture(),j=f.variantJob(),before=f.parent.children.slice();j.variants[1].expected.width=999;
  await assert.rejects(f.execute(j),/Variant conflict/);assert.deepEqual(f.parent.children,before);assert.equal(f.components[0].c.name,'light');
});
test('document key persists across plugin reload only for authorized test files',async()=>{
  for(const name of ['Sentinel Grid｜主な機能｜プラグイン検証','Protected source TOP']){
    const f=fixture();f.figma.root.name=name;const a=await f.execute({op:'context'});const sandbox={module:{exports:{}},figma:f.figma,console,Uint8Array};vm.createContext(sandbox);vm.runInContext(code,sandbox);const b=await sandbox.module.exports.execute({op:'context'});
    if(name.startsWith('Sentinel'))assert.equal(a.documentKey,b.documentKey);else{assert.notEqual(a.documentKey,b.documentKey);assert.equal(f.figma.root.getPluginData('stage10.bridge.v1'),'');}
  }
});

test('build receipts keep explicitly planned refs when instance descendants inherit the same metadata',async()=>{
  const f=fixture(),j=build(),a=await f.execute(j),root=f.registry.get(a.rootId),text=f.registry.get(a.refs.heading);
  const instance=new f.Node('INSTANCE');instance.setPluginData('stage10.bridge.v1',JSON.stringify({key:'planned-instance'}));root.appendChild(instance);
  const child=new f.Node('TEXT');child.setPluginData('stage10.bridge.v1',text.getPluginData('stage10.bridge.v1'));instance.appendChild(child);
  const r=await f.execute(j);assert.equal(r.reused,true);assert.equal(r.refs.heading,text.id);assert.equal(r.refs['planned-instance'],instance.id);assert.ok(r.nodeIds.includes(child.id));
});

const packed = j => packCommand(j, '.');
const parentState = p => ({childIds:p.children.map(n=>n.id),width:p.width,height:p.height,layoutMode:p.layoutMode});
function insertion(parent, nodes, key='insert-1',index=0) {
  return packed({op:'insertNodes',key,parentId:parent.id,index,expected:parentState(parent),nodes});
}
const freeFrame = key => ({key,type:'FRAME',props:{name:key,width:500,height:300,layoutMode:'NONE'}});
const addedText = key => ({key,type:'TEXT',props:{fontName:{family:'Inter',style:'Regular'},characters:'Added',fontSize:30,width:200,textAutoResize:'HEIGHT'}});
function arrangement(parents, orders, nodes=[],key='arrange-1') {
  return packed({op:'rearrangeNodes',key,parents:parents.map((p,i)=>({id:p.id,expected:parentState(p),childIds:orders[i]})),nodes});
}
const relocation = (n,x=20,y=30) => ({id:n.id,expected:{parentId:n.parent.id,x:n.x,y:n.y},props:{x,y}});

test('insert supports editable text, native line, nested frame, vector, SVG and cropped raster without replacing existing nodes',async()=>{
  const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),title=f.registry.get(a.refs.heading);
  title.characters='Manual text';
  f.figma.createNodeFromSvg=()=>{const n=new f.Node('FRAME');n.appendChild(new f.Node('VECTOR'));return n;};
  const specs=[addedText('new-copy'),{key:'slant',type:'LINE',props:{width:200,height:0,rotation:30}},
    {...freeFrame('group'),children:[{key:'vector',type:'VECTOR',props:{width:10,height:10},vectorPaths:[{windingRule:'NONZERO',data:'M 0 0 L 10 10'}]}]},
    {key:'photo',type:'RECTANGLE',props:{width:400,height:300},image:{assetId:'photo',scaleMode:'CROP',imageTransform:[[0.5,0,0.1],[0,0.5,0.2]]}},
    {key:'svg',type:'SVG',assetId:'icon',props:{width:32,height:32}}];
  const j=packed({...insertion(root,specs),assets:{photo:{base64:'AQID'},icon:{svg:'<svg/>'}}}),r=await f.execute(j),again=await f.execute(j);
  assert.equal(r.reused,false);assert.equal(again.reused,true);assert.deepEqual(Array.from(r.rootIds),root.children.slice(0,5).map(n=>n.id));
  assert.equal(root.children[5],title);assert.equal(title.characters,'Manual text');
  assert.equal(f.registry.get(r.refs['new-copy']).type,'TEXT');assert.equal(f.registry.get(r.refs.slant).height,0);
  assert.equal(f.registry.get(r.refs.photo).fills[0].scaleMode,'CROP');
  assert.equal(f.registry.get(r.refs.vector).parent.id,r.refs.group);assert.equal(f.registry.get(r.refs.svg).children.length,1);
  assert.equal(root.getPluginData('stage10.bridge.v1').includes('AQID'),false);
});
test('insert rejects stale parent state, duplicate existing keys and invalid index before allocation',async()=>{
  for(const kind of ['stale','key','index']) {
    const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),j=insertion(root,[addedText('new')]),count=f.registry.size;
    if(kind==='stale')j.expected.childIds=[];if(kind==='key')j.nodes[0].key='heading';if(kind==='index')j.index=99;
    await assert.rejects(f.execute(packed(j)),/conflict|already exists|out of range/);assert.equal(f.registry.size,count);
  }
});
test('insert rolls back all newly allocated nodes on font failure and permits a corrected fresh request',async()=>{
  const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),old=root.children.slice(),bad=addedText('bad');bad.props.fontName.family='Missing';
  await assert.rejects(f.execute(insertion(root,[freeFrame('first'),bad])),/Missing font/);
  assert.deepEqual(root.children,old);assert.equal(JSON.parse(root.getPluginData('stage10.bridge.v1')).editReceipts.length,0);
  const ok=await f.execute(insertion(root,[addedText('good')]));assert.ok(ok.refs.good);
});
test('insert retries preserve manual edits and reject a changed payload with the same edit key',async()=>{
  const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),j=insertion(root,[addedText('new')]),r=await f.execute(j),t=f.registry.get(r.refs.new);
  t.characters='Human correction';await assert.rejects(f.execute(j),/node changed/);assert.equal(t.characters,'Human correction');
  j.nodes[0].props.fontSize=99;await assert.rejects(f.execute(packed(j)),/different instructions/);
});
test('rearrange wraps an existing text into a new frame, preserves IDs, sets explicit coordinates and safely replays',async()=>{
  const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),text=f.registry.get(a.refs.heading);
  const added=await f.execute(insertion(root,[freeFrame('group')])),group=f.registry.get(added.refs.group),count=f.registry.size;
  const j=arrangement([root,group],[[group.id],[text.id]],[relocation(text,50,70)]),r=await f.execute(j),again=await f.execute(j);
  assert.equal(r.reused,false);assert.equal(again.reused,true);assert.equal(f.registry.size,count);assert.equal(text.parent,group);
  assert.equal(text.characters,'Title');assert.equal(text.x,50);assert.equal(text.y,70);assert.deepEqual(root.children,[group]);
});
test('rearrange changes layer order in place and can move an instance itself',async()=>{
  const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),text=root.children[0],instance=new f.Node('INSTANCE');root.appendChild(instance);
  const j=arrangement([root],[[instance.id,text.id]]);await f.execute(j);assert.deepEqual(root.children,[instance,text]);
  const count=f.registry.size;await f.execute(j);assert.equal(f.registry.size,count);
});
test('rearrange rejects stale later parents and node properties before any reorder',async()=>{
  for(const kind of ['parent','props']) {
    const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),text=root.children[0];
    const r=await f.execute(insertion(root,[freeFrame('group')])),group=f.registry.get(r.refs.group),before=root.children.slice();
    const j=arrangement([root,group],[[group.id],[text.id]],[relocation(text)]);
    if(kind==='parent')j.parents[1].expected.width=999;else j.nodes[0].expected.x=999;
    await assert.rejects(f.execute(packed(j)),/conflict/);assert.deepEqual(root.children,before);assert.equal(text.parent,root);
  }
});
test('rearrange rejects cycles, missing IDs and duplicate IDs without moving nodes',async()=>{
  for(const kind of ['cycle','missing','duplicate']) {
    const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),text=root.children[0];
    const r=await f.execute(insertion(root,[freeFrame('a'),freeFrame('b')])),one=f.registry.get(r.refs.a),two=f.registry.get(r.refs.b),before=root.children.slice();
    let orders=kind==='cycle'?[[text.id],[two.id],[one.id]]:kind==='missing'?[[one.id,two.id],[],[]]:[[...before.map(n=>n.id)],[text.id],[]];
    const j=arrangement([root,one,two],orders,[relocation(one),relocation(two)]);
    await assert.rejects(f.execute(j),/cycle|exactly|Duplicate/);assert.deepEqual(root.children,before);
  }
});
test('rearrange handles a valid parent-child inversion without a temporary API cycle',async()=>{
  const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),text=root.children[0];
  const r=await f.execute(insertion(root,[{...freeFrame('a'),children:[freeFrame('b')]}])),one=f.registry.get(r.refs.a),two=f.registry.get(r.refs.b);
  await f.execute(arrangement([one,two,root],[[/* a becomes empty */],[one.id],[text.id,two.id]],[relocation(one),relocation(two)]));
  assert.equal(two.parent,root);assert.equal(one.parent,two);assert.equal(one.children.length,0);
});
test('rearrange requires explicit coordinates when reparenting into free layout',async()=>{
  const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),text=root.children[0];
  const r=await f.execute(insertion(root,[freeFrame('group')])),group=f.registry.get(r.refs.group),before=root.children.slice();
  await assert.rejects(f.execute(arrangement([root,group],[[group.id],[text.id]])),/explicit x\/y/);assert.deepEqual(root.children,before);
});
test('structural edits reject source frames, cross-section moves and children inside instances',async()=>{
  const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),source=new f.Node('FRAME');f.page.appendChild(source);
  await assert.rejects(f.execute(insertion(source,[addedText('outside')])),/plugin-created section/);
  const j=build();j.plan.sectionId='second';j.planDigest='second';const b=await f.execute(j),other=f.registry.get(b.rootId);
  await assert.rejects(f.execute(arrangement([root,other],[[...other.children.map(n=>n.id)],[...root.children.map(n=>n.id)]])),/one section/);
  const instance=new f.Node('INSTANCE'),inside=new f.Node('FRAME');root.appendChild(instance);instance.appendChild(inside);
  await assert.rejects(f.execute(insertion(inside,[addedText('inside')])),/inside an instance/);
});
test('a concurrent edit during font loading aborts rearrangement before its writes',async()=>{
  const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),text=root.children[0];
  const r=await f.execute(insertion(root,[freeFrame('group')])),group=f.registry.get(r.refs.group);
  const j=arrangement([root,group],[[group.id],[text.id]],[{...relocation(text),expected:{...relocation(text).expected,characters:'Title'},props:{...relocation(text).props,characters:'New'}}]);
  let once=true;f.figma.loadFontAsync=async()=>{if(once){once=false;root.appendChild(new f.Node('RECTANGLE'));}};
  await assert.rejects(f.execute(j),/children conflict/);assert.equal(text.parent,root);assert.equal(text.characters,'Title');assert.equal(group.children.length,0);
});
test('partial rearrangement failures report affected IDs and cannot blindly replay',async()=>{
  const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),text=root.children[0];
  const r=await f.execute(insertion(root,[freeFrame('group')])),group=f.registry.get(r.refs.group),j=arrangement([root,group],[[group.id],[text.id]],[relocation(text)]);
  group.insertChild=()=>{throw new Error('API move failure');};
  const result=await f.execute(j);assert.equal(result.partialFailure,true);assert.equal(result.needsCanvasRead,true);assert.ok(result.inspectNodeIds.includes(text.id));
  await assert.rejects(f.execute(j),/incomplete/);assert.equal(text.characters,'Title');
});
test('pending structural edit receipt blocks duplicate allocation after interruption',async()=>{
  const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),j=insertion(root,[addedText('new')]);
  const m=JSON.parse(root.getPluginData('stage10.bridge.v1'));m.editReceipts=[{key:j.key,signature:j.op+':'+j.mutationDigest,status:'pending'}];root.setPluginData('stage10.bridge.v1',JSON.stringify(m));
  const count=f.registry.size;await assert.rejects(f.execute(j),/incomplete/);assert.equal(f.registry.size,count);
});
test('parent layout changes require coordinates and happen before child geometry; fonts load only at preflight',async()=>{
  const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),text=root.children[0];
  const r=await f.execute(insertion(root,[{...freeFrame('group'),props:{...freeFrame('group').props,layoutMode:'VERTICAL'}}])),group=f.registry.get(r.refs.group);
  const layout={id:group.id,expected:{parentId:root.id,layoutMode:'VERTICAL'},props:{layoutMode:'NONE'}};
  await assert.rejects(f.execute(arrangement([root,group],[[group.id],[text.id]],[layout], 'missing-coords')),/explicit x\/y/);
  let seen, fontLoads=0;f.figma.loadFontAsync=async()=>{fontLoads++;};let x=text.x;
  Object.defineProperty(text,'x',{get:()=>x,set:v=>{seen=group.layoutMode;x=v;}});
  const move={id:text.id,expected:{parentId:root.id,x:text.x,y:text.y,characters:'Title'},props:{x:30,y:40,characters:'Updated'}};
  await f.execute(arrangement([root,group],[[group.id],[text.id]],[move,layout],'parent-layout'));
  assert.equal(seen,'NONE');assert.equal(text.characters,'Updated');assert.equal(fontLoads,1);assert.equal(text.parent,group);
});
test('receipt storage errors during insertion remove the added nodes without discarding existing text',async()=>{
  const f=fixture(),a=await f.execute(build()),root=f.registry.get(a.rootId),old=root.children.slice(),large=addedText('large');
  large.props.characters='字'.repeat(40000);const count=f.registry.size;
  await assert.rejects(f.execute(insertion(root,[large])),/storage limit/);
  assert.equal(f.registry.size,count);assert.deepEqual(root.children,old);assert.equal(old[0].characters,'Title');
});
