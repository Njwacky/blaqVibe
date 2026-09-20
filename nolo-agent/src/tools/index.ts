import { serverTool } from '@openrouter/agent';
import type { AgentConfig } from '../config.js';
import { fileReadTool } from './file-read.js';
import { fileWriteTool } from './file-write.js';
import { fileEditTool } from './file-edit.js';
import { globTool } from './glob.js';
import { grepTool } from './grep.js';
import { listDirTool } from './list-dir.js';
import { shellTool } from './shell.js';
import { approvalState } from './policy.js';
import { configureGuard, redactDeep } from './guard.js';

/** Tools whose calls can change the machine — these are the ones the approval gate covers. */
export const MUTATING_TOOLS = new Set(['file_write', 'file_edit', 'shell']);

type AnyLocalTool = { function: { execute: (...args: any[]) => Promise<unknown> | unknown } };

/**
 * Every local tool result passes through the secret redactor before it is
 * handed to the model. The tools already refuse protected files; this is the
 * second layer for credentials that live in ordinary files (a key pasted into
 * a settings module, a token in a log, a DSN in a test fixture).
 */
function guarded<T extends AnyLocalTool>(t: T): T {
  const original = t.function.execute;
  t.function.execute = async (...args: unknown[]) => {
    const { value, redacted } = redactDeep(await original(...args));
    if (redacted && value && typeof value === 'object' && !Array.isArray(value)) {
      return { ...(value as Record<string, unknown>), redacted: true, redactionNote: 'Credential-looking values were masked before reaching Nolo.' };
    }
    return value;
  };
  return t;
}

export function buildTools(config: AgentConfig) {
  approvalState.policy = config.approvalPolicy;
  configureGuard(process.cwd(), config.workspace);
  return [
    // User-defined tools — executed client-side, inside the workspace guard
    guarded(fileReadTool),
    guarded(fileWriteTool),
    guarded(fileEditTool),
    guarded(globTool),
    guarded(grepTool),
    guarded(listDirTool),
    guarded(shellTool),

    // Server tools — executed by OpenRouter, no client implementation needed.
    // web_search is for third-party docs; the system prompt forbids using it
    // as a source of truth about BlaqVibes itself.
    serverTool({ type: 'web_search' }),
    serverTool({ type: 'openrouter:datetime', parameters: { timezone: 'Africa/Johannesburg' } }),
  ] as const;
}

export type NoloTools = ReturnType<typeof buildTools>;
