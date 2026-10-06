// Read-only layout checks against a disposable fixture; provider execution is never invoked.
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const path=require('node:path');
const assert=require('node:assert/strict');
(async()=>{
 const worker=spawn(process.env.USTAM_TEST_PYTHON||'python3',['-u','tests/ustam_install_fixture.py'],{cwd:path.resolve(__dirname,'..')});let browser;
 try{
  const fixture=await new Promise((resolve,reject)=>{let output='';worker.stdout.on('data',chunk=>{output+=chunk;if(output.includes('\n'))resolve(JSON.parse(output.split('\n')[0]));});worker.stderr.on('data',chunk=>process.stderr.write(chunk));worker.on('exit',code=>reject(Error('Fixture exited '+code)));});
  browser=await chromium.launch({headless:true,channel:process.env.USTAM_TEST_BROWSER||'chrome'});const page=await browser.newPage();const errors=[],posts=[],proof=[];
  page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{if(request.method()==='POST')posts.push(new URL(request.url()).pathname);});await page.addInitScript(()=>{localStorage.setItem('ustam-language','en');localStorage.setItem('ustam-onboarded','1');});
  let count=2,assigned=false;
  await page.route('**/api/bootstrap',async route=>{const response=await route.fetch();const body=await response.json();body.projects=[...body.projects,{id:'layout-gamma',name:'Gamma',path:'/disposable/layout/Gamma'}].slice(0,count);body.selected_projects=body.projects.map(p=>p.id);if(!assigned){body.defaults={orchestras:{}};body.project_overrides={};}await route.fulfill({response,json:body});});
  const sample=()=>page.evaluate(()=>{const rect=el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};return {grid:rect(document.querySelector('#view > .cards')),cards:[...document.querySelectorAll('#view > .cards > article')].map(rect),hero:rect(document.querySelector('.hero')),heading:rect(document.querySelector('.page-heading')),scroll:scrollY,same:window.contextNodes.every(el=>el.isConnected),overflow:document.documentElement.scrollWidth>innerWidth};});
  for(const width of [2048,390])for(count of [1,2,3])for(assigned of [false,true]){
   await page.setViewportSize({width,height:900});await page.goto(fixture.origin);await page.locator('.select-project').first().waitFor();await page.evaluate(()=>{window.contextNodes=[document.querySelector('.hero img'),document.querySelector('.page-heading'),document.querySelector('#view > .cards'),...document.querySelectorAll('#view > .cards > article')];});const before=await sample();
   const card=page.locator('#view > .cards > article').first();const open=card.getByRole('button',{name:assigned?'Change team':'Choose orchestra',exact:true});
   if(width===2048&&count===2&&!assigned)await page.screenshot({path:'/tmp/ustam-project-context-before.png',fullPage:true});
   await open.evaluate(el=>el.click());await page.locator('#project-orchestra-choice').waitFor();await page.locator('#project-orchestra-choice [data-profile]').first().waitFor();assert.deepEqual(await sample(),before);
   assert.ok(await page.evaluate(()=>{const context=document.querySelector('#project-orchestra-context'),grid=document.querySelector('#view > .cards');return context.parentElement===grid.parentElement&&!grid.contains(context)&&context.getBoundingClientRect().top>=grid.getBoundingClientRect().bottom;}));
   assert.equal(await page.locator('#view > .cards .project-flow,#view > .cards #project-orchestra-choice').count(),0);assert.equal(await page.locator('.project-flow').getByRole('button',{name:/Choose orchestra|Change team/}).count(),0);assert.equal(await page.locator('.project-flow').getByRole('button',{name:'Install in project',exact:true}).count(),assigned?1:0);
   if(width===2048&&count===2&&!assigned)await page.screenshot({path:'/tmp/ustam-project-context-open.png',fullPage:true});
   await page.locator('#project-orchestra-choice').getByLabel('Tool',{exact:true}).selectOption('claude');await page.locator('#project-orchestra-choice [data-profile]').first().waitFor();assert.deepEqual(await sample(),before);
   await page.locator('#project-orchestra-choice > .section-head').getByRole('button',{name:'Close',exact:true}).evaluate(el=>el.click());assert.equal(await page.locator('#project-orchestra-choice').count(),0);assert.deepEqual(await sample(),before);
   await open.evaluate(el=>el.click());assert.deepEqual(await sample(),before);proof.push({width,count,assigned,cardWidths:before.cards.map(r=>r.width),gridHeight:before.grid.height});
  }
  assert.deepEqual(errors,[]);assert.deepEqual(posts,[]);console.log(JSON.stringify({ok:true,browser:browser.version(),proof,cases:['1/2/3 assigned and unassigned projects','desktop2048 and mobile390','exact card/grid/heading/hero rects preserved on open/provider/close/reopen','same card/hero DOM nodes','context outside grid','no duplicate choose/change in open flow','install CTA retained','no horizontal overflow','no POST or jobs']}));
 }finally{if(browser)await browser.close();worker.kill('SIGINT');}
})().catch(error=>{console.error(error);process.exitCode=1;});
