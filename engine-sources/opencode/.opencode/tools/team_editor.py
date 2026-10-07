"""Conservative installed-project helper roster editor; no shell or network."""
from __future__ import annotations
import copy, hashlib, json, os, re, shutil, time
from pathlib import Path

ROLES=('acceptance-test-author','fast-lookup','explorer','researcher','implementer','verifier','failure-analyst','qa-operator','reviewer','advisor')
SELECTOR=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._/-]*(?:#[A-Za-z0-9][A-Za-z0-9._-]*)?\Z')
DUTY=re.compile(r'[A-Za-z0-9À-ž _.,:;!?()/-]{0,80}\Z')
MAX_HELPERS=50
SLOT=re.compile(r'\.opencode/agents/helper-(?:0[1-9]|[1-4][0-9]|50)\.md\Z')
PROFILES={
 'balanced':[36,10,22,22,34,20,22,20,22,24],
 'quality':[56,16,34,34,52,32,34,32,36,40],
 'economy':[24,7,14,14,22,13,14,13,14,16],
 'quota-saver':[18,5,10,10,16,9,10,9,10,12],
}
for _name, _steps in list(PROFILES.items()):
    PROFILES[_name] = (_steps[0], _steps[5], *_steps[1:])

ALL_ROLES=('owner',*ROLES)
class TeamError(ValueError): pass

def validate_team(value):
    if not isinstance(value,list) or not 1<=len(value)<=MAX_HELPERS: raise TeamError('Choose 1 to 50 helper slots.')
    result=[]
    for item in value:
        if not isinstance(item,dict) or set(item)-{'role','model','duty'}: raise TeamError('Invalid helper slot.')
        role=item.get('role'); model=item.get('model',''); duty=item.get('duty','')
        if role not in ROLES or not isinstance(model,str) or (model and not SELECTOR.fullmatch(model)) or not isinstance(duty,str) or not DUTY.fullmatch(duty): raise TeamError('Invalid helper role, model, or duty.')
        result.append({'role':role,'model':model,'duty':duty.strip()})
    return result

def name(index): return f'helper-{index:02d}'
def digest(data): return hashlib.sha256(data).hexdigest()
def safe(root,relative):
    path=root/relative; parent=root
    for part in relative.parts[:-1]:
        parent/=part
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()): raise TeamError('Unsafe helper path.')
    if path.is_symlink() or (path.exists() and not path.is_file()): raise TeamError('Unsafe helper path.')
    return path

def assert_private_backups(root: Path):
    relative=Path('.opencode/.bounded-orchestrator/backups')
    location=root/relative
    parent=root
    for part in relative.parts:
        parent/=part
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()): raise TeamError('Unsafe private backup directory.')
    if location.exists() and any(path.is_symlink() for path in location.rglob('*')): raise TeamError('Symlink inside private backups refused.')

def prepare(root: Path, request: dict):
    if not isinstance(request,dict) or set(request)-{'team','profile','model','replace','allow_mixed','revision'}: raise TeamError('Invalid team request.')
    assert_private_backups(root)
    team=validate_team(request.get('team'))
    profile=request.get('profile'); model=request.get('model','')
    if profile not in PROFILES and profile!='custom': raise TeamError('Invalid working style.')
    if not isinstance(model,str) or (model and (not SELECTOR.fullmatch(model) or '#' in model)): raise TeamError('Invalid chief model.')
    selectors=[value for value in [model,*[item['model'] for item in team]] if value]
    if (len({value.split('/',1)[0] for value in selectors})>1 or (not model and len(selectors)>0)) and request.get('allow_mixed') is not True: raise TeamError('Confirm mixed or inherited providers.')
    import console
    config_path=safe(root,Path('.opencode/opencode.jsonc')); manifest_path=safe(root,Path('.opencode/.bounded-orchestrator/install.json'))
    if not manifest_path.is_file(): raise TeamError('Install this project first.')
    text,config=console.read(config_path); manifest_text,manifest=console.read(manifest_path)
    if manifest.get('schema')!=1 or not isinstance(manifest.get('files'),dict): raise TeamError('Invalid install manifest.')
    current=manifest.get('team',[])
    if not isinstance(current,list) or len(current)>MAX_HELPERS: raise TeamError('Invalid saved team.')
    try:
        console.reject_new_superseded_model(model,config.get('model',''))
        for index,item in enumerate(team):
            previous=current[index].get('model','') if index<len(current) and isinstance(current[index],dict) else ''
            console.reject_new_superseded_model(item['model'],previous)
    except console.ConsoleError as exc: raise TeamError(str(exc)) from exc
    revision=digest((text+'\x00'+manifest_text).encode())
    if request.get('revision') not in (None,revision): raise TeamError('Project changed; review again.')
    agents=config.get('agents'); owner=agents.get('owner') if isinstance(agents,dict) else None
    if not isinstance(owner,dict): raise TeamError('Chief configuration unavailable.')
    entry=manifest['files'].get('.opencode/opencode.jsonc',{})
    if entry.get('sha256')!=digest(text.encode()) and request.get('replace') is not True: raise TeamError('Project settings changed elsewhere; confirm backup and replace.')
    changes={}; removed=[]
    rules=[{'action':'*','resource':'*','effect':'deny'},{'action':'subagent','resource':'*','effect':'deny'},*[{'action':'subagent','resource':name(i),'effect':'allow'} for i in range(1,len(team)+1)],{'action':'skill','resource':'bounded-orchestrator','effect':'allow'},{'action':'question','resource':'*','effect':'allow'}]
    updated=console.patch(text,['agents','owner','permissions'],rules)
    if profile!='custom':
        for index,role in enumerate(ALL_ROLES): updated=console.patch(updated,['agents',role,'steps'],PROFILES[profile][index])
    updated=console.patch(updated,['model'],model,not bool(model))
    for index,item in enumerate(team,1):
        role=item['role']; base=agents.get(role)
        if not isinstance(base,dict) or not isinstance(base.get('permissions'),list) or not any(rule.get('action')=='subagent' and rule.get('effect')=='deny' for rule in base['permissions'] if isinstance(rule,dict)): raise TeamError('Selected role is not a bounded helper.')
        agent=copy.deepcopy(base);agent['description']=f"Helper {index:02d}: {item['duty'] or role}"
        if profile!='custom': agent['steps']=PROFILES[profile][ALL_ROLES.index(role)]
        if item['model']: agent['model']=item['model']
        updated=console.patch(updated,['agents',name(index)],agent)
        source=safe(root,Path(f'.opencode/agents/{role}.md'))
        if not source.is_file(): raise TeamError('Selected role file is unavailable.')
        body=source.read_text(encoding='utf-8')
        if item['duty']: body+=f"\nAssigned duty for this helper slot: {item['duty']}\n"
        body=body.replace('description: ',f'description: Helper {index:02d} · ',1)
        relative=Path(f'.opencode/agents/{name(index)}.md');destination=safe(root,relative)
        previous=manifest['files'].get(relative.as_posix(),{}).get('sha256')
        if destination.exists() and digest(destination.read_bytes())!=previous and request.get('replace') is not True: raise TeamError('A helper file changed elsewhere; confirm backup and replace.')
        changes[relative]=(destination,body.encode())
    for index in range(len(team)+1,MAX_HELPERS+1):
        relative=Path(f'.opencode/agents/{name(index)}.md')
        if relative.as_posix() not in manifest['files']: continue
        destination=safe(root,relative)
        if destination.exists() and digest(destination.read_bytes())!=manifest['files'][relative.as_posix()]['sha256']: raise TeamError('A removed helper was modified; resolve it before changing team size.')
        removed.append((relative,destination))
        updated=console.patch(updated,['agents',name(index)],None,True)
    # Keep Markdown chief permissions aligned with JSON. Preserve body, replace only owned frontmatter permissions.
    owner_relative=Path('.opencode/agents/owner.md');owner_path=safe(root,owner_relative)
    if not owner_path.is_file(): raise TeamError('Chief role file is unavailable.')
    owner_text=owner_path.read_text(encoding='utf-8');start=owner_text.find('permissions:');end=owner_text.find('---',start)
    if start<0 or end<0: raise TeamError('Chief role permissions unavailable.')
    owner_rules='permissions:\n  - { action: "*", resource: "*", effect: deny }\n  - { action: subagent, resource: "*", effect: deny }\n'
    owner_rules+=''.join(f'  - {{ action: subagent, resource: {name(index)}, effect: allow }}\n' for index in range(1,len(team)+1))
    owner_rules+='  - { action: skill, resource: bounded-orchestrator, effect: allow }\n  - { action: question, resource: "*", effect: allow }\n'
    owner_data=(owner_text[:start]+owner_rules+owner_text[end:]).encode()
    if digest(owner_path.read_bytes())!=manifest['files'].get(owner_relative.as_posix(),{}).get('sha256') and request.get('replace') is not True: raise TeamError('Chief role file changed elsewhere; confirm backup and replace.')
    changes[owner_relative]=(owner_path,owner_data)
    json.loads(console.scrub(updated))
    actions=[]
    if updated!=text: actions.append('UPDATE project settings')
    for relative,(path,data) in changes.items():
        if not path.exists() or path.read_bytes()!=data: actions.append('UPDATE '+relative.as_posix())
    actions.extend('REMOVE '+relative.as_posix() for relative,_ in removed)
    return {'revision':revision,'actions':actions,'team':team,'profile':profile,'model':model,'updated':updated,'manifest':manifest,'text':text,'changes':changes,'removed':removed,'config_path':config_path,'manifest_path':manifest_path}

def save(root: Path, request: dict, on_commit=None):
    import console
    plan=prepare(root,request)
    if request.get('revision')!=plan['revision']: raise TeamError('Review changes before saving.')
    root=root.resolve(); manifest=plan['manifest']; files=manifest['files']
    runtime=root/'.opencode/.bounded-orchestrator'; sentinel=runtime/'.gitignore'
    if not sentinel.is_file() or sentinel.read_text(encoding='utf-8')!='*\n!.gitignore\n': raise TeamError('Private backup protection is unavailable.')
    assert_private_backups(root)
    backup_dir=runtime/'backups'/('team-'+str(int(time.time()*1000)))
    while backup_dir.exists() or backup_dir.is_symlink():backup_dir=backup_dir.with_name(backup_dir.name+'-next')
    changing=[plan['config_path'],*[path for path,data in plan['changes'].values() if path.exists() and path.read_bytes()!=data],*[path for _,path in plan['removed'] if path.exists()]]
    touched=[plan['config_path'],*[path for path,_ in plan['changes'].values()],*[path for _,path in plan['removed']],plan['manifest_path']]
    before={path:path.read_bytes() if path.exists() else None for path in dict.fromkeys(touched)}
    mutated={}
    def unchanged(path):
        safe(root,path.relative_to(root))
        current=path.read_bytes() if path.exists() else None
        if current!=before[path]: raise TeamError('A team file changed during save; review again.')
    for path in changing:
        if not path.exists(): continue
        unchanged(path)
        relative=path.relative_to(root);destination=backup_dir/relative;safe(root,destination.relative_to(root));destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,destination)
        try: os.chmod(destination,0o600)
        except OSError: pass
    try:
        if plan['updated']!=plan['text']:
            unchanged(plan['config_path']);console.atomic(plan['config_path'],plan['updated']);mutated[plan['config_path']]=plan['updated'].encode()
        files['.opencode/opencode.jsonc']={'sha256':digest(plan['updated'].encode())}
        for relative,(path,data) in plan['changes'].items():
            unchanged(path)
            if not path.exists() or path.read_bytes()!=data: console.atomic(path,data.decode());mutated[path]=data
            files[relative.as_posix()]={'sha256':digest(data)}
        for relative,path in plan['removed']:
            unchanged(path)
            if path.exists():path.unlink();mutated[path]=None
            files.pop(relative.as_posix(),None)
        manifest['team']=plan['team'];manifest['profile']=plan['profile']
        unchanged(plan['manifest_path'])
        manifest_data=json.dumps(manifest,indent=2,sort_keys=True)+'\n'
        console.atomic(plan['manifest_path'],manifest_data);mutated[plan['manifest_path']]=manifest_data.encode()
        if on_commit is not None: on_commit()
    except Exception as exc:
        failed=[]
        for path,applied in reversed(list(mutated.items())):
            try:
                safe(root,path.relative_to(root))
                current=path.read_bytes() if path.exists() else None
                if current!=applied: raise TeamError('Rollback file changed outside this save.')
                data=before[path]
                if data is None:
                    if path.exists():path.unlink()
                elif not path.exists() or path.read_bytes()!=data: console.atomic(path,data.decode('utf-8'))
            except Exception as rollback_exc: failed.append(f'{path.relative_to(root)}: {rollback_exc}')
        if failed: raise TeamError('Team save failed; rollback incomplete: '+', '.join(failed)) from exc
        raise
    return {'saved':True,'actions':plan['actions']}
