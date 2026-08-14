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
(function(){
  const KEY = 'blaq-theme';
  function apply(theme){
    document.documentElement.setAttribute('data-theme', theme);
    const btn = document.getElementById('theme-toggle');
    if(btn) btn.textContent = theme === 'light' ? '🌙' : '☀️';
    try { localStorage.setItem(KEY, theme); } catch(e){}
  }
  const saved = (()=>{ try { return localStorage.getItem(KEY); } catch(e){ return null; }})();
  const prefersLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
  apply(saved || (prefersLight ? 'light' : 'dark'));
  document.addEventListener('click', function(e){
    const themeBtn = e.target.closest('#theme-toggle');
    if(themeBtn){
      const next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
      apply(next);
      try { toast(next === 'light' ? 'Light mode' : 'Dark mode'); } catch(err){}
      return;
    }
    const burger = e.target.closest('#nav-toggle');
    if(burger){
      document.getElementById('nav-links')?.classList.toggle('open');
      return;
    }
    // Avatar dropdown — toggle on the button, close on any outside click.
    const userBtn = e.target.closest('#nav-user-btn');
    const menu = document.getElementById('nav-menu');
    if(userBtn && menu){
      const open = menu.classList.toggle('open');
      userBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      return;
    }
    if(menu && menu.classList.contains('open') && !e.target.closest('#nav-user')){
      menu.classList.remove('open');
      document.getElementById('nav-user-btn')?.setAttribute('aria-expanded','false');
    }
    const logout = e.target.closest('.js-logout');
    if(logout){ e.preventDefault(); const f=document.getElementById('logout-form'); if(f) f.submit(); return; }
    const dismiss = e.target.closest('.js-dismiss');
    if(dismiss){ dismiss.parentElement.remove(); }
  });
})();
document.addEventListener('submit', function(e){
  const confirmForm = e.target.closest('.js-confirm-delete');
  if(confirmForm){
    if(!confirm('Delete forever?')) e.preventDefault();
  }
});
