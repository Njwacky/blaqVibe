import type { Interface } from 'readline';
import { writeFileSync } from 'fs';
import type { AgentConfig, ApprovalPolicy } from './config.js';
import type { ChatMessage } from './agent.js';
import type { ApprovalGate } from './approval.js';
import { approvalState } from './tools/policy.js';
import { checkShellCommand, guardPath, protectedSummary, workspaceRoot } from './tools/guard.js';

const DIM = '\x1b[2m';
const RESET = '\x1b[0m';
const CYAN = '\x1b[36m';
const GREEN = '\x1b[32m';
const YELLOW = '\x1b[33m';

export interface CommandContext {
  config: AgentConfig;
  rl: Interface;
  messages: ChatMessage[];
  sessionPath: string;
  resetSession: () => string;
  totalTokens: { input: number; output: number; cost: number };
  approvalGate: ApprovalGate;
}

export interface SlashCommand {
  name: string;
  description: string;
  execute: (args: string, ctx: CommandContext) => Promise<void>;
}

const commands: SlashCommand[] = [];

export function registerCommand(cmd: SlashCommand): void {
  commands.push(cmd);
}

export function getCommands(): SlashCommand[] {
  return commands;
}

export async function dispatch(input: string, ctx: CommandContext): Promise<boolean> {
  const [name, ...rest] = input.split(' ');
  const cmd = commands.find((c) => c.name === name);
  if (!cmd) {
    console.log(`  ${DIM}Unknown command: ${name}. Type /help for available commands.${RESET}`);
    return true;
  }
  try {
    await cmd.execute(rest.join(' '), ctx);
  } catch (err: any) {
    console.log(`  ${YELLOW}${name} failed: ${err.message}${RESET}`);
  }
  return true;
}

function ask(rl: Interface, prompt: string): Promise<string> {
  return new Promise((r) => rl.question(prompt, r));
}

const fmt = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

// ---------------------------------------------------------------------------
// Default-ON commands
// ---------------------------------------------------------------------------

registerCommand({
  name: '/model',
  description: 'Switch model (search the OpenRouter catalogue, or /model <id>)',
  execute: async (args, ctx) => {
    console.log(`  ${DIM}Current:${RESET} ${CYAN}${ctx.config.model}${RESET}`);
    if (args.trim().includes('/')) {
      ctx.config.model = args.trim();
      console.log(`  ${DIM}Model →${RESET} ${CYAN}${ctx.config.model}${RESET}`);
      return;
    }
    const query = args.trim() || (await ask(ctx.rl, `  ${DIM}Search models:${RESET} `));
    if (!query.trim()) return;
    process.stdout.write(`  ${DIM}Fetching…${RESET}`);
    const res = await fetch('https://openrouter.ai/api/v1/models');
    const { data } = (await res.json()) as { data: { id: string; name: string }[] };
    process.stdout.write('\r\x1b[K');
    const q = query.toLowerCase();
    const matches = data.filter((m) => m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)).slice(0, 15);
    if (!matches.length) {
      console.log(`  ${DIM}No models matching "${query}".${RESET}`);
      return;
    }
    matches.forEach((m, i) => console.log(`  ${DIM}${String(i + 1).padStart(2)})${RESET} ${m.id}`));
    const pick = await ask(ctx.rl, `\n  ${DIM}Select (1-${matches.length}):${RESET} `);
    const idx = parseInt(pick) - 1;
    if (idx >= 0 && idx < matches.length) {
      ctx.config.model = matches[idx].id;
      console.log(`  ${DIM}Model →${RESET} ${CYAN}${ctx.config.model}${RESET}`);
    } else {
      console.log(`  ${DIM}Cancelled.${RESET}`);
    }
  },
});

registerCommand({
  name: '/new',
  description: 'Start a fresh conversation',
  execute: async (_args, ctx) => {
    ctx.messages.length = 0;
    ctx.sessionPath = ctx.resetSession();
    ctx.approvalGate.reset();
    console.log(`  ${GREEN}✓${RESET} ${DIM}New session started.${RESET}`);
  },
});

registerCommand({
  name: '/help',
  description: 'List available commands',
  execute: async (_args, _ctx) => {
    for (const cmd of getCommands()) {
      console.log(`  ${CYAN}${cmd.name.padEnd(12)}${RESET}${DIM}${cmd.description}${RESET}`);
    }
    console.log(`  ${DIM}Type "exit" to quit. Ctrl-C stops the current run.${RESET}`);
  },
});

// ---------------------------------------------------------------------------
// Optional commands selected for Nolo
// ---------------------------------------------------------------------------

registerCommand({
  name: '/session',
  description: 'Show session info and token usage',
  execute: async (_args, ctx) => {
    console.log(`  ${DIM}file${RESET}      ${ctx.sessionPath}`);
    console.log(`  ${DIM}messages${RESET}  ${ctx.messages.length}`);
    console.log(`  ${DIM}model${RESET}     ${ctx.config.model}`);
    console.log(`  ${DIM}cwd${RESET}       ${process.cwd()}`);
    const cost = ctx.totalTokens.cost ? `  ·  $${ctx.totalTokens.cost.toFixed(4)}` : '';
    console.log(`  ${DIM}tokens${RESET}    ${fmt(ctx.totalTokens.input)} in · ${fmt(ctx.totalTokens.output)} out${cost}`);
  },
});

registerCommand({
  name: '/export',
  description: 'Save conversation as Markdown (/export [file])',
  execute: async (args, ctx) => {
    const file = args.trim() || `nolo-session-${new Date().toISOString().replace(/[:.]/g, '-')}.md`;
    const md = ctx.messages
      .map((m) => `## ${m.role === 'user' ? 'User' : m.role === 'assistant' ? 'Nolo' : 'System'}\n\n${m.content}`)
      .join('\n\n---\n\n');
    writeFileSync(file, md, 'utf-8');
    console.log(`  ${GREEN}✓${RESET} ${DIM}Exported to ${file}${RESET}`);
  },
});

registerCommand({
  name: '/workspace',
  description: 'Show the workspace boundary, or test a path/command: /workspace <path> | /workspace ! <command>',
  execute: async (args, _ctx) => {
    const probe = args.trim();
    if (!probe) {
      console.log(`  ${DIM}root${RESET}       ${workspaceRoot()}`);
      console.log(`  ${DIM}rules${RESET}      paths must stay inside the root; shell runs with a scrubbed env; tool output is redacted`);
      console.log(`  ${DIM}protected${RESET}  ${protectedSummary().join('  ')}`);
      console.log(`  ${DIM}shell refuses${RESET} sudo · database clients · manage.py shell/dbshell/dumpdata · env dumps · credential helpers · outside/protected paths`);
      console.log(`  ${DIM}usage: /workspace gallery/views.py   ·   /workspace ! cat .env${RESET}`);
      return;
    }
    if (probe.startsWith('!')) {
      const reason = checkShellCommand(probe.slice(1).trim());
      console.log(reason ? `  ${YELLOW}✗ ${reason}${RESET}` : `  ${GREEN}✓${RESET} ${DIM}allowed (approval policy still applies)${RESET}`);
      return;
    }
    const read = guardPath(probe, 'read');
    console.log(read.ok ? `  ${GREEN}✓${RESET} ${DIM}readable →${RESET} ${read.rel}` : `  ${YELLOW}✗ ${read.error}${RESET}`);
  },
});

registerCommand({
  name: '/approve',
  description: 'Show or set the approval policy: always | dangerous-only | never',
  execute: async (args, ctx) => {
    const policies: ApprovalPolicy[] = ['always', 'dangerous-only', 'never'];
    const wanted = args.trim() as ApprovalPolicy;
    if (!wanted) {
      console.log(`  ${DIM}policy${RESET}  ${approvalState.policy}`);
      const cached = [...ctx.approvalGate.alwaysAllow];
      console.log(`  ${DIM}always-allowed this session${RESET}  ${cached.length ? cached.join(', ') : '—'}`);
      console.log(`  ${DIM}usage: /approve always | dangerous-only | never${RESET}`);
      return;
    }
    if (!policies.includes(wanted)) {
      console.log(`  ${YELLOW}Unknown policy "${wanted}". Use one of: ${policies.join(', ')}${RESET}`);
      return;
    }
    ctx.config.approvalPolicy = wanted;
    approvalState.policy = wanted;
    if (wanted === 'always') ctx.approvalGate.reset();
    console.log(`  ${GREEN}✓${RESET} ${DIM}Approval policy →${RESET} ${wanted}`);
  },
});
