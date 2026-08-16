// Profile — external JS, no inline secrets.
// Works for the header follow button AND every follow button on the
// follower/following cards via event delegation (one listener, any number
// of buttons, no inline handlers).
// 5 Whys: Why update only the clicked button instead of #follow-btn?
// Card buttons point at OTHER users than the profile owner; rewriting the
// header button would show the wrong state. The header button alone also
// refreshes the profile owner's follower counter.
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
    // Only the header button doubles as the profile owner's follower count.
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
