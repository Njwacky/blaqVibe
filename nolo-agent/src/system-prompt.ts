import { readFileSync, existsSync } from 'fs';
import { resolve } from 'path';
import type { AgentConfig } from './config.js';

/** Project context files appended to the prompt when present in the working directory. */
const CONTEXT_FILES = ['AGENTS.md', 'CLAUDE.md', '.agent-context.md', 'NOLO.md'];
const MAX_CONTEXT_CHARS = 12_000;

export function buildSystemPrompt(config: AgentConfig): string {
  let prompt = config.systemPrompt.replace('{cwd}', process.cwd());

  for (const filename of CONTEXT_FILES) {
    const filePath = resolve(filename);
    if (!existsSync(filePath)) continue;
    try {
      let content = readFileSync(filePath, 'utf-8');
      if (content.length > MAX_CONTEXT_CHARS) content = content.slice(0, MAX_CONTEXT_CHARS) + '\n… [truncated]';
      prompt += `\n\n## ${filename}\n\n${content}`;
    } catch {
      /* unreadable context file — skip */
    }
  }

  return prompt;
}
