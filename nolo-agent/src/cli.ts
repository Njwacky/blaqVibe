import { createInterface } from 'readline';
import { existsSync, readFileSync, statSync } from 'fs';
import { join, resolve } from 'path';
import { loadConfig, PACKAGE_DIR, type AgentConfig } from './config.js';
import { runAgentWithRetry, type AgentEvent, type ChatMessage } from './agent.js';
import { TuiRenderer } from './renderer.js';
import { Loader } from './loader.js';
import { printBanner } from './banner.js';
import { detectBg } from './terminal-bg.js';
import { PlainLineReader, styledReadLine, borderedReadLine } from './input.js';
import { initSessionDir, saveMessage, newSessionPath } from './session.js';
import { createApprovalGate } from './approval.js';
import { dispatch, type CommandContext } from './commands.js';
import './commands-init.js';

const DIM = '\x1b[2m';
const RESET = '\x1b[0m';
const BOLD = '\x1b[1m';
const CYAN = '\x1b[36m';
const GREEN = '\x1b[32m';
const YELLOW = '\x1b[33m';
const GRAY = '\x1b[90m';

function formatTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

/** Turn SDK / network errors into one line a human can act on. */
function describeError(err: any): string {
  const status: number | undefined = err?.statusCode ?? err?.status;
  let detail = '';
  if (typeof err?.body === 'string' && err.body) {
    try {
      detail = JSON.parse(err.body)?.error?.message ?? '';
    } catch {
      detail = err.body.slice(0, 200);
    }
  }
  const message: string = detail || err?.message || String(err);
  if (status === 401) return `OpenRouter rejected the key (401). Check OPENROUTER_API_KEY in nolo-agent/.env. ${message}`;
  if (status === 402) return `OpenRouter says the account has no credits (402). Top up at openrouter.ai/credits. ${message}`;
  if (status === 404) return `Model not found (404) — try /model to pick another. ${message}`;
  if (status === 429) return `Rate limited (429). Wait a moment or switch model with /model. ${message}`;
  if (/fetch failed|ECONNRESET|ENOTFOUND|ECONNREFUSED|ETIMEDOUT/i.test(message + (err?.cause?.code ?? ''))) {
    return `Could not reach openrouter.ai (${err?.cause?.code ?? 'network error'}). Check your internet connection or proxy.`;
  }
  return message;
}

function packageVersion(): string {
  try {
    return (JSON.parse(readFileSync(join(PACKAGE_DIR, 'package.json'), 'utf-8')) as { version?: string }).version ?? '0.0.0';
  } catch {
    return '0.0.0';
  }
}

interface CliArgs {
  cwd?: string;
  model?: string;
  prompt?: string;
  help: boolean;
  version: boolean;
}

function parseArgs(argv: string[]): CliArgs {
  const args: CliArgs = { help: false, version: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    if (a === '--cwd' || a === '-C') args.cwd = next();
    else if (a.startsWith('--cwd=')) args.cwd = a.slice(6);
    else if (a === '--model' || a === '-m') args.model = next();
    else if (a.startsWith('--model=')) args.model = a.slice(8);
    else if (a === '--prompt' || a === '-p') args.prompt = next();
    else if (a === '--help' || a === '-h') args.help = true;
    else if (a === '--version' || a === '-v') args.version = true;
    else if (!a.startsWith('-')) args.prompt = args.prompt ? `${args.prompt} ${a}` : a;
  }
  return args;
}

function usage(): void {
  console.log(`${BOLD}nolo${RESET} — the BlaqVibes terminal coding agent (OpenRouter)

  ${DIM}usage${RESET}  npm start                      chat about the BlaqVibes repo (cwd = ..)
         npm run here                   chat about the current directory
         npm start -- -p "question"     one-shot: answer and exit
         npm start -- --cwd /path       work in another directory
         npm start -- --model <id>      pick an OpenRouter model for this run

  ${DIM}env${RESET}    OPENROUTER_API_KEY (required) · AGENT_MODEL · AGENT_MAX_STEPS · AGENT_MAX_COST
         AGENT_APPROVAL=always|dangerous-only|never · NOLO_CWD
  ${DIM}config${RESET} nolo-agent/agent.config.json (and ./agent.config.json in the working directory)`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) return usage();
  if (args.version) return console.log(packageVersion());

  const wantedCwd = args.cwd ?? process.env.NOLO_CWD;
  if (wantedCwd) {
    const target = resolve(wantedCwd);
    if (!existsSync(target) || !statSync(target).isDirectory()) {
      console.error(`${YELLOW}Working directory not found: ${target}${RESET}`);
      process.exit(1);
    }
    process.chdir(target);
  }

  let config: AgentConfig;
  try {
    config = loadConfig(args.model ? { model: args.model } : {});
  } catch (err: any) {
    console.error(`${YELLOW}${err.message}${RESET}`);
    process.exit(1);
  }

  const oneShot = typeof args.prompt === 'string' && args.prompt.trim().length > 0;
  const BG_INPUT = !oneShot && config.display.inputStyle === 'block' ? await detectBg() : '';

  if (!oneShot) {
    if (config.showBanner) {
      printBanner({
        model: config.model,
        cwd: process.cwd().replace(process.env.HOME ?? '', '~'),
        approvalPolicy: config.approvalPolicy,
        slashCommands: config.slashCommands,
        version: packageVersion(),
      });
    } else {
      const width = Math.min(process.stdout.columns || 60, 60);
      const line = GRAY + '─'.repeat(width) + RESET;
      console.log(`\n${line}`);
      console.log(`  ${BOLD}Nolo${RESET}  ${DIM}v${packageVersion()}${RESET}`);
      console.log(`  ${DIM}model${RESET}  ${CYAN}${config.model}${RESET}`);
      if (config.slashCommands) console.log(`  ${DIM}/help for commands${RESET}`);
      console.log(`${line}\n`);
    }
  }

  const rl = createInterface({ input: process.stdin, output: process.stdout, prompt: `${GREEN}>${RESET} `, terminal: process.stdin.isTTY });
  const plainReader = new PlainLineReader(rl);

  // Session persistence (JSONL, append-only)
  initSessionDir(config.sessionDir);
  let sessionPath = newSessionPath(config.sessionDir);
  const messages: ChatMessage[] = [];

  // Loader + renderer + approval gate share a little state so a y/N question
  // never fights with a spinner for the same terminal line.
  const loader = new Loader(config.display.loader);
  let activeRenderer: TuiRenderer | null = null;
  const approvalGate = createApprovalGate({
    rl,
    onPrompt: () => {
      loader.stop();
      activeRenderer?.endTurn();
    },
    onResume: () => loader.start(),
  });

  const cmdCtx: CommandContext = {
    config,
    rl,
    messages,
    sessionPath,
    resetSession: () => {
      sessionPath = newSessionPath(config.sessionDir);
      cmdCtx.sessionPath = sessionPath;
      return sessionPath;
    },
    totalTokens: { input: 0, output: 0, cost: 0 },
    approvalGate,
  };

  let current: AbortController | null = null;
  const onInterrupt = () => {
    if (current) {
      current.abort(new Error('Stopped by user'));
      process.stdout.write(`\n${DIM}  stopping…${RESET}\n`);
    } else {
      console.log();
      process.exit(0);
    }
  };
  rl.on('SIGINT', onInterrupt);
  process.on('SIGINT', onInterrupt);

  async function getInput(): Promise<string | null> {
    switch (config.display.inputStyle) {
      case 'block':
        return styledReadLine(BG_INPUT);
      case 'bordered':
        return borderedReadLine();
      case 'plain':
      default:
        return plainReader.next();
    }
  }

  async function runTurn(prompt: string): Promise<void> {
    messages.push({ role: 'user', content: prompt });
    saveMessage(sessionPath, { role: 'user', content: prompt });
    const agentInput = messages.length > 1 ? messages : prompt;

    console.log();
    const renderer = new TuiRenderer({ display: config.display });
    activeRenderer = renderer;
    const controller = new AbortController();
    current = controller;
    let started = false;
    let partial = '';
    loader.start();

    const handleEvent = (event: AgentEvent) => {
      if (!started) {
        started = true;
        loader.stop();
      }
      if (event.type === 'text') partial += event.delta;
      loader.stop();
      renderer.handle(event);
      // The agent pauses for a model round-trip after every tool result.
      if (event.type === 'tool_result') loader.start();
    };

    try {
      const result = await runAgentWithRetry(config, agentInput, {
        onEvent: handleEvent,
        signal: controller.signal,
        hooks: approvalGate.hooks,
      });
      loader.stop();
      renderer.endTurn();
      messages.push({ role: 'assistant', content: result.text });
      saveMessage(sessionPath, { role: 'assistant', content: result.text });
      cmdCtx.totalTokens.input += result.usage.inputTokens;
      cmdCtx.totalTokens.output += result.usage.outputTokens;
      if (result.usage.cost) cmdCtx.totalTokens.cost += result.usage.cost;
      const cost = result.usage.cost ? ` · $${result.usage.cost.toFixed(4)}` : '';
      console.log(`\n${GRAY}  ${formatTokens(result.usage.inputTokens)} in · ${formatTokens(result.usage.outputTokens)} out${cost}${RESET}\n`);
    } catch (err: any) {
      loader.stop();
      renderer.endTurn();
      if (controller.signal.aborted) {
        const kept = partial.trim() ? `${partial.trim()}\n\n[stopped by user]` : '[stopped by user before answering]';
        messages.push({ role: 'assistant', content: kept });
        saveMessage(sessionPath, { role: 'assistant', content: kept });
        console.log(`${DIM}  stopped.${RESET}\n`);
      } else {
        // Keep history consistent: a question with no answer is dropped.
        messages.pop();
        console.log(`\n${YELLOW}  Error: ${describeError(err)}${RESET}\n`);
      }
    } finally {
      current = null;
      activeRenderer = null;
    }
  }

  if (oneShot) {
    await runTurn(args.prompt!.trim());
    rl.close();
    process.exit(0);
  }

  while (true) {
    const input = await getInput();
    if (input === null) {
      // stdin closed (piped script finished, or Ctrl-D)
      console.log();
      process.exit(0);
    }
    const trimmed = input.trim();
    if (!trimmed) continue;

    if (config.display.inputStyle !== 'plain') {
      const cwd = process.cwd().replace(process.env.HOME ?? '', '~');
      process.stdout.write(`\x1b[K  ${DIM}${cwd}${RESET}\n`);
    }

    const lower = trimmed.toLowerCase();
    if (lower === 'exit' || lower === 'quit' || lower === '/exit' || lower === '/quit') {
      rl.close();
      process.exit(0);
    }

    if (config.slashCommands && trimmed.startsWith('/')) {
      await dispatch(trimmed, cmdCtx);
      continue;
    }

    await runTurn(trimmed);
  }
}

main().catch((err) => {
  console.error(`${YELLOW}${err?.message ?? err}${RESET}`);
  process.exit(1);
});
