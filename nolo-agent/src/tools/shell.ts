import { tool } from '@openrouter/agent/tool';
import { z } from 'zod';
import { spawn } from 'child_process';
import { shellNeedsApproval } from './policy.js';

const DEFAULT_TIMEOUT_S = 120;
const MAX_OUTPUT_BYTES = 256 * 1024;
const MAX_OUTPUT_LINES = 2000;

function truncateOutput(raw: string): { output: string; truncated: boolean } {
  let output = raw;
  let truncated = false;
  if (Buffer.byteLength(output, 'utf-8') > MAX_OUTPUT_BYTES) {
    output = Buffer.from(output, 'utf-8').subarray(-MAX_OUTPUT_BYTES).toString('utf-8');
    truncated = true;
  }
  const lines = output.split('\n');
  if (lines.length > MAX_OUTPUT_LINES) {
    output = lines.slice(-MAX_OUTPUT_LINES).join('\n');
    truncated = true;
  }
  return { output, truncated };
}

export function runShell(command: string, timeoutSeconds = DEFAULT_TIMEOUT_S, signal?: AbortSignal) {
  return new Promise<{ output: string; exitCode: number | null; timedOut: boolean; truncated: boolean }>((resolvePromise) => {
    const shell = process.env.SHELL || '/bin/bash';
    // detached → own process group, so a timeout can kill the whole tree.
    const child = spawn(shell, ['-c', command], {
      cwd: process.cwd(),
      detached: process.platform !== 'win32',
      env: { ...process.env, TERM: 'dumb', NO_COLOR: '1' },
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    let combined = '';
    let timedOut = false;
    const append = (chunk: Buffer) => {
      combined += chunk.toString('utf-8');
      // Keep memory bounded while a chatty command runs.
      if (combined.length > MAX_OUTPUT_BYTES * 2) combined = combined.slice(-MAX_OUTPUT_BYTES);
    };
    child.stdout.on('data', append);
    child.stderr.on('data', append);

    const killTree = () => {
      try {
        if (process.platform !== 'win32' && child.pid) process.kill(-child.pid, 'SIGKILL');
        else child.kill('SIGKILL');
      } catch {
        /* already gone */
      }
    };

    const timer = setTimeout(() => {
      timedOut = true;
      killTree();
    }, timeoutSeconds * 1000);

    const onAbort = () => killTree();
    signal?.addEventListener('abort', onAbort, { once: true });

    child.on('error', (err) => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
      resolvePromise({ output: `Failed to start shell: ${err.message}`, exitCode: null, timedOut: false, truncated: false });
    });

    child.on('close', (code) => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
      const { output, truncated } = truncateOutput(combined);
      resolvePromise({ output, exitCode: code, timedOut, truncated });
    });
  });
}

export const shellTool = tool({
  name: 'shell',
  description:
    'Execute a shell command in the working directory and return its combined stdout/stderr and exit code. Long output is truncated to the last 2000 lines. Prefer glob/grep/file_read for exploring files.',
  inputSchema: z.object({
    command: z.string().describe('Shell command to execute'),
    timeout: z.number().optional().describe(`Timeout in seconds (default ${DEFAULT_TIMEOUT_S})`),
  }),
  requireApproval: ({ command }) => shellNeedsApproval(command),
  execute: async ({ command, timeout }, ctx) => {
    const result = await runShell(command, timeout ?? DEFAULT_TIMEOUT_S, ctx?.signal);
    return {
      output: result.output,
      exitCode: result.exitCode,
      ...(result.timedOut && { timedOut: true, hint: `Killed after ${timeout ?? DEFAULT_TIMEOUT_S}s; pass a larger timeout or run it in the background.` }),
      ...(result.truncated && { truncated: true }),
    };
  },
});
