import { afterEach, describe, expect, it, vi } from 'vitest';
import { publicAppUrl } from './desktop-host';

describe('publicAppUrl', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('uses the browser origin on the web', () => {
    vi.stubGlobal('window', { location: { origin: 'https://app.placement-ai.com' } });
    expect(publicAppUrl('/share/token')).toBe('https://app.placement-ai.com/share/token');
  });

  it('uses the host app URL inside the desktop protocol', () => {
    vi.stubGlobal('window', {
      location: { origin: 'pai://workspace' },
      __paiHost__: { appUrl: 'http://localhost:3000/', signIn() {}, signOut() {} },
    });
    expect(publicAppUrl('/share/token')).toBe('http://localhost:3000/share/token');
  });
});
