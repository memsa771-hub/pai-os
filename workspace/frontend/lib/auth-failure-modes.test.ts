import { afterEach, describe, expect, it, vi } from 'vitest';
import { AuthRejected, AuthUnreachable, authErrorFromStatus, refreshSession } from './supabase-auth';

afterEach(() => vi.unstubAllGlobals());

/**
 * "The server said no" and "I could not reach the server" are different facts,
 * and only the first one invalidates a session.
 *
 * Both surfaces used to collapse them: a bare `catch { clearSession() }` here,
 * and `catch { this._set(null) }` in the launcher's AccountManager.bearer().
 * A dropped wifi, a captive portal, a proxy hiccup or a Supabase blip during a
 * routine token refresh therefore signed the student out mid-session — and on
 * desktop that meant being thrown back to the sign-in screen with nothing on
 * screen explaining why.
 */
describe('auth failure classification', () => {
  it('treats credential refusals as refusals', () => {
    for (const status of [400, 401, 403, 404, 422]) {
      expect(authErrorFromStatus(status, 'no')).toBeInstanceOf(AuthRejected);
    }
  });

  it('treats timeouts, rate limits and provider faults as retryable', () => {
    // None of these is evidence about the credential.
    for (const status of [408, 429, 500, 502, 503, 504]) {
      expect(authErrorFromStatus(status, 'later')).toBeInstanceOf(AuthUnreachable);
    }
  });

  it('reports a transport failure as unreachable, not as a bad token', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    await expect(refreshSession('rt')).rejects.toBeInstanceOf(AuthUnreachable);
  });

  it('reports a provider outage as unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false, status: 503, json: async () => ({ msg: 'service unavailable' }),
    })));
    await expect(refreshSession('rt')).rejects.toBeInstanceOf(AuthUnreachable);
  });

  it('reports a revoked refresh token as a refusal', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: false, status: 400, json: async () => ({ error: 'invalid_grant' }),
    })));
    await expect(refreshSession('spent')).rejects.toBeInstanceOf(AuthRejected);
  });
});
