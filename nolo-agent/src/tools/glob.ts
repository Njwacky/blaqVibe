import { tool } from '@openrouter/agent/tool';
import { z } from 'zod';
import { glob } from 'glob';
import { existsSync, readFileSync } from 'fs';
import { join, resolve } from 'path';

const MAX_RESULTS = 1000;
const ALWAYS_IGNORE = ['**/node_modules/**', '**/.git/**', '**/__pycache__/**', '**/.venv/**', '**/staticfiles/**'];

/** Turn a project's .gitignore into glob ignore patterns (simple lines only). */
function gitignorePatterns(root: string): string[] {
  const path = join(root, '.gitignore');
  if (!existsSync(path)) return [];
  const patterns: string[] = [];
  for (const raw of readFileSync(path, 'utf-8').split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#') || line.startsWith('!')) continue;
    const clean = line.replace(/^\//, '').replace(/\/$/, '');
    if (!clean) continue;
    patterns.push(clean.includes('/') ? `${clean}/**` : `**/${clean}/**`);
    patterns.push(clean.includes('/') ? clean : `**/${clean}`);
  }
  return patterns;
}

export const globTool = tool({
  name: 'glob',
  description: 'Find files by glob pattern (e.g. "gallery/**/*.py"). Respects .gitignore. Returns paths relative to the search directory.',
  inputSchema: z.object({
    pattern: z.string().describe('Glob pattern, e.g. "src/**/*.ts"'),
    path: z.string().optional().describe('Directory to search in (default: working directory)'),
  }),
  execute: async ({ pattern, path }) => {
    const cwd = resolve(path ?? process.cwd());
    try {
      const matches = await glob(pattern, {
        cwd,
        nodir: true,
        dot: true,
        ignore: [...ALWAYS_IGNORE, ...gitignorePatterns(cwd)],
      });
      const sorted = matches.sort();
      return {
        cwd,
        count: sorted.length,
        files: sorted.slice(0, MAX_RESULTS),
        ...(sorted.length > MAX_RESULTS && { truncated: true, hint: `Showing ${MAX_RESULTS} of ${sorted.length}; narrow the pattern.` }),
      };
    } catch (err: any) {
      return { error: err.message };
    }
  },
});
