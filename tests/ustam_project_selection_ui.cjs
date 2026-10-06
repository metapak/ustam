// Disposable fixture: selections and installs never touch user projects or run provider jobs.
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const path=require('node:path');
const assert=require('node:assert/strict');
(async()=>{
 const worker=spawn(process.env.USTAM_TEST_PYTHON||'python3',['-u','tests/ustam_install_fixture.py'],{cwd:path.resolve(__dirname,'..')});let browser;
 try{
  const fixture=await new Promise((resolve,reject)=>{let output='';worker.stdout.on('data',chunk=>{output+=chunk;if(output.includes('\n'))resolve(JSON.parse(output.split('\n')[0]));});worker.stderr.on('data',chunk=>process.stderr.write(chunk));worker.on('exit',code=>reject(Error('Fixture exited '+code)));});
  browser=await chromium.launch({headless:true,channel:process.env.USTAM_TEST_BROWSER||'chrome'});
  const page=await browser.newPage({viewport:{width:1600,height:700}});const errors=[],requests=[];
  page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>requests.push({method:request.method(),path:new URL(request.url()).pathname}));
  await page.addInitScript(()=>{localStorage.setItem('ustam-language','en');localStorage.setItem('ustam-onboarded','1');});await page.goto(fixture.origin);await page.locator('.select-project').first().waitFor();
  const boxes=page.locator('.select-project');const cards=page.locator('#view > .cards > article');const settle=()=>page.waitForFunction(()=>!document.querySelector('.select-project').disabled);
  await boxes.nth(1).check();await settle();await page.evaluate(()=>{window.selectionHero=document.querySelector('.hero img');window.selectionCards=[...document.querySelectorAll('#view > .cards > article')];window.scrollTo(0,150);});
  const sample=()=>page.evaluate(()=>({hero:!!document.querySelector('.hero'),panel:!!document.querySelector('#project-orchestra-choice'),flows:document.querySelectorAll('.project-flow').length,widths:[...document.querySelectorAll('#view > .cards > article')].map(el=>el.getBoundingClientRect().width),scroll:scrollY,heroSame:window.selectionHero===document.querySelector('.hero img'),cardsSame:window.selectionCards.every((el,i)=>el===document.querySelectorAll('#view > .cards > article')[i])}));
  const before=await sample();const baselineRequests=requests.length;
  for(const checked of [false,true,false,true]){await boxes.nth(1).setChecked(checked);await settle();assert.deepEqual(await sample(),before);assert.equal(await page.locator('#project-selection-count').innerText(),checked?'2 projects selected':'1 projects selected');}
  assert.equal(requests.slice(baselineRequests).some(r=>r.path==='/api/models'),false);
  await cards.first().getByRole('button',{name:'Change team',exact:true}).click();await page.locator('[data-profile="balanced"] button').waitFor();await page.locator('[data-profile="balanced"] button').click();await page.locator('#inline-team-preview').waitFor();
  const preview=page.locator('#inline-team-preview');await preview.locator('.team-library summary').click();await preview.getByLabel('Name',{exact:true}).fill('Retained selection draft');
  await page.locator('.chief-actor svg').evaluate(el=>window.selectionChief=el);const openBaseline=requests.length;
  await boxes.nth(1).uncheck();await settle();assert.equal(await page.locator('#project-orchestra-choice').count(),1);assert.ok(await page.locator('.chief-actor svg').evaluate(el=>el===window.selectionChief));
  await boxes.first().uncheck();await settle();assert.equal(await page.locator('#project-orchestra-choice,.project-flow').count(),0);assert.equal(await page.locator('#project-selection-install').isDisabled(),true);
  await boxes.first().check();await settle();assert.equal(await page.locator('#project-orchestra-choice,.project-flow').count(),0);assert.equal(requests.slice(openBaseline).some(r=>r.path==='/api/models'),false);
  await cards.first().getByRole('button',{name:'Change team',exact:true}).click();assert.equal(await page.locator('#inline-team-preview').getByLabel('Name',{exact:true}).inputValue(),'Retained selection draft');
  await page.locator('#project-orchestra-choice').getByRole('button',{name:'Close',exact:true}).first().click();await boxes.first().uncheck();await settle();assert.equal(await page.locator('.project-flow').count(),0);
  // An explicitly opened unselected project keeps its context when another target changes.
  await cards.first().getByRole('button',{name:'Change team',exact:true}).click();await boxes.nth(1).check();await settle();await boxes.nth(1).uncheck();await settle();assert.equal(await page.locator('#project-orchestra-choice').count(),1);await boxes.first().check();await settle();await boxes.first().uncheck();await settle();assert.equal(await page.locator('#project-orchestra-choice,.project-flow').count(),0);
  await page.setViewportSize({width:390,height:844});await page.evaluate(()=>{window.selectionHero=document.querySelector('.hero img');window.selectionCards=[...document.querySelectorAll('#view > .cards > article')];window.scrollTo(0,150);});const mobile=await sample();await boxes.first().check();await settle();assert.deepEqual(await sample(),mobile);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  // Failure reconciles the checkbox without remounting the project screen.
  await page.route('**/api/projects',route=>route.fulfill({status:409,json:{ok:false,error:{message:'Preview expired or metadata changed; preview again'}}}));await boxes.nth(1).check();await settle();assert.equal(await boxes.nth(1).isChecked(),false);assert.equal(await boxes.first().isChecked(),true);assert.equal(await page.locator('#project-selection-count').innerText(),'1 projects selected');assert.ok(await page.evaluate(()=>window.selectionHero===document.querySelector('.hero img')));
  assert.deepEqual(errors,[]);assert.equal(requests.some(r=>r.method==='POST'&&['/api/jobs','/api/preview','/api/apply','/api/defaults','/api/orchestras'].includes(r.path)),false);
  console.log(JSON.stringify({ok:true,browser:browser.version(),before,mobile,cases:['2→1→2 selection keeps hero/cards/scroll/flow stable','checkbox sends no model request','explicit panel','other deselection preserves chief SVG','target deselection clears panel/context','draft retained on explicit reopen','mobile stable/no overflow','failure rollback','no jobs or project-file writes']}));
 }finally{if(browser)await browser.close();worker.kill('SIGINT');}
})().catch(error=>{console.error(error);process.exitCode=1;});
