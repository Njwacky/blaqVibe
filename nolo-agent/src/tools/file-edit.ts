import { tool } from '@openrouter/agent/tool';
import { z } from 'zod';
import { readFile, writeFile } from 'fs/promises';
import { guardPath } from './guard.js';
import { writeNeedsApproval } from './policy.js';

const CONTEXT_LINES = 3;

function countOccurrences(haystack: string, needle: string): number {
  if (!needle) return 0;
  let count = 0;
  let idx = haystack.indexOf(needle);
  while (idx !== -1) {
    count++;
    idx = haystack.indexOf(needle, idx + needle.length);
  }
  return count;
}

/**
 * Build one unified-diff hunk for a search/replace at `index` in `content`.
 * We know exactly where the change is, so no general diff algorithm is needed:
 * `-` lines for the old text, `+` lines for the new text, plus context.
 */
function hunkFor(content: string, index: number, oldText: string, newText: string): string {
  const before = content.slice(0, index);
  const after = content.slice(index + oldText.length);

  const beforeLines = before.split('\n');
  const afterLines = after.split('\n');
  // The line containing the start of the match is the last "before" line; it
  // may be partial, so stitch the partial pieces onto the old/new blocks.
  const prefix = beforeLines.pop() ?? '';
  const suffix = afterLines.shift() ?? '';

  const oldLines = (prefix + oldText + suffix).split('\n');
  const newLines = (prefix + newText + suffix).split('\n');

  const ctxBefore = beforeLines.slice(-CONTEXT_LINES);
  const ctxAfter = afterLines.slice(0, CONTEXT_LINES);

  const oldStart = beforeLines.length - ctxBefore.length + 1;
  const oldCount = ctxBefore.length + oldLines.length + ctxAfter.length;
  const newCount = ctxBefore.length + newLines.length + ctxAfter.length;

  const out: string[] = [`@@ -${oldStart},${oldCount} +${oldStart},${newCount} @@`];
  for (const l of ctxBefore) out.push(' ' + l);
  for (const l of oldLines) out.push('-' + l);
  for (const l of newLines) out.push('+' + l);
  for (const l of ctxAfter) out.push(' ' + l);
  return out.join('\n');
}

export const fileEditTool = tool({
  name: 'file_edit',
  description:
    'Apply one or more exact search-and-replace edits to a file. Each old_text must appear exactly once in the file (include enough surrounding lines to make it unique). Returns a unified diff of what changed.',
  inputSchema: z.object({
    path: z.string().describe('Path to the file, relative to the workspace root'),
    edits: z
      .array(
        z.object({
          old_text: z.string().describe('Exact text to find (must match exactly once)'),
          new_text: z.string().describe('Replacement text'),
        }),
      )
      .min(1)
      .describe('Edits applied in order'),
  }),
  requireApproval: ({ path }) => guardPath(path, 'write').ok && writeNeedsApproval(path),
  execute: async ({ path, edits }) => {
    const check = guardPath(path, 'write');
    if (!check.ok) return { error: check.error };
    const abs = check.abs;
    let content: string;
    try {
      content = await readFile(abs, 'utf-8');
    } catch (err: any) {
      if (err.code === 'ENOENT') return { error: `File not found: ${abs}` };
      return { error: err.message };
    }

    const hunks: string[] = [];
    for (const [i, { old_text, new_text }] of edits.entries()) {
      if (!old_text) return { error: `Edit ${i + 1}: old_text is empty` };
      const occurrences = countOccurrences(content, old_text);
      if (occurrences === 0) {
        return { error: `Edit ${i + 1}: old_text not found in ${abs}. Read the file again and copy the text exactly.` };
      }
      if (occurrences > 1) {
        return {
          error: `Edit ${i + 1}: old_text appears ${occurrences} times in ${abs}; include more surrounding lines so it is unique.`,
        };
      }
      const index = content.indexOf(old_text);
      hunks.push(hunkFor(content, index, old_text, new_text));
      content = content.slice(0, index) + new_text + content.slice(index + old_text.length);
    }

    try {
      await writeFile(abs, content, 'utf-8');
    } catch (err: any) {
      return { error: err.message };
    }

    return {
      edited: true,
      path: abs,
      edits: edits.length,
      diff: [`--- a/${check.rel}`, `+++ b/${check.rel}`, ...hunks].join('\n'),
    };
  },
});
