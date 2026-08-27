/* Nolo Chat — send prompts to the nolo_chat_api endpoint.
   API URL + CSRF token are server-rendered into data attributes on
   #nolo-chat-input-row (this file carries no template tags). */
(function () {
  const inputRow = document.getElementById('nolo-chat-input-row');
  const sendBtn = document.getElementById('nolo-chat-send');
  const input = document.getElementById('nolo-chat-input');
  const messages = document.getElementById('nolo-chat-messages');
  const errorBox = document.getElementById('nolo-chat-error');
  if (!sendBtn || !input || !messages) return;
  const apiUrl = (inputRow && inputRow.dataset.apiUrl) || '';
  const csrfToken = (inputRow && inputRow.dataset.csrf) || '';
  function showError(text) {
    if (!errorBox) return;
    errorBox.textContent = text;
    errorBox.style.display = 'block';
    setTimeout(() => { errorBox.style.display = 'none'; }, 5000);
  }
  function appendMessage(role, text) {
    const bubble = document.createElement('div');
    bubble.style.padding = '16px';
    bubble.style.border = '1px solid var(--line)';
    bubble.style.borderRadius = '18px';
    bubble.style.lineHeight = '1.7';
    bubble.style.color = 'var(--text)';
    bubble.style.background = role === 'user' ? 'var(--accent-soft-bg)' : 'var(--card)';
    bubble.innerHTML = `<div style="font-size:12px;color:var(--muted);margin-bottom:10px">${role === 'user' ? 'You said' : 'Nolo says'}</div><div>${text.replace(/\n/g, '<br>')}</div>`;
    messages.appendChild(bubble);
    bubble.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }
  async function sendPrompt() {
    const prompt = input.value.trim();
    if (!prompt) { showError('Type a question for Nolo first.'); return; }
    appendMessage('user', prompt);
    input.value = '';
    try {
      const response = await fetch(apiUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({ prompt }),
      });
      const data = await response.json();
      if (!response.ok || data.error) {
        showError(data.error || 'Nolo could not answer right now.');
        return;
      }
      const src = data.source && data.source !== 'heuristic' ? ` (${data.source})` : ' (built-in helper)';
      appendMessage('nolo', (data.reply || 'Nolo did not return an answer.') + src);
    } catch (err) {
      showError('Network error — try again.');
    }
  }
  sendBtn.addEventListener('click', sendPrompt);
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); sendPrompt(); }
  });
})();
