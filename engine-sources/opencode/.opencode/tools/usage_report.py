#!/usr/bin/env python3
"""Observed OpenCode stats and explicitly scoped sanitized session exports."""
from __future__ import annotations
import argparse, json, math, re, shutil, subprocess, sys, time
from pathlib import Path
from typing import Any

class UsageError(RuntimeError): pass
LIMITATIONS = ['Stats may contain rounded display counts.', 'No inferred costs, quota, savings, or subscription conversions.', 'Stats has no thread records; use an explicit sanitized session export.']
TOKEN_KEYS = ('input','output','reasoning','cache_read','cache_write')

def run(command: str, args: list[str], root: Path | None = None, timeout: int = 30) -> str:
    executable = shutil.which(command) if '/' not in command else command
    if not executable: raise UsageError('OpenCode CLI was not found; usage data is unavailable.')
    try:
        result = subprocess.run([executable, *args], cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc: raise UsageError('OpenCode command unavailable or timed out.') from exc
    if result.returncode: raise UsageError(f'OpenCode command failed (exit {result.returncode}); no usage available.')
    if len(result.stdout) > 8_000_000: raise UsageError('OpenCode output exceeds the safe size limit.')
    return result.stdout

def normalize_stats(text: str) -> dict[str, Any]:
    if not isinstance(text, str): raise UsageError('Stats must be display text, not an assumed JSON contract.')
    observed = {}; rounded = []
    labels = {'Input':'input', 'Output':'output', 'Cache Read':'cache_read', 'Cache Write':'cache_write', 'Sessions':'sessions', 'Messages':'messages'}
    for label, key in labels.items():
        match = re.search(r'│\s*' + label + r'\s+([\d,.]+[KM]?)\s*│', text)
        if match:
            value = match[1].replace(',', ''); factor = 1000 if value.endswith('K') else 1000000 if value.endswith('M') else 1
            try: observed[key] = float(value.rstrip('KM')) * factor
            except ValueError as exc: raise UsageError('Invalid stats counter.') from exc
            if not math.isfinite(observed[key]): raise UsageError('Invalid stats counter.')
            if factor != 1: rounded.append(key)
    if not observed: raise UsageError('Unrecognized OpenCode stats display; counters unavailable.')
    return {'platform':'opencode', 'status':'available', 'source':'opencode stats (display)', 'observed':observed, 'rounded':rounded, 'groups':[], 'records':[], 'limitations':LIMITATIONS}

def normalize_export(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get('info'), dict) or not isinstance(payload.get('messages'), list): raise UsageError('Unsupported sanitized export shape.')
    ident = payload['info'].get('id')
    if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}',ident): raise UsageError('Invalid exported session id.')
    records = []
    for msg in payload['messages']:
        if not isinstance(msg,dict) or not isinstance(msg.get('info'),dict): raise UsageError('Malformed exported message.')
        info = msg['info']
        if info.get('role') != 'assistant': continue
        tokens = info.get('tokens', {})
        if not isinstance(tokens,dict) or not isinstance(tokens.get('cache',{}),dict): raise UsageError('Malformed tokens.')
        counters = {}
        for key, value in [('input',tokens.get('input')),('output',tokens.get('output')),('reasoning',tokens.get('reasoning')),('cache_read',tokens.get('cache',{}).get('read')),('cache_write',tokens.get('cache',{}).get('write'))]:
            if value is not None:
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value < 0 or value > 10**15: raise UsageError('Invalid token count.')
                counters[key] = value
        model = info.get('modelID'); provider = info.get('providerID'); agent = info.get('agent')
        safe = lambda value: value if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9._/#-]{1,200}',value) else None
        created = info.get('time',{}).get('created') if isinstance(info.get('time'),dict) else None
        if isinstance(created,bool) or not isinstance(created,(int,float)) or not math.isfinite(created) or not 946684800000 <= created < 4102444800000: created = None
        records.append({'thread':ident, 'model':safe(model), 'provider':safe(provider), 'agent':safe(agent), 'variant':safe(info.get('variant')), 'created':created, 'observed':counters})
    totals = {key:sum(record['observed'][key] for record in records if key in record['observed']) for key in {key for record in records for key in record['observed']}}
    project = payload['info'].get('projectID')
    project = project if isinstance(project,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,160}',project) else None
    updated = payload['info'].get('time',{}).get('updated') if isinstance(payload['info'].get('time'),dict) else None
    if type(updated) not in (int,float) or not math.isfinite(updated) or not 946684800000 <= updated < 4102444800000: updated=None
    parent = payload['info'].get('parentID')
    parent = parent if isinstance(parent,str) and re.fullmatch(r'ses_[A-Za-z0-9_-]{1,156}',parent) else ('unresolved' if parent is not None else None)
    agent = payload['info'].get('agent')
    agent = agent if isinstance(agent,str) and re.fullmatch(r'[A-Za-z0-9._-]{1,100}',agent) else None
    return {'platform':'opencode','status':'available','source':'opencode export --sanitize (explicit session)', 'observed':totals,'rounded':[], 'groups':[], 'records':records,'session':{'id':ident,'project':project,'updated':updated,'parent':parent,'agent':agent},'limitations':['Only the selected session is represented; chat bodies and titles are omitted.','Missing counters are unavailable, never zero-filled.']}

def orchestra(sessions: list[dict[str,Any]]) -> dict[str,Any]:
    """Bind only exported session IDs; a role/model is never an agent identity."""
    by_id={}
    for entry in sessions:
        ident=entry['id']
        if ident in by_id: raise UsageError('Duplicate exported session id.')
        by_id[ident]=entry
    roots=[]; orphan=0
    for entry in sessions:
        if entry['parent'] is not None: continue
        members=[]
        for candidate in sessions:
            current=candidate; seen=set(); same_project=True
            while current['parent'] is not None and current['parent'] in by_id and current['id'] not in seen:
                seen.add(current['id']); current=by_id[current['parent']]
                same_project=same_project and current['project']==candidate['project']
            if current['id']==entry['id'] and current['parent'] is None and same_project and current['project']==candidate['project']:
                members.append(candidate)
        if not members: continue
        members.sort(key=lambda item:(item['id']!=entry['id'],item['id']))
        root_total=sum(item['total'] for item in members)
        roots.append({'id':entry['id'],'total':root_total,'chief_total':entry['total'],'helper_count':len(members)-1,'unassigned_total':0,'nodes':members})
    assigned={node['id'] for root in roots for node in root['nodes']}
    orphan=sum(1 for entry in sessions if entry['id'] not in assigned)
    return {'roots':roots,'orphan_sessions':orphan,'observed_sessions':len(sessions),'window_limit':12}

def breakdown(exports: list[dict[str,Any]], *, history: list[dict[str,Any]] | None = None, days: int | None = None, project: str | None = None, now_ms: int | None = None, demo: bool = False) -> dict[str,Any]:
    """Count each observed token component once; missing components remain unknown."""
    if days is not None and (type(days) is not int or not 0 <= days <= 3650): raise UsageError('Days must be 0..3650.')
    now_ms = now_ms if now_ms is not None else int(time.time()*1000)
    cutoff = now_ms-days*86400000 if days else None
    models: dict[str,int|float] = {}; styles: dict[str,int|float] = {}; observed={}; records=[]; partial=0; unknown_date=0; orchestra_sessions=[]
    timeline_days: dict[str,int|float] = {}; timeline_unknown: int|float = 0
    for item in exports:
        report=normalize_export(item['export'])
        session=report['session']; ident=session['id']
        if ident != item.get('id'): raise UsageError('Session list and export ID differ.')
        if project is not None and item.get('directory') != project: continue
        if not demo and (not session['project'] or session['project'] != item.get('project')): continue
        node={'id':ident,'parent':session['parent'],'project':session['project'],'agent':session['agent'],'created':item.get('created') if type(item.get('created')) in (int,float) else None,'models':{},'variants':{},'variant_missing':False,'observed':{},'total':0,'messages':0,'partial_messages':0}
        message_agents=set()
        all_records=report['records']; start=item.get('created')
        if type(start) not in (int,float) or not 946684800000 <= start < 4102444800000: start=None
        updated_values=[value for value in (item.get('updated'),session.get('updated')) if type(value) in (int,float) and math.isfinite(value) and 946684800000 <= value < 4102444800000]
        last_activity=max(updated_values) if updated_values else None
        style=None
        if start is not None and last_activity is not None and start <= last_activity < now_ms-300000 and all(record['created'] is not None and record['created'] <= last_activity for record in all_records):
            matching=[event for event in history or [] if event.get('project') == item.get('directory') and type(event.get('timestamp')) in (int,float) and event['timestamp'] < start]
            if matching:
                event=max(matching,key=lambda value:value['timestamp'])
                # Updated is last observed activity, not a completion guarantee; recent sessions stay unknown.
                if not any(next_event.get('project') == item.get('directory') and type(next_event.get('timestamp')) in (int,float) and start < next_event['timestamp'] <= last_activity for next_event in history or []):
                    style=event.get('profile') if event.get('profile') in {'economy','balanced','quality','quota-saver','custom'} else None
        for record in all_records:
            created=record['created']
            if cutoff is not None and (created is None or created < cutoff):
                if created is None: unknown_date += 1
                continue
            counters=record['observed']; amount=sum(counters.values())
            if created is None: timeline_unknown += amount
            else:
                day=time.strftime('%Y-%m-%d',time.gmtime(created/1000))
                timeline_days[day]=timeline_days.get(day,0)+amount
            if len(counters)<len(TOKEN_KEYS): partial += 1
            if len(counters)<len(TOKEN_KEYS): node['partial_messages'] += 1
            for key,value in counters.items(): observed[key]=observed.get(key,0)+value
            for key,value in counters.items(): node['observed'][key]=node['observed'].get(key,0)+value
            model=(record['provider']+'/'+record['model']) if record['provider'] and record['model'] else 'unknown'
            models[model]=models.get(model,0)+amount
            node['models'][model]=node['models'].get(model,0)+amount
            if record['variant']:
                node['variants'].setdefault(model,[])
                if record['variant'] not in node['variants'][model]: node['variants'][model].append(record['variant'])
            else: node['variant_missing']=True
            node['total'] += amount; node['messages'] += 1
            if record['agent']: message_agents.add(record['agent'])
            label=style or 'unknown'; styles[label]=styles.get(label,0)+amount
            records.append(record)
        if not node['agent'] and len(message_agents)==1: node['agent']=next(iter(message_agents))
        orchestra_sessions.append(node)
    total=sum(observed.values())
    return {'platform':'opencode','status':'available','source':'DEMO fixture (not live usage)' if demo else 'recent sanitized session exports','observed':observed,'exact_observed_total':total,'model_breakdown':models,'style_breakdown':styles,'timeline':{'buckets':[{'day':day,'tokens':timeline_days[day]} for day in sorted(timeline_days)],'unknown_date_tokens':timeline_unknown},'orchestra':orchestra(orchestra_sessions),'records':records,'rounded':[],'coverage':{'sessions':len(exports),'messages':len(records),'partial_messages':partial,'unknown_date_messages':unknown_date,'limited_to_recent_sessions':not demo},'limitations':['Chart total sums observed input, output, reasoning, cache read and cache write once each; missing components cannot be estimated.','Working style is an estimate from console-managed changes for verified project sessions only; older or ambiguous sessions are unknown.','Only recent exported sessions are included; this is not an account-wide total.']}

def collect_breakdown(command: str = 'opencode', *, root: Path, days: int | None = None, project: str | None = None, session: str | None = None, fixture: Path | None = None, history: list[dict[str,Any]] | None = None) -> dict[str,Any]:
    if fixture:
        if fixture.stat().st_size > 8_000_000: raise UsageError('Fixture too large.')
        try: payload=json.loads(fixture.read_text())
        except (OSError,ValueError) as exc: raise UsageError('Invalid export fixture.') from exc
        if isinstance(payload,dict) and isinstance(payload.get('sessions'),list):
            exports=payload['sessions']; demo_history=payload.get('history',[])
            if not isinstance(demo_history,list): raise UsageError('Invalid demo history.')
            return breakdown(exports,history=demo_history,days=days,project=None,now_ms=1790800000000,demo=True)
        report=normalize_export(payload); ident=report['session']['id']
        return breakdown([{'id':ident,'project':report['session']['project'],'directory':'demo','created':None,'export':payload}],days=days,demo=True)
    if session and not re.fullmatch(r'ses_[A-Za-z0-9_-]{1,156}',session): raise UsageError('Invalid OpenCode session id.')
    raw=run(command,['session','list','--max-count','12','--format','json'],root,timeout=8)
    try: listed=json.loads(raw)
    except ValueError as exc: raise UsageError('Invalid session list JSON.') from exc
    if not isinstance(listed,list): raise UsageError('Unsupported session list shape.')
    selected=[]
    for value in listed[:12]:
        if not isinstance(value,dict): continue
        ident=value.get('id'); directory=value.get('directory'); project_id=value.get('projectId')
        if not isinstance(ident,str) or not re.fullmatch(r'ses_[A-Za-z0-9_-]{1,156}',ident): continue
        if not isinstance(directory,str) or not isinstance(project_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}',project_id): continue
        if session and ident!=session: continue
        if project is not None and directory!=str(root.resolve()): continue
        selected.append({'id':ident,'project':project_id,'directory':directory,'created':value.get('created'),'updated':value.get('updated')})
    if session and not selected: raise UsageError('Selected session was not found in the recent session list.')
    exports=[]; failures=0
    for item in selected:
        try: item['export']=json.loads(run(command,['export',item['id'],'--sanitize'],root,timeout=5)); exports.append(item)
        except (UsageError,ValueError): failures+=1
    if not exports and failures: raise UsageError('Recent session exports unavailable.')
    result=breakdown(exports,history=history,days=days,project=str(root.resolve()) if project is not None else None)
    result['coverage']['listed_sessions']=len(selected); result['coverage']['failed_sessions']=failures
    return result

def collect(command: str = 'opencode', *, root: Path | None = None, days: int | None = None, project: str | None = None, session: str | None = None, fixture: Path | None = None) -> dict[str, Any]:
    if fixture:
        if fixture.stat().st_size > 8_000_000: raise UsageError('Fixture too large.')
        try: report = normalize_export(json.loads(fixture.read_text()))
        except (OSError,ValueError) as exc: raise UsageError('Invalid export fixture.') from exc
        report['source'] = 'DEMO fixture (not live usage)'; return report
    if session:
        if not re.fullmatch(r'ses_[A-Za-z0-9_-]{1,156}',session): raise UsageError('Invalid OpenCode session id.')
        try: return normalize_export(json.loads(run(command,['export',session,'--sanitize'],root)))
        except ValueError as exc: raise UsageError('Invalid export JSON.') from exc
    args = ['stats']
    if days is not None:
        if isinstance(days,bool) or not isinstance(days,int) or not 0 <= days <= 3650: raise UsageError('Days must be 0..3650.')
        args += ['--days', str(days)]
    if project is not None:
        if not isinstance(project,str) or len(project)>160 or (project and not re.fullmatch(r'[A-Za-z0-9_-]+',project)): raise UsageError('Invalid project id.')
        args += ['--project',project]
    return normalize_stats(run(command,args,root))

def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--command',default='opencode'); parser.add_argument('--json',action='store_true'); parser.add_argument('--days',type=int); parser.add_argument('--project'); parser.add_argument('--session'); parser.add_argument('--fixture',type=Path); args=parser.parse_args(argv)
    try: report=collect(args.command,days=args.days,project=args.project,session=args.session,fixture=args.fixture)
    except (UsageError,OSError) as exc:
        print(json.dumps({'platform':'opencode','status':'unavailable','source':'OpenCode CLI','error':str(exc)})); return 2
    print(json.dumps(report,indent=2,ensure_ascii=False)); return 0
if __name__=='__main__': raise SystemExit(main())
