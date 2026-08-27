/* Battle page — swipe right/left → next battle — mobile only, desktop uses buttons */
try {
  const wrap = document.getElementById('battle-swipe');
  if (wrap) {
    let startX = 0;
    const isMobile = window.matchMedia('(max-width: 720px)').matches;
    if (isMobile) {
      wrap.addEventListener(
        'touchstart',
        (e) => {
          startX = e.touches[0].clientX;
        },
        { passive: true },
      );
      wrap.addEventListener('touchend', (e) => {
        const dx = e.changedTouches[0].clientX - startX;
        if (dx > 80) {
          window.location.href = document.getElementById('next-battle').href;
        }
        if (dx < -80) {
          window.location.href = document.getElementById('next-battle').href;
        }
      });
      wrap.style.touchAction = 'pan-y';
    }
    document.querySelectorAll('form[action*="/vote/"]').forEach((form) => {
      form.addEventListener('submit', function () {
        const choice = form.querySelector('[name=choice]').value;
        const card = form.closest('.card');
        const owner = card.textContent.match(/@\w+/)?.[0] || '@creator';
        try {
          toast(`You picked ${owner}'s app • +1 ★`);
        } catch (e) {}
      });
    });
  }
} catch (e) {}
