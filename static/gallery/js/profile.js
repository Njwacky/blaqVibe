document.addEventListener('click', function(e){
  const btn = e.target.closest('.js-follow-btn');
  if(!btn || btn.disabled) return;
  const username = btn.dataset.username;
  const csrf = btn.dataset.csrf;
  if(!username || !csrf) return;
  btn.disabled = true;
  fetch(`/u/${username}/follow/`, {
    method:'POST',
    headers:{'X-CSRFToken': csrf},
    credentials:'same-origin'
  })
  .then(r=>r.json())
  .then(d=>{
    btn.disabled = false;
    if(d.following){
      btn.textContent='✓ Following';
      btn.style.background='var(--line)';
      btn.style.color='var(--text)';
      try{toast('Following @'+username);}catch(e){}
    } else {
      btn.textContent='Follow';
      btn.style.background='var(--violet)';
      btn.style.color='var(--on-accent)';
      try{toast('Unfollowed');}catch(e){}
    }
    if(btn.id === 'follow-btn'){
      const cnt = document.getElementById('followers-count');
      if(cnt && typeof d.followers === 'number') cnt.textContent = d.followers;
    }
  })
  .catch(()=>{
    btn.disabled = false;
    try{toast('Failed');}catch(e){}
  });
});
