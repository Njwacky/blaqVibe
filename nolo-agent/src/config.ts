import { readFileSync, existsSync } from 'fs';
import { resolve, dirname, join } from 'path';
import { fileURLToPath } from 'url';

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
}

/** Absolute path of the nolo-agent package (the folder that holds package.json). */
export const PACKAGE_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '..');

const DEFAULTS: AgentConfig = {
  apiKey: '',
  model: 'anthropic/claude-sonnet-4.5',
  systemPrompt: [
    'You are Nolo, the BlaqVibes coding agent — a terminal assistant for the people who build and run BlaqVibes',
    '(a Django 5 app in the `blaqvibes/` project package with the `gallery` and `users` apps, Celery workers, Tailwind templates,',
    'deployed on Render). You have tools for reading, writing, editing and searching files, and running shell commands.',
    '',
    'Current working directory: {cwd}',
    '',
    'Guidelines:',
    '- Use your tools proactively. Explore the codebase to find answers instead of asking the user.',
    '- Keep working until the task is fully resolved before responding.',
    '- Do not guess or make up information — use your tools to verify.',
    '- Be concise and direct.',
    '- Show file paths clearly when working with files.',
    '- Prefer grep and glob tools over shell commands for file search.',
    '- When editing code, make minimal targeted changes consistent with the existing style.',
    '- Never print, log or paste secrets (API keys, SECRET_KEY, database URLs). Read .env.example, not .env.',
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
};

const APPROVAL_POLICIES: ApprovalPolicy[] = ['always', 'never', 'dangerous-only'];

/**
 * Load a .env file without adding a dependency. Node 21.7+ ships
 * `process.loadEnvFile`; older runtimes just skip (the shell env still works).
 * Existing environment variables are never overwritten.
 */
function loadDotEnv(path: string): void {
  if (!existsSync(path)) return;
  const loader = (process as unknown as { loadEnvFile?: (p: string) => void }).loadEnvFile;
  if (typeof loader === 'function') {
    try {
      loader.call(process, path);
      return;
    } catch {
      /* fall through to the tiny parser below */
    }
  }
  for (const raw of readFileSync(path, 'utf-8').split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
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
  return { ...base, ...file, display };
}

export function loadConfig(overrides: Partial<AgentConfig> = {}): AgentConfig {
  loadDotEnv(join(PACKAGE_DIR, '.env'));
  loadDotEnv(resolve('.env'));

  let config: AgentConfig = { ...DEFAULTS, display: { ...DEFAULTS.display, loader: { ...DEFAULTS.display.loader } } };

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
