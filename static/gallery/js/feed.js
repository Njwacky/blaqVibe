/* Feed page — hero terminal typing loop */
const terminalBody = document.getElementById('terminalBody');

/* Frosted filter bar — the .filter-bar is position: sticky, so once the page
   scrolls far enough it pins and cards slide underneath it. The glass state
   (.is-stuck, styled in blaqvibes.css) turns it translucent + backdrop-blurred so the content
   passing under reads as frosted glass instead of a flat opaque slab. The
   sticky offset is the measured mobile nav height (--bv-nav-h + 12px, or 12px
   on the desktop rail), so read it from the computed style instead of
   hard-coding. */
const filterBar = document.querySelector('.filter-bar');
if (filterBar) {
  let ticking = false;
  const updateFilterBar = () => {
    ticking = false;
    const stickyTop = parseFloat(getComputedStyle(filterBar).top) || 0;
    filterBar.classList.toggle(
      'is-stuck',
      filterBar.getBoundingClientRect().top <= stickyTop + 0.5
    );
  };
  const onScroll = () => {
    if (!ticking) {
      ticking = true;
      requestAnimationFrame(updateFilterBar);
    }
  };
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll, { passive: true });
  updateFilterBar();
}
const PROMPT_HTML = `<span class="c-path">~/vibe</span><span class="c-prompt">$</span> `;
const MAX_LINES = 6;
/* The loop we actually sell: build, show, remix, compete (§5, §19). */
const sequence = [
  { type: 'cmd', text: 'blaq publish stock-tracker' },
  { type: 'success', text: 'Published — live preview on' },
  { type: 'log', text: '@thando starred your project' },
  { type: 'log', text: '@zanele remixed it: dark-mode edition' },
  { type: 'cmd', text: 'blaq forks stock-tracker' },
  { type: 'success', text: 'Family tree: 2 remixes, 3 builders' },
  { type: 'cmd', text: 'blaq join daily-challenge' },
  { type: 'success', text: 'Entry in — see you on the feed' },
];
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function typeText(element, text, speed = 20) {
  for (let i = 0; i < text.length; i++) {
    element.textContent += text.charAt(i);
    await sleep(speed + Math.random() * 15);
  }
}
function manageLineLimit() {
  const lines = Array.from(terminalBody.querySelectorAll('.line'));
  if (lines.length > MAX_LINES) {
    const oldest = lines[0];
    oldest.classList.add('fading');
    setTimeout(() => {
      if (oldest.parentNode) oldest.parentNode.removeChild(oldest);
    }, 400);
  }
}
async function runTerminalLoop() {
  terminalBody.innerHTML = '';
  for (const step of sequence) {
    const line = document.createElement('div');
    line.className = 'line';
    line.innerHTML = PROMPT_HTML;
    const contentSpan = document.createElement('span');
    if (step.type === 'cmd') contentSpan.className = 'c-cmd';
    if (step.type === 'log') contentSpan.className = 'c-log';
    if (step.type === 'success') contentSpan.className = 'c-success';
    const cursor = document.createElement('span');
    cursor.className = 'cursor';
    line.appendChild(contentSpan);
    line.appendChild(cursor);
    terminalBody.appendChild(line);
    manageLineLimit();
    await typeText(contentSpan, step.text, step.type === 'cmd' ? 30 : 15);
    line.removeChild(cursor);
    await sleep(400);
  }
  await sleep(1500);
  runTerminalLoop();
}
window.addEventListener('DOMContentLoaded', function () {
  if (!terminalBody) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (window.matchMedia('(max-width: 900px)').matches) return;
  runTerminalLoop();
});
