import { KineticEngine } from './engine.js';
import { samples } from './samples.js';

const $ = (selector, root=document) => root.querySelector(selector);
const main=$('#main'), overlay=$('#overlay');
const icon = (name, size=20) => {
  const paths={
    arrow:'<path d="M5 12h14m-6-6 6 6-6 6"/>', diagonal:'<path d="M6 18 18 6M6 6h12v12"/>',
    sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
    moon:'<path d="M20 14A9 9 0 0 1 10 4a9 9 0 1 0 10 10Z"/>',
    pause:'<path d="M8 5v14M16 5v14"/>', play:'<path d="m8 5 11 7-11 7V5Z"/>',
    command:'<path d="M8 8V5a3 3 0 1 0-3 3h14a3 3 0 1 0-3-3v14a3 3 0 1 0 3-3H5a3 3 0 1 0 3 3V8Z"/>',
    plus:'<path d="M12 5v14M5 12h14"/>', check:'<path d="m5 12 4 4L19 6"/>',
    close:'<path d="m6 6 12 12M6 18 18 6"/>', layers:'<path d="m12 3 10 5-10 5L2 8l10-5Zm-10 9 10 5 10-5M2 16l10 5 10-5"/>',
    shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 11 3 3 5-5"/>',
    branch:'<circle cx="6" cy="5" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="6" cy="19" r="2"/><path d="M6 7v10m12-9c0 5-12 5-12 9"/>',
    folder:'<path d="M3 6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10H3V6Z"/>',
    search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
    code:'<path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16"/>',
    clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    terminal:'<path d="m5 7 5 5-5 5m8 0h6"/>',
  };
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.arrow}</svg>`;
};
const escape = value => String(value).replace(/[&<>"']/g, char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const phases=['Plan','Implement','Approve','Verify','Review'];
const explanations=[
  ['Intent, made exact.','A bounded plan. Explicit outputs. Nothing left implicit.'],
  ['Intelligence, in motion.','Scoped implementation. Every file change stays visible.'],
  ['The next move is yours.','One exact request. One revision. No invisible permissions.'],
  ['Evidence, not confidence.','Real checks decide the outcome. Animation never does.'],
  ['Finished means reviewed.','A final review before any commit or pull request.'],
];
const state={surface:'experience',tab:'overview',run:'payment',scenario:'approval',phase:2,exploded:false,paused:matchMedia('(prefers-reduced-motion: reduce)').matches,theme:'light',draft:'',file:'confirm.py',hideContext:false,decision:null};
let engine=null, toastTimer, dialogReturn;
const engineMarkup=(compact=false)=>`<div class="engine-stage ${compact?'compact':''}">
  <div class="stage-grid" aria-hidden="true"></div><div class="engine-shadow" aria-hidden="true"></div>
  <canvas class="engine-canvas" aria-hidden="true"></canvas>
  <div class="fallback-stack" aria-hidden="true">${[0,1,2,3,4].map(i=>`<img src="mark.svg" alt="" style="--layer:${i}">`).join('')}</div>
  <span class="stage-coordinate top-coordinate" aria-hidden="true">S / ${compact?'ASSEMBLY':'CORE'} 001</span>
  <span class="stage-coordinate bottom-coordinate" aria-hidden="true">${compact?'PLAN &#8594; EVIDENCE':'A SYSTEM WITH YOU AT ITS CENTER'}</span>
  <button class="explode-button" aria-pressed="${state.exploded}">${icon('layers',16)} <span>${state.exploded?'Assemble layers':'Explode layers'}</span></button>
</div>`;

function renderExperience() {
  return `<section class="experience-page" aria-labelledby="experience-title">
    <div class="experience-intro">
      <span class="eyebrow"><span class="mini-rule"></span> AUTONOMY, UNDER YOUR CONTROL</span>
      <h1 id="experience-title">Ambition,<br><span>engineered.</span></h1>
      <p>From the first idea to the final review.<br>Meet the local engineering system that<br class="desktop-break"> puts you at the center.</p>
      <a href="#studio" class="primary-button">Step inside Studio ${icon('arrow')}</a>
      <span class="entry-note">Your machine. Your repository. Your rules.</span>
      <div class="edition"><span class="edition-number">S&#8201;/&#8201;01</span><span>ENGINEERING<br>INSTRUMENTS</span></div>
    </div>
    <div class="experience-object">
      <div class="object-heading"><span>THE SYNDRA ENGINE</span><span>INTERACTIVE / REAL-TIME 3D</span></div>
      ${engineMarkup()}
      <div class="phase-copy" aria-live="polite"><span class="phase-index">0${state.phase+1}</span><div><h2>${explanations[state.phase][0]}</h2><p>${explanations[state.phase][1]}</p></div></div>
      <div class="phase-selector" aria-label="Explore execution layers">${phases.map((phase,i)=>`<button data-phase="${i}" aria-pressed="${state.phase===i}"><span>0${i+1}</span>${phase}</button>`).join('')}</div>
    </div>
    <div class="experience-bottom">
      <div><span class="eyebrow">01 / THINK IN SYSTEMS</span><h3>Not another chat window.</h3><p>Plan, implementation, decisions and evidence.<br>One continuous, inspectable workflow.</p></div>
      <div><span class="eyebrow">02 / KEEP YOUR HAND ON THE CONTROLS</span><h3>Power without the blind spots.</h3><p>See the exact operation before it happens.<br>Permission is a boundary, not a vibe.</p></div>
      <a href="#studio" class="studio-entry"><span>Less spectacle.<br>More substance.</span><span class="entry-arrow">${icon('diagonal',28)}</span><small>EXPLORE THE WORKING SURFACE</small></a>
    </div>
  </section>`;
}

const runs=samples;
function activeRun() {return runs.find(run=>run.id===state.run);}
function scenarioStatus() {return state.scenario==='success'?'Completed':state.scenario==='failure'?'Verification failed':state.decision==='reject'?'Rejected':state.decision==='approve'?'Ready for verification':'Needs approval';}
function queue() {
  return `<aside class="run-queue" aria-label="Sample run queue"><div class="queue-header"><span>WORKSPACE</span><button class="icon-button" data-action="workspace" aria-label="Inspect sample workspace">${icon('folder',17)}</button></div>
    <button class="workspace-switch" data-action="workspace"><span class="workspace-avatar">P</span><span>Personal workspace<small>Local engineering</small></span><span aria-hidden="true">&#8964;</span></button>
    <div class="queue-title"><h2>Work in progress</h2><span>01</span></div>
    <div class="queue-runs">${runs.map(run=>`<button class="queue-run ${state.run===run.id?'selected':''}" data-run="${run.id}" aria-pressed="${state.run===run.id}"><span class="run-number">${run.number}</span><span><strong>${run.name}</strong><small>${run.repo}</small><span class="queue-status ${run.id===state.run?(state.scenario==='success'?'success':state.scenario==='failure'||state.decision==='reject'?'failure':'attention'):run.id==='payment'?'attention':run.id==='cache'?'failure':'success'}"><i></i>${run.id===state.run?scenarioStatus():run.status}</span></span></button>`).join('')}</div>
    <label class="mobile-run-selector" for="mobile-run">Open sample run<select id="mobile-run">${runs.map(run=>`<option value="${run.id}" ${state.run===run.id?'selected':''}>${run.number} / ${run.name}</option>`).join('')}</select></label>
    <button class="queue-action" data-action="history">${icon('clock',17)} All work <span>3</span></button>
    <button class="queue-action" data-action="workspace">${icon('shield',17)} Workspace & policy ${icon('diagonal',14)}</button>
    <div class="queue-footer"><span class="mono">S / LOCAL FIRST</span><p>Great work.<br>Close to the source.</p><div class="local-signal"><i></i> Sample connection <span>v0.6</span></div></div>
  </aside>`;
}
function renderStudio() {
  const run=activeRun(), status=scenarioStatus();
  return `<section class="studio-page" aria-label="Syndra Studio interactive concept">
    <div class="studio-toolbar"><div class="workspace-breadcrumb">${icon('folder',16)}<button data-action="workspace">${run.repo}</button><span>/</span><span>Run ${run.number}</span></div><div><button class="command-trigger" data-action="commands">${icon('search',16)} Find anything <kbd>Ctrl K</kbd></button><button class="new-task" data-action="draft">${icon('plus',17)} New task</button></div></div>
    <div class="studio-body">${queue()}<div class="run-main">
      <header class="run-heading"><div><span class="eyebrow">RUN ${run.number} <span class="heading-slash">/</span> MULTI-AGENT WORKFLOW</span><h1>${run.id==='payment'?'Make retries<br><span>boring.</span>':run.id==='search'?'Find the<br><span>right thing.</span>':'Make the cache<br><span>behave.</span>'}</h1></div><button data-action="decision" aria-label="Inspect ${state.scenario==='approval'&&!state.decision?'pending decision':'outcome record'}" class="run-status ${state.scenario==='failure'||state.decision==='reject'?'failure':state.scenario==='success'?'success':'attention'}"><i></i><span>${status}<small>${state.scenario==='success'?'Verification + review passed':state.scenario==='failure'?'Files preserved. Review not run.':state.decision?'Demo decision recorded':'Inspect the exact request'} ${icon('diagonal',12)}</small></span></button></header>
      <div class="run-context"><span>${icon('branch',15)} <code>fix/${run.id==='payment'?'idempotency':run.id==='search'?'search-ranking':'cache-keys'}</code></span><span>${icon('shield',15)} Scoped repository</span><span class="sample-indicator">Sample run</span></div>
      <nav class="artifact-tabs" aria-label="Run artifacts">${[['overview','Execution'],['changes','Changes <span>2</span>'],['checks','Checks'],['evidence','Evidence']].map(([tab,label])=>`<button data-tab="${tab}" aria-pressed="${state.tab===tab}">${label}</button>`).join('')}<button class="activity-link" data-action="activity">Activity ${icon('diagonal',14)}</button></nav>
      <div id="artifact-content">${artifact()}</div>
    </div><aside class="decision-column" aria-label="Decision and sample state">${decision()}<section class="preview-controls"><h2>Explore the states</h2><p>Same system. Different outcomes.<br>These are illustrations, not live runs.</p><div>${[['approval','Approval'],['success','Verified'],['failure','Failed']].map(([scenario,label])=>`<button data-scenario="${scenario}" aria-pressed="${state.scenario===scenario}">${label}</button>`).join('')}</div></section></aside></div>
  </section>`;
}
function artifact() {
  if(state.tab==='changes') return changes();
  if(state.tab==='checks') return checks();
  if(state.tab==='evidence') return evidence();
  return `<section class="execution-assembly" aria-labelledby="assembly-title"><div class="assembly-label"><h2 id="assembly-title">Execution assembly</h2><span>5 layers / one accountable system</span></div><div class="assembly-body">${engineMarkup(true)}<ol class="execution-phases">${phases.map((phase,i)=>`<li><button data-phase="${i}" aria-pressed="${state.phase===i}"><span class="phase-dot ${i<2||state.scenario==='success'?'done':i===2&&state.scenario==='approval'?'attention':''}">${i<2||state.scenario==='success'?icon('check',12):`0${i+1}`}</span><span><strong>${phase}</strong><small>${phaseState(i)}</small></span><span class="phase-tail">${i===state.phase?'&#8592;':''}</span></button></li>`).join('')}</ol></div><div class="assembly-caption"><span>INSPECT / ${phases[state.phase].toUpperCase()}</span><p>${explanations[state.phase][1]}</p></div></section>
    <section class="next-move"><div class="next-move-icon">${icon(state.scenario==='success'?'check':state.scenario==='failure'?'terminal':'code',22)}</div><div><span class="eyebrow">${state.scenario==='success'?'THE WORK, ACCOUNTED FOR':state.scenario==='failure'?'WHERE THE WORK STOPPED':'WHAT CHANGED'}</span><h2>${state.scenario==='success'?'Verified. Reviewed. Ready.':state.scenario==='failure'?'The behavior check failed.':activeRun().summary}</h2><p>${state.scenario==='failure'?'The implementation stays on disk. Nothing was committed. Inspect the failing check before trying again.':activeRun().description}</p></div><button data-tab="${state.scenario==='failure'?'checks':'changes'}" class="text-button">Inspect ${state.scenario==='failure'?'checks':'changes'} ${icon('arrow',18)}</button></section>
    <div class="execution-footnote"><span>${icon('shield',15)} No automatic commit or publish</span><button data-tab="evidence">Inspect the evidence ${icon('diagonal',14)}</button></div>`;
}
function phaseState(i) {
  if(state.scenario==='success')return ['3 bounded tasks','2 files changed','Exact request approved','12 checks passed','Final review approved'][i];
  if(state.scenario==='failure')return ['Plan complete','Artifacts preserved','Approved in sample','1 check failed','Not run'][i];
  return ['3 bounded tasks','2 files changed',state.decision==='approve'?'Approved in sample':state.decision==='reject'?'Rejected in sample':'Waiting for you','Not run yet','Not run yet'][i];
}
function highlighted(text) {return escape(text).replace(/\b(import|from|async|def|await|if|return|class|with|as|is|not|None|lambda)\b/g,'<span class="syntax-keyword">$1</span>');}
function changes() {
  const run=activeRun(), lines=state.file===run.files[0]?run.source:run.tests;
  const added=lines.filter(([kind])=>kind==='add').length, removed=lines.filter(([kind])=>kind==='remove').length;
  return `<section class="changes-surface" aria-labelledby="changes-title"><div class="artifact-heading"><div><span class="eyebrow">REPOSITORY ARTIFACTS</span><h2 id="changes-title">Small change. Clear intent.</h2></div><span class="diff-summary"><b>+${added}</b><span>&minus;${removed}</span></span></div>
    <div class="file-switcher">${run.files.map(file=>`<button data-file="${file}" aria-pressed="${state.file===file}">${icon('code',15)} ${file}</button>`).join('')}<label><input type="checkbox" id="hide-context" ${state.hideContext?'checked':''}> Changed lines only</label></div>
    <div class="diff-meta"><code>${run.directory}/${state.file}</code><span>Sample diff / read only</span></div>
    <div class="diff-code" tabindex="0" role="region" aria-label="Sample code changes"><div class="diff-hunk">@@ sample change / ${added} added, ${removed} removed @@</div>${lines.map(([kind,line],index)=>state.hideContext&&kind==='context'?'':`<div class="code-line ${kind}"><span class="line-number">${index+18}</span><span class="diff-sign">${kind==='add'?'+':kind==='remove'?'&minus;':' '}</span><code>${highlighted(line)||' '}</code></div>`).join('')}</div><div class="artifact-note">${icon('shield',16)} Illustrative code, not a production patch. Runtime storage is never a source change.</div></section>`;
}
function checks() {
  const pending=state.scenario==='approval', failed=state.scenario==='failure';
  return `<section class="checks-surface" aria-labelledby="checks-title"><div class="artifact-heading"><div><span class="eyebrow">CORRECTNESS IS A SEPARATE GATE</span><h2 id="checks-title">${pending?'Confidence is not a check.':failed?'A useful failure.':'Nothing left to guess.'}</h2></div>${icon(pending?'clock':failed?'terminal':'check',28)}</div>
    <div class="check-result ${failed?'failure':pending?'pending':'success'}"><span class="check-result-number">${pending?'--':failed?'01':'12'}</span><div><h3>${pending?'Verification has not run':failed?'Failing check':'Checks passed in this sample'}</h3><p>${pending?state.decision==='approve'?'Approval was recorded; verification has not run.':state.decision==='reject'?'Approval was rejected; no execution continues.':'Approval is pending. There is no verified outcome to display.':failed?activeRun().failingCheck:'Static analysis and behavior coverage passed in this sample.'}</p></div></div>
    <div class="check-records">${[['Type check',pending?'Not run':'Passed'],['Behavior tests',pending?'Not run':failed?'Failed':'Passed'],['Mandatory final review',pending||failed?'Not run':'Approved']].map(([title,status])=>`<div><span>${title}</span><strong class="${status==='Failed'?'failure':status==='Passed'||status==='Approved'?'success':'muted'}">${status}</strong></div>`).join('')}</div>
    ${failed?`<pre class="output-block">${escape(activeRun().failureOutput)}</pre>`:'<div class="checks-message">'+icon('shield',19)+'<p>Verification and review remain separate. A human approval is never proof that the implementation is correct.</p></div>'}<button class="text-button" data-tab="changes">Return to artifacts ${icon('arrow',17)}</button></section>`;
}
function evidence() {
  return `<section class="evidence-surface" aria-labelledby="evidence-title"><div class="artifact-heading"><div><span class="eyebrow">TRACE / PROVENANCE / ACCOUNTABILITY</span><h2 id="evidence-title">Every move leaves a record.</h2></div><span class="sample-indicator">Illustrative trace</span></div><div class="evidence-strip"><div><small>RUN ID</small><code>study-run-${activeRun().number}</code></div><div><small>ORIGIN</small><strong>Design sample</strong></div><div><small>DISPATCHED</small><strong>Nothing</strong></div></div>
    <label class="timeline-label" for="event-scrubber">Inspect a captured sample event <span id="event-number">0${state.phase+1} / 05</span></label><input type="range" id="event-scrubber" min="0" max="4" value="${state.phase}" step="1"><div class="event-inspection"><span class="event-index">0${state.phase+1}</span><div><h3 id="event-title">${phases[state.phase]}</h3><p id="event-description">${explanations[state.phase][1]}</p><span class="mono">CAPTURED SAMPLE / NOT RE-EXECUTED</span></div></div>
    <div class="evidence-ledger">${phases.map((phase,i)=>`<button data-event="${i}" aria-pressed="${state.phase===i}"><span class="mono">00:${String(12+i*8).padStart(2,'0')}</span><span>${phase}</span><small>${phaseState(i)}</small>${icon('diagonal',14)}</button>`).join('')}</div><p class="artifact-note">This demo does not calculate integrity, export a real trace, or replay tools. Those controls require backend-authoritative records.</p></section>`;
}
function decision() {
  const terminal=state.scenario!=='approval'||state.decision;
  return `<section class="decision-panel"><div class="decision-head"><span>${icon('shield',17)} ${terminal?'OUTCOME RECORD':'HUMAN DECISION'}</span><span class="decision-revision">${terminal?'SAMPLE':'REV. 03'}</span></div>
    <div class="decision-intro"><span class="decision-symbol">${icon(state.scenario==='failure'||state.decision==='reject'?'close':terminal?'check':'arrow',27)}</span><h2>${state.scenario==='success'?'The work checks out.':state.scenario==='failure'?'Stopped. Not hidden.':state.decision==='reject'?'Request rejected.':state.decision==='approve'?'Permission granted.':'Your boundary.\nYour decision.'}</h2><p>${state.scenario==='success'?'This sample passed verification and final review. It is eligible for a separate commit decision.':state.scenario==='failure'?'A failing check prevents completion. Changed files remain available for inspection.':state.decision==='reject'?'The sample request was rejected. No execution continues. File changes are not rolled back.':state.decision==='approve'?'Only this exact sample request is approved. Verification and final review are still pending.':'The agent wants to run the repository tests. Inspect the exact operation before allowing it.'}</p></div>
    ${terminal?`<div class="outcome-facts"><div><span>Verification</span><strong>${state.scenario==='success'?'Passed':state.scenario==='failure'?'Failed':'Not run'}</strong></div><div><span>Final review</span><strong>${state.scenario==='success'?'Approved':'Not run'}</strong></div><div><span>Commit / PR</span><strong>Not created</strong></div><div><span>Changed files</span><strong>2 preserved</strong></div></div><button class="decision-reset" data-scenario="approval">Return to approval sample ${icon('arrow',17)}</button>`:`<div class="exact-operation"><span class="operation-label">EXACT SAMPLE OPERATION</span><code>python -m unittest<br>discover -s tests -v</code><div class="operation-path">${icon('folder',14)} /workspace/${activeRun().repo}</div></div><dl class="operation-scope"><div><dt>Tool</dt><dd>managed.python</dd></div><div><dt>Revision</dt><dd>03 / immutable</dd></div><div><dt>Timeout</dt><dd>120 seconds</dd></div><div><dt>Network</dt><dd>Not allowed</dd></div><div><dt>Write scope</dt><dd>Target repository</dd></div><div><dt>Authority</dt><dd>Exact request only</dd></div></dl><button class="scope-link" data-action="scope">Inspect full request ${icon('diagonal',15)}</button><label class="decision-note">Decision note <span>optional</span><input id="decision-note" maxlength="200" placeholder="Add context for the record"></label><div class="decision-actions"><button data-decision="reject">Reject</button><button data-decision="approve">Approve sample ${icon('arrow',17)}</button></div>`}
    <p class="authority-note">${icon('shield',13)} ${terminal?'Illustrated outcome. No runtime was invoked.':'Demo only. Nothing executes. Production decisions stay server-authoritative.'}</p></section>`;
}
function render() {
  clearTimeout(toastTimer);$('.toast').hidden=true;
  engine?.destroy();engine=null;
  main.innerHTML=state.surface==='studio'?renderStudio():renderExperience();
  document.body.dataset.surface=state.surface;
  document.querySelectorAll('[data-surface]').forEach(link=>{if(link.dataset.surface===state.surface)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
  const canvas=$('.engine-canvas');
  if(canvas) engine=new KineticEngine(canvas,{compact:state.surface==='studio',paused:state.paused,selected:state.phase});
  engine?.set({exploded:state.exploded});
}
function renderArtifact() {
  engine?.destroy();engine=null;
  $('#artifact-content').innerHTML=artifact();
  document.querySelectorAll('[data-tab]').forEach(button=>{if(button.closest('.artifact-tabs'))button.setAttribute('aria-pressed',String(button.dataset.tab===state.tab));});
  const canvas=$('.engine-canvas');if(canvas) {engine=new KineticEngine(canvas,{compact:true,paused:state.paused,selected:state.phase});engine.set({exploded:state.exploded});}
}
function changePhase(phase) {
  state.phase=phase;engine?.set({selected:phase});
  document.querySelectorAll('[data-phase],[data-event]').forEach(button=>button.setAttribute('aria-pressed',String(Number(button.dataset.phase??button.dataset.event)===phase)));
  if(state.surface==='experience') {
    $('.phase-index').textContent=`0${phase+1}`;$('.phase-copy h2').textContent=explanations[phase][0];$('.phase-copy p').textContent=explanations[phase][1];
  } else if(state.tab==='overview') {
    $('.assembly-caption span').textContent=`INSPECT / ${phases[phase].toUpperCase()}`;$('.assembly-caption p').textContent=explanations[phase][1];
    document.querySelectorAll('.phase-tail').forEach((item,i)=>item.textContent=i===phase?'\u2190':'');
  } else if(state.tab==='evidence') {
    $('#event-scrubber').value=phase;$('#event-number').textContent=`0${phase+1} / 05`;$('.event-index').textContent=`0${phase+1}`;$('#event-title').textContent=phases[phase];$('#event-description').textContent=explanations[phase][1];
  }
}
function toast(message) {
  const element=$('.toast');element.textContent=message;element.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{element.hidden=true;},6000);
}
function openDialog(title,content,className='') {
  dialogReturn=document.activeElement;
  overlay.className=className;
  $('#dialog-content').innerHTML=`<header class="dialog-heading"><div><span class="eyebrow">SYNDRA / DESIGN STUDY</span><h2 id="dialog-title">${title}</h2></div><button class="icon-button" data-action="close" aria-label="Close dialog">${icon('close')}</button></header>${content}`;
  overlay.showModal();
}
const commands=[['Execution assembly','overview'],['Inspect code changes','changes'],['Verification and review','checks'],['Trace and evidence','evidence'],['New task draft','draft'],['Workspace and connections','workspace'],['Work history','history'],['Design rationale','notes']];
function openCommands() {
  openDialog('Find your next move.',`<label class="command-search">${icon('search',19)}<input id="command-query" placeholder="Search artifacts, actions, settings..." aria-label="Search commands" autocomplete="off" autofocus></label><div id="command-results" class="command-results"></div><div class="command-help"><span><kbd>&uarr;</kbd> <kbd>&darr;</kbd> to navigate</span><span><kbd>Enter</kbd> to open</span><span><kbd>Esc</kbd> to close</span></div>`,'command-dialog');
  filterCommands('');$('#command-query').focus();
}
function filterCommands(query) {
  const matches=commands.filter(([label])=>label.toLowerCase().includes(query.toLowerCase()));
  $('#command-results').innerHTML=matches.map(([label,action],i)=>`<button data-command="${action}" ${i===0?'class="command-selected"':''}>${icon(['overview','changes','checks','evidence'].includes(action)?'layers':'diagonal',17)}<span>${label}</span><small>${i===0?'Enter':''}</small></button>`).join('')||'<p class="no-results">No matching commands. Try "changes" or "workspace".</p>';
}
function selectRun(id) {
  state.run=id;state.file=activeRun().files[0];state.hideContext=false;
  state.scenario=id==='payment'?'approval':id==='search'?'success':'failure';
  state.phase=state.scenario==='approval'?2:state.scenario==='success'?4:3;
  state.decision=null;state.tab='overview';if(overlay.open)overlay.close();render();
  const target=matchMedia('(max-width: 760px)').matches?$('#mobile-run'):document.querySelector(`[data-run="${id}"]`);
  target?.focus();
}
function action(name) {
  if(name==='close'){overlay.close();return;}
  if(name==='decision'){const heading=$('.decision-intro h2');heading.tabIndex=-1;heading.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});heading.focus({preventScroll:true});return;}
  if(name==='commands'){openCommands();return;}
  if(name==='notes') {openDialog('Kinetic Precision.',`<div class="rationale"><h3>An engineering instrument. Not a dashboard skin.</h3><p>The five physical layers represent plan, implementation, approval, verification and review. Orange identifies the selected layer; it never claims success.</p><p>Warm porcelain. Machined graphite. One confident signal color. Expressive display type, quiet functional type, and code that gets the space it deserves.</p><div class="rationale-principles"><span>01 / Motion explains state.</span><span>02 / Decisions remain exact.</span><span>03 / Depth has a purpose.</span><span>04 / Every state is designed.</span></div><p>This is an independent prototype with sample data. It has no API client, no credential storage, and no execution capabilities. Production Studio remains unchanged.</p><a href="PROPOSAL.md" target="_blank" rel="noreferrer">Read the complete design proposal ${icon('diagonal',15)}</a></div>`);return;}
  if(name==='workspace') {openDialog('Close to the source.',`<div class="workspace-dialog"><p class="dialog-subtitle">Sample configuration. No providers are contacted.</p><div class="workspace-address">${icon('folder',26)}<div><strong>${activeRun().repo}</strong><code>/workspace/${activeRun().repo}</code></div></div><div class="setting-row"><span>Repository boundary<small>Canonical root matches the workspace</small></span><strong class="success">Confirmed in sample</strong></div><div class="setting-row"><span>Azure OpenAI<small>Model reachability has not been tested</small></span><strong>Not checked</strong></div><div class="setting-row"><span>Ollama<small>No probe was dispatched</small></span><strong>Not checked</strong></div><div class="setting-row"><span>Network policy<small>Managed test command</small></span><strong>Denied</strong></div><div class="setting-row"><span>Runtime storage<small>AgentBus-owned state stays out of diffs</small></span><code>.syndra/runs</code></div><p class="artifact-note">Production setup must show genuine configuration, reachability, checked time and errors separately. Never infer readiness from a saved key.</p></div>`);return;}
  if(name==='scope') {openDialog('One request. No hidden access.',`<div class="scope-dialog"><p class="dialog-subtitle">Immutable sample request / revision 03</p><pre class="scope-json">tool          managed.python\narguments     ["-m", "unittest", "discover",\n               "-s", "tests", "-v"]\nworking dir   /workspace/${activeRun().repo}\nwrite scope   target repository\nnetwork       denied\ntimeout       120 seconds\nmax output    64 KiB\nrevision      03\nauthority     this exact request</pre><p>Changing arguments, scope or revision invalidates the decision. In production, the server must confirm acceptance before the UI reports approval.</p><p class="artifact-note">Safe example values only. The design study contains no real credentials or unrestricted runtime arguments.</p></div>`);return;}
  if(name==='history') {openDialog('The work ledger.',`<div class="history-dialog"><p class="dialog-subtitle">Three illustrative records. Production history uses server-backed paging and filters.</p>${runs.map(run=>`<button data-history-run="${run.id}"><span class="run-number">${run.number}</span><span><strong>${run.name}</strong><small>${run.repo}</small></span><span>${run.status}</span>${icon('arrow',17)}</button>`).join('')}</div>`);return;}
  if(name==='activity') {openDialog('The moves that matter.',`<div class="activity-dialog"><p class="dialog-subtitle">Sample observations. No prompts or model reasoning are shown.</p>${phases.map((phase,i)=>`<div><span class="mono">00:${12+i*8}</span><span class="activity-node ${i===2?'attention':''}"></span><div><strong>${phase}</strong><p>${phaseState(i)}</p></div></div>`).join('')}</div>`);return;}
  if(name==='draft') {openDialog('What should we make next?',`<form id="draft-form"><div class="draft-label"><label for="task-text">Task</label><textarea id="task-text" rows="5" placeholder="Describe an outcome, not a command..." required maxlength="2000" autofocus>${escape(state.draft)}</textarea></div><div class="draft-settings"><label>Workflow<select><option>Multi-agent / durable</option></select></label><label>Workspace<input value="${escape(activeRun().repo)}" readonly></label></div><p class="artifact-note">Design preview only. Saving this draft never creates a run or contacts a model.</p><button class="primary-button" type="submit">Keep demo draft ${icon('arrow',18)}</button></form>`);$('#task-text').focus();return;}
}
document.addEventListener('click',event=>{
  const button=event.target.closest('button');if(!button)return;
  if(button.dataset.phase!==undefined)changePhase(Number(button.dataset.phase));
  if(button.dataset.event!==undefined)changePhase(Number(button.dataset.event));
  if(button.classList.contains('explode-button')) {state.exploded=!state.exploded;button.setAttribute('aria-pressed',String(state.exploded));$('span',button).textContent=state.exploded?'Assemble layers':'Explode layers';engine?.set({exploded:state.exploded});}
  if(button.dataset.tab) {state.tab=button.dataset.tab;renderArtifact();}
  if(button.dataset.file) {state.file=button.dataset.file;renderArtifact();document.querySelector(`[data-file="${state.file}"]`)?.focus();}
  if(button.dataset.scenario) {state.scenario=button.dataset.scenario;state.decision=null;state.phase=state.scenario==='approval'?2:state.scenario==='failure'?3:4;render();document.querySelector(`[data-scenario="${state.scenario}"]`)?.focus();}
  if(button.dataset.run||button.dataset.historyRun) selectRun(button.dataset.run??button.dataset.historyRun);
  if(button.dataset.decision) {state.decision=button.dataset.decision;render();toast(`Demo request ${state.decision==='approve'?'approved':'rejected'}. Nothing executed.`);$('.decision-reset')?.focus();}
  if(button.dataset.action)action(button.dataset.action);
  if(button.dataset.command) {const command=button.dataset.command;overlay.close();if(['overview','changes','checks','evidence'].includes(command)){if(state.surface!=='studio'){state.surface='studio';location.hash='#studio';state.tab=command;render();}else{state.tab=command;renderArtifact();document.querySelector(`[data-tab="${command}"]`)?.focus();}}else action(command);}
});
document.addEventListener('input',event=>{
  if(event.target.id==='command-query')filterCommands(event.target.value);
  if(event.target.id==='task-text')state.draft=event.target.value;
  if(event.target.id==='event-scrubber')changePhase(Number(event.target.value));
});
document.addEventListener('change',event=>{if(event.target.id==='hide-context'){state.hideContext=event.target.checked;renderArtifact();$('#hide-context').focus();}if(event.target.id==='mobile-run')selectRun(event.target.value);});
document.addEventListener('submit',event=>{if(event.target.id==='draft-form'){event.preventDefault();state.draft=$('#task-text').value;overlay.close();toast('Draft kept in this demo session. No run was dispatched.');}});
document.addEventListener('keydown',event=>{
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();if(overlay.open)overlay.close();openCommands();return;}
  if(!overlay.open||!$('#command-query'))return;
  const results=[...document.querySelectorAll('[data-command]')];
  if(!results.length)return;
  let current=results.findIndex(button=>button.classList.contains('command-selected'));
  if(event.key==='ArrowDown'||event.key==='ArrowUp') {event.preventDefault();current=(current+(event.key==='ArrowDown'?1:-1)+results.length)%results.length;results.forEach((button,i)=>{button.classList.toggle('command-selected',i===current);$('small',button).textContent=i===current?'Enter':'';});results[current].scrollIntoView({block:'nearest'});}
  if(event.key==='Enter' && event.target.id==='command-query'){event.preventDefault();results[Math.max(0,current)].click();}
});
overlay.addEventListener('close',()=>{if(!overlay.open && dialogReturn?.isConnected)dialogReturn.focus();});
$('#open-notes').addEventListener('click',()=>action('notes'));
function syncTools() {
  $('#theme-toggle').innerHTML=icon(state.theme==='light'?'moon':'sun');$('#theme-toggle').setAttribute('aria-label',`Switch to ${state.theme==='light'?'dark':'light'} appearance`);
  $('#motion-toggle').innerHTML=icon(state.paused?'play':'pause');$('#motion-toggle').setAttribute('aria-pressed',String(state.paused));$('#motion-toggle').setAttribute('aria-label',state.paused?'Resume ambient motion':'Pause ambient motion');$('#motion-toggle').title=state.paused?'Resume ambient motion':'Pause ambient motion';
}
$('#theme-toggle').addEventListener('click',()=>{state.theme=state.theme==='light'?'dark':'light';document.documentElement.dataset.theme=state.theme;syncTools();});
$('#motion-toggle').addEventListener('click',()=>{if(state.paused&&matchMedia('(prefers-reduced-motion: reduce)').matches){toast('Your reduced-motion preference keeps ambient animation off.');return;}state.paused=!state.paused;engine?.set({paused:state.paused});syncTools();});
window.addEventListener('hashchange',()=>{const next=location.hash==='#studio'?'studio':'experience';if(next!==state.surface){state.surface=next;render();window.scrollTo(0,0);}});
state.surface=location.hash==='#studio'?'studio':'experience';syncTools();render();
