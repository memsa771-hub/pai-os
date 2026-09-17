import { afterEach, describe, expect, it, vi } from 'vitest';
import { goToCentralLogin, goToCentralLogout } from './auth-redirects';

afterEach(() => vi.unstubAllGlobals());

describe('desktop account boundaries', () => {
  it('uses desktop sign-in instead of navigating the embedded app itself', () => {
    const signIn = vi.fn();
    const location = { hostname: 'workspace', href: 'pai://workspace/index.html#/team' };
    vi.stubGlobal('window', { location, __paiHost__: { signIn } });
    goToCentralLogin();
    expect(signIn).toHaveBeenCalledOnce();
    expect(location.href).toBe('pai://workspace/index.html#/team');
  });

  it('leaves navigation to the host after it clears its session', async () => {
    const signOut = vi.fn().mockResolvedValue(undefined);
    const location = { hostname: 'workspace', href: 'pai://workspace/index.html#/team' };
    vi.stubGlobal('window', { location, __paiHost__: {} });
    await goToCentralLogout(signOut);
    expect(signOut).toHaveBeenCalledOnce();
    expect(location.href).toBe('pai://workspace/index.html#/team');
  });
});

describe('web sign-in', () => {
  // The bug this pins: these used to redirect to openagents.org/login, and on
  // localhost fell through to a callback that is a no-op outside the desktop
  // app — so the workspace gate's "Log in" button did nothing, and signing out
  // was a one-way trip.
  it.each([
    ['localhost', 'http://localhost:3000/fb2fa89c'],
    ['placement-ai.com', 'https://placement-ai.com/fb2fa89c'],
    ['workspace.openagents.org', 'https://workspace.openagents.org/team'],
  ])('sends %s to the sign-in page on this origin', (hostname, href) => {
    const location = { hostname, href };
    vi.stubGlobal('window', { location });
    goToCentralLogin();
    expect(location.href).toBe('/sign-in');
  });

  it('does not depend on the fallback callback', () => {
    const fallback = vi.fn();
    const location = { hostname: 'localhost', href: 'http://localhost:3000/x' };
    vi.stubGlobal('window', { location });
    goToCentralLogin(fallback);
    expect(location.href).toBe('/sign-in');
  });

  it('lands on /sign-in after signing out, and forgets the workspace', async () => {
    const signOut = vi.fn().mockResolvedValue(undefined);
    const location = { hostname: 'localhost', href: 'http://localhost:3000/fb2fa89c' };
    const written: string[] = [];
    vi.stubGlobal('window', { location });
    vi.stubGlobal('document', { set cookie(v: string) { written.push(v); } });

    await goToCentralLogout(signOut);

    expect(signOut).toHaveBeenCalledOnce();
    expect(location.href).toBe('/sign-in');
    expect(written.some((c) => c.startsWith('oa_workspace=;'))).toBe(true);
    expect(written.some((c) => c.startsWith('oa_has_workspace=;'))).toBe(true);
    expect(written.every((c) => c.includes('max-age=0'))).toBe(true);
  });

  it('still signs out even if the sign-out call throws', async () => {
    const signOut = vi.fn().mockRejectedValue(new Error('already gone'));
    const location = { hostname: 'localhost', href: 'http://localhost:3000/x' };
    vi.stubGlobal('window', { location });
    vi.stubGlobal('document', { set cookie(_v: string) {} });
    await goToCentralLogout(signOut);
    expect(location.href).toBe('/sign-in');
  });
});
