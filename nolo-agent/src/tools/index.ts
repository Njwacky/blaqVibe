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

/** Tools whose calls can change the machine — these are the ones the approval gate covers. */
export const MUTATING_TOOLS = new Set(['file_write', 'file_edit', 'shell']);

export function buildTools(config: AgentConfig) {
  approvalState.policy = config.approvalPolicy;
  return [
    // User-defined tools — executed client-side
    fileReadTool,
    fileWriteTool,
    fileEditTool,
    globTool,
    grepTool,
    listDirTool,
    shellTool,

    // Server tools — executed by OpenRouter, no client implementation needed
    serverTool({ type: 'web_search' }),
    serverTool({ type: 'openrouter:datetime', parameters: { timezone: 'Africa/Johannesburg' } }),
  ] as const;
}

export type NoloTools = ReturnType<typeof buildTools>;
