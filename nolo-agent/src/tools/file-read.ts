import { tool } from '@openrouter/agent/tool';
import { z } from 'zod';
import { readFile } from 'fs/promises';
import { extname, resolve } from 'path';

const DEFAULT_LINE_LIMIT = 2000;
const MAX_LINE_CHARS = 2000;

const IMAGE_MIME: Record<string, string> = {
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.png': 'image/png',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
};

export const fileReadTool = tool({
  name: 'file_read',
  description:
    'Read the contents of a file. Output is capped at 2000 lines by default (use offset/limit to paginate) and any line longer than 2000 characters is truncated. When the response is truncated, the hint field tells you how to continue. Image files are returned as base64.',
  inputSchema: z.object({
    path: z.string().describe('Path to the file (absolute, or relative to the working directory)'),
    offset: z.number().optional().describe('Start reading from this line (1-indexed)'),
    limit: z.number().optional().describe(`Maximum lines to return (default ${DEFAULT_LINE_LIMIT})`),
  }),
  execute: async ({ path, offset, limit }) => {
    const abs = resolve(path);
    try {
      const mime = IMAGE_MIME[extname(abs).toLowerCase()];
      if (mime) {
        const data = await readFile(abs);
        return { type: 'image', mimeType: mime, data: data.toString('base64') };
      }

      const content = await readFile(abs, 'utf-8');
      const lines = content.split('\n');

      const start = offset ? Math.max(0, offset - 1) : 0;
      const end = Math.min(start + (limit ?? DEFAULT_LINE_LIMIT), lines.length);
      let longLines = 0;
      const slice = lines.slice(start, end).map((line) => {
        if (line.length <= MAX_LINE_CHARS) return line;
        longLines++;
        return line.slice(0, MAX_LINE_CHARS) + `… [line truncated, ${line.length - MAX_LINE_CHARS} chars dropped]`;
      });
      const tailTruncated = end < lines.length;
      const truncated = tailTruncated || longLines > 0;
      const hintParts: string[] = [`Showing lines ${start + 1}-${end} of ${lines.length}.`];
      if (tailTruncated) hintParts.push(`Use offset=${end + 1} to continue.`);
      if (longLines > 0) {
        hintParts.push(
          `${longLines} line(s) exceeded ${MAX_LINE_CHARS} chars and were per-line truncated; use grep to fetch content from those lines.`,
        );
      }

      return {
        content: slice.join('\n'),
        totalLines: lines.length,
        ...(truncated && {
          truncated: true,
          ...(tailTruncated && { nextOffset: end + 1 }),
          hint: hintParts.join(' '),
        }),
      };
    } catch (err: any) {
      if (err.code === 'ENOENT') return { error: `File not found: ${abs}` };
      if (err.code === 'EACCES') return { error: `Permission denied: ${abs}` };
      if (err.code === 'EISDIR') return { error: `Is a directory (use list_dir): ${abs}` };
      return { error: err.message };
    }
  },
});
