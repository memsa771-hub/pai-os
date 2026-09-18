import { describe, expect, it, vi, afterEach } from 'vitest';
import { API_URL, DEFAULT_API_URL, DEFAULT_APP_URL, SIGN_IN_PATH } from './config';
import { isPaiHostname } from './pai-auth-context';
import { goToSignIn, signOutAndReturnToSignIn } from './auth-redirects';

afterEach(() => vi.unstubAllGlobals());

// Placement AI is its own product. The hosted app and every auth route live on
// app.placement-ai.com; the backend on api.placement-ai.com. No OpenAgents
// host takes part in hosted sign-in, sign-out or API traffic.
describe('canonical Placement AI origins', () => {
  it('defaults the API to api.placement-ai.com', () => {
    expect(DEFAULT_API_URL).toBe('https://api.placement-ai.com');
    // Whatever this build resolved to, it is never the retired endpoint.
    expect(API_URL).not.toContain('openagents.org');
  });

  it('names app.placement-ai.com as the application origin', () => {
    expect(DEFAULT_APP_URL).toBe('https://app.placement-ai.com');
  });

  it('spells "placement" correctly', () => {
    // A typo'd host here is a silent production outage: DNS, the Supabase
    // redirect allowlist and CORS all have to agree on one spelling.
    for (const url of [DEFAULT_API_URL, DEFAULT_APP_URL]) {
      expect(url).toMatch(/^https:\/\/(api|app)\.placement-ai\.com$/);
    }
  });

  it('recognises the hosted app origin as our own', () => {
    expect(isPaiHostname('app.placement-ai.com')).toBe(true);
  });

  it('no longer treats any OpenAgents host as a PAI auth origin', () => {
    expect(isPaiHostname('workspace.openagents.org')).toBe(false);
    expect(isPaiHostname('openagents.org')).toBe(false);
  });
});

describe('hosted auth never leaves Placement AI', () => {
  const onPaiApp = () => {
    const location = { hostname: 'app.placement-ai.com', href: 'https://app.placement-ai.com/fb2fa89c' };
    vi.stubGlobal('window', { location });
    vi.stubGlobal('document', { set cookie(_v: string) {} });
    return location;
  };

  it('sends login to /sign-in on this origin', () => {
    const location = onPaiApp();
    goToSignIn();
    expect(location.href).toBe(SIGN_IN_PATH);
    expect(location.href).not.toContain('openagents');
  });

  it('sends logout to /sign-in on this origin', async () => {
    const location = onPaiApp();
    await signOutAndReturnToSignIn(async () => {});
    expect(location.href).toBe(SIGN_IN_PATH);
    expect(location.href).not.toContain('openagents');
  });

  it('keeps localhost development on its own origin', async () => {
    const location = { hostname: 'localhost', href: 'http://localhost:3000/x' };
    vi.stubGlobal('window', { location });
    vi.stubGlobal('document', { set cookie(_v: string) {} });
    goToSignIn();
    expect(location.href).toBe(SIGN_IN_PATH);
  });
});
