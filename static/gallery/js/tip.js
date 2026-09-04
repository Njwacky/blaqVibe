(function(){
  const tipBtn = document.getElementById('tip-btn');
  const tipPanel = document.getElementById('tip-panel');
  if(!tipBtn || !tipPanel) return;
  const username = tipBtn.dataset.username;
  const csrf = tipBtn.dataset.csrf;
  if(!username || !csrf) return;

  tipBtn.addEventListener('click', function(){
    tipPanel.style.display = tipPanel.style.display === 'none' ? 'block' : 'none';
  });
  document.querySelectorAll('.js-tip-preset').forEach(function(b){
    b.addEventListener('click', function(){
      const input = document.getElementById('tip-amount');
      if(input) input.value = b.dataset.amount;
    });
  });

  const sendBtn = document.getElementById('tip-send');
  if(!sendBtn) return;
  sendBtn.addEventListener('click', function(){
    if(sendBtn.disabled) return;
    const input = document.getElementById('tip-amount');
    const msg = document.getElementById('tip-message');
    const amount = parseInt(input ? input.value : '', 10);
    if(!amount || amount < 1){
      try{toast('Enter at least 1★');}catch(e){}
      return;
    }
    sendBtn.disabled = true;
    const body = new URLSearchParams();
    body.append('amount', amount);
    if(msg && msg.value) body.append('message', msg.value);
    fetch(`/u/${username}/tip/`, {
      method:'POST',
      headers:{'X-CSRFToken': csrf},
      credentials:'same-origin',
      body: body
    })
    .then(r=>r.json())
    .then(d=>{
      sendBtn.disabled = false;
      if(d.ok){
        const bal = document.getElementById('tip-balance');
        if(bal) bal.textContent = 'Your balance: ' + d.balance + ' ★';
        tipPanel.style.display = 'none';
        const listEl = document.getElementById('recent-tips-list');
        const totalEl = document.getElementById('tips-total');
        if(listEl){
          const row = document.createElement('div');
          row.style.cssText='display:flex;gap:8px;align-items:center;padding:6px 0;border-top:1px solid var(--line);font-size:12px;margin-top:6px';
          const a = document.createElement('a');
          a.href = '/u/' + encodeURIComponent(username) + '/';
          a.textContent = '@' + username;
          a.style.cssText='color:var(--link);font-weight:700';
          const amt = document.createElement('span');
          amt.textContent = '+' + d.amount + '★';
          amt.style.cssText='font-weight:800;color:var(--warning-text)';
          const note = document.createElement('span');
          if(d.message){ note.textContent = '“' + d.message + '”'; note.style.cssText='color:var(--muted);flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'; }
          row.append(a, amt, note);
          listEl.prepend(row);
        }
        if(totalEl){
          const cur = parseInt(totalEl.textContent.replace(/[^0-9]/g,''), 10) || 0;
          totalEl.textContent = (cur + d.amount) + '★ total';
        }
        try{toast('Tipped ' + d.amount + '★');}catch(e){}
      } else {
        try{toast(d.error || 'Failed');}catch(e){}
      }
    })
    .catch(()=>{
      sendBtn.disabled = false;
      try{toast('Failed');}catch(e){}
    });
  });
})();
