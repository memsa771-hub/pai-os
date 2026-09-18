import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';

/**
 * The product rule, enforced against the source rather than trusted:
 *
 *   Placement AI owns login. Supabase owns authentication.
 *   OpenAgents owns nothing in the login/logout flow.
 *
 * A grep in a terminal proves this once. A test proves it on every commit,
 * which matters because this exact regression has already happened three
 * times — a redirect in app/page.tsx, one in auth-redirects.ts, and a whole
 * update feed — each found only by reading the code again.
 */

// Every file that participates in deciding where an unauthenticated or
// signing-out user goes.
const AUTH_FLOW_FILES = [
  'lib/auth-redirects.ts',
  'lib/pai-auth-context.tsx',
  'lib/config.ts',
  'lib/supabase-auth.ts',
  'lib/auth-api.ts',
  'app/sign-in/page.tsx',
  'app/sign-up/page.tsx',
  'app/auth/callback/page.tsx',
  'app/page.tsx',
  'app/[workspaceId]/page.tsx',
  'components/layout/user-menu.tsx',
];

/** Source with comments removed — a comment naming the old flow is history, not a route. */
function code(path: string): string {
  const raw = readFileSync(path, 'utf8');
  return raw
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
}

describe('no OpenAgents host in the login/logout flow', () => {
  it.each(AUTH_FLOW_FILES)('%s routes nowhere near OpenAgents', (file) => {
    const src = code(file);
    for (const forbidden of [
      'openagents.org/login',
      'openagents.org/logout',
      'workspace.openagents.org',
      'workspace-endpoint.openagents.org',
    ]) {
      expect(src).not.toContain(forbidden);
    }
  });

  it('only auth-redirects names .openagents.org, and only to expire a stale cookie', () => {
    const offenders = AUTH_FLOW_FILES.filter((f) => code(f).includes('.openagents.org'));
    expect(offenders).toEqual(['lib/auth-redirects.ts']);
    // ...and only as an expiry, never as a destination.
    const src = code('lib/auth-redirects.ts');
    expect(src).toContain('max-age=0');
    expect(src).not.toMatch(/location\.href\s*=\s*['"`]https?:\/\/[^'"`]*openagents/);
  });

  it('sends the user off-origin only to Supabase, for authentication', () => {
    // Supabase owns authentication, so its authorize URL is the one external
    // navigation the flow is allowed to make.
    const src = code('lib/supabase-auth.ts');
    expect(src).toContain('window.location.href = url');
    expect(src).toContain('/authorize?');
  });
});

describe('the whole repository, for the routes that matter', () => {
  it('has no hosted-PAI login or logout pointing at OpenAgents', () => {
    // Tracked files only, so build output and node_modules cannot mask a hit.
    const files = execFileSync('git', ['ls-files', '*.ts', '*.tsx', '*.mjs', '*.conf'], {
      cwd: '../..', encoding: 'utf8',
    }).trim().split('\n');

    const hits: string[] = [];
    for (const rel of files) {
      if (rel.includes('/docs/') || rel.startsWith('docs/') || rel.includes('/sdk/')) continue;
      let src: string;
      try { src = readFileSync(`../../${rel}`, 'utf8'); } catch { continue; }
      src = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
      if (/https?:\/\/[^\s'"`]*openagents\.org\/(login|logout)/.test(src)) hits.push(rel);
    }
    expect(hits).toEqual([]);
  });
});
