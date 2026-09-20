import { tool } from '@openrouter/agent/tool';
import { z } from 'zod';
// import { guardPath, checkShellCommand } from './guard.js';

/**
 * Template for a BlaqVibes-specific tool. Copy this file, give the tool a
 * name/description the model can understand, and add it to src/tools/index.ts.
 *
 * Ideas: `django_manage` (run a whitelisted manage.py command), `render_logs`
 * (tail the Render deploy log via its API), `nolo_review` (score a vibe with
 * gallery/nolo_review.py).
 *
 * Keep the workspace rules: resolve any path the model gives you with
 * `guardPath(path, 'read' | 'write')` and any command with
 * `checkShellCommand(cmd)` before acting, and wrap the tool with `guarded()`
 * in index.ts so its output is redacted like the others.
 */
export const myCustomTool = tool({
  name: 'my_tool',
  description: 'Describe what this tool does',
  inputSchema: z.object({
    // Define your input parameters here
    param: z.string().describe('Description of the parameter'),
  }),
  // Optional: require user approval before execution
  // requireApproval: true,
  execute: async ({ param }) => {
    // Implement your tool logic here
    return { result: `done: ${param}` };
  },
});
