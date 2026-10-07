#!/usr/bin/env python3
"""Local browser settings and observed usage. Python stdlib only."""
from __future__ import annotations
import argparse, copy, difflib, hashlib, importlib.util, json, os, re, secrets, shutil, subprocess, sys, tempfile, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import usage_report
import team_editor

ROLES = ('owner','acceptance-test-author','fast-lookup','explorer','researcher','implementer','verifier','failure-analyst','qa-operator','reviewer','advisor')
PROFILES = {
 'balanced':[36,10,22,22,34,20,22,20,22,24], 'quality':[56,16,34,34,52,32,34,32,36,40],
 'economy':[24,7,14,14,22,13,14,13,14,16], 'quota-saver':[18,5,10,10,16,9,10,9,10,12],
}
for _name, _steps in list(PROFILES.items()):
    PROFILES[_name] = [_steps[0], _steps[5], *_steps[1:]]

MODEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._/-]*(?:#[A-Za-z0-9][A-Za-z0-9._-]*)?$')
SUPERSEDED_GPT = re.compile(r'(?:^|/)gpt-5(?:[.-]|$)', re.IGNORECASE)
class ConsoleError(ValueError): pass

def reject_new_superseded_model(selected, saved):
    if selected and selected != saved and SUPERSEDED_GPT.search(selected):
        raise ConsoleError('This GPT-5 model is superseded and cannot be chosen anew; an unchanged saved choice can be retained.')

def scrub(text):
    # Remove comments without changing offsets or touching string literals.
    pattern = r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/'
    clean = re.sub(pattern,lambda m: m[0] if m[0].startswith('"') else ''.join('\n' if c=='\n' else ' ' for c in m[0]),text)
    return re.sub(r',(?=\s*[}\]])',' ',clean)

def read(path):
    if not path.exists(): return '{}\n', {}
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000: raise ConsoleError('Unsafe or oversized configuration path.')
    text = path.read_text(encoding='utf-8')
    try: value=json.loads(scrub(text))
    except ValueError as exc: raise ConsoleError('Configuration is not valid JSON/JSONC.') from exc
    if not isinstance(value,dict): raise ConsoleError('Configuration must be an object.')
    return text,value

def safe(path):
    for parent in [path,*path.parents]:
        if parent.is_symlink(): raise ConsoleError('Symlinked configuration or state path refused.')

def atomic(path,data):
    safe(path); path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='') as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)

def digest(text): return hashlib.sha256(text.encode()).hexdigest()

# JSONC edits preserve every unrelated byte, including comments and credentials.
def spans(text):
    clean=scrub(text); decoder=json.JSONDecoder()
    def whitespace(i):
        while i<len(clean) and clean[i].isspace(): i+=1
        return i
    def parse(i):
        i=whitespace(i); start=i
        if clean[i]!='{':
            _,end=decoder.raw_decode(clean,i); return {'start':start,'end':end,'children':{}}
        children={}; i=whitespace(i+1)
        while clean[i]!='}':
            key,end=decoder.raw_decode(clean,i); i=whitespace(end)
            if clean[i]!=':': raise ConsoleError('Invalid JSONC member.')
            child=parse(i+1); child['member_end']=end
            children[key]=child; i=whitespace(child['end'])
            if clean[i]==',': i=whitespace(i+1)
            elif clean[i]!='}': raise ConsoleError('Invalid JSONC separator.')
        return {'start':start,'end':i+1,'close':i,'children':children}
    return parse(0)

def patch(text,path,value,remove=False):
    # Work recursively; a missing parent is inserted once as a small object.
    tree=spans(text); node=tree
    for index,key in enumerate(path):
        child=node['children'].get(key)
        if child is None:
            if remove: return text
            nested=value
            for part in reversed(path[index+1:]): nested={part:nested}
            close=node.get('close')
            if close is None: raise ConsoleError('Expected object at managed field.')
            before=text[node['start']+1:close]; stripped=scrub(before).rstrip()
            separator='' if not stripped else ','
            # An existing trailing comma was scrubbed; retain it rather than add another.
            raw_no_comments=re.sub(r'//[^\n]*|/\*[\s\S]*?\*/','',before).rstrip()
            if raw_no_comments.endswith(','): separator=''
            addition=separator+'\n'+json.dumps(key)+': '+json.dumps(nested,ensure_ascii=False)+'\n'
            return text[:close]+addition+text[close:]
        if index==len(path)-1:
            if remove:
                # Removal via null is not equivalent to absence. Locate key and separator.
                keys=list(node['children']); pos=keys.index(key)
                left=node['start']+1 if pos==0 else node['children'][keys[pos-1]]['end']
                right=node['close'] if pos==len(keys)-1 else node['children'][keys[pos+1]]['start']
                # Recover start of next member's quoted key using scrubbed structure.
                clean=scrub(text)
                key_start=clean.rfind('"',left,child['start'])
                key_start=clean.rfind('"',left,key_start)
                if pos < len(keys)-1:
                    comma=clean.find(',',child['end'],right); return text[:key_start]+text[comma+1:]
                if pos>0:
                    comma=clean.find(',',left,child['start']); return text[:comma]+text[child['end']:]
                return text[:key_start]+text[child['end']:]
            return text[:child['start']]+json.dumps(value,ensure_ascii=False)+text[child['end']:]
        node=child
    return text

def get(config,path):
    node=config
    for key in path:
        if not isinstance(node,dict) or key not in node: return {'present':False}
        node=node[key]
    return {'present':True,'value':node}

def model_catalog(root: Path, command: str = 'opencode', refresh: bool = False) -> dict[str, Any]:
    executable=shutil.which(command) if '/' not in command else command
    reason='missing' if not executable else 'unavailable'
    if executable:
        try:
            result=subprocess.run([executable,'models',*(['--refresh'] if refresh else [])],cwd=root,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20,check=False)
            if result.returncode==0 and len(result.stdout)<=2_000_000:
                found=sorted({line.strip() for line in result.stdout.splitlines() if MODEL.fullmatch(line.strip()) and '#' not in line})
                if found: return {'status':'connected','source':'opencode models','models':found[:3000],'limitations':['Available for the selected project at the time of this command; model access can change.']+(['Only the first 3000 model IDs are shown.'] if len(found)>3000 else [])}
        except subprocess.TimeoutExpired: reason='timeout'
        except OSError: reason='unavailable'
    return {'status':'unavailable','reason':reason,'source':'opencode models','models':[],'limitations':['No project-scoped model IDs could be verified. Existing saved model choices remain available; connect OpenCode and refresh the list for new choices.']}

class Settings:
    def __init__(self,root,user=None):
        self.root=root.resolve(); self.user=user or Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'opencode'
        self.targets={'project':self.root/'.opencode/opencode.jsonc','user':self.user/'opencode.jsonc'}
        self.lock=threading.Lock()
    def target(self,name):
        if name not in self.targets: raise ConsoleError('Unknown target.')
        path=self.targets[name]; other=path.with_suffix('.json')
        if other.exists() and path.exists(): raise ConsoleError('Both .json and .jsonc targets exist; merge them manually first.')
        path=other if other.exists() else path
        safe(path); return path
    def statepath(self,name):
        return (self.root/'.opencode/.bounded-orchestrator' if name=='project' else self.user/'.bounded-orchestrator')/'console-save.json'
    def historypath(self,name): return self.statepath(name).parent/'console-settings-history.json'
    def history(self,name):
        path=self.historypath(name); safe(path)
        if not path.exists(): return []
        if path.stat().st_size>1_000_000: raise ConsoleError('Settings history is too large.')
        try: value=json.loads(path.read_text(encoding='utf-8'))
        except ValueError as exc: raise ConsoleError('Settings history is invalid.') from exc
        if not isinstance(value,list) or len(value)>5000: raise ConsoleError('Settings history is invalid.')
        return value
    def record_history(self,name,profile,updated,events):
        path=self.historypath(name); config=json.loads(scrub(updated))
        # Whitelist scalar preferences. No credentials, prompts, chat, or full config.
        agents=config.get('agents',{})
        effective={'model':config.get('model') if isinstance(config.get('model'),str) and MODEL.fullmatch(config['model']) else '',
                   'roles':{role:{key:value for key,value in agents.get(role,{}).items() if (key=='model' and isinstance(value,str) and MODEL.fullmatch(value)) or (key=='steps' and type(value)==int and value>0)} for role in ROLES}}
        timestamp=max(int(time.time()*1000),events[-1]['timestamp']+1 if events and type(events[-1].get('timestamp')) is int else 0)
        events.append({'timestamp':timestamp,'project':str(self.root) if name=='project' else None,'scope':name,'profile':profile,'effective':effective})
        atomic(path,json.dumps(events[-5000:],ensure_ascii=False,separators=(',',':'))+'\n')
    def selected_team(self,name):
        if name!='project': return []
        path=self.root/'.opencode/.bounded-orchestrator/install.json'
        safe(path)
        if not path.exists(): return []
        _,manifest=read(path)
        if manifest.get('schema')!=1 or not isinstance(manifest.get('team',[]),list): raise ConsoleError('Invalid installed team state.')
        team=manifest.get('team',[])
        if not team:return []
        try:return team_editor.validate_team(team)
        except team_editor.TeamError as exc: raise ConsoleError('Invalid installed team state.') from exc

    def profile_from_config(self,updated,name='project'):
        config=json.loads(scrub(updated)); roles=config.get('agents',{});team=self.selected_team(name)
        for profile,steps in PROFILES.items():
            if all(isinstance(roles.get(role),dict) and roles[role].get('steps')==steps[index] for index,role in enumerate(ROLES)) and all(isinstance(roles.get(f'helper-{index:02d}'),dict) and roles[f'helper-{index:02d}'].get('steps')==steps[ROLES.index(item['role'])] for index,item in enumerate(team,1)): return profile
        return 'custom'
    def can_restore(self,name,path,config):
        statepath=self.statepath(name)
        if statepath.is_symlink() or not statepath.is_file(): return False
        try:
            _,state=read(statepath)
            if state.get('schema')!=1 or state.get('target')!=str(path) or not isinstance(state.get('operations'),list) or not state['operations']: return False
            for op in state['operations']:
                if not isinstance(op,dict): return False
                keys=op.get('path',[])
                if not (keys==['model'] or (isinstance(keys,list) and len(keys)==3 and keys[0]=='agents' and (keys[1] in ROLES or re.fullmatch(r'helper-(?:0[1-9]|[1-4][0-9]|50)',keys[1])) and keys[2] in {'model','steps'})): return False
                if get(config,keys)!=op.get('after'): return False
            return True
        except (ConsoleError,OSError,ValueError,TypeError): return False
    def snapshot(self,name):
        path=self.target(name); text,config=read(path)
        agents=config.get('agents',{})
        if not isinstance(agents,dict) or any(not isinstance(agents.get(role,{}),dict) for role in ROLES): raise ConsoleError('Invalid agents config.')
        roles={role:{key:value for key,value in agents.get(role,{}).items() if (key=='model' and isinstance(value,str) and MODEL.fullmatch(value)) or (key=='steps' and type(value)==int and value>0)} for role in ROLES}
        # Never return arbitrary config, provider credentials, prompts, or system text.
        return {'target':str(path),'revision':digest(text),'model':config.get('model') if isinstance(config.get('model'),str) and MODEL.fullmatch(config['model']) else '', 'roles':roles,'effective':self.effective(),'installed':(self.root/'.opencode/.bounded-orchestrator/install.json').is_file(),'restore_available':self.can_restore(name,path,config),'profiles':list(PROFILES),'limitations':['Model availability and variants depend on the configured provider.','Markdown agent model/steps can override JSON settings; the console refuses conflicting Markdown overrides.','Parallelism has no verified numeric V2 setting; use one specialist by default.','Context, retry and report guidance are prompt preferences, not token ceilings.']}
    def effective(self):
        # Reconstruct only safe scalar fields from documented locations, never full config.
        result={'model':None,'roles':{role:{} for role in ROLES},'sources':[]}
        def apply(path):
            if not path.exists(): return
            safe(path); _,value=read(path); result['sources'].append(str(path))
            model=value.get('model')
            if isinstance(model,str) and MODEL.fullmatch(model): result['model']=model
            agents=value.get('agents',{})
            if not isinstance(agents,dict): raise ConsoleError('Invalid agents config.')
            for role in ROLES:
                values=agents.get(role,{})
                if not isinstance(values,dict): raise ConsoleError('Invalid agent config.')
                for key,item in values.items():
                    if (key=='model' and isinstance(item,str) and MODEL.fullmatch(item)) or (key=='steps' and type(item)==int and item>0): result['roles'][role][key]=item
        for ext in ['json','jsonc']: apply(self.user/('opencode.'+ext))
        ancestors=list(reversed([self.root,*self.root.parents]))
        for parent in ancestors:
            for ext in ['json','jsonc']: apply(parent/('opencode.'+ext))
        for parent in ancestors:
            for ext in ['json','jsonc']: apply(parent/'.opencode'/('opencode.'+ext))
        for folder in [self.user/'agents',*[parent/'.opencode/agents' for parent in ancestors]]:
            for role in ROLES:
                file=folder/(role+'.md')
                if not file.exists(): continue
                safe(file)
                if file.stat().st_size>100000: raise ConsoleError('Agent file too large.')
                front=file.read_text().split('---',2)
                if len(front)<3: continue
                for key in ['model','steps']:
                    found=re.search(r'^'+key+r':\s*(\S+)\s*$',front[1],re.M)
                    if found:
                        value=found[1]
                        if key=='model' and MODEL.fullmatch(value): result['roles'][role][key]=value
                        if key=='steps' and value.isdigit(): result['roles'][role][key]=int(value)
        result['limitations']=['Local scalar merge only; environment/remote config and provider catalog are not resolved. Confirm the running session with OpenCode debug config/agent.']
        return result
    def prepare(self,name,request):
        if not isinstance(request,dict) or set(request)-{'revision','profile','model','roles','allow_mixed','replace'}: raise ConsoleError('Unknown settings field.')
        path=self.target(name); text,config=read(path)
        if request.get('revision')!=digest(text): raise ConsoleError('Configuration changed. Reload before saving.')
        if not isinstance(config.get('agents',{}),dict) or any(not isinstance(config.get('agents',{}).get(role,{}),dict) for role in ROLES): raise ConsoleError('Invalid agent config.')
        profile=request.get('profile','balanced')
        if profile not in PROFILES and profile!='custom': raise ConsoleError('Invalid profile.')
        team=self.selected_team(name)
        roles=request.get('roles',{})
        if not isinstance(roles,dict) or set(roles)-set(ROLES): raise ConsoleError('Unknown role.')
        changes=[]; models=[]
        model=request.get('model','')
        if not isinstance(model,str) or (model and (not MODEL.fullmatch(model) or '#' in model)): raise ConsoleError('Root model needs provider/model without #variant.')
        reject_new_superseded_model(model,config.get('model',''))
        changes.append((['model'],{'present':bool(model),'value':model}))
        if model: models.append(model)
        for index,role in enumerate(ROLES):
            values=roles.get(role,{})
            if not isinstance(values,dict) or set(values)-{'model'}: raise ConsoleError('Role allows only a model selector.')
            selected=values.get('model','')
            if not isinstance(selected,str) or (selected and not MODEL.fullmatch(selected)): raise ConsoleError('Role model needs provider/model[#variant].')
            reject_new_superseded_model(selected,config.get('agents',{}).get(role,{}).get('model',''))
            if selected: models.append(selected)
            agent_path=(self.root/'.opencode/agents' if name=='project' else self.user/'agents')/(role+'.md')
            if agent_path.exists():
                safe(agent_path)
                front=agent_path.read_text().split('---',2)
                if len(front)>2 and re.search(r'^model:',front[1],re.M): raise ConsoleError(f'{role} has a Markdown model override; merge it manually first.')
            changes.append((['agents',role,'model'],{'present':bool(selected),'value':selected}))
            if profile!='custom': changes.append((['agents',role,'steps'],{'present':True,'value':PROFILES[profile][index]}))
        if profile!='custom':
            for index,item in enumerate(team,1):
                slot=f'helper-{index:02d}'
                if not isinstance(config['agents'].get(slot),dict): raise ConsoleError('Installed helper configuration is missing.')
                changes.append((['agents',slot,'steps'],{'present':True,'value':PROFILES[profile][ROLES.index(item['role'])]}))
        if (len({m.split('/',1)[0] for m in models})>1 or (not model and models)) and request.get('allow_mixed') is not True: raise ConsoleError('Mixed or inherited providers require the explicit allow checkbox.')
        updated=text; operations=[]
        for keys,wanted in changes:
            previous=get(config,keys)
            if previous['present'] and ((keys[-1]=='model' and (not isinstance(previous['value'],str) or not MODEL.fullmatch(previous['value']))) or (keys[-1]=='steps' and (type(previous['value'])!=int or previous['value']<1))): raise ConsoleError('Existing managed field has an unsupported value; merge manually.')
            if previous==wanted or (not previous['present'] and not wanted['present']): continue
            # Steps in Markdown have precedence too; reject a mismatch, no hidden changes.
            if keys[-1]=='steps':
                agent_path=(self.root/'.opencode/agents' if name=='project' else self.user/'agents')/(keys[1]+'.md')
                if agent_path.exists():
                    front=agent_path.read_text().split('---',2)
                    found=re.search(r'^steps:\s*(\d+)\s*$',front[1],re.M) if len(front)>2 else None
                    if found and int(found[1])!=wanted['value']: raise ConsoleError('Profile differs from installed Markdown steps. Apply this preset with scripts/install.py first, or select custom to keep steps.')
            updated=patch(updated,keys,wanted.get('value'),not wanted['present']); operations.append({'path':keys,'before':previous,'after':wanted})
        read_value=json.loads(scrub(updated))
        for op in operations:
            if get(read_value,op['path'])!=op['after']: raise ConsoleError('Patch validation failed.')
        preview='\n'.join(''.join(difflib.unified_diff([json.dumps(op['before'],ensure_ascii=False)+'\n'],[json.dumps(op['after'],ensure_ascii=False)+'\n'],fromfile='before '+'.'.join(op['path']),tofile='after '+'.'.join(op['path']))) for op in operations)
        return path,text,updated,operations,preview
    def preview(self,name,request):
        _,_,_,ops,diff=self.prepare(name,request); return {'diff':diff or 'Değişiklik yok.','fields':len(ops)}
    def ownership(self,path,text,replace):
        manifest=self.root/'.opencode/.bounded-orchestrator/install.json'
        if path==self.targets['project'] and manifest.exists():
            safe(manifest); _,data=read(manifest)
            if data.get('schema')!=1 or not isinstance(data.get('files'),dict): raise ConsoleError('Invalid installer manifest.')
            entry=data.get('files',{}).get('.opencode/opencode.jsonc')
            if entry and entry.get('sha256')!=digest(text) and not replace: raise ConsoleError('Installer-owned config was modified. Explicit backup and replace consent is required.')
            return manifest,data,entry
        return None,None,None
    def private_runtime(self,name):
        # Backups can contain credentials: establish the package's reserved ignore
        # sentinel before writing any state, including on an uninstalled project.
        runtime=self.statepath(name).parent
        safe(runtime)
        ignore=runtime/'.gitignore'; safe(ignore)
        expected='*\n!.gitignore\n'
        if ignore.exists():
            if not ignore.is_file() or ignore.read_text(encoding='utf-8')!=expected:
                raise ConsoleError('Reserved runtime ignore is changed; restore its managed sentinel or reinstall before saving. Existing patterns were preserved.')
        else:
            runtime.mkdir(parents=True,exist_ok=True,mode=0o700)
            atomic(ignore,expected)
        try: os.chmod(runtime,0o700)
        except OSError: pass
        return runtime
    def save(self,name,request):
        with self.lock:
            path,text,updated,ops,diff=self.prepare(name,request)
            manifest,data,entry=self.ownership(path,text,request.get('replace') is True)
            if not ops: return {'saved':False,'diff':diff}
            events=self.history(name) # Validate the private history before any config, state or manifest write.
            self.private_runtime(name)
            statepath=self.statepath(name); safe(statepath)
            backup=statepath.parent/'backups'/('console-'+secrets.token_hex(8))/path.name
            atomic(backup,text)
            atomic(statepath,json.dumps({'schema':1,'target':str(path),'backup':str(backup),'operations':ops,'saved_hash':digest(updated)},indent=2)+'\n')
            atomic(path,updated)
            if entry:
                entry['sha256']=digest(updated); atomic(manifest,json.dumps(data,indent=2,sort_keys=True)+'\n')
            self.record_history(name,request.get('profile','custom'),updated,events)
            return {'saved':True,'fields':len(ops)}
    def restore(self,name,revision):
        with self.lock:
            path=self.target(name); text,config=read(path)
            if digest(text)!=revision: raise ConsoleError('Configuration changed. Reload first.')
            statepath=self.statepath(name); _,state=read(statepath)
            if state.get('schema')!=1 or state.get('target')!=str(path) or not isinstance(state.get('operations'),list): raise ConsoleError('No console-managed save to restore.')
            updated=text
            for op in reversed(state['operations']):
                keys=op.get('path',[])
                if not (keys==['model'] or (len(keys)==3 and keys[0]=='agents' and (keys[1] in ROLES or re.fullmatch(r'helper-(?:0[1-9]|[1-4][0-9]|50)',keys[1])) and keys[2] in {'model','steps'})): raise ConsoleError('Invalid restore field.')
                if get(config,keys)!=op['after']: raise ConsoleError('A saved field changed outside the console; restore refused.')
                updated=patch(updated,keys,op['before'].get('value'),not op['before']['present'])
            json.loads(scrub(updated)); manifest,data,entry=self.ownership(path,text,True)
            events=self.history(name) # A malformed history must leave restore fully untouched.
            touched=[path,statepath,*([manifest] if entry else [])]
            before={item:item.read_bytes() if item.exists() else None for item in touched}
            applied={}
            try:
                safe(path); atomic(path,updated); applied[path]=updated.encode()
                if entry:
                    entry['sha256']=digest(updated)
                    safe(manifest); manifest_text=json.dumps(data,indent=2,sort_keys=True)+'\n'
                    atomic(manifest,manifest_text); applied[manifest]=manifest_text.encode()
                safe(statepath); statepath.unlink(); applied[statepath]=None
                self.record_history(name,self.profile_from_config(updated,name),updated,events)
            except Exception as exc:
                failed=[]
                for item,expected in reversed(list(applied.items())):
                    try:
                        safe(item)
                        current=item.read_bytes() if item.exists() else None
                        if current!=expected: raise ConsoleError('Restore file changed outside this operation.')
                        original=before[item]
                        if original is None:
                            if item.exists(): item.unlink()
                        elif current!=original: atomic(item,original.decode('utf-8'))
                    except Exception as rollback_exc: failed.append(f'{item.name}: {rollback_exc}')
                if failed: raise ConsoleError('Restore failed; rollback incomplete: '+', '.join(failed)) from exc
                raise
            return {'restored':True}

def local_project(value, default):
    path=Path(value or default).expanduser()
    if not path.is_absolute() or path.is_symlink() or not path.is_dir(): raise ConsoleError('Choose an existing local project folder.')
    return path.resolve()

def install_module(path):
    if path is None: return None
    source=Path(path)
    if source.name!='install.py' or not source.is_file(): raise ConsoleError('Distribution installer unavailable.')
    spec=importlib.util.spec_from_file_location('opencode_distribution_installer',source)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module

def team_revision(root):
    pieces=[]
    for relative in ['.opencode/opencode.jsonc','.opencode/.bounded-orchestrator/install.json','.opencode/agents/owner.md']:
        path=root/relative; safe(path); pieces.append(path.read_bytes() if path.is_file() else b'')
    return hashlib.sha256(b'\0'.join(pieces)).hexdigest()

def uninstall_plan(settings, installer, body):
    if installer is None: raise ConsoleError('Open the distribution launcher to remove this project installation.')
    if os.name=='nt': raise ConsoleError('Browser removal is unavailable on Windows; use the command-line uninstaller for this project.')
    if not isinstance(body,dict): raise ConsoleError('Invalid uninstall request.')
    project=local_project(body.get('project'),settings.root)
    if project!=settings.root: raise ConsoleError('Reopen the app and choose the other project folder.')
    manifest=project/installer.MANIFEST
    if not manifest.is_file() or manifest.is_symlink(): raise ConsoleError('No valid project installation found to remove.')
    try:
        saved=installer.load_manifest(project)
        actions=installer.uninstall(project,True)
        hashes=[]
        for name in sorted(saved['files']):
            relative=Path(name);installer.ensure_safe_parent(project,relative)
            path=project/relative
            if path.is_symlink() or (path.exists() and not path.is_file()): raise installer.InstallError(f'Refusing unsafe path: {relative}')
            hashes.append((name,installer.digest(path) if path.is_file() else None))
        agents=project/'AGENTS.md'
        fingerprint=hashlib.sha256(json.dumps([installer.digest(manifest),hashes,installer.digest(agents) if agents.is_file() else None,actions],ensure_ascii=False).encode()).hexdigest()
    except installer.InstallError as exc: raise ConsoleError(str(exc)) from exc
    return project,actions,fingerprint

def team_request(settings, installer, body, mode):
    if not isinstance(body,dict): raise ConsoleError('Invalid team request.')
    project=local_project(body.get('project'),settings.root)
    if project!=settings.root: raise ConsoleError('Reopen the app and choose the other project folder.')
    if mode=='inspect':
        manifest=project/'.opencode/.bounded-orchestrator/install.json';safe(manifest)
        saved={}
        if manifest.exists():
            _,saved=read(manifest)
            if saved.get('schema')!=1 or not isinstance(saved.get('team',[]),list): raise ConsoleError('Invalid installed team state.')
        config_path=project/'.opencode/opencode.jsonc';safe(config_path)
        _,config=read(config_path)
        current_model=config.get('model','') if isinstance(config.get('model'),str) and MODEL.fullmatch(config['model']) and '#' not in config['model'] else ''
        return {'project':str(project),'installed':bool(saved),'team':saved.get('team',[]),'profile':settings.profile_from_config(config_path.read_text(encoding='utf-8')) if saved else 'balanced','model':current_model,'revision':team_revision(project),'models':model_catalog(project),'mode':'distribution' if installer is not None else 'installed'}
    team=team_editor.validate_team(body.get('team'))
    profile=body.get('profile','balanced');model=body.get('model','')
    if profile not in team_editor.PROFILES and profile!='custom': raise ConsoleError('Invalid profile.')
    if not isinstance(model,str) or (model and (not MODEL.fullmatch(model) or '#' in model)): raise ConsoleError('Invalid chief model.')
    _,saved_config=read(project/'.opencode/opencode.jsonc')
    manifest=project/'.opencode/.bounded-orchestrator/install.json'
    _,saved_manifest=read(manifest)
    saved_team=saved_manifest.get('team',[]) if isinstance(saved_manifest.get('team',[]),list) else []
    reject_new_superseded_model(model,saved_config.get('model','') if saved_manifest else '')
    for index,item in enumerate(team):
        previous=saved_team[index].get('model','') if index<len(saved_team) and isinstance(saved_team[index],dict) else ''
        reject_new_superseded_model(item['model'],previous)
    replace=body.get('replace') is True; allow_mixed=body.get('allow_mixed') is True
    if body.get('revision')!=team_revision(project): raise ConsoleError('Project changed; review again.')
    events=settings.history('project') if mode=='save' else None
    if installer is not None:
        try:
            actions=installer.install(project,profile,replace,True,model,{},allow_mixed,team)
        except installer.InstallError as exc: raise ConsoleError(str(exc)) from exc
        if any(action.startswith('KEEP ') for action in actions): raise ConsoleError('Some files conflict. Review and confirm backup and replace.')
        if mode=='preview': return {'actions':actions,'revision':team_revision(project),'fields':sum(not action.startswith('UNCHANGED') for action in actions)}
        try: installer.install(project,profile,replace,False,model,{},allow_mixed,team,on_commit=lambda: settings.record_history('project',profile,(project/'.opencode/opencode.jsonc').read_text(encoding='utf-8'),events))
        except installer.InstallError as exc: raise ConsoleError(str(exc)) from exc
        return {'saved':True,'actions':actions}
    request={'team':team,'profile':profile,'model':model,'replace':replace,'allow_mixed':allow_mixed,'revision':None}
    try:
        plan=team_editor.prepare(project,request)
        if mode=='preview': return {'actions':plan['actions'],'revision':team_revision(project),'fields':len(plan['actions'])}
        request['revision']=plan['revision'];result=team_editor.save(project,request,on_commit=lambda: settings.record_history('project',profile,(project/'.opencode/opencode.jsonc').read_text(encoding='utf-8'),events))
        return result
    except team_editor.TeamError as exc: raise ConsoleError(str(exc)) from exc

def server(settings,port=0,fixture=None,installer=None):
    token=secrets.token_urlsafe(32)
    removal_preview={}
    removal_lock=threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def send(self,status,payload,kind='application/json; charset=utf-8'):
            body=payload.encode() if isinstance(payload,str) else json.dumps(payload,ensure_ascii=False).encode()
            self.send_response(status); self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'"); self.end_headers(); self.wfile.write(body)
        def authorized(self):
            host=f'127.0.0.1:{self.server.server_port}'
            return self.headers.get('Host')==host and (self.headers.get('Origin') in (None,'http://'+host)) and self.headers.get('X-Console-Token')==token
        def do_GET(self):
            parsed=urlsplit(self.path)
            host=f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host')!=host: return self.send(403,{'error':'Host refused.'})
            if parsed.path in {'/','/console.js','/console.css','/orchestra-actors.svg'}:
                name={'/':'console.html','/console.js':'console.js','/console.css':'console.css','/orchestra-actors.svg':'orchestra-actors.svg'}[parsed.path]
                body=(Path(__file__).parent/name).read_text(encoding='utf-8'); return self.send(200,body,{'/':'text/html; charset=utf-8','/console.js':'text/javascript; charset=utf-8','/console.css':'text/css; charset=utf-8','/orchestra-actors.svg':'image/svg+xml'}[parsed.path])
            if not self.authorized(): return self.send(403,{'error':'Session token required.'})
            try:
                query=parse_qs(parsed.query,keep_blank_values=True); target=query.get('target',['project'])[0]
                if parsed.path=='/api/settings': return self.send(200,settings.snapshot(target))
                if parsed.path=='/api/models': return self.send(200,model_catalog(settings.root))
                if parsed.path=='/api/usage':
                    try:
                        days=int(query['days'][0]) if query.get('days',[''])[0] else None
                        if query.get('breakdown',[''])[0]=='1':
                            payload=usage_report.collect_breakdown(root=settings.root,days=days,project=query.get('project',[None])[0],session=query.get('session',[None])[0] or None,fixture=fixture,history=settings.history('project'))
                        else: payload=usage_report.collect(root=settings.root,days=days,project=query.get('project',[None])[0],session=query.get('session',[None])[0] or None,fixture=fixture)
                    except (usage_report.UsageError,OSError,ValueError) as exc: payload={'platform':'opencode','status':'unavailable','source':'OpenCode CLI','error':str(exc),'records':[]}
                    return self.send(200,payload)
                return self.send(404,{'error':'Unknown endpoint.'})
            except (ConsoleError,OSError,ValueError): return self.send(400,{'error':'Configuration unavailable or invalid; inspect selected local file.'})
        def do_POST(self):
            if not self.authorized(): return self.send(403,{'error':'Origin, host or session token refused.'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=65536 or self.headers.get('Content-Type')!='application/json': raise ConsoleError('Invalid body.')
                body=json.loads(self.rfile.read(length)); name=body.get('target','project')
                if self.path=='/api/models/refresh': result=model_catalog(settings.root,refresh=True)
                elif self.path=='/api/team/inspect': result=team_request(settings,installer,body,'inspect')
                elif self.path=='/api/team/preview': result=team_request(settings,installer,body,'preview')
                elif self.path=='/api/team/save': result=team_request(settings,installer,body,'save')
                elif self.path=='/api/team/uninstall/preview':
                    project,actions,fingerprint=uninstall_plan(settings,installer,body)
                    with removal_lock:
                        removal_preview.clear()
                        preview_id=secrets.token_urlsafe(32)
                        removal_preview.update(id=preview_id,project=project,fingerprint=fingerprint,expires=time.monotonic()+300)
                    result={'project':str(project),'actions':actions,'preview_id':preview_id}
                elif self.path=='/api/team/uninstall/cancel':
                    with removal_lock: removal_preview.clear()
                    result={'cancelled':True}
                elif self.path=='/api/team/uninstall/confirm':
                    project=local_project(body.get('project'),settings.root)
                    if project!=settings.root: raise ConsoleError('Reopen the app and choose the other project folder.')
                    if body.get('confirm') is not True: raise ConsoleError('Confirm the reviewed project removal first.')
                    with removal_lock:
                        if body.get('preview_id')!=removal_preview.get('id') or project!=removal_preview.get('project') or time.monotonic()>removal_preview.get('expires',0):
                            raise ConsoleError('Removal preview expired; review again.')
                        expected=removal_preview['fingerprint'];removal_preview.clear()
                        _,actions,current=uninstall_plan(settings,installer,body)
                        if current!=expected: raise ConsoleError('Project changed; review removal again.')
                        try: installer.uninstall(project,False,expected_actions=actions)
                        except installer.InstallError as exc: raise ConsoleError(str(exc)) from exc
                    result={'removed':True,'actions':actions}
                elif self.path=='/api/close':
                    self.send(200,{'closed':True}); threading.Thread(target=self.server.shutdown,daemon=True).start(); return
                elif self.path=='/api/preview': result=settings.preview(name,body.get('settings'))
                elif self.path=='/api/save': result=settings.save(name,body.get('settings'))
                elif self.path=='/api/restore': result=settings.restore(name,body.get('revision'))
                else: return self.send(404,{'error':'Unknown endpoint.'})
                self.send(200,result)
            except (ConsoleError,OSError,ValueError,TypeError,AttributeError) as exc: self.send(400,{'error':str(exc) if isinstance(exc,ConsoleError) else 'Invalid request or local file.'})
    http=ThreadingHTTPServer(('127.0.0.1',port),Handler); http.daemon_threads=True
    return http,'http://127.0.0.1:'+str(http.server_port)+'/#'+token

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('action',nargs='?',choices=['configure','dashboard'],default='configure'); parser.add_argument('--root',type=Path,default=Path.cwd()); parser.add_argument('--port',type=int,default=0); parser.add_argument('--no-browser',action='store_true'); parser.add_argument('--fixture',type=Path); parser.add_argument('--distribution-install',action='store_true'); args=parser.parse_args(argv)
    if not args.root.is_dir() or not 0<=args.port<=65535: parser.error('Existing root and port 0..65535 required.')
    source=Path(__file__).resolve().parents[2]/'scripts/install.py' if args.distribution_install else None
    http,url=server(Settings(args.root),args.port,args.fixture,install_module(source)); print('OpenCode local console: '+url,flush=True)
    if not args.no_browser: webbrowser.open(url)
    try: http.serve_forever()
    except KeyboardInterrupt: pass
    finally: http.server_close()
    return 0
if __name__=='__main__': raise SystemExit(main())
