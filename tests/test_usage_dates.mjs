import assert from 'node:assert/strict';
import {usageRoleKey,usageRoleGroups,calendarRange,accountingInstant,normalizeUsageReport as rawNormalizeUsageReport,filterUsageReport,usageGroups,actorCategory} from '../ustam/ui/usage-data.mjs';
function normalizeUsageReport(data){return rawNormalizeUsageReport({...data,installation_boundary:data.installation_boundary||{status:'verified',started_at:'2000-01-01T00:00:00Z'}});}
process.env.TZ='America/New_York';
const now=new Date('2026-03-09T12:00:00-04:00');
const seven=calendarRange('7','','',now);assert.equal(seven.from,'2026-03-03');assert.equal(seven.to,'2026-03-09');assert.equal((seven.end-seven.start)/3600000,167);
const thirty=calendarRange('30','','',now);assert.equal(thirty.from,'2026-02-08');
const custom=calendarRange('custom','2026-03-08','2026-03-08');assert.equal((custom.end-custom.start)/3600000,23);
for(const pair of [['2026-02-30','2026-03-02'],['','2026-03-02'],['2026-03-09','2026-03-08']])assert.throws(()=>calendarRange('custom',...pair),/invalid_date_range/);
assert.equal(accountingInstant({timestamp:'2026-03-08T04:59:59Z'}),custom.start-1000);assert.equal(accountingInstant({timestamp:'unknown'}),null);assert.equal(accountingInstant({timestamp:'2026-02-30T00:00:00Z'}),null);assert.equal(accountingInstant({timestamp:'2026-03-08T12:00:00'}),null);
const report={status:'available',totals:{total_tokens:1031},records:[
 {thread:'chief',timestamp:'2026-03-08T04:59:59Z',usage:{total_tokens:1000,input_tokens:900,cached_input_tokens:800,output_tokens:100}},
 {thread:'chief',timestamp:'2026-03-08T05:00:00Z',usage:{total_tokens:10,input_tokens:8,cached_input_tokens:6,output_tokens:2}},
 {thread:'h1',timestamp:'2026-03-09T03:59:59Z',usage:{total_tokens:7,input_tokens:5,cached_input_tokens:3,output_tokens:2}},
 {thread:'h2',timestamp:'2026-03-09T04:00:00Z',usage:{total_tokens:5,input_tokens:3,cached_input_tokens:1,output_tokens:2}},
 {thread:'h2',timestamp:'unknown',usage:{total_tokens:4,input_tokens:3,cached_input_tokens:1,output_tokens:1}},
 {thread:'mystery',timestamp:'2026-03-08T12:00:00Z',usage:{total_tokens:3,input_tokens:2,output_tokens:1}},
 {thread:'h1',timestamp:'2026-03-08T12:00:00Z',usage:{total_tokens:2,input_tokens:1,output_tokens:1}},
],agents:[{id:'chief',source:'root'},{id:'h1',source:'subagent',parent:'chief'},{id:'h2',source:'subagent',parent:'chief'}],orchestra:{scopes:[{id:'chief'}],actors:[{id:'chief',kind:'conductor'},{id:'h1',kind:'helper'},{id:'h2',kind:'helper'},{id:'mystery',name:'chief',model:'chief-model',role:'unrecognized'}]}};
const normalized=normalizeUsageReport(report);const filtered=filterUsageReport(normalized,custom);assert.equal(filtered.totals.total_tokens,22);assert.equal(filtered.dateCoverage.unknownExcluded,1);assert.equal(filtered.runs[0].totals.total_tokens,19);
const groups=usageGroups(filtered);assert.equal(groups.all.totals.total_tokens,22);assert.equal(groups.all.count,3);assert.equal(groups.chiefs.totals.total_tokens,10);assert.equal(groups.helpers.totals.total_tokens,9);assert.equal(groups.helpers.totals.cached_input_tokens,3);assert.equal(groups.helpers.count,1);assert.equal(groups.unknown.totals.total_tokens,3);assert.equal(groups.unknown.count,1);assert.equal(actorCategory({name:'chief',model:'chief',role:'nonsense'}),'unknown');assert.equal(actorCategory({kind:'unknown',role:'chief'}),'unknown');
assert.equal(filterUsageReport(normalized,calendarRange()).totals.total_tokens,1027);assert.equal(normalized.accounting.length,7);assert.equal(filterUsageReport(normalized,custom).totals.total_tokens,22);
const changed=structuredClone(report);changed.records.push({thread:'h1',timestamp:'2026-03-08T13:00:00Z',usage:{total_tokens:6}});assert.equal(filterUsageReport(normalizeUsageReport(changed),custom).totals.total_tokens,28);
// Normalized cumulative delta remains 50 after the older 100 baseline falls outside the range.
const delta=normalizeUsageReport({records:[{thread:'t',timestamp:'2026-03-07T00:00:00Z',usage:{total_tokens:100}},{thread:'t',timestamp:'2026-03-08T12:00:00Z',semantics:'cumulative',usage:{total_tokens:50}}],actors:[{id:'t',kind:'helper'}]});assert.equal(filterUsageReport(delta,custom).totals.total_tokens,50);
const oc=normalizeUsageReport({platform:'opencode',status:'available',records:[{thread:'root',created:custom.start,observed:{input:5,output:2,reasoning:1,cache_read:3,cache_write:4}},{thread:'child',created:custom.start+1,observed:{input:2,output:1,cache_read:7}},{thread:'orphan',created:null,observed:{input:3}}],orchestra:{roots:[{id:'root',nodes:[{id:'root',parent:null,models:{}},{id:'child',parent:'root',models:{}}]}]}});
const ocView=filterUsageReport(oc,custom);assert.equal(ocView.totals.total_tokens,25);assert.equal(ocView.totals.cached_input_tokens,10);assert.equal(usageGroups(ocView).helpers.totals.total_tokens,10);assert.equal(ocView.dateCoverage.unknownExcluded,1);
const cc=normalizeUsageReport({platform:'claude',status:'available',events:[{metric:'claude_code.token.usage',unit:'tokens',type:'input',value:8,thread:'session',date:'2026-03-08T12:00:00Z'},{metric:'claude_code.token.usage',unit:'tokens',type:'output',value:2,thread:'session',date:null},{metric:'claude_code.token.usage',unit:'tokens',type:'cacheRead',value:3,thread:'session',date:'2026-03-08T12:00:00Z'},{metric:'claude_code.cost',unit:'cost',type:'input',value:999,date:'2026-03-08T12:00:00Z'}]});
const ccView=filterUsageReport(cc,custom);assert.equal(ccView.totals.total_tokens,8);assert.equal(ccView.totals.cached_input_tokens,3);assert.equal(usageGroups(ccView).unknown.totals.total_tokens,8);assert.equal(usageGroups(ccView).chiefs.usage_observed,false);
const unavailable=filterUsageReport(normalizeUsageReport({platform:'claude',status:'unavailable',source:'none'}),custom);assert.deepEqual(unavailable.totals,{});
const undated=filterUsageReport(normalizeUsageReport({totals:{total_tokens:9},actors:[{id:'t',total_tokens:9}]}),custom);assert.deepEqual(undated.totals,{});assert.equal(undated.dateCoverage.detailUnavailable,true);
console.log('Usage calendar, DST, boundaries, unknown dates, groups, cache reuse and three-provider normalization passed.');

const partial=usageGroups({totals:{total_tokens:100,cached_input_tokens:20},actors:[{id:'a',kind:'conductor',total_tokens:30,cached_input_tokens:5},{id:'b',kind:'helper',total_tokens:40,cached_input_tokens:7}]});assert.equal(partial.unknown.totals.total_tokens,30);assert.equal(partial.unknown.totals.cached_input_tokens,8);assert.equal(partial.unknown.count,0);
// Contradictory thread/session roots stay unattributed, matching backend family().
const conflict=normalizeUsageReport({status:'available',records:[{thread:'A',session_id:'B',timestamp:'2026-03-08T12:00:00Z',usage:{total_tokens:11}}],agents:[{id:'A',source:'root'},{id:'B',source:'root'}],orchestra:{scopes:[{id:'A',total_tokens:0},{id:'B',total_tokens:0}],actors:[{id:'A',kind:'conductor'},{id:'B',kind:'conductor'}]}});
assert.equal(conflict.accounting[0].run_id,null);
for(const range of [calendarRange(),custom]){
 const view=filterUsageReport(conflict,range);const summary=usageGroups(view);
 assert.equal(view.totals.total_tokens,11);assert.equal(summary.all.totals.total_tokens,11);assert.equal(summary.unknown.totals.total_tokens,11);assert.deepEqual(summary.chiefs.totals,{});
 for(const run of view.runs){assert.deepEqual(run.totals,{});assert.deepEqual(run.accounting,[]);assert.equal(usageGroups(run).all.usage_observed,false);}
 for(const actor of view.actors){assert.deepEqual(actor.usage,{});assert.equal(actor.usage_observed,false);}
 if(range.start!==null)assert.deepEqual(view.actors,[]);
}
const mixedSource=structuredClone(conflict);mixedSource.records.push({thread:'A',session_id:'A',timestamp:'2026-03-08T12:00:00Z',usage:{total_tokens:5}});const mixedConflict=normalizeUsageReport(mixedSource);
for(const range of [calendarRange(),custom]){
 const view=filterUsageReport(mixedConflict,range);const summary=usageGroups(view);
 assert.equal(summary.all.totals.total_tokens,16);assert.equal(summary.chiefs.totals.total_tokens,5);assert.equal(summary.unknown.totals.total_tokens,11);
 assert.equal(view.actors.find(a=>a.id==='A').usage.total_tokens,5);assert.equal(view.runs.find(r=>r.id==='A').totals.total_tokens,5);assert.deepEqual(view.runs.find(r=>r.id==='B').totals,{});
}

const roleActors=[{id:'chief-role',kind:'conductor',name:'Helper'},...Array.from({length:46},(_,i)=>({id:'helper-'+i,kind:'helper',name:'Forge',role:i<20?'implementer':i<30?'explorer':'invented'}))];
const roleReport=normalizeUsageReport({actors:roleActors,records:roleActors.map(a=>({actor_id:a.id,timestamp:'2026-03-08T12:00:00Z',usage:{total_tokens:a.kind==='conductor'?5:1,cached_input_tokens:1}}))});
for(const range of [calendarRange(),custom]){const view=filterUsageReport(roleReport,range);const groups=usageRoleGroups(view);assert.equal(groups.find(g=>g.key==='chiefs').totals.total_tokens,5);assert.equal(groups.find(g=>g.key==='implementer').count,20);assert.equal(groups.find(g=>g.key==='explorer').count,10);assert.equal(groups.find(g=>g.key==='helpers').count,16);assert.equal(groups.reduce((n,g)=>n+(g.totals.total_tokens||0),0),51);assert.equal(groups.reduce((n,g)=>n+(g.totals.cached_input_tokens||0),0),47);assert.equal(view.actors.length,47);}
assert.equal(usageRoleKey({kind:'helper',role:'unknown',name:'explorer'}),'helpers');assert.equal(usageRoleKey({kind:'unknown',role:'implementer',name:'Forge'}),'unknown');assert.equal(usageRoleKey({kind:'conductor',name:'Helper'}),'chiefs');
assert.equal(usageRoleGroups(filterUsageReport(mixedConflict,custom)).find(g=>g.key==='unknown').totals.total_tokens,11);

assert.equal(usageRoleGroups({totals:{total_tokens:100},actors:[{id:'p',kind:'conductor',total_tokens:30},{id:'q',kind:'helper',role:'implementer',total_tokens:40}]}).find(g=>g.key==='unknown').totals.total_tokens,30);

const installed=normalizeUsageReport({installation_boundary:{status:'verified',started_at:'2026-03-08T05:00:00Z',observed_at:'2026-03-09T04:00:00Z'},actors:[{id:'t',kind:'helper'}],records:[{actor_id:'t',timestamp:'2026-03-07T00:00:00Z',usage:{total_tokens:100}},{actor_id:'t',timestamp:'2026-03-08T05:00:00Z',semantics:'cumulative',usage:{total_tokens:50}},{actor_id:'t',timestamp:'unknown',usage:{total_tokens:9}},{actor_id:'t',timestamp:'2026-03-10T00:00:00Z',usage:{total_tokens:7}}]});
const installedView=filterUsageReport(installed,calendarRange());assert.equal(installedView.totals.total_tokens,50);assert.equal(installedView.dateCoverage.unknownExcluded,1);assert.equal(usageGroups(installedView).helpers.totals.total_tokens,50);assert.equal(filterUsageReport(installed,custom).totals.total_tokens,50);
const noBoundary=rawNormalizeUsageReport({records:[{thread:'t',timestamp:'2026-03-08T12:00:00Z',usage:{total_tokens:999}}]});const closed=filterUsageReport(noBoundary,calendarRange());assert.deepEqual(closed.totals,{});assert.deepEqual(closed.actors,[]);assert.equal(closed.dateCoverage.installationUnknown,true);
