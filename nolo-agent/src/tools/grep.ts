import { tool } from '@openrouter/agent/tool';
import { z } from 'zod';
import { execFile } from 'child_process';
import { readdir, readFile, stat } from 'fs/promises';
import { join, relative, resolve } from 'path';
import { promisify } from 'util';
import { guardPath, isProtectedPath, workspaceRoot } from './guard.js';

const execFileAsync = promisify(execFile);
const MAX_RESULTS = 100;
const SKIP_DIRS = new Set(['node_modules', '.git', '__pycache__', '.venv', 'venv', 'env', 'staticfiles', 'media', 'dist', '.sessions']);

type Match = { file: string; line: number; content: string };

let rgAvailable: boolean | null = null;
async function hasRipgrep(): Promise<boolean> {
  if (rgAvailable !== null) return rgAvailable;
  try {
    await execFileAsync('rg', ['--version']);
    rgAvailable = true;
  } catch {
    rgAvailable = false;
  }
  return rgAvailable;
}

async function grepWithRipgrep(pattern: string, path: string, fileGlob?: string, ignoreCase?: boolean): Promise<Match[]> {
  const args = ['--line-number', '--no-heading', '--color', 'never', '--max-columns', '400'];
  if (ignoreCase) args.push('-i');
  if (fileGlob) args.push('-g', fileGlob);
  args.push('-e', pattern, path);
  let stdout = '';
  try {
    ({ stdout } = await execFileAsync('rg', args, { maxBuffer: 8 * 1024 * 1024 }));
  } catch (err: any) {
    // Exit code 1 = no matches; anything else is a real error.
    if (err.code === 1) return [];
    throw new Error(err.stderr?.toString().trim() || err.message);
  }
  const matches: Match[] = [];
  const skipped = new Set<string>();
  for (const line of stdout.split('\n')) {
    if (!line) continue;
    const m = line.match(/^(.*?):(\d+):(.*)$/);
    if (!m) continue;
    if (skipped.has(m[1])) continue;
    if (isProtectedPath(m[1])) {
      skipped.add(m[1]);
      continue;
    }
    matches.push({ file: relative(workspaceRoot(), resolve(m[1])) || m[1], line: Number(m[2]), content: m[3].trimEnd() });
    if (matches.length >= MAX_RESULTS + 1) break;
  }
  return matches;
}

function globToRegExp(fileGlob: string): RegExp {
  const escaped = fileGlob
    .replace(/[.+^${}()|[\]\\]/g, '\\$&')
    .replace(/\*\*/g, '\u0000')
    .replace(/\*/g, '[^/]*')
    .replace(/\?/g, '[^/]')
    .replace(/\u0000/g, '.*');
  return new RegExp(`(^|/)${escaped}$`);
}

async function grepWithNode(pattern: string, path: string, fileGlob?: string, ignoreCase?: boolean): Promise<Match[]> {
  const re = new RegExp(pattern, ignoreCase ? 'i' : '');
  const fileRe = fileGlob ? globToRegExp(fileGlob) : null;
  const matches: Match[] = [];

  async function searchFile(file: string): Promise<void> {
    let text: string;
    try {
      text = await readFile(file, 'utf-8');
    } catch {
      return;
    }
    if (text.includes('\u0000')) return; // binary
    const lines = text.split('\n');
    for (let i = 0; i < lines.length && matches.length <= MAX_RESULTS; i++) {
      if (re.test(lines[i])) {
        matches.push({ file: relative(workspaceRoot(), file) || file, line: i + 1, content: lines[i].slice(0, 400).trimEnd() });
      }
    }
  }

  async function walk(dir: string): Promise<void> {
    if (matches.length > MAX_RESULTS) return;
    let entries;
    try {
      entries = await readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      if (matches.length > MAX_RESULTS) return;
      const full = join(dir, entry.name);
      if (isProtectedPath(full)) continue;
      if (entry.isDirectory()) {
        if (!SKIP_DIRS.has(entry.name)) await walk(full);
      } else if (entry.isFile()) {
        if (!fileRe || fileRe.test(full)) await searchFile(full);
      }
    }
  }

  const info = await stat(path);
  if (info.isDirectory()) await walk(path);
  else await searchFile(path);
  return matches;
}

export const grepTool = tool({
  name: 'grep',
  description: 'Search file contents inside the BlaqVibes workspace with a regular expression. Uses ripgrep when available. Returns up to 100 matches as file:line:content; protected files are skipped.',
  inputSchema: z.object({
    pattern: z.string().describe('Regex pattern to search for'),
    path: z.string().optional().describe('Directory or file to search, relative to the workspace root (default: the root)'),
    glob: z.string().optional().describe('File filter, e.g. "*.py" or "templates/**/*.html"'),
    ignoreCase: z.boolean().optional().describe('Case-insensitive search'),
  }),
  execute: async ({ pattern, path, glob: fileGlob, ignoreCase }) => {
    const check = guardPath(path ?? '.', 'read');
    if (!check.ok) return { error: check.error };
    const abs = check.abs;
    try {
      const matches = (await hasRipgrep())
        ? await grepWithRipgrep(pattern, abs, fileGlob, ignoreCase)
        : await grepWithNode(pattern, abs, fileGlob, ignoreCase);
      const truncated = matches.length > MAX_RESULTS;
      return {
        count: truncated ? `${MAX_RESULTS}+` : matches.length,
        matches: matches.slice(0, MAX_RESULTS),
        ...(truncated && { truncated: true, hint: 'More than 100 matches; narrow the pattern, path or glob.' }),
      };
    } catch (err: any) {
      if (err.code === 'ENOENT') return { error: `Path not found: ${abs}` };
      return { error: err.message };
    }
  },
});
