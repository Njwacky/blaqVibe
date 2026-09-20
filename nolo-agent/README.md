# Nolo — the BlaqVibes terminal coding agent

Nolo already answers questions inside the BlaqVibes site (`gallery/nolo_ai.py`).
This folder gives Nolo a second home: a **terminal agent** that can read, search,
edit and run things in the BlaqVibes repo, powered by
[OpenRouter](https://openrouter.ai) through [`@openrouter/agent`](https://www.npmjs.com/package/@openrouter/agent).

Scaffolded with the OpenRouter
[`create-agent-tui`](https://github.com/OpenRouterTeam/skills/tree/main/skills/create-agent-tui)
skill with these choices:

| Area | Choice |
|------|--------|
| Tools | `file_read`, `file_write`, `file_edit`, `glob`, `grep`, `list_dir`, `shell` + OpenRouter server tools `web_search` and `datetime` (Africa/Johannesburg) |
| Harness | JSONL session persistence, ASCII **NOLO** banner, **tool approval gate** (y / n / always), system-prompt composition from `AGENTS.md` / `CLAUDE.md` / `NOLO.md` |
| Slash commands | `/model`, `/new`, `/help`, `/session`, `/export`, `/approve`, `/workspace` |
| Boundary | **Workspace guard** (`src/tools/guard.ts`): workspace-only paths, protected files, refused shell commands, scrubbed shell environment, secret redaction — see below |
| Input style | `plain` readline prompt (works in any terminal; `block` and `bordered` are also implemented) |
| Tool display | `grouped` (`● Ran`, `● Edited` with tree-branch output) — `emoji`, `minimal`, `hidden` available |
| Loader | braille spinner with the text "Nolo is working" |

## Setup

```bash
cd nolo-agent
npm install
cp .env.example .env        # paste your OpenRouter key (sk-or-…)
```

`OPENROUTER_API_KEY` is read from `nolo-agent/.env`, the shell, or — because
the production key was added on Render under the name `openai_key` — from
`OPENAI_API_KEY` / `OPENAI_KEY` / `openai_key` when the value is an OpenRouter
key. Keys are never written to disk by the agent.

## Run

```bash
npm start                       # chat, working directory = the blaqVibe repo root
npm run here                    # chat about whatever directory you are in
npm start -- -p "Where is the star trade logic?"   # one-shot answer, then exit
npm start -- --cwd ~/other/project                  # another project
npm start -- --model openai/gpt-4.1-mini            # different model for this run
```

Type `exit` to quit. **Ctrl-C** stops the current run but keeps the session.

### Approval gate

Every `file_write`, `file_edit` and `shell` call shows what it is about to do
and waits for you:

```
⚠ Nolo wants to run shell (high risk)
  $ python manage.py test gallery
  Allow? [y]es / [n]o / [a]lways for shell:
```

* `y` (or Enter) — run it once · `a` — stop asking for that tool this session ·
  `n` — refuse; Nolo is told you declined and changes course.
* `/approve dangerous-only` only asks for `rm`, `sudo`, `git push`, `migrate … zero`,
  writes outside the working directory, and similar. `/approve never` turns the gate off.
* `AGENT_APPROVAL=…` or `"approvalPolicy"` in `agent.config.json` sets the default.

### What Nolo cannot touch (workspace guard)

Nolo is scoped to the BlaqVibes repo the same way it is scoped inside the app.
The rules are enforced by the tools themselves, not just asked for in the
prompt, and they hold under every approval policy (`/approve never` included):

| Layer | Rule |
|-------|------|
| Boundary | Every path must resolve **inside the directory Nolo was started in** (symlinks are followed first). `../`, `~`, `/etc/…`, other homes: refused. |
| Protected files | Never read or written, even inside the workspace: `.env*` (except `.env.example`), private keys/certs (`*.pem`, `*.key`, `id_rsa*`, …), databases and dumps (`*.sqlite3`, `*.db`, `*.sql`, `*.bak`), `media/`, `backups/`, `.git/` internals, `.sessions/`, `.ssh`/`.aws`/`.netrc`-style credentials, `local_settings.py`, shell history. `list_dir` shows them as `(protected)`; `glob`/`grep` skip them. |
| Shell | Refused outright (no approval prompt): `sudo`; database clients (`sqlite3`, `psql`, …); `manage.py shell / dbshell / dumpdata / changepassword`; environment dumps (`env`, `printenv`, `export -p`, `/proc/*/environ`); credential helpers (`gh auth token`, `git credential`, …); any command that names a protected or outside path (`cat .env`, `cp media/… /tmp`, `cd ..`). |
| Shell environment | Child processes get an allow-list of runtime plumbing (`PATH`, `HOME`, `VIRTUAL_ENV`, `DJANGO_SETTINGS_MODULE`, …) — **no API keys, `SECRET_KEY` or `DATABASE_URL`**, not even Nolo's own OpenRouter key. |
| Redaction | Every tool result is scanned before it reaches the model: OpenRouter/OpenAI/Anthropic/Groq/Google/GitHub/Paystack-style tokens, JWTs, `user:password@` URLs, private-key blocks and `*_SECRET_KEY = '…'` literals are masked. Code that merely *reads* a setting (`os.getenv('SECRET_KEY', …)`) is left intact. |
| Process | Only `OPENROUTER_API_KEY` / `OPENAI_*` / `AGENT_*` / `NOLO_*` lines are lifted from a `.env` file into the agent process; the rest of the BlaqVibes `.env` never enters it. |

`/workspace` prints the root and rules; `/workspace <path>` and
`/workspace ! <command>` tell you what Nolo would do with them.

Tune it in `agent.config.json → "workspace"`:

```json
"workspace": {
  "protectedPaths": ["ops/vault", "*.tfstate"],
  "allowPaths": [".env.test"],
  "passEnv": ["SENTRY_ENVIRONMENT"],
  "blockedShell": ["\\brender\\s+deploy\\b"]
}
```

`npm test` runs the guard suite (`test/guard.test.ts`) against a throwaway
workspace with a fake `.env`, database, `media/` and `.git/`.

The system prompt matches the guard: Nolo is told it has no access to
accounts, payments or uploaded projects, that everything it says about
BlaqVibes must come from files in this repository (cite paths, never invent
endpoints or rules), and that web search is for third-party docs only.

### Slash commands

| Command | What it does |
|---------|--------------|
| `/model [query or id]` | Search the OpenRouter catalogue and switch models (`/model anthropic/claude-sonnet-4.5` switches directly) |
| `/new` | Fresh conversation + fresh session file |
| `/session` | Session file, message count, tokens and cost so far |
| `/export [file]` | Save the conversation as Markdown |
| `/approve [policy]` | Show or change the approval policy |
| `/workspace [path \| ! command]` | Show the workspace boundary, or test what the guard would do with a path/command |
| `/help` | List commands |

## Configuration

`agent.config.json` (in this folder, and optionally another one in the working
directory which wins):

```json
{
  "model": "anthropic/claude-sonnet-4.5",
  "maxSteps": 20,
  "maxCost": 1.0,
  "showBanner": true,
  "approvalPolicy": "always",
  "display": {
    "inputStyle": "plain",
    "toolDisplay": "grouped",
    "reasoning": false,
    "loader": { "text": "Nolo is working", "style": "spinner" }
  },
  "workspace": { "protectedPaths": [], "allowPaths": [], "passEnv": [], "blockedShell": [] }
}
```

Environment overrides: `AGENT_MODEL`, `AGENT_MAX_STEPS`, `AGENT_MAX_COST`,
`AGENT_APPROVAL`, `NOLO_CWD`.

`maxCost` is a hard stop per run (USD, as reported by OpenRouter). `maxSteps`
caps model round-trips per run. Both are stop conditions from
`@openrouter/agent/stop-conditions`; doom-loop detection is on as well.

## Project context for Nolo

Drop an `AGENTS.md`, `CLAUDE.md`, `NOLO.md` or `.agent-context.md` in the
working directory and its content is appended to the system prompt on every
run (capped at 12k characters).

## Layout

```
src/
  cli.ts            entry point: banner, REPL loop, one-shot mode, Ctrl-C handling
  config.ts         defaults + agent.config.json + env (key aliases)
  agent.ts          OpenRouter client, callModel loop, event stream, retries
  approval.ts       PermissionRequest hook → terminal y/n/always prompt
  system-prompt.ts  Nolo prompt + AGENTS.md/CLAUDE.md/NOLO.md composition
  renderer.ts       grouped / emoji / minimal / hidden tool display
  loader.ts         spinner / gradient / minimal loader
  input.ts          plain (queued readline), block, bordered input styles
  terminal-bg.ts    OSC-11 background detection for the block style
  session.ts        JSONL append-only session log (.sessions/)
  commands.ts       slash command registry + commands
  banner.ts         NOLO ASCII banner
  tools/            file-read, file-write, file-edit, glob, grep, list-dir, shell,
                    guard (workspace boundary, protected files, shell policy,
                    env scrubbing, redaction), policy (approval rules),
                    custom (template), index (wiring + redaction wrapper)
test/
  guard.test.ts     node:test suite for the guard (`npm test`)
```

## Adding a BlaqVibes-specific tool

Copy `src/tools/custom.ts`, implement `execute`, and add it to the array in
`src/tools/index.ts` wrapped in `guarded(...)` so its output is redacted like
the others. Resolve any path the model hands you with
`guardPath(path, 'read' | 'write')` and any command with
`checkShellCommand(cmd)` before acting. Set `requireApproval: true` (or a
function of the input) for anything that changes state. Run
`npm run typecheck` and `npm test` afterwards.
