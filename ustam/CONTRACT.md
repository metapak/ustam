# Ustam hub contract / Ustam hub sözleşmesi

The loopback server returns JSON `{ok:true,...}` or `{ok:false,error:{code,message}}`.
GET `/api/bootstrap` returns `csrf`, `revision`, `providers` (codex, claude, opencode, antigravity), `defaults`, `selected_providers`, `roots`, `projects`, `orchestras`, `selected_projects`. Projects: `{id,path,name,is_git}`. Orchestra v1: `{id,name,provider,chief:{model,effort},helpers:[{id,role,name,model,effort}],concurrency,profile}`. No provider mapping is inferred.

Döngü sunucusu aynı JSON sözleşmesini kullanır. Keşif ve varsayılan değişiklikleri proje dosyalarına yazmaz; sağlayıcı eşlemesi çıkarılmaz.

All POST requests require `X-Ustam-CSRF` from bootstrap and the exact server Origin. Bodies are JSON objects, at most 1 MiB. Metadata writes may provide `revision` for optimistic locking. Routes:
- `/api/projects`: `{action:"refresh"|"add"|"remove"|"selection", roots?:[path],path?:path,id?:id,ids?:[id],providers?:[provider]}`. refresh explicitly replaces roots if supplied.
- `/api/orchestras`: `{action:"save"|"remove",orchestra?:object,id?:id}`.
- `/api/defaults`: `{defaults?:object,selected_providers?:[provider],project_id?:id,override?:object}`. Project override must explicitly name a registered project.
- `/api/preview`: `{project_ids:[id],provider,payload:object}` => `results:[{project_id,ok,preview_id,preview}|{project_id,ok:false,error}]`. The hub preview_id binds the metadata revision and adapter preview; stale previews fail.
- `/api/apply`: `{preview_ids:[id]}` => per-project `results`. No global atomic guarantee.
- `/api/restore`: `{project_ids:[id],provider,payload:object}` => per-project `results`.
- `/api/usage`: POST `{project_ids:[id],provider}`; GET accepts `project_id` and `provider`.
- `/api/models`: GET `provider` and optional `project_id`.
- `/api/jobs`: GET returns `{jobs:[...]}`; POST `{action:"plan"|"start"|"cancel"|"resume",...}`. GET `/api/jobs/<id>` returns `{job:...}`.

AdapterManager: no-argument construction, inspect(provider,target), preview(provider,target,payload), apply(provider,target,preview_id), restore(provider,target,payload), usage(provider,target), models(provider,target), close(). target is canonical registered project path (string). Results are normalized dictionaries; preview result MUST include `preview_id`. AdapterManager owns worker/session retention. `python -m ustam --adapter ...` forwards remaining arguments to `ustam.adapters.worker_main(argv)` (alias adapter_main/main accepted).

JobManager: construction `JobManager(state_dir, adapters)`; list(), get(id), plan(payload), start(id), cancel(id), resume(id), close(). Dictionaries returned. Hub resolves project_ids to canonical `targets:[{project_id,path}]` before plan and rejects unregistered IDs. JobManager must not independently accept arbitrary target paths from HTTP. Jobs writer may negotiate constructor differences before integration.

UI resides under `ustam/ui`, loads bootstrap before writes, sends the CSRF header plus browser Origin, and renders all messages as text. Server binds 127.0.0.1 by default and never loads project code.

Catalog example: `GET /api/models?provider=claude` returns `{ok:true,result:{provider:"claude",status:"unverified",models:[{id:"opus",efforts:["low","medium","high"],origin:"backend_alias"}],limitations:["Account access is unverified"]}}`. Project context is optional for models; the adapter owns a private scratch catalog context when absent. Usage requires a registered project. Preview payload is the direct generic orchestra object; a nested `{orchestra:...}` is normalized for compatibility. OpenCode effort may be an explicitly empty string where the engine has no effort mapping. Final capability validation occurs in the provider adapter.

Configuration apply/restore take the jobs manager lock and refuse any path in jobs.active. Jobs start takes that same lock, closing the check/start race.

POST `/api/projects/pick` accepts `{}` or `{kind:"directory"}` under the same exact Origin/Host/CSRF rules and returns `{ok:true,path:"/canonical/folder",cancelled:false}` or `{ok:true,path:null,cancelled:true}`. Selection does not register a project or change metadata. Native fixed-argument OS dialogs have a 120-second timeout; unavailable dialogs return the ordinary error envelope and the UI offers manual path entry. No browser-supplied arguments enter a subprocess. Tests mock dialogs; actual OS presentation requires native QA. Bound hub previews expire after 300 seconds, matching the engine preview lifetime.

Bootstrap advertises `capabilities.native_picker.endpoint` as `/api/projects/pick`, jobs availability, and per-provider maximum configuration concurrency. Picker existence does not promise desktop dialog availability; failed OS launches return a manual-path fallback error.

Job start/resume revalidate the saved project ID and canonical path against the current registry. Removal or redirected paths require a new plan. Cancellation remains available for an existing job after registry removal.

Metadata operations use a stable private `.hub.lock` with POSIX flock / Windows byte-range locking and reload `hub.json` while holding it. Independent hub instances see current committed state and stale revisions are refused. Start/resume hold the same metadata lock across project validation and the jobs transition/prepare; lock order is metadata then jobs. This prevents registry removal from interleaving with a validated transition.

Antigravity uses the same generic orchestra and six adapter methods. Its model catalog contains only `inherit`, `flash`, `pro`; chief/helper effort is an explicit empty string. `max_concurrency:1` is a compatibility/policy limit, not verified native enforcement. Catalog and inspect advertise `capabilities:{preview:true,apply:true,restore:true,jobs:false,usage:false,role_effort:false,native_concurrency_limit:false,max_concurrency:1}` and `support_matrix` with separate CLI and legacy IDE evidence. CLI status is `supported`, `missing`, or `unsupported`; local version/help probes never start a model call. Versions below 1.2.16 or unknown versions are refused; newer versions need the same capability probe. Bootstrap provider job capabilities include `enabled:false` and a reason; Jobs plan/prepare refuse Antigravity. Usage is `status:unsupported`, `source:none`, `totals:{}`, `records:[]`; no measured zero is implied.

Antigravity preview results bind `root_identity:{device,inode}` in both the worker pending preview and Hub preview record; apply refuses a replaced project directory before invoking any new worker. Installed manifests bind the same identity for fresh-worker restore. Lost worker-local previews must be recreated. Antigravity preview results include `files`/`changes` entries `{path,action}` and a completed `preview` phase. Successful apply returns `applied:true,verified:true,backup_id,phases:[{phase:"preflight",status:"completed"},{phase:"backup",status:"completed"},{phase:"applied",status:"completed"},{phase:"verified",status:"completed"}]`. These are completed operation receipts, not simulated progress or native execution guarantees. Restore returns `restored:true,verified:true` with completed `restore` and `verified` phases. Typed provider failures preserve the ordinary `error` string and may additionally include per-project `stage` and `recovery_status` (`not_started`, `rolled_back` or `conflicts_retained`). No completed verification receipt is emitted on failure. The native fixed bridge uses `--work-protocol antigravity`; it exposes the same manual local protocol and no trusted human approval/apply action.
