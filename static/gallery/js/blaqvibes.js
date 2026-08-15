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
      const links = document.getElementById('nav-links');
      const open = links ? links.classList.toggle('open') : false;
      // Keep the button's state announced — screen readers otherwise never
      // learn the panel opened, and the label stays stuck on "Open menu".
      burger.setAttribute('aria-expanded', open ? 'true' : 'false');
      burger.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
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

/* Desktop sidebar resize/collapse control. Pointer events cover mouse, pen and
   touch; keyboard users can focus the separator and use Left/Right arrows. */
(function(){
  const root = document.documentElement;
  const rail = document.getElementById('nav-resizer');
  const button = document.getElementById('nav-collapse');
  if(!rail || !button) return;

  const MIN = 180;
  const MAX = 380;
  const DEFAULT = 230;
  const WIDTH_KEY = 'blaq-nav-width';
  const COLLAPSED_KEY = 'blaq-nav-collapsed';
  let drag = null;

  const clamp = value => Math.min(MAX, Math.max(MIN, Math.round(value)));
  function width(){
    const value = parseFloat(getComputedStyle(root).getPropertyValue('--nav-w'));
    return Number.isFinite(value) ? clamp(value) : DEFAULT;
  }
  function setWidth(value, save){
    const next = clamp(value);
    root.style.setProperty('--nav-w', next + 'px');
    rail.setAttribute('aria-valuenow', String(next));
    if(save){ try{ localStorage.setItem(WIDTH_KEY, String(next)); }catch(e){} }
  }
  function collapsed(){ return root.getAttribute('data-nav-collapsed') === 'true'; }
  function setCollapsed(value){
    if(value) root.setAttribute('data-nav-collapsed', 'true');
    else root.removeAttribute('data-nav-collapsed');
    button.textContent = value ? '>>' : '<<';
    button.setAttribute('aria-label', value ? 'Expand sidebar' : 'Collapse sidebar');
    button.setAttribute('aria-expanded', value ? 'false' : 'true');
    try{ localStorage.setItem(COLLAPSED_KEY, value ? '1' : '0'); }catch(e){}
  }

  setWidth(width(), false);
  setCollapsed(collapsed());

  button.addEventListener('click', function(e){
    e.stopPropagation();
    setCollapsed(!collapsed());
  });

  rail.addEventListener('pointerdown', function(e){
    if(e.target.closest('#nav-collapse') || e.button !== 0) return;
    const wasCollapsed = collapsed();
    if(wasCollapsed) setCollapsed(false);
    drag = {pointerId: e.pointerId, startX: e.clientX, startWidth: wasCollapsed ? MIN : width()};
    setWidth(drag.startWidth, false);
    root.setAttribute('data-nav-resizing', 'true');
    rail.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  rail.addEventListener('pointermove', function(e){
    if(!drag || drag.pointerId !== e.pointerId) return;
    setWidth(drag.startWidth + e.clientX - drag.startX, false);
  });
  function finishDrag(e){
    if(!drag || drag.pointerId !== e.pointerId) return;
    setWidth(width(), true);
    root.removeAttribute('data-nav-resizing');
    drag = null;
  }
  rail.addEventListener('pointerup', finishDrag);
  rail.addEventListener('pointercancel', finishDrag);
  rail.addEventListener('dblclick', function(e){
    if(e.target.closest('#nav-collapse')) return;
    setCollapsed(false);
    setWidth(DEFAULT, true);
  });
  rail.addEventListener('keydown', function(e){
    if(e.target.closest('#nav-collapse')) return;
    let next = null;
    if(e.key === 'ArrowLeft') next = width() - 10;
    if(e.key === 'ArrowRight') next = width() + 10;
    if(e.key === 'Home') next = MIN;
    if(e.key === 'End') next = MAX;
    if(next === null) return;
    e.preventDefault();
    setCollapsed(false);
    setWidth(next, true);
  });
})();

document.addEventListener('submit', function(e){
  const confirmForm = e.target.closest('.js-confirm-delete');
  if(confirmForm){
    if(!confirm('Delete forever?')) e.preventDefault();
  }
});
