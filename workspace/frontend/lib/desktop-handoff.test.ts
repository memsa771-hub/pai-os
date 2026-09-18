import { afterEach, describe, expect, it, vi } from 'vitest';
import { desktopHost, type HostSession } from './desktop-host';

afterEach(() => vi.unstubAllGlobals());

/**
 * The desktop sign-in loop.
 *
 * The launcher signs the user in, then opens the embedded workspace view. The
 * view came up SIGNED OUT and rendered its own sign-in gate, moments after the
 * user had just signed in — and clicking that gate calls host.signIn(), which
 * main answers by signing the account out, so it went round again.
 *
 * The cause was a handoff that crossed no boundary: the preload wrote the
 * session to `localStorage.oa_workspace_session`, in the host's own shape, and
 * nothing in the web app reads that key. The app reads `oa_supabase_session`
 * and, in desktop, skips stored sessions entirely to wait for `onSession` —
 * which by its own contract only fires on a RENEWAL, never on first load.
 *
 * So the fix is that the bridge carries the opening session itself.
 */
describe('desktop session handoff', () => {
  const session: HostSession = {
    token: 'access-1',
    email: 'student@example.com',
    displayName: 'Student',
    expiresAt: Math.floor(Date.now() / 1000) + 3600,
  };

  it('exposes the opening session on the bridge', () => {
    vi.stubGlobal('window', { __paiHost__: { session, signIn() {}, signOut() {} } });
    expect(desktopHost()?.session).toEqual(session);
  });

  it('tolerates an older preload that has no session field', () => {
    // A newer bundle can briefly run against an older preload in dev; that
    // must degrade to "no session yet", not throw.
    vi.stubGlobal('window', { __paiHost__: { signIn() {}, signOut() {} } });
    expect(desktopHost()?.session).toBeUndefined();
  });

  it('is absent on the web, where the app owns its own session', () => {
    vi.stubGlobal('window', {});
    expect(desktopHost()).toBeNull();
  });

  it('does not rely on the storage key the preload writes', async () => {
    // Pinning the mismatch that caused this: if someone "fixes" the handoff by
    // writing oa_workspace_session again, this says why that will not work.
    const { readFileSync } = await import('node:fs');
    const app = readFileSync('lib/auth-session.ts', 'utf8');
    expect(app).toContain("'oa_supabase_session'");
    expect(app).not.toContain('oa_workspace_session');
  });
});
