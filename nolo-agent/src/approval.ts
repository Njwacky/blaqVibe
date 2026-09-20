import type { Interface } from 'readline';
import type { PermissionRequestPayload, PermissionRequestResult } from '@openrouter/agent';

const RESET = '\x1b[0m';
const DIM = '\x1b[2m';
const BOLD = '\x1b[1m';
const YELLOW = '\x1b[33m';
const RED = '\x1b[31m';
const GRAY = '\x1b[90m';

export interface ApprovalGateOptions {
  rl: Interface;
  /** Called right before the question is shown (stop loaders, flush output). */
  onPrompt?: () => void;
  /** Called after the user answered (restart loaders). */
  onResume?: () => void;
}

type Decision = PermissionRequestResult['decision'];

function preview(toolName: string, input: Record<string, unknown>): string[] {
  const lines: string[] = [];
  switch (toolName) {
    case 'shell':
      lines.push(`${GRAY}$${RESET} ${String(input.command ?? '')}`);
      break;
    case 'file_write': {
      const content = String(input.content ?? '');
      lines.push(`${DIM}path${RESET}  ${String(input.path ?? '')}`);
      lines.push(`${DIM}size${RESET}  ${content.split('\n').length} lines`);
      for (const l of content.split('\n').slice(0, 8)) lines.push(`  ${GRAY}│${RESET} ${l.slice(0, 100)}`);
      if (content.split('\n').length > 8) lines.push(`  ${GRAY}│ …${RESET}`);
      break;
    }
    case 'file_edit': {
      lines.push(`${DIM}path${RESET}  ${String(input.path ?? '')}`);
      const edits = Array.isArray(input.edits) ? (input.edits as { old_text?: string; new_text?: string }[]) : [];
      for (const [i, e] of edits.slice(0, 3).entries()) {
        lines.push(`  ${DIM}edit ${i + 1}${RESET}`);
        for (const l of String(e.old_text ?? '').split('\n').slice(0, 6)) lines.push(`  ${RED}- ${l.slice(0, 100)}${RESET}`);
        for (const l of String(e.new_text ?? '').split('\n').slice(0, 6)) lines.push(`  \x1b[32m+ ${l.slice(0, 100)}${RESET}`);
      }
      if (edits.length > 3) lines.push(`  ${GRAY}… ${edits.length - 3} more edit(s)${RESET}`);
      break;
    }
    default: {
      const json = JSON.stringify(input);
      lines.push(`${DIM}args${RESET}  ${json.length > 200 ? json.slice(0, 200) + '…' : json}`);
    }
  }
  return lines;
}

/**
 * Terminal approval gate, wired in as a `PermissionRequest` lifecycle hook.
 *
 * The SDK fires the hook right before it would pause the run for a tool whose
 * `requireApproval` returned true. Answering here keeps the whole run inside
 * one `callModel` call — no state store or resume dance needed:
 *
 *   y / enter  → allow this call
 *   a          → allow this tool for the rest of the session (Codex-style cache)
 *   n          → deny; the model sees a rejected result and can change course
 */
export function createApprovalGate(opts: ApprovalGateOptions) {
  const alwaysAllow = new Set<string>();

  const ask = (question: string) => new Promise<string>((res) => opts.rl.question(question, res));

  async function decide(payload: PermissionRequestPayload): Promise<PermissionRequestResult> {
    if (alwaysAllow.has(payload.toolName)) return { decision: 'allow' };

    opts.onPrompt?.();
    const risk = payload.riskLevel === 'high' ? `${RED}high risk${RESET}` : `${YELLOW}${payload.riskLevel}${RESET}`;
    console.log(`\n${YELLOW}⚠${RESET} ${BOLD}Nolo wants to run ${payload.toolName}${RESET} ${DIM}(${risk}${DIM})${RESET}`);
    for (const line of preview(payload.toolName, payload.toolInput)) console.log(`  ${line}`);

    let decision: Decision = 'deny';
    let reason: string | undefined;
    const answer = (await ask(`  ${DIM}Allow? [y]es / [n]o / [a]lways for ${payload.toolName}:${RESET} `)).trim().toLowerCase();
    if (answer === '' || answer === 'y' || answer === 'yes') {
      decision = 'allow';
    } else if (answer === 'a' || answer === 'always') {
      alwaysAllow.add(payload.toolName);
      decision = 'allow';
    } else {
      reason = 'The user declined this action. Ask before trying an alternative.';
    }
    console.log(decision === 'allow' ? `  ${DIM}✓ allowed${RESET}\n` : `  ${DIM}✗ denied${RESET}\n`);
    opts.onResume?.();
    return reason ? { decision, reason } : { decision };
  }

  return {
    /** Inline hook config to spread into callModel({ hooks }) */
    hooks: {
      PermissionRequest: [{ handler: (payload: PermissionRequestPayload) => decide(payload) }],
    },
    alwaysAllow,
    reset: () => alwaysAllow.clear(),
  };
}

export type ApprovalGate = ReturnType<typeof createApprovalGate>;
