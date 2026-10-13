import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { DEFAULT_STATE } from './server.mjs';
import { preflight } from './cli.mjs';

// A separate run ledger preserves the source project's historical stage10/stage11 records.
const read=file=>JSON.parse(fs.readFileSync(file,'utf8'));
const write=(file,data)=>{fs.writeFileSync(file+'.tmp',JSON.stringify(data,null,2));fs.renameSync(file+'.tmp',file)};
const fail=msg=>{throw new Error(msg)};
function jobById(state,id){
 if(!/^[a-f0-9-]{36}$/.test(id))fail('Invalid job ID');
 const j=read(path.join(state,'jobs',id+'.json'));if(j.status!=='succeeded')fail('Job must have succeeded');return j;
}
function newerWrites(state,documentKey,time){
 return fs.readdirSync(path.join(state,'jobs')).filter(n=>n.endsWith('.json')).map(n=>read(path.join(state,'jobs',n)))
 .some(j=>j.documentKey===documentKey && ['buildSection','patchNodes','textStyles','insertNodes','rearrangeNodes'].includes(j.command.op)
 && (j.startedAt||j.createdAt)>time && !['cancelled','queued'].includes(j.status));
}
const args=process.argv.slice(2),action=args.shift();
let state=DEFAULT_STATE;const stateAt=args.indexOf('--state');if(stateAt>=0){state=path.resolve(args[stateAt+1]);args.splice(stateAt,2)}
try{
 if(action==='init'){
  const [project,runDir,...ids]=args;if(!project||!runDir||!ids.length)fail('init PROJECT RUN_DIR SECTION...');
  const target=path.resolve(runDir);fs.mkdirSync(target,{recursive:true});const ledger=path.join(target,'analysis.json');
  if(fs.existsSync(ledger))fail('Run already exists; do not overwrite it');
  const reports=ids.map(id=>preflight(project,id));if(reports.some(r=>!r.readyForPlanning))fail('Preflight failed');
  const original=read(reports[0].analysisPath);write(path.join(target,'source-analysis.json'),original);
  const sections=ids.map(id=>{
   const s=structuredClone(original.sections.find(s=>s.id===id));s.frame_id=null;s.status='pending';s.comparison={};
   for(const e of s.elements||[])e.node_ids=[];return s;
  });
  write(ledger,{schema_version:11,run_id:'plugin-'+crypto.randomUUID(),status:original.status,
   inputs:{...original.inputs,source_project:path.resolve(project),source_analysis:reports[0].analysisPath},
   figma:{...original.figma,target_node_id:null,wrapper_node_id:null},sections,
   stage10:{status:'pending',current_step:'01',design_path:reports[0].designPath,changes:[],adopted_values:[],reference_values:[],
    transport:'local-plugin',jobs:[],stage10_handoff:null},stage11:{status:'pending'},unresolved_issues:[]});
  console.log(JSON.stringify({ledger,sections:ids}));
 }else if(action==='record'){
  const [runDir,id]=args,file=path.resolve(runDir,'analysis.json'),a=read(file),j=jobById(state,id);
  if(a.stage10.jobs.some(x=>x.id===id))fail('Job already recorded');
  if(a.stage10.documentKey && a.stage10.documentKey!==j.documentKey)fail('Run document mismatch');
  a.stage10.documentKey=j.documentKey;
  if(j.command.op==='buildSection'){
   const s=a.sections.find(s=>s.id===j.result.sectionId);if(!s)fail('Section not in this run');
   const idx=a.sections.indexOf(s);if(a.sections.slice(0,idx).some(s=>s.status!=='passed'))fail('Earlier section still needs review');
   s.frame_id=j.result.rootId;s.status='in_progress';s.plan_digest=j.result.planDigest;
   for(const e of s.elements||[]) e.node_ids=(j.result.elementMap||[]).filter(m=>m.id===e.id).map(m=>j.result.refs[m.ref]).filter(Boolean);
   a.figma.wrapper_node_id=j.result.wrapperId;a.figma.target_node_id=j.result.rootId;
  }
  if(['patchNodes','textStyles','insertNodes','rearrangeNodes'].includes(j.command.op)) for(const s of a.sections.filter(s=>s.frame_id)) {
    s.status='in_progress';s.common_values_verified=false;
  }
  a.stage10.status='in_progress';a.stage10.jobs.push({id,op:j.command.op,finishedAt:j.finishedAt});
  a.stage10.changes.push({job_id:id,operation:j.command.op,result:j.result});write(file,a);console.log(JSON.stringify({recorded:id,ledger:file}));
 }else if(action==='review'){
  const [runDir,reviewFile]=args,file=path.resolve(runDir,'analysis.json'),a=read(file),r=read(reviewFile);
  const s=a.sections.find(s=>s.id===r.sectionId);if(!s?.frame_id)fail('Section has not been built and recorded');
  const v=jobById(state,r.validationJobId),e=jobById(state,r.exportJobId);
  if(v.command.op!=='validateSection'||!v.result.structuralPassed||v.result.nodeId!==s.frame_id)fail('Passing structural validation required');
  if(e.command.op!=='exportNode'||e.result.nodeId!==s.frame_id||!e.result.exportPath)fail('Export for this section required');
  if(v.documentKey!==a.stage10.documentKey||e.documentKey!==a.stage10.documentKey)fail('Review document mismatch');
  if(newerWrites(state,e.documentKey,v.finishedAt)||newerWrites(state,e.documentKey,e.finishedAt))fail('Canvas writes happened after checks; validate/export again');
  if(r.visualPassed!==true||!Array.isArray(r.checks)||r.checks.length===0||!r.reviewer)fail('Explicit visual review with evidence is required');
  if(!fs.existsSync(e.result.exportPath))fail('Export file missing');
  if(crypto.createHash('sha256').update(fs.readFileSync(e.result.exportPath)).digest('hex')!==e.result.exportSha256)fail('Export changed');
  s.status='passed';s.comparison={checked:r.checks,reviewer:r.reviewer,latest_export_path:e.result.exportPath,
   validation_job_id:v.id,export_job_id:e.id,reviewed_at:new Date().toISOString(),unresolved_issue_ids:[]};
  if(r.commonValuesVerified===true)s.common_values_verified=true;
  write(file,a);console.log(JSON.stringify({section:s.id,status:s.status,ledger:file}));
 }else if(action==='finish'){
  const [runDir]=args,file=path.resolve(runDir,'analysis.json'),a=read(file);
  if(a.sections.some(s=>s.status!=='passed'||!s.common_values_verified))fail('Every section needs visual review and commonValuesVerified');
  for(const s of a.sections){const e=jobById(state,s.comparison.export_job_id);if(newerWrites(state,e.documentKey,e.finishedAt))fail('A later write invalidates final review; re-export affected sections');}
  a.stage10.status='passed';a.stage10.current_step='completed';
  a.stage10.stage10_handoff={status:'passed',completed_at:new Date().toISOString(),wrapper_node_id:a.figma.wrapper_node_id,
    sections:a.sections.map(s=>({id:s.id,frame_id:s.frame_id,latest_export_path:s.comparison.latest_export_path})),unresolved_issues:[]};
  write(file,a);console.log(JSON.stringify({status:'passed',ledger:file,stage11:'pending'}));
 }else console.log('init PROJECT RUN_DIR SECTION...\nrecord RUN_DIR JOB_ID\nreview RUN_DIR REVIEW.json\nfinish RUN_DIR\nOptional: --state DIR');
}catch(e){console.error(e.message);process.exitCode=1}
