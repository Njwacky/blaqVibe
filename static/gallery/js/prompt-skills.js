(() => {
  const form = document.getElementById('promptBuilder');
  if (!form) return;
  const $ = id => document.getElementById(id);
  const output = $('promptOutput');
  const count = $('tokenCount');
  const note = $('qualityNote');
  const fields = ['goal','context','constraints','format','success'];
  const estimate = text => Math.max(0, Math.ceil(text.length / 4));
  function assemble() {
    const goal = $('goal').value.trim();
    if (!goal) { output.textContent = 'Fill in the outcome to generate a prompt.'; count.textContent = '0 est. tokens'; return; }
    const context = $('context').value.trim();
    const constraints = $('constraints').value.trim();
    const format = $('format').value;
    const success = $('success').value.trim();
    let prompt = `Task\n${goal}`;
    if (context) prompt += `\n\nContext\n${context}`;
    prompt += `\n\nOutput\nReturn a ${format.toLowerCase()}.`;
    if (constraints) prompt += `\nConstraints\n${constraints}`;
    if (success) prompt += `\n\nQuality check\n${success}`;
    prompt += '\n\nIf required information is missing, state what is missing instead of guessing.';
    output.textContent = prompt;
    count.textContent = `${estimate(prompt)} est. tokens`;
    note.textContent = success ? 'Nice: you gave the model a way to verify its own output.' : 'Add a quality check to make “good” measurable. It usually saves a follow-up prompt.';
  }
  form.addEventListener('submit', e => { e.preventDefault(); assemble(); output.scrollIntoView({behavior:'smooth', block:'nearest'}); });
  fields.forEach(id => $(id).addEventListener('input', assemble));
  $('copyPrompt').addEventListener('click', async () => { if (!output.textContent || output.textContent.startsWith('Fill in')) return; await navigator.clipboard.writeText(output.textContent); $('copyPrompt').textContent = 'Copied ✓'; setTimeout(() => $('copyPrompt').textContent = 'Copy prompt', 1400); });
  $('clearPrompt').addEventListener('click', () => { ['goal','context','constraints','success'].forEach(id => $(id).value = ''); $('format').selectedIndex = 0; assemble(); $('goal').focus(); });
})();
