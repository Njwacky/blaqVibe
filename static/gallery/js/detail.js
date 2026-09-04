(function(){
  function esc(s){
    return String(s ?? '').replace(/[&<>"']/g, function(c){
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]);
    });
  }

  const banner = document.getElementById('scan-banner');
  const scanText = document.getElementById('scan-text');
  const scanUrl = banner ? banner.dataset.scanUrl : null;

  // Put the project first: visitors should understand the builder, artifact,
  // evidence and remix lineage before they encounter optional AI tooling.
  function addTrustHierarchy(){
    const layout = document.querySelector('.detail-layout');
    const sidebar = layout ? layout.children[1] : null;
    if(!sidebar || document.getElementById('build-evidence')) return;

    const text = document.body.innerText || '';
    const hasAi = /🤖/.test(text) || /AI PROMPT/i.test(text) || /AI README/i.test(text);
    const forked = /Forked from/i.test(sidebar.innerText || '');
    const checked = /✓ Checked/i.test(sidebar.innerText || '');
    const scanned = /Scanned/i.test(sidebar.innerText || '');
    const preview = /Playable here/i.test(sidebar.innerText || '') || /▶ Run preview/i.test(sidebar.innerText || '');
    const files = (sidebar.innerText.match(/Files\s+([0-9]+)/i) || [])[1];
    const creator = (sidebar.innerText.match(/Publisher\s+@?([A-Za-z0-9_.-]+)/i) || [])[1] || 'Creator';

    const method = forked ? 'Remixed' : (hasAi ? 'AI-assisted' : 'Human-built');
    const methodDetail = forked
      ? 'Built from another project and published as a new direction.'
      : hasAi
        ? 'AI involvement is disclosed as provenance; the project itself is the evidence.'
        : 'Published by its creator; inspect the files, README and preview to evaluate it.';
    const checks = checked ? 'Checked' : (scanned ? 'Scanned' : 'Not yet checked');
    const previewText = preview ? 'Runnable preview available' : 'Files / README available';

    const card = document.createElement('section');
    card.id = 'build-evidence';
    card.className = 'card build-evidence-card';
    card.innerHTML = `
      <div class="build-evidence__eyebrow">PROJECT EVIDENCE</div>
      <h3 class="build-evidence__title">What you can verify</h3>
      <p class="build-evidence__intro">A real project should stand on its own: creator, artifact, checks and history.</p>
      <div class="build-evidence__grid">
        <div><span>Creator</span><b>@${esc(creator)}</b></div>
        <div><span>Build method</span><b>${esc(method)}</b><small>${esc(methodDetail)}</small></div>
        <div><span>Safety check</span><b>${esc(checks)}</b><small>Platform scanning and moderation signals.</small></div>
        <div><span>Artifact</span><b>${esc(files ? files + ' files' : 'Project files')}</b><small>${esc(previewText)}</small></div>
      </div>
    `;
    sidebar.insertBefore(card, sidebar.firstElementChild);

    const style = document.createElement('style');
    style.textContent = `
      .build-evidence-card{margin-bottom:12px;border-color:var(--accent-border);background:var(--card)}
      .build-evidence__eyebrow{font-size:10px;letter-spacing:.1em;font-weight:800;color:var(--link)}
      .build-evidence__title{font-size:17px;font-weight:800;margin-top:4px}
      .build-evidence__intro{font-size:11px;line-height:1.55;color:var(--muted);margin:5px 0 10px}
      .build-evidence__grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
      .build-evidence__grid>div{padding:9px;background:var(--bg);border:1px solid var(--line);border-radius:9px;min-width:0}
      .build-evidence__grid span{display:block;font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
      .build-evidence__grid b{display:block;font-size:12px;margin-top:3px;overflow-wrap:anywhere}
      .build-evidence__grid small{display:block;font-size:10px;color:var(--muted);line-height:1.45;margin-top:3px}
      @media(max-width:640px){.build-evidence__grid{grid-template-columns:1fr}}
    `;
    document.head.appendChild(style);

    // AI remains visible and honest, but it is provenance—not the hero.
    document.querySelectorAll('.badge').forEach(function(b){
      if(/🤖/.test(b.textContent)){
        b.textContent = 'Build method: AI-assisted';
        b.style.background = 'var(--input)';
        b.style.border = '1px solid var(--line)';
        b.style.color = 'var(--muted)';
      }
    });
    document.querySelectorAll('div').forEach(function(el){
      const label = (el.textContent || '').trim();
      if(label === '🤖 AI PROMPT') el.textContent = 'BUILD NOTES';
      if(label === '🤖 AI README — Gemini/Groq') el.textContent = 'README HELPER — OPTIONAL';
    });
  }

  // Keep the rich waiting panel (headline, file details, queue position and
  // the stage checklist) in sync with each poll — so the page never looks
  // frozen while a vibe is being checked.
  function renderProgress(p){
    if(!p) return;
    const headline = document.getElementById('scan-headline');
    if(headline && p.headline) headline.textContent = p.headline;
    const reasonEl = document.getElementById('scan-reason');
    if(reasonEl) reasonEl.textContent = p.reason ? (' — ' + p.reason) : '';
    if(p.file){
      const nameEl = document.getElementById('scan-file-name');
      if(nameEl && p.file.name){ nameEl.textContent = p.file.name; nameEl.title = p.file.name; }
      const sizeEl = document.getElementById('scan-file-size');
      if(sizeEl && p.file.size_human) sizeEl.textContent = p.file.size_human;
      const countEl = document.getElementById('scan-file-count');
      if(countEl && p.file.file_count){
        countEl.textContent = p.file.file_count + ' file' + (p.file.file_count===1?'':'s');
      }
    }
    const queueEl = document.getElementById('scan-queue');
    if(queueEl){
      if(p.queue_position){ queueEl.textContent = '#' + p.queue_position + ' in line'; queueEl.style.display=''; }
      else queueEl.style.display='none';
    }
    const stepsRoot = document.getElementById('scan-steps');
    if(stepsRoot && Array.isArray(p.steps)){
      p.steps.forEach(function(step){
        const li = stepsRoot.querySelector('[data-key="'+step.key+'"]');
        if(!li) return;
        li.className = 'scan-step scan-step--' + step.state;
        const label = li.querySelector('.scan-step-label');
        if(label && step.label) label.textContent = step.label;
        const detail = li.querySelector('.scan-step-detail');
        if(detail && step.detail) detail.textContent = step.detail;
      });
    }
  }

  if(scanUrl){
    (function poll(){
      fetch(scanUrl).then(r=>r.json()).then(d=>{
        renderProgress(d.progress);
        if(scanText) scanText.textContent=d.status;
        if(d.reason && scanText) scanText.textContent = d.status + ' — ' + d.reason;
        if(d.is_published || d.status==='clean'){
          if(scanText) scanText.textContent='clean — project is live!';
          const hl = document.getElementById('scan-headline');
          if(hl) hl.textContent = '✓ Your project is live!';
          if(banner){ banner.classList.remove('scan-panel--held','scan-panel--blocked'); banner.classList.add('scan-panel--live'); }
          try{ toast('✓ Your project is live!'); }catch(e){}
          setTimeout(()=>location.reload(),1200);
        } else if(d.status==='quarantined'){
          if(banner){ banner.classList.remove('scan-panel--held','scan-panel--live'); banner.classList.add('scan-panel--blocked'); }
          if(scanText) scanText.textContent='quarantined — blocked for review';
          try{ toast('Your project was quarantined — check My Vibes.'); }catch(e){}
        } else if((d.progress && d.progress.held)){
          if(banner){ banner.classList.add('scan-panel--held'); }
          setTimeout(poll, 4000);
        } else setTimeout(poll, 2000);
      }).catch(()=>setTimeout(poll,3000));
    })();
  }

  const treeRoot = document.getElementById('tree-root');
  if(treeRoot){
    const slug = treeRoot.dataset.slug;
    let tree = null;
    const scriptEl = document.getElementById('tree-data');
    if(scriptEl){
      try { tree = JSON.parse(scriptEl.textContent); } catch(e){}
    } else if(treeRoot.dataset.tree){
      try { tree = JSON.parse(treeRoot.dataset.tree); } catch(e){}
    }
    if(tree){
      function renderTree(node, prefix){
        let html="<ul style='list-style:none;margin-left:12px;border-left:1px solid var(--line);padding-left:10px'>";
        for(const [k,v] of Object.entries(node)){
          const path = prefix + k;
          if(v===null){
            html+=`<li style="color:var(--text);cursor:pointer" data-path="${encodeURIComponent(path)}">\u00A0📄 ${esc(k)}</li>`;
          } else {
            html+=`<li><span style="color:var(--warning-text);cursor:pointer">📁 ${esc(k)}/</span>${renderTree(v, prefix+k+'/')}</li>`;
          }
        }
        html+="</ul>"; return html;
      }
      treeRoot.innerHTML = renderTree(tree, '');
      treeRoot.addEventListener('click', function(e){
        const li = e.target.closest('[data-path]');
        if(!li) return;
        const path = decodeURIComponent(li.dataset.path);
        fetch(`/app/${slug}/file/${encodeURIComponent(path).replace(/%2F/g,'/')}`).then(r=>r.json().then(d=>({ok:r.ok,d}))).then(({ok,d})=>{
          if(!ok || d.error){ try{toast(d.error || 'Locked');}catch(e){} return; }
          document.getElementById('fileName').textContent = d.path;
          document.getElementById('fileCode').textContent = d.content;
          document.getElementById('filePreview').style.display='block';
        }).catch(()=>{ try{toast('File not found')}catch(e){}});
      });
    }
  }

  document.addEventListener('click', function(e){
    const copyBtn = e.target.closest('.js-copy');
    if(copyBtn){ try{ copyText(copyBtn.dataset.copy);}catch(err){} return; }
    const copySnippet = e.target.closest('.js-copy-snippet');
    if(copySnippet){ const t=document.getElementById(copySnippet.dataset.target); if(t) try{ copyText(t.innerText);}catch(err){} return; }
    const copyCode = e.target.closest('.js-copy-code');
    if(copyCode){ const t=document.getElementById(copyCode.dataset.target); if(t){ try{ copyText(t.innerText);}catch(err){} const url=copyCode.dataset.copyUrl; const csrf=copyCode.dataset.csrf; if(url&&csrf){ try{ fetch(url,{method:'POST',headers:{'X-CSRFToken':csrf}});}catch(err){} } } return; }
    const closeBtn = e.target.closest('.js-close-preview');
    if(closeBtn){ const p=document.getElementById('filePreview'); if(p) p.style.display='none'; }
  });
  document.addEventListener('submit', function(e){
    if(e.target.classList.contains('js-star-form')){
      e.preventDefault();
      const form=e.target;
      const csrf=form.querySelector('[name=csrfmiddlewaretoken]')?.value;
      fetch(form.action,{method:'POST',headers:csrf?{'X-CSRFToken':csrf}:{}}).then(r=>r.json()).then(d=>{
        try{ toast(d.starred?'Starred!':'Unstarred'); }catch(err){}
        setTimeout(()=>location.reload(),500);
      }).catch(()=>{ try{toast('Failed')}catch(err){}});
    }
    if(e.target.classList.contains('js-delete-form')){
      if(!confirm('Delete forever?')) e.preventDefault();
    }
    if(e.target.classList.contains('js-save-form')){
      e.preventDefault();
      const form=e.target;
      const csrf=form.querySelector('[name=csrfmiddlewaretoken]')?.value;
      fetch(form.action,{method:'POST',headers:csrf?{'X-CSRFToken':csrf}:{}}).then(r=>r.json()).then(d=>{
        try{ toast(d.saved?'Saved':'Removed from saved'); }catch(err){}
        setTimeout(()=>location.reload(),400);
      }).catch(()=>{ try{toast('Failed')}catch(err){}});
    }
    if(e.target.classList.contains('js-fork-form')){
      if(!confirm('Fork this project to your account? You will get your own copy to remix.')) e.preventDefault();
    }
    if(e.target.id==='nolo-form'){
      e.preventDefault();
      window.noloCompare();
    }
  });

  window.noloCompare = function(){
    const bEl = document.getElementById('nolo-b');
    if(!bEl || !bEl.value){ try{toast('Pick a project to compare')}catch(e){} return; }
    const form = document.getElementById('nolo-form');
    if(!form) return;
    const fd = new FormData(form);
    const url = form.dataset.url;
    const csrf = form.querySelector('[name=csrfmiddlewaretoken]')?.value;
    fetch(url, {method:'POST', body: fd, headers: csrf ? {'X-CSRFToken': csrf} : {}})
    .then(r=>r.json()).then(d=>{
      if(d.error){ try{toast(d.error)}catch(e){} return; }
      const el=document.getElementById('nolo-result');
      if(!el) return;
      el.style.display='block';
      const chips = (list)=> (list||[]).map(f=>`<span style="background:var(--input);border:1px solid var(--line);padding:3px 7px;border-radius:8px;font-size:11px;margin:2px;display:inline-block">${esc(f)}</span>`).join('');
      const langs = (obj)=> Object.entries(obj||{}).map(([k,v])=>esc(k)+' '+esc(v)+'%').join(', ')||"—";
      el.innerHTML = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div><b style="color:var(--warning-text)">${esc(d.a.title)}</b><div style="font-size:11px;color:var(--muted)">${esc(d.a.tech_stack)} • ${esc(d.a.file_count)} files • ★${esc(d.a.stars)}</div><div style="margin-top:6px">${chips(d.a.features)}</div><div style="margin-top:6px;font-size:11px;color:var(--muted)">Languages: ${langs(d.a.languages)}</div></div>
            <div><b style="color:var(--link)">${esc(d.b.title)}</b><div style="font-size:11px;color:var(--muted)">${esc(d.b.tech_stack)} • ${esc(d.b.file_count)} files • ★${esc(d.b.stars)}</div><div style="margin-top:6px">${chips(d.b.features)}</div><div style="margin-top:6px;font-size:11px;color:var(--muted)">Languages: ${langs(d.b.languages)}</div></div>
          </div>
          <div style="margin-top:10px;padding-top:10px;border-top:1px solid var(--line)">
            <div style="font-size:11px;color:var(--muted)">Common: ${esc((d.diff.common||[]).join(', ')||"—")}</div>
            <div style="font-size:11px;color:var(--warning-text)">Only in A: ${esc((d.diff.only_in_a||[]).join(', ')||"—")}</div>
            <div style="font-size:11px;color:var(--link)">Only in B: ${esc((d.diff.only_in_b||[]).join(', ')||"—")}</div>
          </div>`;
    }).catch(()=>{ try{toast('Compare failed')}catch(e){}});
  };

  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', addTrustHierarchy);
  else addTrustHierarchy();
})();
