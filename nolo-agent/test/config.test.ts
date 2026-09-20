/**
 * config.ts — only Nolo's own variables are lifted out of a .env file.
 * The BlaqVibes root .env holds SECRET_KEY, DATABASE_URL, PAYSTACK keys…;
 * none of that may enter the agent process.
 */
import { after, before, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';

import { loadConfig } from '../src/config.js';

let dir: string;
const originalCwd = process.cwd();

before(() => {
  dir = mkdtempSync(join(tmpdir(), 'nolo-config-'));
  writeFileSync(
    join(dir, '.env'),
    [
      '# BlaqVibes production-style .env',
      'SECRET_KEY=django-insecure-do-not-leak-me-0123456789',
      'DATABASE_URL=postgres://blaq:hunter2@db.internal:5432/blaqvibes',
      'PAYSTACK_SECRET_KEY=sk_live_abcdefghij0123456789',
      "export openai_key='sk-or-v1-fromdotenv0123456789abcdef0123456789'",
      'AGENT_MAX_STEPS=7',
      'NOLO_CWD=/somewhere',
      '',
    ].join('\n'),
  );
  for (const name of ['OPENROUTER_API_KEY', 'OPENAI_API_KEY', 'OPENAI_KEY', 'openai_key', 'AGENT_MAX_STEPS', 'SECRET_KEY', 'DATABASE_URL', 'PAYSTACK_SECRET_KEY', 'NOLO_CWD']) {
    delete process.env[name];
  }
  process.chdir(dir);
});

after(() => {
  process.chdir(originalCwd);
  rmSync(dir, { recursive: true, force: true });
});

describe('dotenv loading', () => {
  it('lifts only Nolo variables and leaves Django secrets out of the process', () => {
    const config = loadConfig();
    assert.equal(config.apiKey, 'sk-or-v1-fromdotenv0123456789abcdef0123456789');
    assert.equal(config.maxSteps, 7);
    assert.equal(process.env.SECRET_KEY, undefined);
    assert.equal(process.env.DATABASE_URL, undefined);
    assert.equal(process.env.PAYSTACK_SECRET_KEY, undefined);
    assert.equal(process.env.NOLO_CWD, '/somewhere');
  });

  it('ships an empty, overridable workspace section', () => {
    const config = loadConfig();
    assert.deepEqual(config.workspace, { protectedPaths: [], allowPaths: [], passEnv: [], blockedShell: [] });
    const custom = loadConfig({ workspace: { protectedPaths: ['ops/vault'], allowPaths: [], passEnv: [], blockedShell: [] } });
    assert.deepEqual(custom.workspace.protectedPaths, ['ops/vault']);
  });

  it('tells Nolo where the boundary is in the system prompt', () => {
    const config = loadConfig();
    assert.match(config.systemPrompt, /You are Nolo, the BlaqVibes assistant/);
    assert.match(config.systemPrompt, /Everything you say about BlaqVibes comes from this repository/);
    assert.match(config.systemPrompt, /no access to BlaqVibes accounts, emails, payments/);
    assert.match(config.systemPrompt, /Web search is only for third-party documentation/);
  });
});
