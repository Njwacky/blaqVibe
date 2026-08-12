// BlaqVibes - external JS, no inline secrets
function toast(msg){
  const t=document.getElementById('toast');
  if(!t) return;
  t.textContent=msg;
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),1800);
}
function copyText(t){
  try{
    navigator.clipboard.writeText(t).then(()=>toast('Copied!'));
  }catch(e){
    try{
      const ta=document.createElement('textarea');
      ta.value=t; document.body.appendChild(ta); ta.select();
      document.execCommand('copy'); ta.remove(); toast('Copied!');
    }catch(e2){ toast('Copy failed'); }
  }
}
document.addEventListener('click', function(e){
  const logout = e.target.closest('.js-logout');
  if(logout){ e.preventDefault(); const f=document.getElementById('logout-form'); if(f) f.submit(); return; }
  const dismiss = e.target.closest('.js-dismiss');
  if(dismiss){ dismiss.parentElement.remove(); return; }
});
document.addEventListener('submit', function(e){
  const confirmForm = e.target.closest('.js-confirm-delete');
  if(confirmForm){
    if(!confirm('Delete forever?')) e.preventDefault();
  }
});
