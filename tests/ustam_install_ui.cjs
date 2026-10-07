// Run with a preinstalled Playwright runtime; this test never executes provider jobs.
const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
 const worker = spawn(process.env.USTAM_TEST_PYTHON || 'python3', ['-u','tests/ustam_install_fixture.py'], {cwd:path.resolve(__dirname,'..')});
 let browser;
 try {
  const fixture = await new Promise((resolve,reject) => {let output='';worker.stdout.on('data',chunk=>{output+=chunk;if(output.includes('\n'))resolve(JSON.parse(output.split('\n')[0]));});worker.stderr.on('data',chunk=>process.stderr.write(chunk));worker.on('exit',code=>reject(Error('Fixture exited '+code)));});
  browser = await chromium.launch({headless:true,channel:process.env.USTAM_TEST_BROWSER || 'chrome'});
  const page = await browser.newPage({viewport:{width:1280,height:900},reducedMotion:'reduce'});
  const errors=[],posts=[];page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.method()==='POST')posts.push(new URL(request.url()).pathname);});
  await page.addInitScript(()=>{localStorage.setItem('ustam-language','en');localStorage.setItem('ustam-onboarded','1');});
  const ready = async()=>{await page.goto(fixture.origin);await page.evaluate(async ids=>{const data=await (await fetch('/api/bootstrap')).json();await fetch('/api/projects',{method:'POST',headers:{'Content-Type':'application/json','X-Ustam-CSRF':data.csrf},body:JSON.stringify({action:'selection',ids:[ids[0]],providers:['codex','claude','opencode'],revision:data.revision})});},fixture.ids);await page.reload();await page.locator('.select-project').first().waitFor();await page.locator('.select-project').first().locator('xpath=ancestor::article').getByRole('button',{name:'Change team',exact:true}).click();await page.locator('#project-orchestra-choice').getByRole('button',{name:'Close',exact:true}).click();await page.locator('.project-flow').waitFor();};
  const flow = ()=>page.locator('.project-flow');
  const motionProof=[];
  const motion=async(selector,name)=>{
   const svg=page.locator(selector).locator('.chief-actor svg');assert.equal(await svg.locator('use').getAttribute('href'),'#conductor-active');
   const sample=()=>page.evaluate(()=>{const arm=document.querySelector('#baton-arm-active');const note=document.querySelector('#conductor-active text');return {arm:arm.transform.animVal.getItem(0).matrix.a,note:note.transform.animVal.getItem(0).matrix.f,opacity:getComputedStyle(note).opacity};});
   const first=await sample();const frame=await svg.screenshot();await page.waitForTimeout(650);const next=await sample();assert.notEqual(first.arm,next.arm);assert.notEqual(first.note,next.note);assert.notEqual(first.opacity,next.opacity);assert.equal(frame.equals(await svg.screenshot()),false);
   await page.locator(selector).locator('.chief-actor svg').evaluate(svg=>{window.motionChief=svg;});await page.locator(selector).locator('.stage-helper').first().click();assert.ok(await page.locator(selector).locator('.chief-actor svg').evaluate(svg=>svg===window.motionChief));assert.equal(await page.locator(selector).locator('.actor-metadata').count(),1+await page.locator(selector).locator('.stage-helper').count());motionProof.push({surface:name,first,next});
  };
  const install = async()=>{await flow().getByRole('button',{name:'Install in project',exact:true}).click();await page.locator('#modal .result').waitFor();await page.waitForFunction(()=>!document.querySelector('#notice').classList.contains('busy'));};
  const close = ()=>page.locator('#modal .modal-actions').getByRole('button',{name:'Close',exact:true}).click();
  await ready();assert.equal(await page.locator('.job-box, .job-box textarea').count(),0);assert.equal(await page.getByRole('button',{name:'View work steps',exact:true}).count(),0);
  // Choose a ready team: saving the choice cannot install or start work.
  await flow().getByRole('button',{name:'Change team',exact:true}).click();
  await page.locator('[data-profile="balanced"]').getByRole('button',{name:'Review team',exact:true}).click();
  for(const [task,count] of [['bug',5],['game',6],['feature',4]]) {await page.locator('#project-orchestra-choice').getByLabel('Task type',{exact:true}).selectOption(task);assert.equal(await page.locator('.team-stage .stage-helper').count(),count);}
  await motion('#inline-team-preview','ready team');

  const preview=page.locator('#inline-team-preview');assert.equal(await preview.locator('.team-details[open]').count(),0);assert.equal(await preview.locator('.task-purpose:visible,.team-working-facts:visible').count(),0);
  await preview.getByLabel('Helper name',{exact:true}).fill('Existing edited helper');await preview.locator('.selected-team-control').getByLabel('Model',{exact:true}).selectOption('gpt-6-luna');await preview.locator('.selected-team-control').getByLabel('Review depth',{exact:true}).selectOption('low');
  for(let index=0;index<7;index++)await preview.getByRole('button',{name:'+ Add helper',exact:true}).click();
  assert.equal(await preview.locator('.stage-helper').count(),11);await preview.getByLabel('Helper name',{exact:true}).fill('Added specialist');await preview.locator('.selected-team-control').getByLabel('Model',{exact:true}).selectOption('gpt-6.1-sol');await preview.locator('.selected-team-control').getByLabel('Review depth',{exact:true}).selectOption('high');
  await preview.locator('.team-library summary').click();await preview.getByLabel('Name',{exact:true}).fill('Expanded ready team');await page.locator('#project-orchestra-choice').getByLabel('Task type',{exact:true}).selectOption('backend');assert.equal(await preview.locator('.stage-helper').count(),11);assert.equal(await preview.getByLabel('Name',{exact:true}).inputValue(),'Expanded ready team');assert.equal(await preview.locator('.stage-helper').first().locator('.actor-effort-value').innerText(),'Low');
  assert.equal(await page.locator('.inline-team-editor').count(),0);await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await preview.locator('.team-library summary').click();await preview.screenshot({path:'/tmp/ustam-ready-expanded-390.png'});await page.setViewportSize({width:1280,height:900});
  await page.locator('#inline-team-preview').getByRole('button',{name:'Use for this project',exact:true}).click();await flow().waitFor();
  assert.equal(fs.existsSync(path.join(fixture.root,'Alpha','.codex','config.toml')),false);
  const stored=await (await page.request.get(fixture.origin+'/api/bootstrap')).json();const expanded=stored.orchestras.find(o=>o.name==='Expanded ready team');assert.equal(expanded.helpers.length,11);assert.deepEqual([expanded.helpers[0].name,expanded.helpers[0].model,expanded.helpers[0].effort],['Existing edited helper','gpt-6-luna','low']);assert.deepEqual([expanded.helpers[10].name,expanded.helpers[10].model,expanded.helpers[10].effort],['Added specialist','gpt-6.1-sol','high']);assert.ok(expanded.concurrency<=10);
  await install();assert.match(await page.locator('#modal').innerText(),/Projects with team installed: 1 · Not completed: 0/);const manifest=JSON.parse(fs.readFileSync(path.join(fixture.root,'Alpha','.codex','.bounded-orchestrator','install.json'),'utf8'));assert.equal(manifest.team_slots.length,11);assert.equal(manifest.team_slots[10].title,'Added specialist');assert.equal(manifest.team_slots[0].effort,'low');await close();
  await flow().getByRole('button',{name:'Undo last change',exact:true}).click();await page.locator('#modal').getByRole('button',{name:'Undo last change',exact:true}).click();await page.locator('#modal .result.success').waitFor();await page.locator('#modal').getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(fs.existsSync(path.join(fixture.root,'Alpha','.codex','config.toml')),false);
  // Custom teams also save without touching the project; only Install authorizes writes.
  await flow().getByRole('button',{name:'Change team',exact:true}).click();
  await page.locator('#project-orchestra-choice').getByRole('button',{name:'Choose / create your own orchestra',exact:true}).click();
  await page.getByRole('button',{name:'Create another orchestra',exact:true}).click();
  await motion('.inline-team-editor','custom editor');
  await page.locator('.inline-team-editor').getByRole('button',{name:'Use for this project',exact:true}).click();await flow().waitFor();
  assert.equal(fs.existsSync(path.join(fixture.root,'Alpha','.codex','config.toml')),false);
  await install();assert.match(await page.locator('#modal').innerText(),/Projects with team installed: 1 · Not completed: 0/);
  assert.equal(fs.existsSync(path.join(fixture.root,'Alpha','.codex','config.toml')),true);
  await close();assert.equal(await page.getByText('Installation verified in this session',{exact:true}).count(),1);
  // A restart has no persisted receipt: do not imply filesystem installation was inspected.
  await ready();assert.equal(await page.getByText('Installation verified in this session',{exact:true}).count(),0);
  // Other native providers use the same single-click path and real adapters.
  for(const provider of ['claude','opencode']) {
   await flow().getByRole('button',{name:'Change team',exact:true}).click();
   await page.locator('#project-orchestra-choice').getByLabel('Tool',{exact:true}).selectOption(provider);
   await page.locator('#project-orchestra-choice').getByRole('button',{name:'Choose / create your own orchestra',exact:true}).click();
   await page.locator('#project-orchestra-choice').getByRole('button',{name:'Use for this project',exact:true}).last().click();await flow().waitFor();
   await install();assert.match(await page.locator('#modal').innerText(),/Projects with team installed: 1 · Not completed: 0/);await close();
  }
  // Batch authorizes only the selected targets. Conflict fails individually and is never applied.
  await ready();await page.getByRole('button',{name:'Select all',exact:true}).click();const hero=page.locator('.hero img');const heroFrame=await hero.screenshot();await page.waitForTimeout(650);assert.equal(heroFrame.equals(await hero.screenshot()),false);motionProof.push({surface:'hero',pixelsChanged:true});
  await page.locator('.selection-bar').getByRole('button',{name:'Install in project',exact:true}).click();
  await page.locator('#modal').getByLabel('Selected orchestra',{exact:true}).selectOption('codex-team');
  let appliedIds=[];
  await page.route('**/api/preview',async route=>{const response=await route.fetch();const body=await response.json();body.results[1].preview.conflicts=['fixture conflicting file'];await route.fulfill({response,json:body});});
  await page.route('**/api/apply',async route=>{appliedIds=route.request().postDataJSON().preview_ids;await route.continue();});
  await page.locator('#modal').getByRole('button',{name:'Install in project',exact:true}).click();await page.locator('#modal .result.error').waitFor();
  await page.waitForFunction(()=>document.querySelector('#modal-status').textContent.includes('Not completed: 1'));
  assert.equal(appliedIds.length,1);assert.equal(await page.locator('#modal .result.success').count(),1);assert.match(await page.locator('#modal').innerText(),/fixture conflicting file/);
  assert.equal(fs.existsSync(path.join(fixture.root,'Beta','.codex','config.toml')),false);await close();await page.unroute('**/api/preview');await page.unroute('**/api/apply');
  // Real stale-file protection survives the now automatic preview/apply sequence.
  await ready();await page.route('**/api/preview',async route=>{const response=await route.fetch();const body=await response.json();fs.appendFileSync(path.join(fixture.root,'Alpha','.codex','config.toml'),'\n# external edit\n');await route.fulfill({response,json:body});});
  await install();assert.equal(await page.locator('#modal .result.success').count(),0);assert.match(await page.locator('#modal').innerText(),/changed|conflict/i);await close();assert.equal(await page.getByText('Installation verified in this session',{exact:true}).count(),0);await page.unroute('**/api/preview');
  // Missing apply output fails closed; no receipt or installed result is invented.
  await page.route('**/api/preview',route=>route.fulfill({json:{ok:true,results:[{project_id:fixture.ids[0],ok:true,preview_id:'missing',preview:{can_apply:true}}]}}));
  await page.route('**/api/apply',route=>route.fulfill({json:{ok:true,results:[]}}));
  await install();assert.equal(await page.locator('#modal .result.success').count(),0);assert.match(await page.locator('#modal .result.error').innerText(),/No verified installation result/);await close();
  await page.locator('#nav').getByRole('button',{name:'My orchestras',exact:true}).click();await page.locator('.card').filter({has:page.getByRole('heading',{name:'codex team',exact:true})}).getByRole('button',{name:'Edit',exact:true}).click();await motion('#modal','saved orchestra editor');await page.locator('#modal').getByRole('button',{name:'Cancel',exact:true}).click();
  assert.equal(posts.some(url=>url.startsWith('/api/jobs')),false);assert.deepEqual(errors,[]);
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  console.log(JSON.stringify({ok:true,motionProof,browser:browser.version(),cases:['ready helpers add/edit/save/install11 slots','ready/custom choices save only','task rosters 4/5/6','single-click real codex/claude/opencode install','session receipt invalidation','batch partial conflict','real stale preview rejection','missing apply result fails closed','no job POST','mobile overflow'],postCount:posts.length}));
 } finally {if(browser)await browser.close();worker.kill('SIGINT');}
})().catch(error=>{console.error(error);process.exitCode=1;});
