import { tool } from '@openrouter/agent/tool';
import { z } from 'zod';
import { mkdir, writeFile } from 'fs/promises';
import { dirname, resolve } from 'path';
import { writeNeedsApproval } from './policy.js';

export const fileWriteTool = tool({
  name: 'file_write',
  description:
    'Write content to a file, creating it (and any missing parent directories) if needed. Overwrites existing content — use file_edit for targeted changes.',
  inputSchema: z.object({
    path: z.string().describe('Path to the file (absolute, or relative to the working directory)'),
    content: z.string().describe('Full file content to write'),
  }),
  requireApproval: ({ path }) => writeNeedsApproval(path),
  execute: async ({ path, content }) => {
    const abs = resolve(path);
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
