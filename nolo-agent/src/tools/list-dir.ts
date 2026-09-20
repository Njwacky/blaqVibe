import { tool } from '@openrouter/agent/tool';
import { z } from 'zod';
import { readdir } from 'fs/promises';
import { join } from 'path';
import { guardPath, isProtectedPath } from './guard.js';

const MAX_ENTRIES = 500;

export const listDirTool = tool({
  name: 'list_dir',
  description: 'List the contents of a directory inside the BlaqVibes workspace. Directories are suffixed with "/"; protected entries are marked "(protected)" and cannot be opened.',
  inputSchema: z.object({
    path: z.string().optional().describe('Directory to list, relative to the workspace root (default: the root)'),
  }),
  execute: async ({ path }) => {
    const check = guardPath(path ?? '.', 'read');
    if (!check.ok) return { error: check.error };
    const abs = check.abs;
    try {
      const entries = await readdir(abs, { withFileTypes: true });
      // Protected entries are listed but labelled, so the model knows they
      // exist and knows not to ask for them.
      const names = entries
        .map((e) => (e.isDirectory() ? `${e.name}/` : e.name) + (isProtectedPath(join(abs, e.name)) ? '  (protected)' : ''))
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
