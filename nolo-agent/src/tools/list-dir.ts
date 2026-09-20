import { tool } from '@openrouter/agent/tool';
import { z } from 'zod';
import { readdir } from 'fs/promises';
import { resolve } from 'path';

const MAX_ENTRIES = 500;

export const listDirTool = tool({
  name: 'list_dir',
  description: 'List the contents of a directory. Directories are suffixed with "/".',
  inputSchema: z.object({
    path: z.string().optional().describe('Directory to list (default: working directory)'),
  }),
  execute: async ({ path }) => {
    const abs = resolve(path ?? process.cwd());
    try {
      const entries = await readdir(abs, { withFileTypes: true });
      const names = entries
        .map((e) => (e.isDirectory() ? `${e.name}/` : e.name))
        .sort((a, b) => a.localeCompare(b));
      return {
        path: abs,
        count: names.length,
        entries: names.slice(0, MAX_ENTRIES),
        ...(names.length > MAX_ENTRIES && { truncated: true }),
      };
    } catch (err: any) {
      if (err.code === 'ENOENT') return { error: `Directory not found: ${abs}` };
      if (err.code === 'ENOTDIR') return { error: `Not a directory: ${abs}` };
      return { error: err.message };
    }
  },
});
