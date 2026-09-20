import { tool } from '@openrouter/agent/tool';
import { z } from 'zod';
import { glob } from 'glob';
import { existsSync, readFileSync } from 'fs';
import { join, resolve } from 'path';
import { guardPath, isProtectedPath } from './guard.js';

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
  description: 'Find files by glob pattern (e.g. "gallery/**/*.py") inside the BlaqVibes workspace. Respects .gitignore; protected files are omitted. Returns paths relative to the search directory.',
  inputSchema: z.object({
    pattern: z.string().describe('Glob pattern, e.g. "src/**/*.ts"'),
    path: z.string().optional().describe('Directory to search in, relative to the workspace root (default: the root)'),
  }),
  execute: async ({ pattern, path }) => {
    const check = guardPath(path ?? '.', 'read');
    if (!check.ok) return { error: check.error };
    const cwd = check.abs;
    try {
      const matches = await glob(pattern, {
        cwd,
        nodir: true,
        dot: true,
        ignore: [...ALWAYS_IGNORE, ...gitignorePatterns(cwd)],
      });
      // A pattern like "../**" or "/etc/*" can reach outside; protected files
      // never appear in results at all.
      let hidden = 0;
      const sorted = matches
        .filter((m) => {
          if (isProtectedPath(resolve(cwd, m))) {
            hidden++;
            return false;
          }
          return true;
        })
        .sort();
      return {
        cwd,
        count: sorted.length,
        files: sorted.slice(0, MAX_RESULTS),
        ...(hidden > 0 && { protectedHidden: hidden }),
        ...(sorted.length > MAX_RESULTS && { truncated: true, hint: `Showing ${MAX_RESULTS} of ${sorted.length}; narrow the pattern.` }),
      };
    } catch (err: any) {
      return { error: err.message };
    }
  },
});
