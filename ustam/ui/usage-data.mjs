/* Accounting is normalized before calendar filtering; this module never collects data. */
const counters=['total_tokens','input_tokens','output_tokens','cached_input_tokens'];
const helperRoles=new Set(['worker','helper','explorer','implementer','verifier','reviewer','researcher','advisor','failure_analyst','fast_lookup','qa_operator']);
export function actorCategory(actor){
 if(actor?.ambiguous)return 'unknown';
 if(actor?.kind==='conductor'||actor?.kind==='chief')return 'chiefs';
 if(actor?.kind==='helper')return 'helpers';
 if(actor?.kind)return 'unknown';
 const role=String(actor?.role||actor?.duty||'').replaceAll('-','_');
 if(['chief','owner','conductor'].includes(role))return 'chiefs';
 return helperRoles.has(role)?'helpers':'unknown';
}
function day(value){
 if(!/^\d{4}-\d{2}-\d{2}$/.test(value||''))throw Error('invalid_date_range');
 const [year,month,date]=value.split('-').map(Number);
 const result=new Date(0);result.setFullYear(year,month-1,date);result.setHours(0,0,0,0);
 if(result.getFullYear()!==year||result.getMonth()!==month-1||result.getDate()!==date)throw Error('invalid_date_range');
 return result;
}
function dayText(value){return [value.getFullYear(),String(value.getMonth()+1).padStart(2,'0'),String(value.getDate()).padStart(2,'0')].join('-');}
export function calendarRange(period='all',from='',to='',now=new Date()){
 if(period==='all')return {period,from:'',to:'',start:null,end:null};
 if(period==='7'||period==='30'){
  const first=new Date(now);first.setHours(0,0,0,0);first.setDate(first.getDate()-(Number(period)-1));
  from=dayText(first);to=dayText(now);
 }else if(period!=='custom')throw Error('invalid_date_range');
 const start=day(from),last=day(to);if(start>last)throw Error('invalid_date_range');
 const end=new Date(last);end.setDate(end.getDate()+1);
 return {period,from,to,start:start.getTime(),end:end.getTime()};
}
export function accountingInstant(record){
 if(typeof record.created==='number'&&Number.isFinite(record.created)&&record.created>=946684800000&&record.created<4102444800000)return record.created;
 const value=record.timestamp??record.date;
 if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value))return null;
 // Reject normalized invalid calendar dates without using the browser's local zone.
 const [year,month,date]=value.slice(0,10).split('-').map(Number);const valid=new Date(Date.UTC(year,month-1,date));
 if(valid.getUTCFullYear()!==year||valid.getUTCMonth()!==month-1||valid.getUTCDate()!==date)return null;
 const time=Date.parse(value);return Number.isFinite(time)?time:null;
}
function usage(source){
 const raw=source?.usage||source?.totals||source?.observed||source||{};const result={};
 const aliases={total_tokens:['total_tokens','total','primary'],input_tokens:['input_tokens','input'],output_tokens:['output_tokens','output'],cached_input_tokens:['cached_input_tokens','cached','cache_read','cacheRead']};
 for(const [key,names] of Object.entries(aliases))for(const name of names)if(typeof raw[name]==='number'&&Number.isFinite(raw[name])&&raw[name]>=0){result[key]=raw[name];break;}
 return result;
}
function aggregate(rows){const result={};for(const row of rows)for(const [key,value] of Object.entries(row.usage||{}))if(counters.includes(key)&&typeof value==='number'&&Number.isFinite(value)&&value>=0)result[key]=(result[key]??0)+value;return result;}
function identity(value){return typeof value==='string'&&value&&value!=='unknown'?value:null;}
function rootOf(id,agents){
 const visited=new Set();while(agents.has(id)&&!visited.has(id)){visited.add(id);const a=agents.get(id);if(a.ambiguous)return null;if(!a.parent)return a.source==='root'?id:null;if(a.source!=='subagent')return null;id=a.parent;}return null;
}
export function normalizeUsageReport(data){
 const report=structuredClone(data);report.actors=report.actors||[];report.runs=report.runs||[];
 if(report.status==='unavailable'||report.source==='none')return {...report,totals:{},actors:[],runs:[],accounting:[],hasAccounting:false};
 let rows=[];
 if(report.orchestra?.scopes){
  report.actors=(report.orchestra.actors||[]).map(a=>({...a,role:String(a.role||'').replaceAll('-','_')}));
  const agents=new Map((report.agents||[]).map(a=>[a.id,a]));const scopes=report.orchestra.scopes;
  rows=(report.records||[]).map(r=>{
   let root=rootOf(r.thread,agents);const linked=identity(r.session_id);
   const attributionConflict=Boolean(root&&linked&&linked!==root);
   if(attributionConflict)root=null;
   if(!attributionConflict&&!root&&linked&&rootOf(linked,agents)===linked)root=linked;
   // Legacy reports may expose a verified scope without the agent graph.
   if(!agents.size&&scopes.some(s=>s.id===r.thread))root=r.thread;
   return {...r,identity:identity(r.thread),run_id:root,attributionConflict,usage:usage(r)};
  });
  report.runs=scopes.map(scope=>({...scope,name:scope.name||scope.id,actors:report.actors.filter(a=>a.id===scope.id||rootOf(a.id,agents)===scope.id)}));
 }else if(report.platform==='opencode'&&Array.isArray(report.records)){
  const roots=report.orchestra?.roots||[];const nodes=roots.flatMap(root=>(root.nodes||[]).map(n=>({...n,kind:n.id===root.id?'conductor':'helper',run_id:root.id,models:Object.keys(n.models||{}),efforts:[...new Set(Object.values(n.variants||{}).flat())],role:''})));
  const byId=new Map(nodes.map(n=>[n.id,n]));report.actors=nodes;report.runs=roots.map(root=>({id:root.id,name:root.id,actors:nodes.filter(n=>n.run_id===root.id)}));
  rows=report.records.map(r=>{const values=usage(r);if(r.observed&&Object.values(r.observed).every(v=>typeof v==='number'&&Number.isFinite(v)&&v>=0))values.total_tokens=Object.values(r.observed).reduce((a,b)=>a+b,0);return {...r,identity:identity(r.thread),run_id:byId.get(r.thread)?.run_id||null,usage:values};});
 }else if(report.platform==='claude'&&Array.isArray(report.events)){
  const types={input:'input_tokens',output:'output_tokens',cacheRead:'cached_input_tokens'};
  rows=report.events.filter(r=>r.metric==='claude_code.token.usage'&&r.unit==='tokens'&&types[r.type]&&typeof r.value==='number'&&Number.isFinite(r.value)&&r.value>=0).map(r=>({...r,identity:identity(r.thread),run_id:identity(r.thread),usage:{[types[r.type]]:r.value,...(['input','output'].includes(r.type)?{total_tokens:r.value}:{})}}));
  report.actors=[];report.runs=[...new Set(rows.map(r=>r.run_id).filter(Boolean))].map(id=>({id,name:id,actors:[]}));
 }else if(Array.isArray(report.records)&&report.records.every(r=>r.usage||r.observed)){
  rows=report.records.map(r=>({...r,identity:identity(r.actor_id||r.thread),run_id:identity(r.run_id||r.session_id),usage:usage(r)}));
 }
 const byId=new Map(report.actors.map(a=>[a.id,a]));
 report.accounting=rows.map(r=>({...r,category:r.attributionConflict?'unknown':actorCategory(byId.get(r.identity))}));report.hasAccounting=rows.length>0;
 return report;
}
function projected(report,rows,bounded){
 const actors=(report.actors||[]).map(a=>{const own=rows.filter(r=>!r.attributionConflict&&r.identity===a.id);return {...a,usage:aggregate(own),usage_observed:own.length>0,model:undefined,effort:undefined,models:[...new Set(own.map(r=>r.model).filter(v=>v&&v!=='unknown'))],efforts:[...new Set(own.map(r=>r.effort).filter(v=>v&&v!=='unknown'))],model_efforts:[]};});
 const visible=bounded?actors.filter(a=>a.usage_observed):actors;
 return {...report,totals:aggregate(rows),actors:visible,accounting:rows};
}
export function filterUsageReport(report,range){
 const installation=report.installation_boundary;const start=accountingInstant({timestamp:installation?.started_at});const observed=accountingInstant({timestamp:installation?.observed_at});
 if(installation?.status!=='verified'||start===null){return {...projected(report,[],true),runs:[],hasAccounting:false,dateCoverage:{installationUnknown:true,unknownExcluded:0,detailUnavailable:false,from:range.from,to:range.to}};}
 const bounded=true;const calendarBounded=range.start!==null;let unknown=0;
 const rows=(report.accounting||[]).filter(r=>{const time=accountingInstant(r);if(time===null){unknown++;return false;}return time>=start&&(observed===null||time<=observed)&&(!calendarBounded||time>=range.start&&time<range.end);});
 let result;
 if(report.hasAccounting){
  result=projected(report,rows,bounded);
  result.runs=(report.runs||[]).map(run=>projected(run,rows.filter(r=>r.run_id===run.id),bounded));
 }else if(bounded){result={...report,totals:{},actors:(report.actors||[]).map(a=>({...a,usage:{},usage_observed:false})),runs:(report.runs||[]).map(r=>({...r,totals:{},actors:(r.actors||[]).map(a=>({...a,usage:{},usage_observed:false}))})),accounting:[]};}
 else result=report;
 return {...result,dateCoverage:{unknownExcluded:unknown,detailUnavailable:bounded&&!report.hasAccounting&&report.status!=='unavailable',from:range.from,to:range.to,installationStart:installation.started_at,conservative:installation.conservative}};
}
export function usageGroups(selected){
 const names=['all','chiefs','helpers','unknown'];const result=Object.fromEntries(names.map(name=>[name,{totals:{},count:0,usage_observed:false}]));
 if(selected?.hasAccounting||selected?.accounting?.length){
  const rows=selected.accounting||[];
  for(const name of names){const own=name==='all'?rows:rows.filter(r=>r.category===name);result[name]={totals:aggregate(own),count:new Set(own.map(r=>r.identity).filter(Boolean)).size,usage_observed:own.length>0};}
 }else{
  const actors=(selected?.actors||[]).filter(a=>a.usage_observed!==false);
  result.all={totals:usage(selected),count:new Set(actors.map(a=>identity(a.id)).filter(Boolean)).size,usage_observed:Object.keys(usage(selected)).length>0};
  for(const name of names.slice(1)){const own=actors.filter(a=>actorCategory(a)===name);result[name]={totals:aggregate(own.map(a=>({usage:usage(a)}))),count:new Set(own.map(a=>identity(a.id)).filter(Boolean)).size,usage_observed:own.some(a=>Object.keys(usage(a)).length)};}
  for(const key of counters){const all=result.all.totals[key];const classified=(result.chiefs.totals[key]??0)+(result.helpers.totals[key]??0);if(typeof all==='number'&&classified<=all&&(result.unknown.totals[key]??0)<=all-classified){result.unknown.totals[key]=all-classified;result.unknown.usage_observed=true;}}
 }
 return result;
}

// Display groups use canonical metadata only; nicknames never determine a role.
export function usageRoleKey(actor){
 const category=actorCategory(actor);
 if(category==='chiefs')return 'chiefs';
 if(category==='unknown')return 'unknown';
 const role=String(actor?.role||actor?.duty||'').replaceAll('-','_');
 return helperRoles.has(role)&&!['worker','helper'].includes(role)?role:'helpers';
}
export function usageRoleGroups(selected){
 const actors=selected?.actors||[];const byId=new Map(actors.map(a=>[a.id,a]));const buckets=new Map();
 const add=(key,row)=>{if(!buckets.has(key))buckets.set(key,[]);buckets.get(key).push(row);};
 if(selected?.hasAccounting||selected?.accounting?.length){
  for(const row of selected.accounting||[])add(row.category==='unknown'?'unknown':usageRoleKey(byId.get(row.identity)),row);
 }else{
  for(const actor of actors)add(usageRoleKey(actor),{identity:identity(actor.id),usage:actor.usage_observed===false?{}:usage(actor)});
  const unattributed=usageGroups(selected).unknown;
  if(unattributed.usage_observed){const identities=(buckets.get('unknown')||[]).map(r=>r.identity);buckets.set('unknown',[{identity:identities[0]||null,usage:unattributed.totals},...identities.slice(1).map(id=>({identity:id,usage:{}}))]);}
 }

 return [...buckets].map(([key,rows])=>({key,totals:aggregate(rows),count:new Set(rows.map(r=>r.identity).filter(Boolean)).size,usage_observed:rows.some(r=>Object.keys(r.usage||{}).length)})).sort((a,b)=>a.key==='chiefs'?-1:b.key==='chiefs'?1:a.key.localeCompare(b.key));
}
