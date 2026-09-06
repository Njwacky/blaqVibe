/* Feed page — hero terminal typing loop */
const terminalBody = document.getElementById('terminalBody');

/* Frosted filter bar — the .filter-bar is position: sticky, so once the page
   scrolls far enough it pins and cards slide underneath it. The glass state
   (.is-stuck, styled in blaqvibes.css) turns it translucent + backdrop-blurred so the content
   passing under reads as frosted glass instead of a flat opaque slab. The
   sticky offset differs per breakpoint (64px mobile / 12px desktop rail), so
   read it from the computed style instead of hard-coding. */
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
const sequence = [
  { type: 'cmd', text: 'pip install vibe-cli' },
  { type: 'log', text: 'Collecting packages...' },
  { type: 'success', text: 'Installed v2.0.0' },
  { type: 'cmd', text: 'vibe generate "neon city"' },
  { type: 'log', text: 'Processing assets...' },
  { type: 'log', text: 'Optimizing shaders...' },
  { type: 'success', text: 'Build complete!' },
  { type: 'cmd', text: 'publish vibe' },
  { type: 'success', text: 'On the feed — preview files' },
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
