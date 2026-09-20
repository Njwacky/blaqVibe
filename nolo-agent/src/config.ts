import { readFileSync, existsSync } from 'fs';
import { resolve, dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { DEFAULT_WORKSPACE, type WorkspaceConfig } from './tools/guard.js';

export type { WorkspaceConfig } from './tools/guard.js';

export type ToolDisplay = 'emoji' | 'grouped' | 'minimal' | 'hidden';
export type InputStyle = 'block' | 'bordered' | 'plain';
export type LoaderStyle = 'gradient' | 'spinner' | 'minimal';
export type ApprovalPolicy = 'always' | 'never' | 'dangerous-only';

export interface LoaderConfig {
  text: string;
  style: LoaderStyle;
}

export interface DisplayConfig {
  toolDisplay: ToolDisplay;
  reasoning: boolean;
  inputStyle: InputStyle;
  loader: LoaderConfig;
}

export interface AgentConfig {
  apiKey: string;
  model: string;
  systemPrompt: string;
  maxSteps: number;
  maxCost: number;
  sessionDir: string;
  showBanner: boolean;
  /** Which mutating tools ask before running — see src/approval.ts */
  approvalPolicy: ApprovalPolicy;
  display: DisplayConfig;
  slashCommands: boolean;
  /** Sent to OpenRouter as app attribution (shows up on openrouter.ai/activity) */
  appName: string;
  appUrl: string;
  /** What Nolo may touch — see src/tools/guard.ts */
  workspace: WorkspaceConfig;
}

/** Absolute path of the nolo-agent package (the folder that holds package.json). */
export const PACKAGE_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '..');

const DEFAULTS: AgentConfig = {
  apiKey: '',
  model: 'anthropic/claude-sonnet-4.5',
  systemPrompt: [
    'You are Nolo, the BlaqVibes assistant — the same Nolo that helps builders inside the BlaqVibes app, here as a terminal coding agent',
    'for the BlaqVibes codebase: a Django 5 project (`blaqvibes/` settings package, `gallery` and `users` apps, Celery workers, Django templates)',
    'deployed on Render. You have tools for reading, writing, editing and searching files, and running shell commands.',
    '',
    'BlaqVibes workspace (your only working area): {cwd}',
    '',
    'Boundaries — these are enforced by your tools, not just requested:',
    '- You work inside the workspace only. Paths outside it and protected files (.env, keys, databases, media uploads, backups, .git internals)',
    '  are refused. Never try to work around a refusal; tell the user what was blocked and why, and offer a safe alternative.',
    '- Never print, log, paste or commit secrets. Configuration questions are answered from .env.example and blaqvibes/settings.py — the names',
    '  and defaults — never from real values. Shell commands run with a scrubbed environment, so real keys are not available to you anyway.',
    '- You have no access to BlaqVibes accounts, emails, payments, stars ledgers, trades or uploaded projects: they live in the database, which',
    '  is out of bounds. Reason about them from models, views and tests only, and say so if asked for live data.',
    '',
    'Everything you say about BlaqVibes comes from this repository:',
    '- Read the relevant file before you describe how something works, and cite paths (e.g. gallery/views_community.py:76).',
    '- If the code does not show it, say you could not find it. Do not invent endpoints, settings, pages, star rules or moderation policy.',
    '- Web search is only for third-party documentation (Django, Celery, Render, libraries) — never a source of truth about BlaqVibes itself.',
    '- Use BlaqVibes vocabulary as the code uses it: vibes, stars, trades, remixes, battles, challenges, Studio, Nolo review.',
    '',
    'Guidelines:',
    '- Use your tools proactively. Explore the codebase to find answers instead of asking the user.',
    '- Keep working until the task is fully resolved before responding.',
    '- Be concise and direct. Show file paths clearly when working with files.',
    '- Prefer grep and glob tools over shell commands for file search.',
    '- When editing code, make minimal targeted changes consistent with the existing style.',
    '- Django checks: run tests with `python manage.py test <app>` and keep migrations in sync with models.',
    '- Nolo is an assistant, not the author: explain what you changed and why, so the human stays in control.',
  ].join('\n'),
  maxSteps: 20,
  maxCost: 1.0,
  sessionDir: join(PACKAGE_DIR, '.sessions'),
  showBanner: true,
  approvalPolicy: 'always',
  display: {
    toolDisplay: 'grouped',
    reasoning: false,
    inputStyle: 'plain',
    loader: { text: 'Nolo is working', style: 'spinner' },
  },
  slashCommands: true,
  appName: 'BlaqVibes Nolo',
  appUrl: 'https://github.com/Njwacky/blaqVibe',
  workspace: { ...DEFAULT_WORKSPACE },
};

const APPROVAL_POLICIES: ApprovalPolicy[] = ['always', 'never', 'dangerous-only'];

/** Only these names are lifted out of a .env file. The BlaqVibes root .env also
 * holds SECRET_KEY, DATABASE_URL, PAYSTACK keys… — those must never enter the
 * agent process, where a tool result could carry them to the model. */
const ENV_NAMES_WANTED = /^(?:OPENROUTER_API_KEY|OPENAI_API_KEY|OPENAI_KEY|openai_key|AGENT_[A-Z_]+|NOLO_[A-Z_]+)$/;

/**
 * Load the Nolo-relevant lines of a .env file without adding a dependency.
 * Existing environment variables are never overwritten.
 */
function loadDotEnv(path: string): void {
  if (!existsSync(path)) return;
  let text: string;
  try {
    text = readFileSync(path, 'utf-8');
  } catch {
    return;
  }
  for (const raw of text.split('\n')) {
    const line = raw.trim().replace(/^export\s+/, '');
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    if (!ENV_NAMES_WANTED.test(key)) continue;
    let value = line.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (key && process.env[key] === undefined) process.env[key] = value;
  }
}

/**
 * The OpenRouter key. `OPENROUTER_API_KEY` is canonical, but the same key was
 * added on Render under `openai_key`, so the OpenAI-style names are accepted
 * too — as long as the value is actually an OpenRouter key (`sk-or-…`).
 */
function resolveApiKey(): string {
  const direct = (process.env.OPENROUTER_API_KEY ?? '').trim();
  if (direct) return direct;
  for (const name of ['OPENAI_API_KEY', 'OPENAI_KEY', 'openai_key']) {
    const value = (process.env[name] ?? '').trim();
    if (value.startsWith('sk-or-')) return value;
  }
  return '';
}

function readConfigFile(path: string): Partial<AgentConfig> | null {
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, 'utf-8')) as Partial<AgentConfig>;
  } catch (err) {
    throw new Error(`Could not parse ${path}: ${(err as Error).message}`);
  }
}

function mergeConfig(base: AgentConfig, file: Partial<AgentConfig>): AgentConfig {
  const display: DisplayConfig = {
    ...base.display,
    ...(file.display ?? {}),
    loader: { ...base.display.loader, ...(file.display?.loader ?? {}) },
  };
  const workspace: WorkspaceConfig = { ...base.workspace, ...(file.workspace ?? {}) };
  return { ...base, ...file, display, workspace };
}

export function loadConfig(overrides: Partial<AgentConfig> = {}): AgentConfig {
  loadDotEnv(join(PACKAGE_DIR, '.env'));
  loadDotEnv(resolve('.env'));

  let config: AgentConfig = {
    ...DEFAULTS,
    display: { ...DEFAULTS.display, loader: { ...DEFAULTS.display.loader } },
    workspace: { ...DEFAULTS.workspace },
  };

  // Package-level config first, then a project-level one in the working
  // directory (so a repo can carry its own agent.config.json).
  for (const path of [join(PACKAGE_DIR, 'agent.config.json'), resolve('agent.config.json')]) {
    const file = readConfigFile(path);
    if (file) config = mergeConfig(config, file);
  }

  config.apiKey = resolveApiKey() || config.apiKey;
  if (process.env.AGENT_MODEL) config.model = process.env.AGENT_MODEL;
  if (process.env.AGENT_MAX_STEPS) config.maxSteps = Number(process.env.AGENT_MAX_STEPS);
  if (process.env.AGENT_MAX_COST) config.maxCost = Number(process.env.AGENT_MAX_COST);
  if (process.env.AGENT_APPROVAL && APPROVAL_POLICIES.includes(process.env.AGENT_APPROVAL as ApprovalPolicy)) {
    config.approvalPolicy = process.env.AGENT_APPROVAL as ApprovalPolicy;
  }

  config = mergeConfig(config, overrides);

  if (!config.apiKey) {
    throw new Error(
      'OPENROUTER_API_KEY is required. Create nolo-agent/.env from .env.example and paste your key from https://openrouter.ai/settings/keys',
    );
  }
  if (!APPROVAL_POLICIES.includes(config.approvalPolicy)) {
    throw new Error(`approvalPolicy must be one of ${APPROVAL_POLICIES.join(', ')} (got "${config.approvalPolicy}")`);
  }
  return config;
}
