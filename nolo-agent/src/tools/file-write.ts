import { tool } from '@openrouter/agent/tool';
import { z } from 'zod';
import { mkdir, writeFile } from 'fs/promises';
import { dirname } from 'path';
import { guardPath } from './guard.js';
import { writeNeedsApproval } from './policy.js';

export const fileWriteTool = tool({
  name: 'file_write',
  description:
    'Write content to a file inside the BlaqVibes workspace, creating it (and any missing parent directories) if needed. Overwrites existing content — use file_edit for targeted changes. Protected paths (.env, keys, databases, media/, .git/) are refused.',
  inputSchema: z.object({
    path: z.string().describe('Path to the file, relative to the workspace root'),
    content: z.string().describe('Full file content to write'),
  }),
  // A blocked path never reaches the approval prompt: the answer is already no.
  requireApproval: ({ path }) => guardPath(path, 'write').ok && writeNeedsApproval(path),
  execute: async ({ path, content }) => {
    const check = guardPath(path, 'write');
    if (!check.ok) return { error: check.error };
    const abs = check.abs;
    try {
      await mkdir(dirname(abs), { recursive: true });
      await writeFile(abs, content, 'utf-8');
      return { written: true, path: abs, bytes: Buffer.byteLength(content, 'utf-8') };
    } catch (err: any) {
      if (err.code === 'EACCES') return { error: `Permission denied: ${abs}` };
      if (err.code === 'EISDIR') return { error: `Is a directory: ${abs}` };
      return { error: err.message };
    }
  },
});
