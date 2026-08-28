(function(){
  function esc(s){
    return String(s ?? '').replace(/[&<>"']/g, function(c){
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]);
    });
  }

  const banner = document.getElementById('pending-panel') || document.getElementById('scan-banner');
  const scanText = document.getElementById('scan-text');
  const scanUrl = banner ? banner.dataset.scanUrl : null;

  function setText(id, value){
    const el = document.getElementById(id);
    if(el && value != null && value !== '') el.textContent = value;
  }
  function paintSteps(steps){
    const root = document.getElementById('pending-steps');
    if(!root || !Array.isArray(steps)) return;
    root.innerHTML = steps.map(function(s){
      const label = esc(s.label || s.id || '');
      const state = esc(s.state || 'todo');
      return '<span class="pending-step ' + state + '" data-step-id="' + esc(s.id || '') + '">' + label + '</span>';
    }).join('');
  }
  function applyHold(d){
    if(!d) return;
    if(scanText){
      scanText.textContent = d.status || '';
    }
    if(d.reason){
      setText('pending-reason-text', ' — ' + d.reason);
    }
    setText('pending-headline', d.headline);
    setText('pending-why', d.why_waiting);
    setText('pending-next', d.next_step);
    setText('pending-status-label', d.status_label);
    setText('pending-file-name', d.file_name);
    setText('pending-file-bytes', d.file_bytes_label);
    if(d.file_count != null) setText('pending-file-count', String(d.file_count));
    paintSteps(d.steps);
    if(!banner || !banner.classList.contains('pending-panel')) return;
    banner.classList.remove('is-scanning','is-hold','is-quarantine','is-live');
    if(d.phase === 'scanning') banner.classList.add('is-scanning');
    else if(d.phase === 'quarantined') banner.classList.add('is-quarantine');
    else if(d.phase === 'published') banner.classList.add('is-live');
    else banner.classList.add('is-hold');
  }

  if(scanUrl){
    (function poll(){
      fetch(scanUrl).then(r=>r.json()).then(d=>{
        applyHold(d);
        if(d.is_published || d.status==='clean'){
          if(scanText) scanText.textContent='clean — vibe is live!';
          try{ toast('✓ Your vibe is live!'); }catch(e){}
          setTimeout(()=>location.reload(),1200);
        } else if(d.status==='quarantined' || d.phase==='quarantined'){
          if(scanText && !d.reason) scanText.textContent='quarantined — blocked for review';
          try{ toast('Your vibe was quarantined — check My Vibes.'); }catch(e){}
        } else if(d.poll === false){
          return;
        } else {
          const wait = Number(d.poll_ms) > 0 ? Number(d.poll_ms) : 2000;
          setTimeout(poll, wait);
        }
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
      if(!confirm('Fork this vibe to your account? You will get your own copy to remix.')) e.preventDefault();
    }
    if(e.target.id==='nolo-form'){
      e.preventDefault();
      window.noloCompare();
    }
  });

  window.noloCompare = function(){
    const bEl = document.getElementById('nolo-b');
    if(!bEl || !bEl.value){ try{toast('Pick a vibe to compare')}catch(e){} return; }
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
})();
