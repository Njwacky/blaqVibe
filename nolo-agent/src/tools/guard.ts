/**
 * Workspace guard — what Nolo may touch, and what never leaves the machine.
 *
 * Every local tool goes through this module, so the rules live in one place:
 *
 *   1. Boundary  — paths must resolve (through symlinks) inside the workspace
 *                  root, which is the directory Nolo was started in.
 *   2. Protected — secrets, private keys, databases, uploads (media/), backups,
 *                  VCS internals and shell/agent history are never read or
 *                  written, even inside the workspace. `.env.example` stays
 *                  readable: that is the documented configuration surface.
 *   3. Shell     — commands that open the database, dump the environment,
 *                  read stored credentials, escalate to root or reference a
 *                  protected/outside path are refused outright (no approval
 *                  prompt: the answer is always no). Child processes get a
 *                  scrubbed environment — no API keys, SECRET_KEY, DATABASE_URL.
 *   4. Redaction — anything that still looks like a credential in a tool
 *                  result is masked before it reaches the model.
 *
 * The lists are defaults; `agent.config.json → workspace` can add protected
 * or allowed patterns, extra blocked shell patterns and extra env names.
 */
import { existsSync, realpathSync } from 'fs';
import { basename, dirname, isAbsolute, relative, resolve, sep } from 'path';

export interface WorkspaceConfig {
  /** Extra protected patterns (basename glob like "*.pem", or root-relative path like "ops/vault"). */
  protectedPaths: string[];
  /** Patterns that are readable even when a protected pattern matches (e.g. ".env.test"). */
  allowPaths: string[];
  /** Extra environment variable names (or regex sources) the shell may see. */
  passEnv: string[];
  /** Extra shell patterns (regex sources) that are refused. */
  blockedShell: string[];
}

export const DEFAULT_WORKSPACE: WorkspaceConfig = { protectedPaths: [], allowPaths: [], passEnv: [], blockedShell: [] };

interface ProtectedRule {
  pattern: string;
  why: string;
}

/** Basename globs (no slash) match at any depth; root-relative patterns contain a slash. */
const DEFAULT_PROTECTED: ProtectedRule[] = [
  { pattern: '.env', why: 'environment secrets' },
  { pattern: '.env.*', why: 'environment secrets' },
  { pattern: '*.env', why: 'environment secrets' },
  { pattern: '.envrc', why: 'environment secrets' },
  { pattern: '*.pem', why: 'private key or certificate' },
  { pattern: '*.key', why: 'private key' },
  { pattern: '*.p12', why: 'private key store' },
  { pattern: '*.pfx', why: 'private key store' },
  { pattern: '*.jks', why: 'private key store' },
  { pattern: '*.keystore', why: 'private key store' },
  { pattern: '*.ppk', why: 'private key' },
  { pattern: 'id_rsa*', why: 'SSH private key' },
  { pattern: 'id_ed25519*', why: 'SSH private key' },
  { pattern: 'id_ecdsa*', why: 'SSH private key' },
  { pattern: 'id_dsa*', why: 'SSH private key' },
  { pattern: '*.sqlite', why: 'database with user data' },
  { pattern: '*.sqlite3', why: 'database with user data' },
  { pattern: '*.sqlite3-*', why: 'database with user data' },
  { pattern: '*.db', why: 'database with user data' },
  { pattern: '*.sql', why: 'database dump' },
  { pattern: '*.dump', why: 'database dump' },
  { pattern: '*.bak', why: 'backup copy' },
  { pattern: 'media', why: 'user uploads (paid ZIPs, avatars)' },
  { pattern: 'backups', why: 'backups' },
  { pattern: 'private', why: 'private data' },
  { pattern: 'secrets', why: 'secrets' },
  { pattern: '.git', why: 'VCS internals (remotes may embed tokens) — use git commands instead' },
  { pattern: '.sessions', why: 'Nolo chat transcripts' },
  { pattern: '.ssh', why: 'SSH credentials' },
  { pattern: '.aws', why: 'cloud credentials' },
  { pattern: '.gnupg', why: 'GPG keys' },
  { pattern: '.kube', why: 'cluster credentials' },
  { pattern: '.docker', why: 'registry credentials' },
  { pattern: '.netrc', why: 'stored credentials' },
  { pattern: '.npmrc', why: 'stored credentials' },
  { pattern: '.pypirc', why: 'stored credentials' },
  { pattern: '.git-credentials', why: 'stored credentials' },
  { pattern: '.htpasswd', why: 'stored credentials' },
  { pattern: 'credentials', why: 'stored credentials' },
  { pattern: 'credentials.*', why: 'stored credentials' },
  { pattern: 'service-account*.json', why: 'cloud credentials' },
  { pattern: 'service_account*.json', why: 'cloud credentials' },
  { pattern: 'client_secret*.json', why: 'OAuth credentials' },
  { pattern: '*.secret', why: 'secrets' },
  { pattern: 'secrets.*', why: 'secrets' },
  { pattern: 'token.json', why: 'stored token' },
  { pattern: 'tokens.json', why: 'stored token' },
  { pattern: '*.kdbx', why: 'password database' },
  { pattern: '*.gpg', why: 'encrypted secrets' },
  { pattern: 'local_settings.py', why: 'local settings often hold real keys' },
  { pattern: 'settings_local.py', why: 'local settings often hold real keys' },
  { pattern: '.bash_history', why: 'shell history' },
  { pattern: '.zsh_history', why: 'shell history' },
  { pattern: '.python_history', why: 'shell history' },
  { pattern: '.node_repl_history', why: 'shell history' },
];

const DEFAULT_ALLOW = ['.env.example', '.env.sample', '.env.template', '.env.dist', '*.example', '*.sample'];

interface ShellRule {
  re: RegExp;
  why: string;
}

const CMD = String.raw`(?:^|[\s;&|(\`{])`; // start of a command word
const DEFAULT_BLOCKED_SHELL: ShellRule[] = [
  { re: new RegExp(CMD + String.raw`(?:sudo|doas|su)\b`), why: 'Nolo runs as you, never as root' },
  {
    re: new RegExp(CMD + String.raw`(?:sqlite3|psql|pg_dump|pg_dumpall|pg_restore|mysql|mysqldump|mariadb|mongo|mongosh|mongodump|redis-cli)\b`),
    why: 'direct database access — user data is out of bounds; read the models and tests instead',
  },
  {
    re: /(?:manage\.py|django-admin)\s+(?:dbshell|dumpdata|shell|shell_plus|changepassword)\b/,
    why: 'opens the database or user accounts — use `manage.py test`, `check` or `makemigrations` instead',
  },
  {
    re: new RegExp(CMD + String.raw`(?:printenv\b|env(?:\s*$|\s*[|;&>)]|\s+-[A-Za-z0]*\s*$))`),
    why: 'dumps environment variables',
  },
  { re: new RegExp(CMD + String.raw`(?:export|declare|typeset)\s+-p\b`), why: 'dumps environment variables' },
  { re: new RegExp(CMD + String.raw`set\s*(?:$|[|;&>)])`), why: 'dumps environment variables' },
  { re: /\/proc\/(?:self|\d+)\/environ\b/, why: 'dumps environment variables' },
  {
    re: new RegExp(
      CMD +
        String.raw`(?:gh\s+auth\s+(?:token|status\s+--show-token)|git\s+credential|aws\s+configure|gcloud\s+auth|az\s+account\s+get-access-token|heroku\s+config|vercel\s+env|flyctl\s+secrets|fly\s+secrets|render\s+env|doppler\s+secrets|op\s+(?:read|item)|pass\s+show|security\s+find-|keyctl|secret-tool|kubectl\s+get\s+secret|docker\s+inspect)\b`,
    ),
    why: 'reads stored credentials',
  },
  { re: /\bgit\s+config\b[^|;&]*\b(?:credential|token|password)\b/i, why: 'may reveal stored credentials' },
];

/** Absolute prefixes outside the workspace that shell commands may still mention (binaries, scratch). */
const SYSTEM_PREFIXES = ['/dev/null', '/dev/stdin', '/dev/stdout', '/dev/stderr', '/dev/tty', '/tmp/', '/usr/', '/bin/', '/sbin/', '/lib', '/opt/', '/snap/', '/nix/', '/System/', '/Library/', '/Applications/', '/private/tmp/', '/var/folders/'];
/** Absolute prefixes that are refused even when nothing exists there yet (other users' homes, system config, kernel). */
const DENY_PREFIXES = ['/etc', '/proc', '/sys', '/root', '/home', '/Users', '/var', '/srv', '/mnt', '/private/etc', '/private/var', '/boot', '/run'];

interface GuardState {
  root: string;
  rules: ProtectedRule[];
  allow: string[];
  shellRules: ShellRule[];
  passEnv: RegExp[];
}

let state: GuardState = build(process.cwd(), DEFAULT_WORKSPACE);

function build(root: string, cfg: Partial<WorkspaceConfig>): GuardState {
  return {
    root: safeRealpath(resolve(root)),
    rules: [...DEFAULT_PROTECTED, ...(cfg.protectedPaths ?? []).map((pattern) => ({ pattern, why: 'protected by agent.config.json' }))],
    allow: [...DEFAULT_ALLOW, ...(cfg.allowPaths ?? [])],
    shellRules: [...DEFAULT_BLOCKED_SHELL, ...(cfg.blockedShell ?? []).map((src) => ({ re: new RegExp(src, 'i'), why: 'blocked by agent.config.json' }))],
    passEnv: (cfg.passEnv ?? []).map((name) => new RegExp(`^(?:${name})$`)),
  };
}

/** Point the guard at a workspace root. Called once from buildTools(); tests call it with a temp dir. */
export function configureGuard(root: string, cfg: Partial<WorkspaceConfig> = {}): void {
  state = build(root, cfg);
}

export function workspaceRoot(): string {
  return state.root;
}

export function protectedSummary(): string[] {
  return state.rules.map((r) => r.pattern);
}

// ---------------------------------------------------------------------------
// Paths

function globToRegExp(glob: string): RegExp {
  const escaped = glob.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*\*/g, '\u0000').replace(/\*/g, '[^/]*').replace(/\?/g, '[^/]').replace(/\u0000/g, '.*');
  return new RegExp(`^${escaped}$`, 'i');
}

const regexCache = new Map<string, RegExp>();
function matchGlob(glob: string, value: string): boolean {
  let re = regexCache.get(glob);
  if (!re) {
    re = globToRegExp(glob);
    regexCache.set(glob, re);
  }
  return re.test(value);
}

/** Realpath of `abs` when it exists; otherwise realpath of its deepest existing ancestor plus the remainder. */
function safeRealpath(abs: string): string {
  const missing: string[] = [];
  let cursor = abs;
  while (!existsSync(cursor)) {
    const parent = dirname(cursor);
    if (parent === cursor) return abs;
    missing.unshift(basename(cursor));
    cursor = parent;
  }
  try {
    return [realpathSync(cursor), ...missing].join(sep);
  } catch {
    return abs;
  }
}

function toPosix(p: string): string {
  return p.split(sep).join('/');
}

/** Match a root-relative posix path against the protected/allow lists. */
function protectedRuleFor(rel: string): ProtectedRule | null {
  if (!rel || rel === '.') return null;
  const segments = rel.split('/');
  for (let i = 0; i < segments.length; i++) {
    const prefix = segments.slice(0, i + 1).join('/');
    const name = segments[i];
    if (state.allow.some((glob) => matchGlob(glob, name) || matchGlob(glob, prefix))) continue;
    for (const rule of state.rules) {
      const hit = rule.pattern.includes('/') ? matchGlob(rule.pattern.replace(/\/$/, ''), prefix) : matchGlob(rule.pattern, name);
      if (hit) return rule;
    }
  }
  return null;
}

export type PathCheck = { ok: true; abs: string; rel: string } | { ok: false; error: string };

/**
 * Resolve `input` against the workspace and decide whether Nolo may read or
 * write it. Returns the absolute path on success, or a message the model can
 * act on (it names the rule, so the model can explain instead of retrying).
 */
export function guardPath(input: string, mode: 'read' | 'write'): PathCheck {
  const raw = (input ?? '').trim() || '.';
  if (raw.startsWith('~')) {
    return { ok: false, error: `Blocked: "${raw}" is outside the BlaqVibes workspace (${state.root}). Nolo only ${mode}s inside it.` };
  }
  const abs = safeRealpath(resolve(state.root, raw));
  const rel = relative(state.root, abs);
  if (rel.startsWith('..') || isAbsolute(rel)) {
    return { ok: false, error: `Blocked: "${raw}" is outside the BlaqVibes workspace (${state.root}). Nolo only ${mode}s inside it.` };
  }
  const posixRel = toPosix(rel) || '.';
  const rule = protectedRuleFor(posixRel);
  if (rule) {
    return {
      ok: false,
      error: `Blocked: "${posixRel}" is protected (${rule.why}; rule "${rule.pattern}"). Nolo never ${mode}s secrets, databases, uploads or VCS internals${
        rule.pattern.startsWith('.env') ? ' — read .env.example and blaqvibes/settings.py for configuration' : ''
      }.`,
    };
  }
  return { ok: true, abs, rel: posixRel };
}

/** True when a path (absolute or root-relative) must not be shown to the model. */
export function isProtectedPath(p: string): boolean {
  const abs = safeRealpath(resolve(state.root, p));
  const rel = relative(state.root, abs);
  if (rel.startsWith('..') || isAbsolute(rel)) return true;
  return protectedRuleFor(toPosix(rel)) !== null;
}

// ---------------------------------------------------------------------------
// Shell

const URL_RE = /^[a-z][a-z0-9+.-]*:\/\//i;

/**
 * Bare words are only treated as paths when they exist on disk, so a grep for
 * the word "credentials" is fine while `cat credentials` is not. Anything with
 * a slash, a dot or a glob character is checked regardless.
 */
function looksLikePath(token: string): boolean {
  if (token.includes('/') || token.includes('.') || token.includes('*') || token.includes('?')) return true;
  return existsSync(resolve(state.root, token));
}

/** Split a shell command into candidate path tokens (quotes stripped, options and assignments unwrapped). */
function pathTokens(command: string): string[] {
  const out: string[] = [];
  for (const piece of command.replace(/\\\n/g, ' ').split(/[\s;&|<>()`]+/)) {
    let token = piece.trim().replace(/^["']+|["']+$/g, '');
    if (!token) continue;
    if (token.startsWith('-')) {
      const eq = token.indexOf('=');
      if (eq === -1) continue;
      token = token.slice(eq + 1).replace(/^["']+|["']+$/g, '');
    } else if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(token)) {
      token = token.slice(token.indexOf('=') + 1).replace(/^["']+|["']+$/g, '');
    }
    if (token.startsWith('@')) token = token.slice(1); // curl -F file=@.env
    if (!token || URL_RE.test(token)) continue;
    out.push(token);
  }
  return out;
}

function outsideReason(token: string): string | null {
  if (token.startsWith('~') || token.startsWith('$HOME') || token.startsWith('${HOME}')) {
    return `references "${token}" outside the BlaqVibes workspace`;
  }
  if (isAbsolute(token)) {
    const abs = safeRealpath(token);
    const rel = relative(state.root, abs);
    if (!rel.startsWith('..') && !isAbsolute(rel)) return null;
    if (SYSTEM_PREFIXES.some((prefix) => token === prefix.replace(/\/$/, '') || token.startsWith(prefix))) return null;
    if (DENY_PREFIXES.some((prefix) => token === prefix || token.startsWith(prefix + '/'))) {
      return `references "${token}" outside the BlaqVibes workspace`;
    }
    // URL paths in Django code ("/api/v1/apps/", "/nolo/chat/") look absolute
    // too — only a path that actually exists on this machine is a filesystem escape.
    return existsSync(abs) ? `references "${token}" outside the BlaqVibes workspace` : null;
  }
  if (token === '..' || token.startsWith('../') || token.includes('/../') || token.endsWith('/..')) {
    const abs = safeRealpath(resolve(state.root, token));
    const rel = relative(state.root, abs);
    if (rel.startsWith('..') || isAbsolute(rel)) return `references "${token}" outside the BlaqVibes workspace`;
  }
  return null;
}

/**
 * Decide whether a shell command may run at all. Returns null when it may
 * (the approval policy still applies), or the reason it is refused.
 */
export function checkShellCommand(command: string): string | null {
  const cmd = (command ?? '').trim();
  if (!cmd) return null;
  for (const rule of state.shellRules) {
    if (rule.re.test(cmd)) return `Blocked: this command ${rule.why}.`;
  }
  for (const token of pathTokens(cmd)) {
    const outside = outsideReason(token);
    if (outside) return `Blocked: this command ${outside} (${state.root}). Nolo works inside the workspace only.`;
    if (!looksLikePath(token)) continue;
    const abs = safeRealpath(resolve(state.root, token));
    const rel = relative(state.root, abs);
    // Inside the workspace: full rule check. Elsewhere (system prefixes such as
    // /tmp): the protected names still apply, so /tmp/x/id_rsa is refused too.
    const subject = rel.startsWith('..') || isAbsolute(rel) ? toPosix(abs).replace(/^\/+/, '') : toPosix(rel);
    const rule = protectedRuleFor(subject);
    if (rule) return `Blocked: this command touches "${subject}", which is protected (${rule.why}). Nolo never reads or writes secrets, databases, uploads or VCS internals.`;
  }
  return null;
}

const ENV_ALLOW =
  /^(?:PATH|HOME|USER|LOGNAME|SHELL|LANG|LANGUAGE|LC_[A-Z_]+|TERM|COLORTERM|TZ|TMPDIR|TMP|TEMP|OLDPWD|EDITOR|VISUAL|PAGER|LESS|NODE_ENV|NODE_OPTIONS|NODE_PATH|NVM_[A-Z_]+|npm_config_[a-z_]+|PYTHON[A-Z_]*|PIP_[A-Z_]+|VIRTUAL_ENV|CONDA_[A-Z_]+|PYENV_[A-Z_]+|POETRY_[A-Z_]+|UV_[A-Z_]+|GOPATH|GOROOT|GOFLAGS|GOMODCACHE|CARGO_HOME|RUSTUP_HOME|JAVA_HOME|MAVEN_[A-Z_]+|GRADLE_[A-Z_]+|ANDROID_[A-Z_]+|XDG_[A-Z_]+|DISPLAY|WAYLAND_DISPLAY|DJANGO_SETTINGS_MODULE|DJANGO_LOCAL_DEV|DJANGO_TEST|DEBUG|CI|GIT_[A-Z_]+|HOMEBREW_[A-Z_]+|DOCKER_HOST|COMPOSE_[A-Z_]+|WSL[A-Z_]*|SYSTEMROOT|WINDIR|COMSPEC|APPDATA|LOCALAPPDATA|USERPROFILE|PROGRAMFILES(?:\(X86\))?|NUMBER_OF_PROCESSORS|OS|PATHEXT|SSL_CERT_FILE|SSL_CERT_DIR|REQUESTS_CA_BUNDLE|NODE_EXTRA_CA_CERTS|HTTPS?_PROXY|NO_PROXY|https?_proxy|no_proxy)$/i;
const ENV_DENY = /KEY|SECRET|TOKEN|PASS|PWD|CRED|AUTH|COOKIE|DSN|DATABASE_URL|PRIVATE|SIGNING|SALT|API/i;

/** The environment a child process gets: an allow-list of build/runtime plumbing, never credentials. */
export function scrubEnv(source: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  const out: NodeJS.ProcessEnv = {};
  for (const [name, value] of Object.entries(source)) {
    if (value === undefined) continue;
    const allowed = ENV_ALLOW.test(name) || state.passEnv.some((re) => re.test(name));
    if (!allowed) continue;
    if (ENV_DENY.test(name) && !state.passEnv.some((re) => re.test(name))) continue;
    out[name] = value;
  }
  out.PWD = state.root;
  out.TERM = 'dumb';
  out.NO_COLOR = '1';
  out.NOLO_AGENT = '1';
  out.GIT_TERMINAL_PROMPT = '0'; // never hang on a credential prompt
  out.PYTHONUNBUFFERED = '1';
  return out;
}

// ---------------------------------------------------------------------------
// Redaction

const TOKEN_PATTERNS: RegExp[] = [
  /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g,
  /\bsk-or-v1-[A-Za-z0-9]{16,}\b/g,
  /\bsk-(?:ant-|proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b/g,
  /\b(?:sk|pk|rk|whsec)_(?:live|test)_[A-Za-z0-9]{10,}\b/g,
  /\bgsk_[A-Za-z0-9]{20,}\b/g,
  /\bAIza[0-9A-Za-z_-]{30,}\b/g,
  /\bgh[pousr]_[A-Za-z0-9]{30,}\b/g,
  /\bgithub_pat_[A-Za-z0-9_]{20,}\b/g,
  /\bglpat-[A-Za-z0-9_-]{20,}\b/g,
  /\bxox[abprs]-[A-Za-z0-9-]{10,}\b/g,
  /\bAKIA[0-9A-Z]{16}\b/g,
  /\brnd_[A-Za-z0-9]{20,}\b/g,
  /\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b/g,
  /\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b/g,
  /\bBearer\s+[A-Za-z0-9._~+/=-]{20,}/g,
];
const URL_CREDS = /\b([a-z][a-z0-9+.-]*:\/\/[^\s:/@'"]+):([^\s@/'"]+)@/gi;
// Separator whitespace is [ \t] on purpose: `\s` would let "KEY=\nNEXT=value"
// swallow the next line as the value.
const ASSIGNMENT =
  /\b([A-Za-z_][A-Za-z0-9_.-]*(?:key|secret|token|password|passwd|pwd|credential|database_url|dsn)[A-Za-z0-9_-]*)(["']?)([ \t]*(?:[:=]|=>)[ \t]*)(["'`]?)([^\s"'`,;]{12,})\4/gi;
const PLACEHOLDER =
  /^(?:os\.|env\.|env\(|process\.|settings\.|config\.|get\(|getenv|\$\{|\$[A-Za-z_]|\{\{|<|your[-_]|xxx|\*{3}|change|placeholder|example|dummy|sample|null|none|true|false|undefined|redacted|\[redacted)/i;

export function redactSecrets(text: string): string {
  let out = text;
  for (const re of TOKEN_PATTERNS) out = out.replace(re, (m) => `${m.slice(0, 4)}…[REDACTED]`);
  out = out.replace(URL_CREDS, '$1:[REDACTED]@');
  out = out.replace(ASSIGNMENT, (whole, name: string, nameQuote: string, sep2: string, quote: string, value: string) => {
    if (PLACEHOLDER.test(value) || value.includes('(')) return whole;
    return `${name}${nameQuote}${sep2}${quote}${value.slice(0, 4)}…[REDACTED]${quote}`;
  });
  return out;
}

/** Redact every string inside a tool result. Image payloads (base64) are left alone. */
export function redactDeep<T>(value: T): { value: T; redacted: boolean } {
  let redacted = false;
  const walk = (v: unknown): unknown => {
    if (typeof v === 'string') {
      const r = redactSecrets(v);
      if (r !== v) redacted = true;
      return r;
    }
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === 'object') {
      const obj = v as Record<string, unknown>;
      if (obj.type === 'image' && typeof obj.data === 'string') return v;
      const out: Record<string, unknown> = {};
      for (const [k, item] of Object.entries(obj)) out[k] = walk(item);
      return out;
    }
    return v;
  };
  return { value: walk(value) as T, redacted };
}
