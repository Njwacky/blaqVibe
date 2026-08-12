// Detail page - external JS, no inline, backend only data via data-attributes
// 5 Whys: Why external? Inline JS can see secrets in view-source. Why data-attributes? Django vars passed safely via DOM, not JS string.
(function(){
  // Scan poll - backend only status string
  const banner = document.getElementById('scan-banner');
  const scanText = document.getElementById('scan-text');
  const scanUrl = banner ? banner.dataset.scanUrl : null;
  if(scanUrl){
    (function poll(){
      fetch(scanUrl).then(r=>r.json()).then(d=>{
        if(scanText) scanText.textContent=d.status;
        if(d.is_published || d.status==='clean'){
          if(scanText) scanText.textContent='clean — vibe is live!';
          if(banner){ banner.style.background='linear-gradient(135deg,#0A1A0A 0%,#0A1A14 100%)'; banner.style.borderColor='#10B981'; }
          try{ toast('✓ Your vibe is live! We promised we’d tell you — it’s uploaded!'); }catch(e){}
          setTimeout(()=>location.reload(),1200);
        } else if(d.status==='quarantined'){
          if(banner){ banner.style.background='#3F1A1A'; banner.style.borderColor='#EF4444'; }
          if(scanText) scanText.textContent='quarantined — blocked for review';
          try{ toast('Your vibe was quarantined — check My Vibes for details.'); }catch(e){}
        } else setTimeout(poll, 2000);
      }).catch(()=>setTimeout(poll,3000));
    })();
  }

  // Tree render - backend file_tree via data-tree
  const treeRoot = document.getElementById('tree-root');
  if(treeRoot){
    const treeData = treeRoot.dataset.tree;
    const slug = treeRoot.dataset.slug;
    if(treeData){
      try{
        const tree = JSON.parse(treeData);
        function renderTree(node, prefix){
          let html="<ul style='list-style:none;margin-left:12px;border-left:1px solid #232326;padding-left:10px'>";
          for(const [k,v] of Object.entries(node)){
            const safeK = k.replace(/'/g, "\\'");
            if(v===null){
              html+=`<li style="color:#CFCFD9;cursor:pointer" data-path="${prefix}${safeK}">\u00A0📄 ${k}</li>`;
            } else {
              html+=`<li><span style="color:#F59E0B;cursor:pointer">📁 ${k}/</span>${renderTree(v,prefix+k+'/')}</li>`;
            }
          }
          html+="</ul>"; return html;
        }
        treeRoot.innerHTML = renderTree(tree);
        treeRoot.addEventListener('click', function(e){
          const li = e.target.closest('[data-path]');
          if(!li) return;
          const path = li.dataset.path;
          fetch(`/app/${slug}/file/${encodeURIComponent(path).replace(/%2F/g,'/')}`).then(r=>r.json()).then(d=>{
            if(d.error){ try{toast(d.error);}catch(e){} return; }
            document.getElementById('fileName').textContent = d.path;
            document.getElementById('fileCode').textContent = d.content;
            document.getElementById('filePreview').style.display='block';
          }).catch(()=>{ try{toast('File not found')}catch(e){}});
        });
      }catch(e){}
    }
  }

  // External handlers for copy/star/delete/close - no inline onclick
  document.addEventListener('click', function(e){
    const copyBtn = e.target.closest('.js-copy');
    if(copyBtn){ try{ copyText(copyBtn.dataset.copy);}catch(err){} return; }
    const copySnippet = e.target.closest('.js-copy-snippet');
    if(copySnippet){ const t=document.getElementById(copySnippet.dataset.target); if(t) try{ copyText(t.innerText);}catch(err){} return; }
    const copyCode = e.target.closest('.js-copy-code');
    if(copyCode){ const t=document.getElementById(copyCode.dataset.target); if(t){ try{ copyText(t.innerText);}catch(err){} const url=copyCode.dataset.copyUrl; const csrf=copyCode.dataset.csrf; if(url&&csrf){ try{ fetch(url,{method:'POST',headers:{'X-CSRFToken':csrf}});}catch(err){} } } return; }
    const closeBtn = e.target.closest('.js-close-preview');
    if(closeBtn){ const p=document.getElementById('filePreview'); if(p) p.style.display='none'; return; }
  });
  document.addEventListener('submit', function(e){
    if(e.target.classList.contains('js-star-form')){
      e.preventDefault();
      const form=e.target;
      const csrf=form.querySelector('[name=csrfmiddlewaretoken]')?.value;
      fetch(form.action,{method:'POST',headers:csrf?{'X-CSRFToken':csrf}:{}}).then(r=>r.json()).then(d=>{
        try{ toast(d.starred?'Starred! (+1 ★ for owner)':'Unstarred'); }catch(err){}
        setTimeout(()=>location.reload(),500);
      }).catch(()=>{ try{toast('Failed')}catch(err){}});
    }
    if(e.target.classList.contains('js-delete-form')){
      if(!confirm('Delete forever?')) e.preventDefault();
    }
    if(e.target.classList.contains('js-fork-form')){
      if(!confirm('Fork this vibe to your account? You will get your own copy to remix.')) e.preventDefault();
    }
    if(e.target.id==='nolo-form'){
      e.preventDefault();
      window.noloCompare();
    }
  });

  // Nolo compare - backend only, no judgment
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
      const aFeat = (d.a.features||[]).map(f=>`<span style="background:#1A1A1E;border:1px solid #232326;padding:3px 7px;border-radius:8px;font-size:11px;margin:2px;display:inline-block">${f}</span>`).join('');
      const bFeat = (d.b.features||[]).map(f=>`<span style="background:#1A1A1E;border:1px solid #232326;padding:3px 7px;border-radius:8px;font-size:11px;margin:2px;display:inline-block">${f}</span>`).join('');
      const aLang = Object.entries(d.a.languages||{}).map(([k,v])=>k+' '+v+'%').join(', ')||"—";
      const bLang = Object.entries(d.b.languages||{}).map(([k,v])=>k+' '+v+'%').join(', ')||"—";
      el.innerHTML = `<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div><b style="color:#F59E0B">${d.a.title}</b><div style="font-size:11px;color:#9A9AAF">${d.a.tech_stack} • ${d.a.file_count} files • ★${d.a.stars}</div><div style="margin-top:6px">${aFeat}</div><div style="margin-top:6px;font-size:11px;color:#9A9AAF">Languages: ${aLang}</div></div>
            <div><b style="color:#7C3AED">${d.b.title}</b><div style="font-size:11px;color:#9A9AAF">${d.b.tech_stack} • ${d.b.file_count} files • ★${d.b.stars}</div><div style="margin-top:6px">${bFeat}</div><div style="margin-top:6px;font-size:11px;color:#9A9AAF">Languages: ${bLang}</div></div>
          </div>
          <div style="margin-top:10px;padding-top:10px;border-top:1px solid #232326">
            <div style="font-size:11px;color:#9A9AAF">Common: ${(d.diff.common||[]).join(', ')||"—"}</div>
            <div style="font-size:11px;color:#F59E0B">Only in A: ${(d.diff.only_in_a||[]).join(', ')||"—"}</div>
            <div style="font-size:11px;color:#7C3AED">Only in B: ${(d.diff.only_in_b||[]).join(', ')||"—"}</div>
            <div style="font-size:11px;color:var(--muted);margin-top:6px">Nolo doesn’t judge — you pick the function you want. Then trade stars to download.</div>
          </div>`;
    }).catch(()=>{ try{toast('Compare failed silently')}catch(e){}});
  };
})();
