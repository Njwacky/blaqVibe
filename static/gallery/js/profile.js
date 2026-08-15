// Profile - external JS, no inline secrets
document.addEventListener('click', function(e){
  const btn = e.target.closest('.js-follow-btn');
  if(btn){
    const username = btn.dataset.username;
    const csrf = btn.dataset.csrf;
    fetch(`/u/${username}/follow/`, {method:'POST', headers:{'X-CSRFToken': csrf}})
      .then(r=>r.json()).then(d=>{
        const b=document.getElementById('follow-btn');
        const cnt=document.getElementById('followers-count');
        if(!b) return;
        if(d.following){ b.textContent='✓ Following'; b.style.background='var(--input)'; b.style.color='var(--text)'; try{toast('Following @'+username);}catch(e){} }
        else { b.textContent='Follow'; b.style.background='var(--violet)'; b.style.color='var(--on-accent)'; try{toast('Unfollowed');}catch(e){} }
        if(cnt) cnt.textContent=d.followers;
      }).catch(()=>{ try{toast('Failed')}catch(e){} });
  }
});
