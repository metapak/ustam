/* Native-provider records only. This view never creates work or invokes a model. */
const canonicalHash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const hashesMatch=(left,right)=>canonicalHash(left)&&canonicalHash(right)&&left===right;
export function humanApproved(work) {
 return work.approval_required===false&&work.approval_basis==='trusted_ustam_control'&&hashesMatch(work.approved_hash,work.contract_hash);
}
export function evidenceState(work) {
 const c=work.candidate;
 const bound=!!c&&canonicalHash(c.hash)&&hashesMatch(c.contract_hash,work.contract_hash)&&hashesMatch(c.pack_hash,work.test_pack_hash)&&canonicalHash(work.policy_hash);
 const current=r=>bound&&!!r&&canonicalHash(r.runner_hash)&&hashesMatch(r.candidate_hash,c.hash)&&hashesMatch(r.contract_hash,work.contract_hash)&&hashesMatch(r.test_pack_hash,work.test_pack_hash)&&hashesMatch(r.policy_hash,work.policy_hash);
 const receipts=Array.isArray(work.receipts)?work.receipts:[];
 const observed=receipts.filter(r=>r?.provenance==='controller_observed'&&r.runner==='local_work_protocol'&&r.role==='verifier'&&current(r));
 const passed=observed.some(r=>r.status==='passed');
 const integration=work.integration;
 const combined=passed&&integration?.status==='passed'&&integration.provenance==='controller_observed'&&integration.runner==='local_work_protocol'&&canonicalHash(integration.main_hash)&&canonicalHash(integration.combined_tree_hash)&&observed.some(r=>r.status==='passed'&&hashesMatch(r.runner_hash,integration.runner_hash))&&hashesMatch(integration.candidate_hash,c?.hash)&&hashesMatch(integration.contract_hash,work.contract_hash)&&hashesMatch(integration.policy_hash,work.policy_hash)&&hashesMatch(integration.pack_hash,work.test_pack_hash);
 return {passed,combined,failed:observed.some(r=>r.status==='failed'),reported:receipts.some(r=>r?.provenance==='helper_reported'),stale:receipts.some(r=>r?.provenance==='controller_observed'&&!current(r)),ready:work.status==='ready_to_apply'&&work.approval_required===false&&combined&&canonicalHash(work.integration_hash)};
}
export function decisionGroups(works) {
 const groups=new Map();
 for(const work of works)for(const question of work.questions||[]){
  const key=question.key||JSON.stringify([question.question,question.versions]);
  const group=groups.get(key)||{work,question,answered:false};
  group.answered ||= question.answer!==null&&question.answer!==undefined;
  groups.set(key,group);
 }
 return [...groups.values()].filter(g=>!g.answered);
}
export function mountWorks({host,request,lang,projects,providers,node,button,heading}) {
 const tr=lang==='tr';const text=(a,b)=>tr?a:b;
 const statusNames={contract:text('Kapsam onayı bekliyor','Scope approval pending'),implementing:text('Çalışma alanı hazır','Workspace prepared'),candidate:text('Kontrol gerekli','Check required'),checked:text('Birleşik kontrol gerekli','Integration check required'),ready_to_apply:text('Hazır','Ready'),applied:text('Uygulandı','Applied'),cancelled:text('Kaydı iptal edildi','Record cancelled'),candidate_changed:text('Değişiklik yeniden kontrol edilmeli','Changed candidate needs checking'),unsupported:text('Bu kontrol çalıştırılamadı','This check could not run'),needs_attention:text('Kontrol gerekli','Check required')};
 let works=[],loading=false;const cards=new Map(),pending=new Map(),answerDrafts=new Map(),retries=new Map();
 const notice=node('p','hint');notice.setAttribute('role','status');notice.setAttribute('aria-live','polite');
 const refresh=button(text('Yenile','Refresh'),()=>load());
 const list=node('div','works-list'),decisions=node('section','works-decisions');
 const intro=node('div','works-guide panel');const art=node('img','works-art');art.src='/orchestra.svg';art.alt='';
 const copy=node('div');copy.append(node('h2','section-title',text('İşi aracınızda yazın','Write the task in your tool')),node('p','hint',text('Ekibi projenize kurun, ardından Codex, Claude Code veya OpenCode içinde görevi yazın. Şef kapsamı ve başarı ölçütlerini burada paylaşır; yardımcılar ayrı çalışma alanında uygular ve kontrol eder.','Install the team in your project, then write the task in Codex, Claude Code or OpenCode. The chief publishes scope and success criteria here; helpers implement and verify in a separate workspace.')));const guide=node('details','work-details');guide.append(node('summary','',text('Çalışmamı burada nasıl görürüm?','How does my work appear here?')),node('p','hint',text('Projelerim’den ekibinizi kurun. Codex, Claude Code veya OpenCode komut satırı aracını aynı proje klasöründe açın; ayarları yeniden yükleyin veya yeni sohbet başlatın. İşi o sohbet içinde yazın. Araç kapsamı paylaştıktan sonra buradaki Yenile düğmesine basın.','Install your team from My projects. Open the Codex, Claude Code or OpenCode command-line tool in that project folder; reload settings or start a new conversation. Write the task in that conversation. Refresh here after the tool publishes the scope.')));copy.append(guide);intro.append(copy,art);
 host.replaceChildren(heading(text('Çalışmalar','Works'),text('Aracınızın paylaştığı kapsam, kararlar ve doğrulanmış sonuçlar.','Scope, decisions and verified results published by your tool.'),[refresh]),intro,notice,decisions,list);
 const attached=()=>host.contains(list);
 const projectName=id=>projects.find(p=>p.id===id)?.name||id;
 function controlsDisabled(card,value){card.querySelectorAll('button,input,textarea,select').forEach(el=>el.disabled=value||el.dataset.ineligible==='true');}
 async function act(work,action,extra={}){
  if(pending.has(work.id))return;
  const input={action,id:work.id,version:work.version,...extra};const retryKey=JSON.stringify(input);const payload={...input,operation_id:retries.get(retryKey)||crypto.randomUUID()};retries.set(retryKey,payload.operation_id);
  pending.set(work.id,payload);refresh.disabled=true;const card=cards.get(work.id)?.element;if(card)controlsDisabled(card,true);decisions.querySelectorAll('button,input,textarea,select').forEach(el=>el.disabled=true);
  notice.textContent=text('İşlem sürüyor…','Working…');
  try{const response=await request('/api/works',payload);if(!response.work)throw Error(text('Çalışma sonucu alınamadı.','Work result missing.'));retries.delete(retryKey);works=works.map(w=>w.id===work.id?response.work:w);notice.textContent=text('Kayıt güncellendi.','Record updated.');}
  catch(error){notice.textContent=text('İşlem tamamlanamadı. Güncel kayıtları kontrol edin. ','Action failed. Review refreshed records. ')+error.message;await load(true);}
  finally{pending.delete(work.id);refresh.disabled=loading||pending.size>0;if(attached())draw();}
 }
 function renderCard(work){
  const card=node('article','card work-card');card.dataset.workId=work.id;const proof=evidenceState(work);
  const top=node('div','split-line');top.append(node('h2','section-title',projectName(work.project_id)),node('span','badge',(work.status==='contract'&&!work.approval_required?text('Kapsam paylaşıldı','Scope published'):work.status==='ready_to_apply'&&!proof.ready?text('Kontrol gerekli','Check required'):statusNames[work.status])||text('Kontrol gerekli','Check required')));
  card.append(top,node('p','hint',providers[work.provider]||work.provider),node('h3','work-scope',work.contract?.scope||text('Kapsam eksik','Scope missing')));
  const criteria=node('ul','work-criteria');for(const item of work.contract?.criteria||[])criteria.append(node('li','',item));card.append(node('strong','',text('Başarı ölçütleri','Success criteria')),criteria);
  const facts=node('div','work-facts');for(const value of [work.approval_required?text('Kapsam onayı gerekli','Scope approval required'):humanApproved(work)?text('Kapsamı siz onayladınız','Scope approved by you'):text('Aracın çalışma kapsamı paylaşıldı','Tool work scope published'),work.test_pack?text('Kabul testleri hazır','Acceptance tests prepared'):text('Kabul testleri henüz paylaşılmadı','Acceptance tests not published'),work.workspace?text('Ayrı çalışma alanı hazırlanmış','Separate workspace prepared'):text('Ayrı çalışma alanı henüz hazırlanmadı','Separate workspace not prepared'),proof.passed?text('Bağımsız kontrol çalıştırıldı: geçti','Independent check ran: passed'):proof.failed?text('Bağımsız kontrol çalıştırıldı: başarısız','Independent check ran: failed'):text('Güncel, çalıştırılmış kontrol kanıtı yok','No current executed check evidence'),proof.combined?text('Birleşik proje kontrolü: geçti','Combined project check: passed'):text('Birleşik proje kontrolü gerekli','Combined project check required')])facts.append(node('p','',value));
  if(proof.reported)facts.append(node('p','hint',text('Yardımcı raporu var; çalıştırılmış kontrol kanıtı değildir.','Helper report exists; it is not executed check evidence.')));
  if(proof.stale)facts.append(node('p','error-text',text('Önceki kontrol başka bir değişikliğe ait; yeniden kontrol gerekli.','Earlier check belongs to another candidate; recheck required.')));
  if(work.message)facts.append(node('p','hint',work.message));card.append(facts);
  const actions=node('div','actions');const unresolved=decisionGroups(works).some(g=>Object.hasOwn(g.question.versions||{},work.id));
  if(!['cancelled','applied'].includes(work.status)&&work.approval_required===true){const commands=work.contract?.commands||[];if(commands.length)card.append(node('p','hint',text('Onayla izin vereceğiniz komutlar: ','Commands you will authorize: ')+commands.map(argv=>argv.join(' ')).join(' · ')));actions.append(button(text('Bu kapsamı onayla','Approve this scope'),()=>act(work,'approve',{contract_hash:work.contract_hash}),'primary'));}
  if(proof.ready){const apply=button(text('Bu değişiklikleri projeye uygula','Apply these changes to project'),()=>act(work,'apply',{approve:true,integration_hash:work.integration_hash}),'primary',unresolved);apply.dataset.ineligible=String(unresolved);actions.append(apply);}
  if(work.status==='cancelled')actions.append(button(text('Kaydı sürdür','Resume record'),()=>act(work,'resume')));
  else if(work.status!=='applied')actions.append(button(text('Kaydı iptal et','Cancel record'),()=>act(work,'cancel')));
  card.append(actions,node('p','hint',text('İptal yalnızca bu çalışma kaydını değiştirir; aracınızdaki çalışan oturumu durdurmaz.','Cancellation changes this work record; it does not stop a running tool session.')));
  const detail=node('details','work-details');detail.append(node('summary','',text('Teknik ayrıntılar','Technical details')),node('pre','',JSON.stringify({files:work.contract?.files,commands:work.contract?.commands,workspace:work.workspace,candidate:work.candidate,receipts:work.receipts,integration:work.integration,events:work.events,pending_operations:work.pending_operations},null,2)));card.append(detail);if(pending.has(work.id)||(work.pending_operations||[]).length)controlsDisabled(card,true);return card;
 }
 function draw(){
  if(!attached())return;
  const groups=decisionGroups(works);decisions.replaceChildren();decisions.hidden=!groups.length;
  if(groups.length)decisions.append(node('h2','section-title',text('Yanıt bekliyor','Awaiting answer')));
  for(const {work,question} of groups){const panel=node('div','panel work-question');panel.append(node('h3','',question.question));const affected=Object.keys(question.versions||{});panel.append(node('p','hint',text('Etkilenen çalışmalar: ','Affected works: ')+affected.map(id=>works.find(w=>w.id===id)?.contract?.scope||id).join(' · ')));
   const stale=affected.some(id=>works.find(w=>w.id===id)?.version!==question.versions[id]);
   const draftKey=question.key||JSON.stringify([question.id,question.versions]);const answer=node('textarea');answer.value=answerDrafts.get(draftKey)||'';answer.maxLength=2000;answer.rows=2;answer.setAttribute('aria-label',text('Bu karara yanıtınız','Your answer to this decision'));panel.append(answer);
   if(Array.isArray(question.options)){const options=node('div','actions');for(const option of question.options){if(typeof option==='string')options.append(button(option,()=>{answer.value=option;answer.dispatchEvent(new Event('input'));},'small',stale));}panel.append(options);}
   const send=button(text('Yanıtı paylaş','Share answer'),()=>{if(answer.value.trim())act(work,'answer',{question_id:question.id,answer:answer.value.trim()});},'primary',stale||pending.size>0||!answer.value.trim());answer.disabled=stale||pending.size>0;answer.addEventListener('input',()=>{answerDrafts.set(draftKey,answer.value);send.disabled=stale||pending.size>0||!answer.value.trim();});panel.append(send);if(stale)panel.append(node('p','error-text',text('Bu soru eski kapsama ait. Aracınızda güncel soruyu paylaşın.','This question refers to an old scope. Publish an updated question in your tool.')));decisions.append(panel);
  }
  for(const [id,entry] of cards)if(!works.some(w=>w.id===id)){entry.element.remove();cards.delete(id);}
  list.querySelector('.works-empty')?.remove();
  if(!works.length)list.append(node('p','empty works-empty',text('Henüz paylaşılmış çalışma yok. Kurulum çalışma oluşturmaz. Görevinizi proje klasöründe açtığınız araçta yazın; araç kayıt paylaştıktan sonra Yenile seçin.','No published works yet. Installation does not create work. Write your task in the tool opened in your project folder, then refresh after the tool publishes a record.')));
  for(const work of works){const signature=JSON.stringify([work,pending.has(work.id),groups.map(g=>g.question.key||g.question.id)]);const prior=cards.get(work.id);if(prior?.signature===signature)continue;const element=renderCard(work);if(prior){const open=prior.element.querySelector('details')?.open;prior.element.replaceWith(element);element.querySelector('details').open=!!open;}else list.append(element);cards.set(work.id,{signature,element});}
 }
 async function load(keepNotice=false){if(loading||(pending.size&&!keepNotice))return;loading=true;refresh.disabled=true;if(!keepNotice)notice.textContent=text('Kayıtlar okunuyor…','Reading records…');try{const response=await request('/api/works');if(response.schema_version!==1||!Array.isArray(response.works))throw Error(text('Çalışma kayıt biçimi desteklenmiyor.','Work record format is unsupported.'));works=response.works;if(!keepNotice)notice.textContent='';draw();}catch(error){notice.textContent=text('Çalışmalar okunamadı. ','Could not read works. ')+error.message;}finally{loading=false;refresh.disabled=pending.size>0;}}
 load();return {refresh:load};
}
