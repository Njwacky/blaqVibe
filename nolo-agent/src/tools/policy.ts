import { relative, resolve, isAbsolute } from 'path';
import type { ApprovalPolicy } from '../config.js';

/**
 * Mutable approval policy shared by every mutating tool. Tools read it at
 * call time (not at construction), so `/approve` can switch it mid-session.
 *
 *   always          — every write, edit and shell command asks first
 *   dangerous-only  — only writes outside the working directory and shell
 *                     commands that match DANGEROUS_SHELL ask
 *   never           — nothing asks (the model still gets a doom-loop guard)
 */
export const approvalState: { policy: ApprovalPolicy } = { policy: 'always' };

export const DANGEROUS_SHELL =
  /\brm\b|\bsudo\b|\bchmod\b|\bchown\b|\bdd\b|\bmkfs|\bkill\b|\bpkill\b|git\s+push|git\s+reset\s+--hard|git\s+clean|git\s+checkout\s+--|drop\s+(table|database)|flush\b|migrate\s+\S+\s+zero|truncate\b|curl[^|]*\|\s*(ba|z)?sh|wget[^|]*\|\s*(ba|z)?sh|>\s*\/dev\//i;

export function isOutsideCwd(path: string): boolean {
  const rel = relative(process.cwd(), resolve(path));
  return rel.startsWith('..') || isAbsolute(rel);
}

export function writeNeedsApproval(path: string): boolean {
  switch (approvalState.policy) {
    case 'never':
      return false;
    case 'dangerous-only':
      return isOutsideCwd(path);
    default:
      return true;
  }
}

export function shellNeedsApproval(command: string): boolean {
  switch (approvalState.policy) {
    case 'never':
      return false;
    case 'dangerous-only':
      return DANGEROUS_SHELL.test(command);
    default:
      return true;
  }
}
